"""Focused recoverability and server-owned authoring workflow coverage."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.api.routes import create as create_routes
from app.core.config import settings
from app.core.db import engine
from app.models import (
    Chunk,
    Dataset,
    Document,
    EvalFact,
    EvalItem,
    EvalType,
    FactDecompSaveReceipt,
    ItemStatus,
    RetrievalSubmissionBatch,
    ReviewTask,
    User,
)
from app.schemas import FactDecompSaveCommand
from app.services.assignment import available_item_audit_targets
from app.services.documents import create_document_with_chunks
from tests.utils.user import authentication_token_from_email


def _retrieval_source(db: Session) -> tuple[Dataset, Chunk]:
    dataset = Dataset(
        name=f"recoverable-retrieval-{uuid4()}",
        display_name="Recoverable retrieval",
        eval_type=EvalType.RETRIEVAL,
        double_rate=0,
        trap_rate=0,
    )
    db.add(dataset)
    db.flush()
    assert dataset.id is not None
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Recoverable source",
        content="The selected answer is Baker.",
        external_id=f"recoverable-source-{uuid4()}",
    )
    chunk = db.exec(select(Chunk).where(col(Chunk.document_id) == document.id)).one()
    db.commit()
    return dataset, chunk


def _retrieval_payload(dataset: Dataset, chunk: Chunk, question: str) -> dict:
    assert dataset.id is not None
    assert chunk.id is not None
    answer = "Baker"
    start = chunk.text.index(answer)
    return {
        "dataset_id": dataset.id,
        "document_ids": [chunk.document_id],
        "category": "VERBATIM",
        "question": question,
        "expected_answer": answer,
        "gold_evidence_spans": [
            {
                "chunk_id": chunk.id,
                "start": start,
                "end": start + len(answer),
                "text": answer,
            }
        ],
    }


def _retrieval_matrix_source(db: Session) -> tuple[Dataset, list[Chunk]]:
    dataset = Dataset(
        name=f"retrieval-matrix-{uuid4()}",
        display_name="Retrieval validation matrix",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(dataset)
    db.flush()
    assert dataset.id is not None
    first_document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="First matrix source",
        content="Baker appears in the first source.",
        external_id=f"matrix-first-{uuid4()}",
        chunk_texts=[
            "Baker appears in the first source.",
            "A second supporting passage mentions Baker.",
        ],
    )
    second_document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Second matrix source",
        content="Baker appears in the second source.",
        external_id=f"matrix-second-{uuid4()}",
        chunk_texts=["Baker appears in the second source."],
    )
    chunks = db.exec(
        select(Chunk)
        .where(col(Chunk.document_id).in_([first_document.id, second_document.id]))
        .order_by(col(Chunk.document_id), col(Chunk.position))
    ).all()
    assert len(chunks) == 3
    db.commit()
    return dataset, chunks


def _span(chunk: Chunk) -> dict:
    assert chunk.id is not None
    text = "Baker"
    start = chunk.text.index(text)
    return {
        "chunk_id": chunk.id,
        "start": start,
        "end": start + len(text),
        "text": text,
    }


def _matrix_payload(
    dataset: Dataset,
    chunks: list[Chunk],
    category: str,
    *,
    valid: bool,
) -> dict:
    first, second, third = chunks
    assert dataset.id is not None
    document_ids = [first.document_id]
    gold_spans: list[dict] = [_span(first)]
    trap_spans: list[dict] = []
    why_not_answerable: str | None = None
    if category == "PARAPHRASE":
        gold_spans = [_span(first)] if valid else []
    elif category == "MULTI_CHUNK":
        gold_spans = [_span(first), _span(second)] if valid else [_span(first)]
    elif category == "MULTI_DOCUMENT":
        document_ids = [first.document_id, third.document_id]
        gold_spans = (
            [_span(first), _span(third)] if valid else [_span(first), _span(second)]
        )
    elif category == "ADVERSARIAL":
        gold_spans = []
        trap_spans = [_span(first)] if valid else []
        why_not_answerable = (
            "The source supplies no answer to this question." if valid else None
        )
    elif category == "VERBATIM":
        gold_spans = [_span(first)] if valid else []
    else:
        raise AssertionError(f"Unhandled retrieval category: {category}")
    return {
        "dataset_id": dataset.id,
        "document_ids": document_ids,
        "category": category,
        "question": f"{category} validation question {valid} {uuid4()}",
        "expected_answer": None if category == "ADVERSARIAL" else "Baker",
        "unanswerable": category == "ADVERSARIAL",
        "gold_evidence_spans": gold_spans,
        "trap_evidence_spans": trap_spans,
        "why_not_answerable": why_not_answerable,
    }


def _fact_payload(dataset: Dataset, source_text: str) -> dict:
    assert dataset.id is not None
    return {
        "dataset_id": dataset.id,
        "source_text": source_text,
        "facts": [
            {
                "fact_uuid": "wanted",
                "fact_text": "Baker appears in the source text.",
                "polarity": "SHOULD_LIST",
                "position": 0,
                "provenance_spans": [],
            },
            {
                "fact_uuid": "unwanted",
                "fact_text": "An unsupported answer should not be listed.",
                "polarity": "SHOULD_NOT_LIST",
                "position": 1,
                "provenance_spans": [],
            },
        ],
    }


def _fact_command(payload: dict, *, request_id: str | None = None) -> dict:
    return {**payload, "request_id": request_id or f"fact-save-{uuid4()}"}


def _fact_dataset(db: Session) -> Dataset:
    dataset = Dataset(
        name=f"recoverable-fact-{uuid4()}",
        display_name="Recoverable fact decomposition",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.commit()
    assert dataset.id is not None
    return dataset


def test_retrieval_preview_and_submit_share_server_rules_and_stay_submitted(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset, chunk = _retrieval_source(db)
    payload = _retrieval_payload(dataset, chunk, f"Which answer? {uuid4()}")
    invalid_payload = {**payload, "gold_evidence_spans": []}

    preview = client.post(
        f"{settings.API_V1_STR}/create/retrieval/preview",
        headers=normal_user_token_headers,
        json=invalid_payload,
    )
    submit = client.post(
        f"{settings.API_V1_STR}/create/retrieval/submit",
        headers=normal_user_token_headers,
        json=invalid_payload,
    )
    assert preview.status_code == 200
    assert preview.json()["ok"] is False
    assert submit.status_code == 400
    assert submit.json()["detail"] == preview.json()["flags"]

    rejected_status_override = client.post(
        f"{settings.API_V1_STR}/create/retrieval/submit",
        headers=normal_user_token_headers,
        json={**payload, "status": "ACTIVE"},
    )
    assert rejected_status_override.status_code == 422
    assert rejected_status_override.json()["detail"][0]["loc"] == ["body", "status"]

    created = client.post(
        f"{settings.API_V1_STR}/create/retrieval/submit",
        headers=normal_user_token_headers,
        json=payload,
    )
    assert created.status_code == 200
    assert created.json()["status"] == "SUBMITTED"
    item = db.get(EvalItem, created.json()["id"])
    assert item is not None
    assert item.status == ItemStatus.SUBMITTED


@pytest.mark.parametrize(
    "category",
    ["VERBATIM", "PARAPHRASE", "MULTI_CHUNK", "MULTI_DOCUMENT", "ADVERSARIAL"],
)
def test_retrieval_category_validation_matrix_uses_the_same_preview_and_submit_rules(
    category: str,
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset, chunks = _retrieval_matrix_source(db)
    invalid_payload = _matrix_payload(dataset, chunks, category, valid=False)
    invalid_preview = client.post(
        f"{settings.API_V1_STR}/create/retrieval/preview",
        headers=normal_user_token_headers,
        json=invalid_payload,
    )
    invalid_submit = client.post(
        f"{settings.API_V1_STR}/create/retrieval/submit",
        headers=normal_user_token_headers,
        json=invalid_payload,
    )
    assert invalid_preview.status_code == 200
    assert invalid_preview.json()["ok"] is False
    assert invalid_submit.status_code == 400
    assert invalid_submit.json()["detail"] == invalid_preview.json()["flags"]

    valid_payload = _matrix_payload(dataset, chunks, category, valid=True)
    valid_preview = client.post(
        f"{settings.API_V1_STR}/create/retrieval/preview",
        headers=normal_user_token_headers,
        json=valid_payload,
    )
    valid_submit = client.post(
        f"{settings.API_V1_STR}/create/retrieval/submit",
        headers=normal_user_token_headers,
        json=valid_payload,
    )
    assert valid_preview.status_code == 200
    assert valid_preview.json()["ok"] is True
    assert valid_submit.status_code == 200
    assert valid_submit.json()["status"] == "SUBMITTED"
    item = db.get(EvalItem, valid_submit.json()["id"])
    assert item is not None
    assert item.status == ItemStatus.SUBMITTED


def test_inactive_document_cannot_be_newly_cited_when_document_ids_are_omitted(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset, chunk = _retrieval_source(db)
    document = db.get(Document, chunk.document_id)
    assert document is not None
    document.is_active = False
    db.add(document)
    db.commit()
    payload = _retrieval_payload(dataset, chunk, f"Inactive citation? {uuid4()}")
    payload["document_ids"] = []

    preview = client.post(
        f"{settings.API_V1_STR}/create/retrieval/preview",
        headers=normal_user_token_headers,
        json=payload,
    )
    submitted = client.post(
        f"{settings.API_V1_STR}/create/retrieval/submit",
        headers=normal_user_token_headers,
        json=payload,
    )
    assert preview.status_code == 200
    assert preview.json()["ok"] is False
    assert "inactive or missing document" in preview.json()["flags"][0]["message"]
    assert submitted.status_code == 400
    assert (
        db.exec(
            select(EvalItem).where(col(EvalItem.prompt_text) == payload["question"])
        ).first()
        is None
    )


def test_fact_draft_save_submit_and_authoritative_read_preserve_one_identity(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = _fact_dataset(db)
    first_payload = _fact_payload(dataset, "Baker is in the initial source.")

    rejected_status_override = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json=_fact_command({**first_payload, "status": "SUBMITTED"}),
    )
    assert rejected_status_override.status_code == 422
    assert rejected_status_override.json()["detail"][0]["loc"] == ["body", "status"]

    first_save = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json=_fact_command(first_payload),
    )
    assert first_save.status_code == 200
    first = first_save.json()
    assert first["status"] == "DRAFT"

    second_payload = _fact_payload(dataset, "Baker is in the revised source.")
    second_payload["facts"][0]["fact_text"] = "Baker is in the revised source."
    second_save = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json=_fact_command(
            {
                **second_payload,
                "item_id": first["id"],
                "expected_item_revision": first["item_revision"],
            }
        ),
    )
    assert second_save.status_code == 200
    second = second_save.json()
    assert second["id"] == first["id"]
    assert second["item_revision"] == first["item_revision"] + 1
    db.expire_all()
    facts = db.exec(select(EvalFact).where(col(EvalFact.item_id) == first["id"])).all()
    assert len(facts) == 2

    stale = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json=_fact_command(
            {
                **second_payload,
                "item_id": first["id"],
                "expected_item_revision": first["item_revision"],
            }
        ),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "item_revision_conflict",
        "message": "The draft was changed by another save; reload its current revision.",
        "item_id": first["id"],
        "expected_item_revision": first["item_revision"],
        "actual_item_revision": second["item_revision"],
    }

    missing_identity = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/submit",
        headers=normal_user_token_headers,
        json=_fact_command(second_payload),
    )
    assert missing_identity.status_code == 409
    assert missing_identity.json()["detail"]["code"] == "draft_identity_required"

    submitted = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/submit",
        headers=normal_user_token_headers,
        json=_fact_command(
            {
                **second_payload,
                "item_id": first["id"],
                "expected_item_revision": second["item_revision"],
            }
        ),
    )
    assert submitted.status_code == 200
    assert submitted.json()["id"] == first["id"]
    assert submitted.json()["status"] == "SUBMITTED"

    authoritative = client.get(
        f"{settings.API_V1_STR}/create/items/{first['id']}",
        headers=normal_user_token_headers,
    )
    assert authoritative.status_code == 200
    assert authoritative.json()["status"] == "SUBMITTED"
    assert authoritative.json()["item_revision"] == submitted.json()["item_revision"]


def test_concurrent_fact_saves_with_the_same_revision_have_one_winner(
    db: Session,
    monkeypatch,
) -> None:
    dataset = _fact_dataset(db)
    author = User(email=f"concurrent-author-{uuid4()}@example.com", hashed_password="x")
    db.add(author)
    db.commit()
    assert author.id is not None
    author_id = author.id
    initial = create_routes._create_or_update_fact_decomp_item(
        db,
        FactDecompSaveCommand.model_validate(
            _fact_command(_fact_payload(dataset, "Initial concurrent draft."))
        ),
        author,
        status=ItemStatus.DRAFT,
    )
    barrier = Barrier(2)
    original_claim = create_routes._claim_fact_draft_update

    def synchronized_claim(*args, **kwargs):
        barrier.wait(timeout=5)
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(create_routes, "_claim_fact_draft_update", synchronized_claim)

    def save(source_text: str) -> tuple[str, int | dict]:
        with Session(engine) as session:
            current_author = session.get(User, author_id)
            assert current_author is not None
            body = FactDecompSaveCommand.model_validate(
                _fact_command(
                    {
                        **_fact_payload(dataset, source_text),
                        "item_id": initial.id,
                        "expected_item_revision": initial.item_revision,
                    }
                )
            )
            try:
                response = create_routes._create_or_update_fact_decomp_item(
                    session,
                    body,
                    current_author,
                    status=ItemStatus.DRAFT,
                )
            except HTTPException as exc:
                session.rollback()
                assert isinstance(exc.detail, dict)
                return "conflict", exc.detail
            return "saved", response.id

    sources = ["Concurrent revision A.", "Concurrent revision B."]
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save, sources))

    assert [outcome[0] for outcome in outcomes].count("saved") == 1
    conflicts = [outcome[1] for outcome in outcomes if outcome[0] == "conflict"]
    assert conflicts == [
        {
            "code": "item_revision_conflict",
            "message": "The draft was changed by another save; reload its current revision.",
            "item_id": initial.id,
            "expected_item_revision": initial.item_revision,
            "actual_item_revision": initial.item_revision + 1,
        }
    ]
    db.expire_all()
    item = db.get(EvalItem, initial.id)
    assert item is not None
    assert item.prompt_text in sources
    assert item.revision == initial.item_revision + 1


def test_fact_save_receipts_recover_first_and_later_commits_and_reject_reuse(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = _fact_dataset(db)
    first_request_id = f"first-save-{uuid4()}"
    first_body = _fact_command(
        _fact_payload(dataset, "Receipt first source."),
        request_id=first_request_id,
    )
    first_save = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json=first_body,
    )
    assert first_save.status_code == 200

    first_receipt = client.get(
        f"{settings.API_V1_STR}/create/fact-decomp/receipts/{first_request_id}",
        headers=normal_user_token_headers,
    )
    assert first_receipt.status_code == 200
    assert first_receipt.json()["request"] == FactDecompSaveCommand.model_validate(
        first_body
    ).model_dump(mode="json", exclude={"request_id"})
    assert first_receipt.json()["response"] == first_save.json()
    assert first_receipt.json()["command"] == "draft"
    assert first_receipt.json()["replayed"] is True

    replay = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json=first_body,
    )
    assert replay.status_code == 200
    assert replay.json() == first_save.json()

    mismatch = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json={
            **first_body,
            "source_text": "This must not replace the committed request.",
        },
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "fact_save_request_conflict"

    second_request_id = f"later-save-{uuid4()}"
    second_body = _fact_command(
        {
            **_fact_payload(dataset, "Receipt later source."),
            "item_id": first_save.json()["id"],
            "expected_item_revision": first_save.json()["item_revision"],
        },
        request_id=second_request_id,
    )
    second_save = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json=second_body,
    )
    assert second_save.status_code == 200
    assert second_save.json()["item_revision"] == 2
    later_receipt = client.get(
        f"{settings.API_V1_STR}/create/fact-decomp/receipts/{second_request_id}",
        headers=normal_user_token_headers,
    )
    assert later_receipt.status_code == 200
    assert later_receipt.json()["response"]["prompt_text"] == "Receipt later source."
    assert later_receipt.json()["response"]["item_revision"] == 2

    other_headers = authentication_token_from_email(
        client=client,
        email=f"other-receipt-user-{uuid4()}@example.com",
        db=db,
    )
    hidden = client.get(
        f"{settings.API_V1_STR}/create/fact-decomp/receipts/{second_request_id}",
        headers=other_headers,
    )
    assert hidden.status_code == 404
    assert db.exec(select(FactDecompSaveReceipt)).all()


def test_fact_save_failure_before_commit_leaves_no_item_or_receipt(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _fact_dataset(db)
    request_id = f"failed-save-{uuid4()}"
    source_text = f"Uncommitted fact source {uuid4()}"

    def fail_before_commit(session: Session) -> None:
        _ = session
        raise RuntimeError("forced failure before commit")

    monkeypatch.setattr(create_routes, "_commit_fact_decomp_save", fail_before_commit)
    failed = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json=_fact_command(
            _fact_payload(dataset, source_text),
            request_id=request_id,
        ),
    )
    assert failed.status_code == 500

    missing = client.get(
        f"{settings.API_V1_STR}/create/fact-decomp/receipts/{request_id}",
        headers=normal_user_token_headers,
    )
    assert missing.status_code == 404
    db.expire_all()
    assert (
        db.exec(
            select(EvalItem).where(col(EvalItem.prompt_text) == source_text)
        ).first()
        is None
    )
    assert (
        db.exec(
            select(FactDecompSaveReceipt).where(
                col(FactDecompSaveReceipt.request_id) == request_id
            )
        ).first()
        is None
    )


def test_retrieval_batch_rolls_back_second_row_and_replays_committed_receipt(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
    monkeypatch,
) -> None:
    dataset, chunk = _retrieval_source(db)
    failed_items = [
        _retrieval_payload(dataset, chunk, f"Failed batch first {uuid4()}"),
        _retrieval_payload(dataset, chunk, f"Failed batch second {uuid4()}"),
    ]
    failed_request_id = f"failed-{uuid4()}"
    original_persist = create_routes._persist_retrieval_batch

    def fail_after_the_first_flush(session, receipt, items) -> None:
        session.add(receipt)
        session.add(items[0])
        session.flush()
        raise RuntimeError("forced second-row persistence failure")

    monkeypatch.setattr(
        create_routes, "_persist_retrieval_batch", fail_after_the_first_flush
    )
    failed = client.post(
        f"{settings.API_V1_STR}/create/retrieval/batch",
        headers=normal_user_token_headers,
        json={"request_id": failed_request_id, "items": failed_items},
    )
    assert failed.status_code == 500
    db.expire_all()
    assert (
        db.exec(
            select(EvalItem).where(
                col(EvalItem.prompt_text).in_(
                    [item["question"] for item in failed_items]
                )
            )
        ).all()
        == []
    )
    assert (
        db.exec(
            select(RetrievalSubmissionBatch).where(
                col(RetrievalSubmissionBatch.request_id) == failed_request_id
            )
        ).first()
        is None
    )

    def commit_then_lose_response(session, receipt, items) -> None:
        original_persist(session, receipt, items)
        raise TimeoutError("simulated lost response")

    monkeypatch.setattr(
        create_routes, "_persist_retrieval_batch", commit_then_lose_response
    )
    replay_items = [
        _retrieval_payload(dataset, chunk, f"Replay batch first {uuid4()}"),
        _retrieval_payload(dataset, chunk, f"Replay batch second {uuid4()}"),
    ]
    replay_request_id = f"lost-response-{uuid4()}"
    lost_response = client.post(
        f"{settings.API_V1_STR}/create/retrieval/batch",
        headers=normal_user_token_headers,
        json={"request_id": replay_request_id, "items": replay_items},
    )
    assert lost_response.status_code == 500

    authoritative = client.get(
        f"{settings.API_V1_STR}/create/retrieval/batches/{replay_request_id}",
        headers=normal_user_token_headers,
    )
    assert authoritative.status_code == 200
    assert authoritative.json()["replayed"] is True
    assert len(authoritative.json()["item_ids"]) == 2

    monkeypatch.setattr(create_routes, "_persist_retrieval_batch", original_persist)
    replayed = client.post(
        f"{settings.API_V1_STR}/create/retrieval/batch",
        headers=normal_user_token_headers,
        json={
            "request_id": replay_request_id,
            "items": replay_items,
        },
    )
    assert replayed.status_code == 200
    assert replayed.json() == authoritative.json()

    conflict = client.post(
        f"{settings.API_V1_STR}/create/retrieval/batch",
        headers=normal_user_token_headers,
        json={
            "request_id": replay_request_id,
            "items": [
                _retrieval_payload(dataset, chunk, f"Different request body {uuid4()}")
            ],
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "retrieval_batch_request_conflict"


def test_submitted_authoring_items_require_moderation_before_becoming_eligible(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    retrieval_dataset, chunk = _retrieval_source(db)
    retrieval = client.post(
        f"{settings.API_V1_STR}/create/retrieval/submit",
        headers=normal_user_token_headers,
        json=_retrieval_payload(
            retrieval_dataset, chunk, f"Moderation retrieval {uuid4()}"
        ),
    )
    assert retrieval.status_code == 200
    retrieval_item_id = retrieval.json()["id"]
    reviewer = User(
        email=f"eligibility-reviewer-{uuid4()}@example.com", hashed_password="x"
    )
    db.add(reviewer)
    db.commit()
    assert available_item_audit_targets(db, reviewer, retrieval_dataset) == []

    approved_retrieval = client.post(
        f"{settings.API_V1_STR}/admin/items/{retrieval_item_id}/approve",
        headers=superuser_token_headers,
        json={
            "action": "approve",
            "dataset_id": retrieval_dataset.id,
            "expected_item_revision": retrieval.json()["item_revision"],
        },
    )
    assert approved_retrieval.status_code == 200
    assert [
        item.id
        for item in available_item_audit_targets(db, reviewer, retrieval_dataset)
    ] == [retrieval_item_id]

    fact_dataset = _fact_dataset(db)
    fact_draft = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json=_fact_command(_fact_payload(fact_dataset, "Fact moderation source.")),
    )
    assert fact_draft.status_code == 200
    fact_submitted = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/submit",
        headers=normal_user_token_headers,
        json=_fact_command(
            {
                **_fact_payload(fact_dataset, "Fact moderation source."),
                "item_id": fact_draft.json()["id"],
                "expected_item_revision": fact_draft.json()["item_revision"],
            }
        ),
    )
    assert fact_submitted.status_code == 200
    generated_before = client.post(
        f"{settings.API_V1_STR}/admin/datasets/{fact_dataset.id}/generate-tasks",
        headers=superuser_token_headers,
    )
    assert generated_before.status_code == 200
    assert generated_before.json()["created"] == 0
    assert (
        db.exec(
            select(ReviewTask).where(
                col(ReviewTask.item_a_id) == fact_draft.json()["id"]
            )
        ).first()
        is None
    )

    approved_fact = client.post(
        f"{settings.API_V1_STR}/admin/items/{fact_draft.json()['id']}/approve",
        headers=superuser_token_headers,
        json={
            "action": "approve",
            "dataset_id": fact_dataset.id,
            "expected_item_revision": fact_submitted.json()["item_revision"],
        },
    )
    assert approved_fact.status_code == 200
    generated_after = client.post(
        f"{settings.API_V1_STR}/admin/datasets/{fact_dataset.id}/generate-tasks",
        headers=superuser_token_headers,
    )
    assert generated_after.status_code == 200
    assert generated_after.json()["created"] == 1
