from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    Chunk,
    Dataset,
    EvalItem,
    EvalType,
    ItemStatus,
)
from app.schemas import EvidenceSpan
from app.services.documents import create_document_with_chunks


def test_evidence_span_rejects_invalid_offsets() -> None:
    valid = EvidenceSpan(chunk_id=1, start=0, end=4, text="text")
    assert valid.start == 0

    try:
        EvidenceSpan(chunk_id=1, start=-1, end=4, text="text")
    except ValidationError as exc:
        assert "greater than or equal to 0" in str(exc)
    else:
        raise AssertionError("negative offsets should fail validation")

    try:
        EvidenceSpan(chunk_id=1, start=4, end=4, text="text")
    except ValidationError as exc:
        assert "greater than start" in str(exc)
    else:
        raise AssertionError("empty spans should fail validation")


def test_retrieval_creation_validates_and_persists_spans(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="api-retrieval-create",
        display_name="API Retrieval Create",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(dataset)
    db.flush()
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Create Doc",
        content="The selected answer is Baker.\n\nAnother paragraph.",
        external_id="doc-create",
    )
    db.commit()
    chunk = db.exec(select(Chunk).where(Chunk.document_id == document.id)).first()
    assert chunk is not None
    answer_start = chunk.text.index("Baker")

    payload = {
        "dataset_id": dataset.id,
        "document_ids": [document.id],
        "category": "VERBATIM",
        "question": "What selected answer appears in the document?",
        "expected_answer": "Baker",
        "gold_evidence_spans": [
            {
                "chunk_id": chunk.id,
                "start": answer_start,
                "end": answer_start + len("Baker"),
                "text": "Baker",
            }
        ],
    }
    missing_evidence_payload = {
        **payload,
        "gold_evidence_spans": [],
    }

    missing_evidence_preview = client.post(
        f"{settings.API_V1_STR}/create/validate",
        headers=normal_user_token_headers,
        json=missing_evidence_payload,
    )
    assert missing_evidence_preview.status_code == 200
    assert missing_evidence_preview.json()["ok"] is False
    assert (
        "highlighted answer text"
        in missing_evidence_preview.json()["flags"][0]["message"]
    )

    missing_evidence_submit = client.post(
        f"{settings.API_V1_STR}/create/retrieval/submit",
        headers=normal_user_token_headers,
        json=missing_evidence_payload,
    )
    assert missing_evidence_submit.status_code == 400

    too_many_evidence_payload = {
        **payload,
        "gold_evidence_spans": [
            *payload["gold_evidence_spans"],
            *payload["gold_evidence_spans"],
        ],
    }
    too_many_evidence_preview = client.post(
        f"{settings.API_V1_STR}/create/validate",
        headers=normal_user_token_headers,
        json=too_many_evidence_payload,
    )
    assert too_many_evidence_preview.status_code == 200
    assert too_many_evidence_preview.json()["ok"] is False
    assert "exactly one" in too_many_evidence_preview.json()["flags"][0]["message"]

    preview = client.post(
        f"{settings.API_V1_STR}/create/validate",
        headers=normal_user_token_headers,
        json=payload,
    )
    assert preview.status_code == 200
    assert preview.json()["ok"] is True

    created = client.post(
        f"{settings.API_V1_STR}/create/retrieval/submit",
        headers=normal_user_token_headers,
        json=payload,
    )
    assert created.status_code == 200
    data = created.json()
    assert data["status"] == "SUBMITTED"
    assert data["evidence_spans"][0]["text"] == "Baker"
    assert data["validation"]["ok"] is True

    item = db.get(EvalItem, data["id"])
    assert item is not None
    assert item.status == ItemStatus.SUBMITTED
    assert item.gold_chunk_ids == [chunk.id]
    assert item.evidence_spans[0]["kind"] == "gold"


def test_retrieval_submit_rejects_stale_selected_text(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="api-retrieval-stale",
        display_name="API Retrieval Stale",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(dataset)
    db.flush()
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Stale Doc",
        content="The current answer is Wilson.",
        external_id="doc-stale",
    )
    db.commit()
    chunk = db.exec(select(Chunk).where(Chunk.document_id == document.id)).first()
    assert chunk is not None

    stale = client.post(
        f"{settings.API_V1_STR}/create/retrieval/submit",
        headers=normal_user_token_headers,
        json={
            "dataset_id": dataset.id,
            "document_ids": [document.id],
            "category": "VERBATIM",
            "question": "Which stale answer appears?",
            "expected_answer": "Baker",
            "gold_evidence_spans": [
                {"chunk_id": chunk.id, "start": 0, "end": 5, "text": "Baker"}
            ],
        },
    )

    assert stale.status_code == 400
    assert any("does not match" in row["message"] for row in stale.json()["detail"])


def test_retrieval_draft_rejects_non_retrieval_dataset(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="api-fact-dataset-for-retrieval",
        display_name="Fact Dataset For Retrieval",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.commit()

    rejected = client.post(
        f"{settings.API_V1_STR}/create/retrieval/draft",
        headers=normal_user_token_headers,
        json={
            "dataset_id": dataset.id,
            "document_ids": [],
            "category": "VERBATIM",
            "question": "Should not persist?",
            "expected_answer": "No",
            "gold_evidence_spans": [],
        },
    )

    assert rejected.status_code == 400
    assert (
        rejected.json()["detail"][0]["message"] == "Dataset is not a retrieval dataset."
    )
    persisted = db.exec(
        select(EvalItem).where(
            EvalItem.dataset_id == dataset.id,
            EvalItem.prompt_text == "Should not persist?",
        )
    ).first()
    assert persisted is None


def test_retrieval_draft_rejects_cross_dataset_document(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    retrieval_dataset = Dataset(
        name="api-retrieval-document-scope",
        display_name="API Retrieval Document Scope",
        eval_type=EvalType.RETRIEVAL,
    )
    other_dataset = Dataset(
        name="api-other-retrieval-document-scope",
        display_name="API Other Retrieval Document Scope",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(retrieval_dataset)
    db.add(other_dataset)
    db.flush()
    other_document = create_document_with_chunks(
        db,
        dataset_id=other_dataset.id,
        title="Other Dataset Doc",
        content="Wrong source document.",
        external_id="doc-wrong-source",
    )
    db.commit()

    rejected = client.post(
        f"{settings.API_V1_STR}/create/retrieval/draft",
        headers=normal_user_token_headers,
        json={
            "dataset_id": retrieval_dataset.id,
            "document_ids": [other_document.id],
            "category": "VERBATIM",
            "question": "Should not persist with wrong document?",
            "expected_answer": "No",
            "gold_evidence_spans": [],
        },
    )

    assert rejected.status_code == 400
    assert (
        rejected.json()["detail"][0]["message"]
        == "One or more selected documents are unavailable."
    )
    persisted = db.exec(
        select(EvalItem).where(
            EvalItem.dataset_id == retrieval_dataset.id,
            EvalItem.prompt_text == "Should not persist with wrong document?",
        )
    ).first()
    assert persisted is None
