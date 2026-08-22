"""Focused retrieval authoring validation and batch recovery coverage."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.api.routes import create as create_routes
from app.core.config import settings
from app.models import (
    Chunk,
    Dataset,
    Document,
    EvalItem,
    EvalType,
    ItemStatus,
    RetrievalSubmissionBatch,
)
from app.services.documents import create_document_with_chunks


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
        json={"request_id": replay_request_id, "items": replay_items},
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
