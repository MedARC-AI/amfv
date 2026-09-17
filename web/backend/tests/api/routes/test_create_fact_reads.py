"""Owned fact draft discovery and current editor snapshot coverage."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.core.config import settings
from app.models import Chunk, Dataset, EvalItem, EvalType, ItemStatus
from app.services.documents import create_document_with_chunks
from tests.utils.user import authentication_token_from_email

BASE = f"{settings.API_V1_STR}/create/fact-decomp"


def _dataset(db: Session) -> Dataset:
    dataset = Dataset(
        name=f"draft-reads-{uuid4()}",
        display_name="Draft reads",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.commit()
    assert dataset.id is not None
    return dataset


def _save(client: TestClient, headers: dict[str, str], payload: dict) -> dict:
    response = client.post(
        f"{BASE}/draft",
        headers=headers,
        json={**payload, "request_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_owned_list_paginates_orders_and_hides_other_users_and_types(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    dataset = _dataset(db)
    assert dataset.id is not None
    ids = [
        _save(
            client,
            normal_user_token_headers,
            {"dataset_id": dataset.id, "source_text": f"Source {number}", "facts": []},
        )["id"]
        for number in range(3)
    ]
    other_headers = authentication_token_from_email(
        client=client, email=f"other-draft-{uuid4()}@example.com", db=db
    )
    other_id = _save(
        client,
        other_headers,
        {"dataset_id": dataset.id, "source_text": "Hidden", "facts": []},
    )["id"]
    # The oldest update comes last; the newer timestamp tie uses the ID.
    items = [db.get(EvalItem, item_id) for item_id in ids]
    assert all(item is not None for item in items)
    for item in items[1:]:
        assert item is not None
        item.updated_at = items[0].updated_at + timedelta(minutes=1)
        db.add(item)
    db.commit()

    first = client.get(
        f"{BASE}/items?offset=0&limit=2", headers=normal_user_token_headers
    )
    assert first.status_code == 200
    assert first.json()["total"] == 3
    assert first.json()["offset"] == 0
    assert first.json()["limit"] == 2
    assert [entry["id"] for entry in first.json()["items"]] == ids[2:0:-1]
    assert first.json()["items"][0]["dataset_name"] == "Draft reads"
    assert first.json()["items"][0]["source_preview"] == "Source 2"
    assert first.json()["items"][0]["item_revision"] == 1
    assert first.json()["items"][0]["updated_at"]

    second = client.get(
        f"{BASE}/items?offset=2&limit=2", headers=normal_user_token_headers
    )
    assert second.status_code == 200
    assert [entry["id"] for entry in second.json()["items"]] == ids[:1]
    assert (
        client.get(
            f"{BASE}/items?limit=101", headers=normal_user_token_headers
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"{BASE}/items?offset=-1", headers=normal_user_token_headers
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"{BASE}/items/{other_id}", headers=normal_user_token_headers
        ).status_code
        == 404
    )
    assert (
        client.get(f"{BASE}/items/{ids[0]}", headers=other_headers).status_code == 404
    )

    retrieval = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source="HUMAN",
        author_user_id=items[0].author_user_id,
        prompt_text="Wrong type",
        status=ItemStatus.DRAFT,
    )
    db.add(retrieval)
    db.commit()
    assert retrieval.id is not None
    assert (
        client.get(
            f"{BASE}/items/{retrieval.id}", headers=normal_user_token_headers
        ).status_code
        == 404
    )
    assert (
        client.get(f"{BASE}/items", headers=normal_user_token_headers).json()["total"]
        == 3
    )


def test_detail_restores_ordered_duplicate_facts_unicode_spans_and_reopened_update(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    dataset = _dataset(db)
    assert dataset.id is not None
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Unicode document",
        content="🧬 Café is here. 🧬 Again.",
        external_id=f"unicode-{uuid4()}",
    )
    db.commit()
    assert document.id is not None
    chunk = db.exec(select(Chunk).where(col(Chunk.document_id) == document.id)).first()
    assert chunk is not None and chunk.id is not None
    first_start = chunk.text.index("🧬")
    second_start = chunk.text.index("🧬", first_start + 1)

    def span(start: int) -> dict:
        return {"chunk_id": chunk.id, "start": start, "end": start + 1, "text": "🧬"}

    payload = {
        "dataset_id": dataset.id,
        "document_id": document.id,
        "source_text": "🧬 Café is here. 🧬 Again.",
        "facts": [
            {
                "fact_text": "Same fact",
                "polarity": "SHOULD_LIST",
                "provenance_spans": [span(second_start), span(first_start)],
            },
            {
                "fact_text": "Same fact",
                "polarity": "SHOULD_NOT_LIST",
                "provenance_spans": [span(first_start)],
            },
        ],
    }
    saved = _save(client, normal_user_token_headers, payload)
    detail_response = client.get(
        f"{BASE}/items/{saved['id']}", headers=normal_user_token_headers
    )
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["source_text"] == payload["source_text"]
    assert detail["document_id"] == document.id
    assert detail["facts"] == payload["facts"]
    assert detail["can_edit"] is True
    assert detail["read_only_reason"] is None

    revised_facts = [payload["facts"][1], payload["facts"][0]]
    revised = _save(
        client,
        normal_user_token_headers,
        {
            **payload,
            "facts": revised_facts,
            "item_id": saved["id"],
            "expected_item_revision": detail["item_revision"],
        },
    )
    assert revised["id"] == saved["id"]
    assert revised["item_revision"] == saved["item_revision"] + 1
    reopened = client.get(
        f"{BASE}/items/{saved['id']}", headers=normal_user_token_headers
    )
    assert reopened.json()["facts"] == revised_facts
    assert reopened.json()["item_revision"] == revised["item_revision"]
    stale = client.post(
        f"{BASE}/draft",
        headers=normal_user_token_headers,
        json={
            **payload,
            "item_id": saved["id"],
            "expected_item_revision": detail["item_revision"],
            "request_id": str(uuid4()),
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "item_revision_conflict"


def test_detail_permission_tracks_submitted_and_inactive_sources(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    dataset = _dataset(db)
    assert dataset.id is not None
    no_document = _save(
        client,
        normal_user_token_headers,
        {"dataset_id": dataset.id, "source_text": "Optional", "facts": []},
    )
    detail = client.get(
        f"{BASE}/items/{no_document['id']}", headers=normal_user_token_headers
    ).json()
    assert detail["document_id"] is None
    assert detail["can_edit"] is True
    submitted_response = client.post(
        f"{BASE}/submit",
        headers=normal_user_token_headers,
        json={
            "dataset_id": dataset.id,
            "source_text": "Optional",
            "facts": [
                {
                    "fact_text": "Optional is present.",
                    "polarity": "SHOULD_LIST",
                    "provenance_spans": [],
                },
                {
                    "fact_text": "Unsupported is absent.",
                    "polarity": "SHOULD_NOT_LIST",
                    "provenance_spans": [],
                },
            ],
            "item_id": no_document["id"],
            "expected_item_revision": 1,
            "request_id": str(uuid4()),
        },
    )
    assert submitted_response.status_code == 200, submitted_response.text
    submitted = client.get(
        f"{BASE}/items/{no_document['id']}", headers=normal_user_token_headers
    ).json()
    assert submitted["status"] == "SUBMITTED"
    assert submitted["source_text"] == "Optional"
    assert submitted["can_edit"] is False
    assert submitted["read_only_reason"] == "item_not_draft"
    submitted_list = client.get(
        f"{BASE}/items?status=SUBMITTED", headers=normal_user_token_headers
    )
    assert submitted_list.status_code == 200
    assert [entry["id"] for entry in submitted_list.json()["items"]] == [
        no_document["id"]
    ]

    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Inactive",
        content="Inactive source",
        external_id=f"inactive-{uuid4()}",
    )
    db.commit()
    draft = _save(
        client,
        normal_user_token_headers,
        {
            "dataset_id": dataset.id,
            "document_id": document.id,
            "source_text": "Still visible",
            "facts": [],
        },
    )
    document.is_active = False
    db.add(document)
    db.commit()
    inactive_document = client.get(
        f"{BASE}/items/{draft['id']}", headers=normal_user_token_headers
    ).json()
    assert inactive_document["source_text"] == "Still visible"
    assert inactive_document["read_only_reason"] == "document_inactive"
    dataset.is_active = False
    db.add(dataset)
    db.commit()
    inactive_dataset = client.get(
        f"{BASE}/items/{draft['id']}", headers=normal_user_token_headers
    ).json()
    assert inactive_dataset["read_only_reason"] == "dataset_inactive"
    dataset.is_active = True
    db.add(dataset)
    item = db.get(EvalItem, draft["id"])
    assert item is not None
    item.is_active = False
    db.add(item)
    db.commit()
    inactive_item = client.get(
        f"{BASE}/items/{draft['id']}", headers=normal_user_token_headers
    ).json()
    assert inactive_item["read_only_reason"] == "item_inactive"


def test_malformed_saved_provenance_has_bounded_error(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    dataset = _dataset(db)
    assert dataset.id is not None
    saved = _save(
        client,
        normal_user_token_headers,
        {"dataset_id": dataset.id, "source_text": "Source", "facts": []},
    )
    item = db.get(EvalItem, saved["id"])
    assert item is not None
    item.item_metadata = {
        "fact_provenance": [
            {"fact_position": 99, "chunk_id": 1, "start": 0, "end": 1, "text": "x"}
        ]
    }
    db.add(item)
    db.commit()
    response = client.get(
        f"{BASE}/items/{saved['id']}", headers=normal_user_token_headers
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "Saved fact provenance could not be loaded."
