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
    EvalFact,
    EvalItem,
    EvalType,
    FactDecompSaveReceipt,
    ItemStatus,
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


def _fact_payload(dataset: Dataset, source_text: str) -> dict:
    assert dataset.id is not None
    return {
        "dataset_id": dataset.id,
        "source_text": source_text,
        "facts": [
            {
                "fact_text": "Baker appears in the source text.",
                "polarity": "SHOULD_LIST",
                "provenance_spans": [],
            },
            {
                "fact_text": "An unsupported answer should not be listed.",
                "polarity": "SHOULD_NOT_LIST",
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
