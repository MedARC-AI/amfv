from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    Chunk,
    Dataset,
    EvalFact,
    EvalItem,
    EvalType,
    FactPolarity,
)
from app.services.documents import create_document_with_chunks


def test_fact_decomp_creation_persists_ordered_facts_and_provenance(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="api-fact-create",
        display_name="API Fact Create",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.flush()
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Fact Source",
        content="Aspirin reduced fever.\n\nDistractor answer choice.",
        external_id="doc-fact-create",
    )
    db.commit()
    chunk = db.exec(select(Chunk).where(Chunk.document_id == document.id)).first()
    assert chunk is not None
    span_start = chunk.text.index("Aspirin")

    payload = {
        "dataset_id": dataset.id,
        "document_id": document.id,
        "source_text": "Aspirin reduced fever, while one option was a distractor.",
        "facts": [
            {
                "fact_text": "The distractor answer choice should not be listed.",
                "polarity": "SHOULD_NOT_LIST",
                "provenance_spans": [],
            },
            {
                "fact_text": "Aspirin reduced fever.",
                "polarity": "SHOULD_LIST",
                "provenance_spans": [
                    {
                        "chunk_id": chunk.id,
                        "start": span_start,
                        "end": span_start + len("Aspirin reduced fever."),
                        "text": "Aspirin reduced fever.",
                    }
                ],
            },
        ],
    }

    preview = client.post(
        f"{settings.API_V1_STR}/create/validate",
        headers=normal_user_token_headers,
        json=payload,
    )
    assert preview.status_code == 200
    assert preview.json()["ok"] is True

    draft = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json={**payload, "request_id": f"fact-draft-{uuid4()}"},
    )
    assert draft.status_code == 200
    draft_data = draft.json()
    created = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/submit",
        headers=normal_user_token_headers,
        json={
            **payload,
            "request_id": f"fact-submit-{uuid4()}",
            "item_id": draft_data["id"],
            "expected_item_revision": draft_data["item_revision"],
        },
    )
    assert created.status_code == 200
    data = created.json()
    assert data["status"] == "SUBMITTED"
    assert [fact["fact_text"] for fact in data["facts"]] == [
        "The distractor answer choice should not be listed.",
        "Aspirin reduced fever.",
    ]
    assert data["validation"]["ok"] is True

    item = db.get(EvalItem, data["id"])
    assert item is not None
    assert item.document_id == document.id
    assert item.evidence_spans[0]["fact_position"] == 1
    facts = db.exec(
        select(EvalFact).where(EvalFact.item_id == item.id).order_by(EvalFact.position)
    ).all()
    assert [fact.position for fact in facts] == [0, 1]
    assert [fact.fact_text for fact in facts] == [
        "The distractor answer choice should not be listed.",
        "Aspirin reduced fever.",
    ]
    assert facts[0].polarity == FactPolarity.SHOULD_NOT_LIST


def test_fact_decomp_submit_rejects_missing_unwanted_fact(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="api-fact-missing-unwanted",
        display_name="API Fact Missing Unwanted",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.commit()

    payload = {
        "dataset_id": dataset.id,
        "source_text": "Aspirin reduced fever.",
        "facts": [
            {
                "fact_text": "Aspirin reduced fever.",
                "polarity": "SHOULD_LIST",
                "provenance_spans": [],
            }
        ],
    }
    draft = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json={**payload, "request_id": f"fact-draft-{uuid4()}"},
    )
    assert draft.status_code == 200
    rejected = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/submit",
        headers=normal_user_token_headers,
        json={
            **payload,
            "request_id": f"fact-submit-{uuid4()}",
            "item_id": draft.json()["id"],
            "expected_item_revision": draft.json()["item_revision"],
        },
    )

    assert rejected.status_code == 400
    assert any("SHOULD_NOT_LIST" in row["message"] for row in rejected.json()["detail"])


def test_fact_decomp_rejects_client_position_field(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="api-fact-client-identity",
        display_name="API Fact Client Identity",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.commit()

    payload = {
        "dataset_id": dataset.id,
        "source_text": "Aspirin reduced fever.",
        "facts": [
            {
                "fact_text": "Aspirin reduced fever.",
                "polarity": "SHOULD_LIST",
                "position": 0,
                "provenance_spans": [],
            },
            {
                "fact_text": "Noise fact.",
                "polarity": "SHOULD_NOT_LIST",
                "position": 1,
                "provenance_spans": [],
            },
        ],
    }
    rejected = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json={**payload, "request_id": f"fact-draft-{uuid4()}"},
    )
    assert rejected.status_code == 422
    locations = {tuple(row["loc"]) for row in rejected.json()["detail"]}
    assert ("body", "facts", 0, "position") in locations


def test_fact_decomp_draft_rejects_retrieval_dataset(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="api-retrieval-dataset-for-facts",
        display_name="Retrieval Dataset For Facts",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(dataset)
    db.commit()

    rejected = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json={
            "request_id": f"fact-draft-{uuid4()}",
            "dataset_id": dataset.id,
            "source_text": "Should not persist.",
            "facts": [
                {
                    "fact_text": "Should not persist.",
                    "polarity": "SHOULD_LIST",
                    "provenance_spans": [],
                },
                {
                    "fact_text": "Noise should not persist.",
                    "polarity": "SHOULD_NOT_LIST",
                    "provenance_spans": [],
                },
            ],
        },
    )

    assert rejected.status_code == 400
    assert (
        rejected.json()["detail"][0]["message"]
        == "Dataset is not a fact-decomposition dataset."
    )
    persisted = db.exec(
        select(EvalItem).where(
            EvalItem.dataset_id == dataset.id,
            EvalItem.prompt_text == "Should not persist.",
        )
    ).first()
    assert persisted is None


def test_fact_decomp_draft_rejects_stale_provenance_span(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="api-fact-stale-provenance",
        display_name="API Fact Stale Provenance",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.flush()
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Fact Stale Source",
        content="Current provenance text.",
        external_id="doc-fact-stale",
    )
    db.commit()
    chunk = db.exec(select(Chunk).where(Chunk.document_id == document.id)).first()
    assert chunk is not None

    rejected = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json={
            "request_id": f"fact-draft-{uuid4()}",
            "dataset_id": dataset.id,
            "document_id": document.id,
            "source_text": "Current provenance text.",
            "facts": [
                {
                    "fact_text": "Current provenance text.",
                    "polarity": "SHOULD_LIST",
                    "provenance_spans": [
                        {
                            "chunk_id": chunk.id,
                            "start": 0,
                            "end": 7,
                            "text": "Stale!!",
                        }
                    ],
                },
                {
                    "fact_text": "Noise fact.",
                    "polarity": "SHOULD_NOT_LIST",
                    "provenance_spans": [],
                },
            ],
        },
    )

    assert rejected.status_code == 400
    assert any("does not match" in row["message"] for row in rejected.json()["detail"])
