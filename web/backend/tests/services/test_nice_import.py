from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from sqlmodel import Session, delete, select

from app.core.db import engine
from app.models import (
    Chunk,
    Document,
    NiceDownload,
    NiceImportItem,
    NiceImportItemStatus,
    NiceImportJob,
    NiceImportJobStatus,
    NiceImportLimit,
    User,
)
from app.services import nice as nice_service
from app.services import nice_import, nice_import_scheduler


def _listing_html(*refs: str) -> str:
    documents = [
        {
            "guidanceRef": ref,
            "title": f"{ref} title",
            "pathAndQuery": f"/guidance/{ref.lower()}",
        }
        for ref in refs
    ]
    payload = {
        "props": {
            "pageProps": {
                "results": {"resultCount": len(documents), "documents": documents}
            }
        }
    }
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}</script></body></html>"
    )


def _overview_html(slug: str) -> str:
    return (
        f"<html><head><title>{slug.upper()} title | Guidance | NICE</title></head>"
        "<body>"
        "<nav class='stacked-nav'>"
        f"<a href='/guidance/{slug}/chapter/Recommendations'>Recommendations</a>"
        "</nav>"
        f"<a href='{nice_service.BASE_URL}/guidance/{slug}/chapter/Recommendations-for-research'>Body link</a>"
        f"<a class='prev-next__link' href='/guidance/{slug}/chapter/Recommendations'>Next page Recommendations</a>"
        "</body></html>"
    )


CHAPTER_HTML = """
<html><body>
<div class="chapter">
  <h2>Recommendations</h2>
  <p>Use cached imports for public routes.</p>
</div>
</body></html>
"""


def _mock_client(*refs: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/guidance/published":
            return httpx.Response(200, text=_listing_html(*refs))
        if "/chapter/" in path:
            return httpx.Response(200, text=CHAPTER_HTML)
        slug = path.rsplit("/", 1)[-1]
        return httpx.Response(200, text=_overview_html(slug))

    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url=nice_service.BASE_URL
    )


def _clear_import_state(db: Session) -> None:
    db.rollback()
    db.expunge_all()
    nice_document_ids = db.exec(
        select(Document.id).where(Document.external_id.like("nice-%"))  # type: ignore[attr-defined]
    ).all()
    if nice_document_ids:
        db.execute(delete(Chunk).where(Chunk.document_id.in_(nice_document_ids)))
        db.execute(delete(Document).where(Document.id.in_(nice_document_ids)))
    db.execute(delete(NiceImportItem))
    db.execute(delete(NiceImportJob))
    db.execute(delete(NiceDownload))
    db.commit()
    db.expunge_all()


@pytest.fixture(autouse=True)
def _clean_import_state(db: Session):
    _clear_import_state(db)
    yield
    _clear_import_state(db)


def _user(db: Session) -> User:
    user = db.exec(select(User)).first()
    assert user is not None
    return user


def _job(db: Session, *, limit: int | str = 10) -> NiceImportJob:
    return nice_import.create_import_job(db, limit=limit, started_by=_user(db))


def test_create_import_job_allows_only_one_active_job(db: Session) -> None:
    first = _job(db)

    with Session(engine) as second_session:
        with pytest.raises(nice_import.NiceImportAlreadyRunningError):
            nice_import.create_import_job(
                second_session, limit=20, started_by=_user(second_session)
            )

    first.status = NiceImportJobStatus.completed
    first.active_slot = None
    db.add(first)
    db.commit()

    second = nice_import.create_import_job(db, limit="all", started_by=_user(db))
    assert second.requested_limit == NiceImportLimit.all
    assert second.target_count is None


def test_claim_job_is_one_winner_and_recovers_stale_lease(db: Session) -> None:
    job = _job(db)
    now = nice_import.utcnow()

    with Session(engine) as session_a:
        claimed = nice_import.claim_job(session_a, worker_id="worker-a", now=now)
        assert claimed is not None
        assert claimed.id == job.id

    with Session(engine) as session_b:
        assert nice_import.claim_job(session_b, worker_id="worker-b", now=now) is None

    stale = db.get(NiceImportJob, job.id)
    assert stale is not None
    stale.lease_expires_at = now - timedelta(seconds=1)
    db.add(stale)
    db.commit()

    with Session(engine) as session_b:
        recovered = nice_import.claim_job(
            session_b, worker_id="worker-b", now=now + timedelta(seconds=1)
        )
        assert recovered is not None
        assert recovered.lease_owner == "worker-b"


def test_claim_next_item_is_one_winner_and_recovers_stale_item(db: Session) -> None:
    job = _job(db)
    item = NiceImportItem(
        job_id=job.id or 0,
        reference="NG1",
        slug="ng1",
        title="NG1 title",
        page_url="https://www.nice.org.uk/guidance/ng1",
    )
    db.add(item)
    db.commit()
    now = nice_import.utcnow()

    with Session(engine) as session_a:
        claimed = nice_import.claim_next_item(session_a, job_id=job.id or 0, now=now)
        assert claimed is not None
        assert claimed.status == NiceImportItemStatus.in_progress
        assert claimed.attempt_count == 1

    with Session(engine) as session_b:
        assert nice_import.claim_next_item(session_b, job_id=job.id or 0, now=now) is None

    stale = db.get(NiceImportItem, item.id)
    assert stale is not None
    stale.lease_expires_at = now - timedelta(seconds=1)
    db.add(stale)
    db.commit()

    with Session(engine) as session_b:
        recovered = nice_import.claim_next_item(
            session_b, job_id=job.id or 0, now=now + timedelta(seconds=1)
        )
        assert recovered is not None
        assert recovered.attempt_count == 2


def test_discover_import_items_skips_existing_downloads(db: Session) -> None:
    job = _job(db, limit=10)
    db.add(
        NiceDownload(
            reference="NG1",
            slug="ng1",
            title="Existing",
            page_url="https://www.nice.org.uk/guidance/ng1",
            pdf_url="https://www.nice.org.uk/guidance/ng1",
            content="cached",
        )
    )
    db.commit()

    queued = nice_import.discover_import_items(db, job=job, client=_mock_client("NG1", "NG2"))

    assert queued == 1
    item = db.exec(select(NiceImportItem)).one()
    assert item.reference == "NG2"
    db.refresh(job)
    assert job.target_count == 10


def test_discover_import_items_tolerates_duplicate_queue_attempts(db: Session) -> None:
    job = _job(db, limit=10)
    client = _mock_client("NG1", "NG2")

    assert nice_import.discover_import_items(db, job=job, client=client) == 2
    assert nice_import.discover_import_items(db, job=job, client=_mock_client("NG1", "NG2")) == 0

    refs = db.exec(select(NiceImportItem.reference)).all()
    assert sorted(refs) == ["NG1", "NG2"]


@pytest.mark.parametrize(("limit", "expected"), [(10, 10), (20, 20)])
def test_discover_import_items_caps_finite_limits_before_full_listing_page(
    db: Session, limit: int, expected: int
) -> None:
    job = _job(db, limit=limit)
    refs = [f"NG{index}" for index in range(1, 61)]

    queued = nice_import.discover_import_items(db, job=job, client=_mock_client(*refs))

    assert queued == expected
    queued_refs = db.exec(
        select(NiceImportItem.reference).where(NiceImportItem.job_id == job.id)
    ).all()
    assert len(queued_refs) == expected
    db.refresh(job)
    assert job.target_count == expected


def test_process_claimed_item_materializes_download_and_updates_counters(
    db: Session,
) -> None:
    job = _job(db)
    now = nice_import.utcnow()
    assert nice_import.claim_job(db, worker_id="worker-a", now=now) is not None
    item = NiceImportItem(
        job_id=job.id or 0,
        reference="NG1",
        slug="ng1",
        title="NG1 title",
        page_url="https://www.nice.org.uk/guidance/ng1",
        status=NiceImportItemStatus.in_progress,
        attempt_count=1,
        lease_expires_at=now + timedelta(seconds=nice_import.ITEM_LEASE_SECONDS),
    )
    db.add(item)
    db.commit()

    assert nice_import.process_claimed_item(
        db, item=item, worker_id="worker-a", client=_mock_client("NG1"), now=now
    )

    db.refresh(job)
    db.refresh(item)
    assert item.status == NiceImportItemStatus.completed
    assert job.completed_count == 1
    download = db.exec(select(NiceDownload).where(NiceDownload.reference == "NG1")).one()
    document = db.exec(
        select(Document).where(Document.external_id == f"nice-{download.slug}")
    ).one()
    assert document.content.startswith("# Recommendations")


def test_failed_item_retries_then_fails_without_stopping_job(db: Session) -> None:
    job = _job(db)
    now = nice_import.utcnow()
    assert nice_import.claim_job(db, worker_id="worker-a", now=now) is not None
    item = NiceImportItem(
        job_id=job.id or 0,
        reference="NG404",
        slug="ng404",
        title="NG404 title",
        page_url="https://www.nice.org.uk/guidance/ng404",
        status=NiceImportItemStatus.in_progress,
        attempt_count=1,
        lease_expires_at=now + timedelta(seconds=nice_import.ITEM_LEASE_SECONDS),
    )
    db.add(item)
    db.commit()
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))

    assert not nice_import.process_claimed_item(
        db, item=item, worker_id="worker-a", client=client, now=now
    )
    db.refresh(item)
    assert item.status == NiceImportItemStatus.retry_pending
    assert item.next_attempt_at is not None
    assert job.status == NiceImportJobStatus.running

    item.status = NiceImportItemStatus.in_progress
    item.attempt_count = nice_import.MAX_ITEM_ATTEMPTS
    item.lease_expires_at = now + timedelta(seconds=nice_import.ITEM_LEASE_SECONDS)
    db.add(item)
    db.commit()

    assert not nice_import.process_claimed_item(
        db, item=item, worker_id="worker-a", client=client, now=now
    )
    db.refresh(item)
    db.refresh(job)
    assert item.status == NiceImportItemStatus.failed
    assert job.failed_count == 1


def test_stale_worker_cannot_persist_after_job_lease_loss(db: Session) -> None:
    job = _job(db)
    now = nice_import.utcnow()
    assert nice_import.claim_job(db, worker_id="worker-a", now=now) is not None
    item = NiceImportItem(
        job_id=job.id or 0,
        reference="NG1",
        slug="ng1",
        title="NG1 title",
        page_url="https://www.nice.org.uk/guidance/ng1",
        status=NiceImportItemStatus.in_progress,
        attempt_count=1,
        lease_expires_at=now + timedelta(seconds=nice_import.ITEM_LEASE_SECONDS),
    )
    db.add(item)
    db.commit()

    job.lease_owner = "worker-b"
    job.lease_expires_at = now + timedelta(seconds=nice_import.JOB_LEASE_SECONDS)
    db.add(job)
    db.commit()

    assert not nice_import.process_claimed_item(
        db, item=item, worker_id="worker-a", client=_mock_client("NG1"), now=now
    )

    db.refresh(item)
    db.refresh(job)
    assert item.status == NiceImportItemStatus.in_progress
    assert job.completed_count == 0


def test_worker_waits_for_retry_pending_work(db: Session) -> None:
    job = _job(db)
    now = nice_import.utcnow()
    item = NiceImportItem(
        job_id=job.id or 0,
        reference="NG1",
        slug="ng1",
        title="NG1 title",
        page_url="https://www.nice.org.uk/guidance/ng1",
        status=NiceImportItemStatus.retry_pending,
        next_attempt_at=now + timedelta(seconds=5),
    )
    db.add(item)
    db.commit()
    assert nice_import.claim_job(db, worker_id="worker-a", now=now) is not None

    step = nice_import._run_worker_step(  # noqa: SLF001
        lambda: Session(engine),
        worker_id="worker-a",
        client=_mock_client("NG1"),
        stop_requested=lambda: False,
    )

    assert step.should_continue
    assert 0 < step.sleep_seconds <= 30


def test_worker_stays_alive_after_retryable_lock_during_processing(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job(db)
    item = NiceImportItem(
        job_id=job.id or 0,
        reference="NG1",
        slug="ng1",
        title="NG1 title",
        page_url="https://www.nice.org.uk/guidance/ng1",
    )
    db.add(item)
    db.commit()
    sleeps: list[float] = []
    calls = 0

    def raise_lock(*_args, **_kwargs) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise nice_import.OperationalError(
                "UPDATE nice_import_item",
                {},
                Exception("database is locked"),
            )
        return False

    def stop_requested() -> bool:
        return bool(sleeps)

    monkeypatch.setattr(nice_import, "process_claimed_item", raise_lock)
    nice_import.run_import_worker(
        lambda: Session(engine),
        worker_id="worker-a",
        stop_requested=stop_requested,
        sleep=sleeps.append,
        client_factory=lambda: _mock_client("NG1"),
    )

    assert sleeps
    assert calls == 1


def test_scheduler_ensure_started_is_process_local_and_debounced(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []

    class FakeThread:
        def __init__(self, **kwargs) -> None:
            self._alive = False

        def start(self) -> None:
            self._alive = True
            started.append("start")

        def is_alive(self) -> bool:
            return self._alive

        def join(self, timeout: float | None = None) -> None:
            self._alive = False

    monkeypatch.setattr(nice_import_scheduler.threading, "Thread", FakeThread)
    monkeypatch.setattr(nice_import_scheduler, "_thread", None)
    monkeypatch.setattr(nice_import_scheduler, "_last_nudge_at", 0.0)

    assert nice_import_scheduler.ensure_started(monotonic=lambda: 10.0)
    assert not nice_import_scheduler.ensure_started(monotonic=lambda: 10.5)
    assert started == ["start"]

    nice_import_scheduler.shutdown()
