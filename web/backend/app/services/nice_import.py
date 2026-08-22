from __future__ import annotations

import logging
import random
import socket
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from app.models import (
    NiceDownload,
    NiceImportItem,
    NiceImportItemStatus,
    NiceImportJob,
    NiceImportJobStatus,
    NiceImportLimit,
    User,
)
from app.services.nice import (
    PAGE_SIZE,
    REQUEST_TIMEOUT,
    USER_AGENT,
    GuidanceRef,
    NiceFetchError,
    build_guideline_text,
    get_or_create_nice_dataset,
    list_published_guidance,
    materialize_nice_document,
)

logger = logging.getLogger(__name__)

JOB_LEASE_SECONDS = 300
ITEM_LEASE_SECONDS = 180
MAX_ITEM_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 60
SCRAPE_DELAY_SECONDS = 15.0
SCRAPE_DELAY_JITTER_SECONDS = 3.0

_RETRYABLE_LOCK_ERRORS = ("database is locked", "database table is locked")


class NiceImportAlreadyRunningError(RuntimeError):
    """Raised when an active NICE import job already exists."""


class NiceImportNotFoundError(RuntimeError):
    """Raised when a NICE import job cannot be found."""


@dataclass(frozen=True)
class WorkerStep:
    should_continue: bool
    sleep_seconds: float


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_worker_id() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4()}"


def normalize_limit(limit: int | str | NiceImportLimit) -> NiceImportLimit:
    if isinstance(limit, NiceImportLimit):
        return limit
    try:
        return NiceImportLimit(str(limit).lower())
    except ValueError as exc:
        raise ValueError("NICE import limit must be 10, 20, 50, or all") from exc


def target_count_for_limit(limit: NiceImportLimit) -> int | None:
    if limit == NiceImportLimit.all:
        return None
    return int(limit.value)


def create_import_job(
    session: Session, *, limit: int | str | NiceImportLimit, started_by: User
) -> NiceImportJob:
    requested_limit = normalize_limit(limit)
    job = NiceImportJob(
        status=NiceImportJobStatus.pending,
        requested_limit=requested_limit,
        target_count=target_count_for_limit(requested_limit),
        started_by_user_id=started_by.id,
        active_slot=1,
    )
    session.add(job)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise NiceImportAlreadyRunningError("A NICE import is already running") from exc
    session.refresh(job)
    return job


def get_current_or_recent_job(session: Session) -> NiceImportJob | None:
    active = session.exec(
        select(NiceImportJob)
        .where(NiceImportJob.active_slot == 1)
        .order_by(NiceImportJob.created_at.desc())  # type: ignore[attr-defined]
    ).first()
    if active is not None:
        return active
    return session.exec(
        select(NiceImportJob).order_by(NiceImportJob.created_at.desc())  # type: ignore[attr-defined]
    ).first()


def has_unfinished_jobs(session: Session) -> bool:
    return (
        session.exec(
            select(NiceImportJob.id).where(
                NiceImportJob.status.in_(
                    [NiceImportJobStatus.pending, NiceImportJobStatus.running]
                )
            )
        ).first()
        is not None
    )


def claim_job(session: Session, *, worker_id: str, now: datetime | None = None) -> NiceImportJob | None:
    now = now or utcnow()
    lease_expires_at = now + timedelta(seconds=JOB_LEASE_SECONDS)
    result = session.execute(
        text(
            """
            UPDATE nice_import_job
            SET status = 'running',
                lease_owner = :worker_id,
                lease_expires_at = :lease_expires_at,
                heartbeat_at = :now,
                started_at = COALESCE(started_at, :now),
                updated_at = :now
            WHERE id = (
                SELECT id
                FROM nice_import_job
                WHERE active_slot = 1
                  AND status IN ('pending', 'running')
                  AND (lease_expires_at IS NULL OR lease_expires_at <= :now OR lease_owner = :worker_id)
                ORDER BY created_at ASC, id ASC
                LIMIT 1
            )
            """
        ),
        {
            "worker_id": worker_id,
            "lease_expires_at": lease_expires_at,
            "now": now,
        },
    )
    session.commit()
    if result.rowcount != 1:
        return None
    return session.exec(
        select(NiceImportJob).where(
            NiceImportJob.lease_owner == worker_id,
            NiceImportJob.active_slot == 1,
        )
    ).first()


def heartbeat_job(
    session: Session, *, job_id: int, worker_id: str, now: datetime | None = None
) -> bool:
    now = now or utcnow()
    result = session.execute(
        text(
            """
            UPDATE nice_import_job
            SET lease_expires_at = :lease_expires_at,
                heartbeat_at = :now,
                updated_at = :now
            WHERE id = :job_id
              AND lease_owner = :worker_id
              AND status = 'running'
              AND active_slot = 1
            """
        ),
        {
            "job_id": job_id,
            "worker_id": worker_id,
            "lease_expires_at": now + timedelta(seconds=JOB_LEASE_SECONDS),
            "now": now,
        },
    )
    session.commit()
    return result.rowcount == 1


def cancel_job(session: Session, *, job_id: int, now: datetime | None = None) -> NiceImportJob:
    now = now or utcnow()
    job = session.get(NiceImportJob, job_id)
    if job is None:
        raise NiceImportNotFoundError("NICE import job not found")
    if job.status in {
        NiceImportJobStatus.completed,
        NiceImportJobStatus.failed,
        NiceImportJobStatus.cancelled,
    }:
        return job
    job.status = NiceImportJobStatus.cancelled
    job.finished_at = now
    job.active_slot = None
    job.lease_owner = None
    job.lease_expires_at = None
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def discover_import_items(
    session: Session,
    *,
    job: NiceImportJob,
    client: httpx.Client,
    page_size: int = PAGE_SIZE,
) -> int:
    first_page, total = list_published_guidance(client, page=1, page_size=page_size)
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    target = job.target_count
    queued = _queue_refs(
        session,
        job_id=job.id or 0,
        refs=first_page,
        remaining=target,
    )
    if target is not None and queued >= target:
        _update_target_count(session, job.id or 0, target)
        return queued

    for page in range(2, pages + 1):
        refs = list_published_guidance(client, page=page, page_size=page_size)[0]
        remaining = None if target is None else target - queued
        queued += _queue_refs(
            session,
            job_id=job.id or 0,
            refs=refs,
            remaining=remaining,
        )
        if target is not None and queued >= target:
            break

    _update_target_count(session, job.id or 0, queued if target is None else target)
    return queued


def _queue_refs(
    session: Session,
    *,
    job_id: int,
    refs: list[GuidanceRef],
    remaining: int | None = None,
) -> int:
    queued = 0
    if remaining is not None and remaining <= 0:
        return queued
    downloaded = set(session.exec(select(NiceDownload.reference)).all())
    existing = set(
        session.exec(
            select(NiceImportItem.reference).where(NiceImportItem.job_id == job_id)
        ).all()
    )
    for ref in refs:
        if remaining is not None and queued >= remaining:
            break
        if ref.ref in downloaded or ref.ref in existing:
            continue
        now = utcnow()
        result = session.execute(
            text(
                """
                INSERT OR IGNORE INTO nice_import_item
                    (job_id, reference, slug, title, page_url, status,
                     attempt_count, created_at, updated_at)
                VALUES
                    (:job_id, :reference, :slug, :title, :page_url, 'pending',
                     0, :now, :now)
                """
            ),
            {
                "job_id": job_id,
                "reference": ref.ref,
                "slug": ref.slug,
                "title": ref.title,
                "page_url": ref.page_url,
                "now": now,
            },
        )
        added = max(result.rowcount or 0, 0)
        queued += added
        if added:
            existing.add(ref.ref)
    session.commit()
    return queued


def _update_target_count(session: Session, job_id: int, target_count: int) -> None:
    job = session.get(NiceImportJob, job_id)
    if job is None:
        return
    job.target_count = target_count
    session.add(job)
    session.commit()


def claim_next_item(
    session: Session, *, job_id: int, now: datetime | None = None
) -> NiceImportItem | None:
    now = now or utcnow()
    lease_expires_at = now + timedelta(seconds=ITEM_LEASE_SECONDS)
    result = session.execute(
        text(
            """
            UPDATE nice_import_item
            SET status = 'in_progress',
                lease_expires_at = :lease_expires_at,
                last_attempt_at = :now,
                attempt_count = attempt_count + 1,
                updated_at = :now
            WHERE id = (
                SELECT id
                FROM nice_import_item
                WHERE job_id = :job_id
                  AND (
                    status = 'pending'
                    OR (status = 'retry_pending' AND (next_attempt_at IS NULL OR next_attempt_at <= :now))
                    OR (status = 'in_progress' AND lease_expires_at <= :now)
                  )
                ORDER BY created_at ASC, id ASC
                LIMIT 1
            )
            """
        ),
        {"job_id": job_id, "lease_expires_at": lease_expires_at, "now": now},
    )
    session.commit()
    if result.rowcount != 1:
        return None
    return session.exec(
        select(NiceImportItem)
        .where(NiceImportItem.job_id == job_id)
        .where(NiceImportItem.status == NiceImportItemStatus.in_progress)
        .order_by(NiceImportItem.last_attempt_at.desc(), NiceImportItem.id.desc())  # type: ignore[attr-defined]
    ).first()


def process_claimed_item(
    session: Session,
    *,
    item: NiceImportItem,
    worker_id: str,
    requested_by: User | None = None,
    client: httpx.Client,
    now: datetime | None = None,
) -> bool:
    now = now or utcnow()
    try:
        content, section_count, title = build_guideline_text(
            client,
            GuidanceRef(
                ref=item.reference,
                slug=item.slug,
                title=item.title,
                page_url=item.page_url,
            ),
        )
        download = _create_or_get_download(
            session,
            item=item,
            title=title,
            content=content,
            section_count=section_count,
            requested_by=requested_by,
        )
        dataset = get_or_create_nice_dataset(session)
        materialize_nice_document(session, dataset_id=dataset.id or 0, download=download)
        if not _complete_item_if_claimed(
            session, item=item, worker_id=worker_id, now=utcnow()
        ):
            session.rollback()
            return False
        session.commit()
        return True
    except (httpx.HTTPError, NiceFetchError) as exc:
        _mark_item_failed_or_retry(
            session, item=item, worker_id=worker_id, error=str(exc), now=utcnow()
        )
        return False


def _complete_item_if_claimed(
    session: Session, *, item: NiceImportItem, worker_id: str, now: datetime
) -> bool:
    result = session.execute(
        text(
            """
            UPDATE nice_import_item
            SET status = 'completed',
                last_error = NULL,
                next_attempt_at = NULL,
                lease_expires_at = NULL,
                updated_at = :now
            WHERE id = :item_id
              AND status = 'in_progress'
              AND lease_expires_at > :now
              AND EXISTS (
                SELECT 1 FROM nice_import_job
                WHERE id = nice_import_item.job_id
                  AND lease_owner = :worker_id
                  AND lease_expires_at > :now
                  AND status = 'running'
                  AND active_slot = 1
              )
            """
        ),
        {"item_id": item.id, "worker_id": worker_id, "now": now},
    )
    if result.rowcount != 1:
        return False
    _increment_job_counter(session, item.job_id, completed=1, failed=0)
    return True


def _create_or_get_download(
    session: Session,
    *,
    item: NiceImportItem,
    title: str,
    content: str,
    section_count: int,
    requested_by: User | None,
) -> NiceDownload:
    existing = session.exec(
        select(NiceDownload).where(NiceDownload.reference == item.reference)
    ).first()
    if existing is not None:
        return existing
    download = NiceDownload(
        reference=item.reference,
        slug=item.slug,
        title=title,
        page_url=item.page_url,
        pdf_url=item.page_url,
        page_count=section_count,
        char_count=len(content),
        content=content,
        requested_by_user_id=requested_by.id if requested_by else None,
    )
    session.add(download)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return session.exec(
            select(NiceDownload).where(NiceDownload.reference == item.reference)
        ).one()
    return download


def _increment_job_counter(
    session: Session, job_id: int, *, completed: int, failed: int
) -> None:
    session.execute(
        text(
            """
            UPDATE nice_import_job
            SET completed_count = completed_count + :completed,
                failed_count = failed_count + :failed,
                updated_at = :now
            WHERE id = :job_id
            """
        ),
        {
            "job_id": job_id,
            "completed": completed,
            "failed": failed,
            "now": utcnow(),
        },
    )


def _mark_item_failed_or_retry(
    session: Session, *, item: NiceImportItem, worker_id: str, error: str, now: datetime
) -> None:
    if item.attempt_count >= MAX_ITEM_ATTEMPTS:
        result = session.execute(
            text(
                """
                UPDATE nice_import_item
                SET status = 'failed',
                    last_error = :error,
                    next_attempt_at = NULL,
                    lease_expires_at = NULL,
                    updated_at = :now
                WHERE id = :item_id
                  AND status = 'in_progress'
                  AND lease_expires_at > :now
                  AND EXISTS (
                    SELECT 1 FROM nice_import_job
                    WHERE id = nice_import_item.job_id
                      AND lease_owner = :worker_id
                      AND lease_expires_at > :now
                      AND status = 'running'
                      AND active_slot = 1
                  )
                """
            ),
            {
                "item_id": item.id,
                "worker_id": worker_id,
                "error": error[:1000],
                "now": now,
            },
        )
        if result.rowcount == 1:
            _increment_job_counter(session, item.job_id, completed=0, failed=1)
    else:
        session.execute(
            text(
                """
                UPDATE nice_import_item
                SET status = 'retry_pending',
                    last_error = :error,
                    next_attempt_at = :next_attempt_at,
                    lease_expires_at = NULL,
                    updated_at = :now
                WHERE id = :item_id
                  AND status = 'in_progress'
                  AND lease_expires_at > :now
                  AND EXISTS (
                    SELECT 1 FROM nice_import_job
                    WHERE id = nice_import_item.job_id
                      AND lease_owner = :worker_id
                      AND lease_expires_at > :now
                      AND status = 'running'
                      AND active_slot = 1
                  )
                """
            ),
            {
                "item_id": item.id,
                "worker_id": worker_id,
                "error": error[:1000],
                "next_attempt_at": now
                + timedelta(seconds=RETRY_BACKOFF_SECONDS * item.attempt_count),
                "now": now,
            },
        )
    session.commit()


def finish_job_if_done(
    session: Session, *, job_id: int, worker_id: str, now: datetime | None = None
) -> NiceImportJob | None:
    now = now or utcnow()
    job = session.get(NiceImportJob, job_id)
    if job is None or job.lease_owner != worker_id:
        return job
    pending = session.exec(
        select(NiceImportItem.id)
        .where(NiceImportItem.job_id == job_id)
        .where(
            NiceImportItem.status.in_(
                [
                    NiceImportItemStatus.pending,
                    NiceImportItemStatus.in_progress,
                    NiceImportItemStatus.retry_pending,
                ]
            )
        )
    ).first()
    if pending is not None:
        return job
    job.status = NiceImportJobStatus.completed
    job.finished_at = now
    job.active_slot = None
    job.lease_owner = None
    job.lease_expires_at = None
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def run_import_worker(
    session_factory: Callable[[], Session],
    *,
    worker_id: str | None = None,
    stop_requested: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
    client_factory: Callable[[], httpx.Client] | None = None,
) -> None:
    worker_id = worker_id or new_worker_id()
    owns_client_factory = client_factory is None
    client_factory = client_factory or _default_client
    with client_factory() as client:
        while not stop_requested():
            try:
                step = _run_worker_step(
                    session_factory,
                    worker_id=worker_id,
                    client=client,
                    stop_requested=stop_requested,
                )
            except OperationalError as exc:
                if _is_retryable_lock(exc):
                    sleep(0.25)
                    continue
                raise
            if not step.should_continue:
                break
            if step.sleep_seconds > 0 and not stop_requested():
                sleep(step.sleep_seconds)
    if owns_client_factory:
        logger.debug("NICE import worker %s stopped", worker_id)


def _run_worker_step(
    session_factory: Callable[[], Session],
    *,
    worker_id: str,
    client: httpx.Client,
    stop_requested: Callable[[], bool],
) -> WorkerStep:
    with session_factory() as session:
        job = claim_job(session, worker_id=worker_id)
        if job is None:
            return WorkerStep(should_continue=False, sleep_seconds=0)
        if stop_requested():
            return WorkerStep(should_continue=False, sleep_seconds=0)
        if not session.exec(
            select(NiceImportItem.id).where(NiceImportItem.job_id == job.id)
        ).first():
            discover_import_items(session, job=job, client=client)
        heartbeat_job(session, job_id=job.id or 0, worker_id=worker_id)
        item = claim_next_item(session, job_id=job.id or 0)
        if item is None:
            finish_job_if_done(session, job_id=job.id or 0, worker_id=worker_id)
            return _wait_for_unfinished_work(session, job_id=job.id or 0)

    with session_factory() as session:
        item = session.get(NiceImportItem, item.id)
        if item is None:
            return WorkerStep(should_continue=True, sleep_seconds=0.25)
        heartbeat_job(session, job_id=item.job_id, worker_id=worker_id)
        process_claimed_item(session, item=item, worker_id=worker_id, client=client)
        heartbeat_job(session, job_id=item.job_id, worker_id=worker_id)
        finish_job_if_done(session, job_id=item.job_id, worker_id=worker_id)
        return WorkerStep(should_continue=True, sleep_seconds=_scrape_delay())


def _wait_for_unfinished_work(session: Session, *, job_id: int) -> WorkerStep:
    job = session.get(NiceImportJob, job_id)
    if job is None or job.status not in {
        NiceImportJobStatus.pending,
        NiceImportJobStatus.running,
    }:
        return WorkerStep(should_continue=False, sleep_seconds=0)

    now = utcnow()
    waits: list[float] = []
    for value in session.exec(
        select(NiceImportItem.next_attempt_at)
        .where(NiceImportItem.job_id == job_id)
        .where(NiceImportItem.status == NiceImportItemStatus.retry_pending)
        .where(NiceImportItem.next_attempt_at.is_not(None))  # type: ignore[union-attr]
    ).all():
        waits.append(max(0.25, (_as_aware_utc(value) - now).total_seconds()))
    for value in session.exec(
        select(NiceImportItem.lease_expires_at)
        .where(NiceImportItem.job_id == job_id)
        .where(NiceImportItem.status == NiceImportItemStatus.in_progress)
        .where(NiceImportItem.lease_expires_at.is_not(None))  # type: ignore[union-attr]
    ).all():
        waits.append(max(0.25, (_as_aware_utc(value) - now).total_seconds()))

    if waits:
        return WorkerStep(should_continue=True, sleep_seconds=min(min(waits), 30.0))
    return WorkerStep(should_continue=False, sleep_seconds=0)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _scrape_delay() -> float:
    return SCRAPE_DELAY_SECONDS + random.uniform(0, SCRAPE_DELAY_JITTER_SECONDS)


def _default_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    )


def _is_retryable_lock(exc: OperationalError) -> bool:
    message = str(exc).lower()
    return any(fragment in message for fragment in _RETRYABLE_LOCK_ERRORS)
