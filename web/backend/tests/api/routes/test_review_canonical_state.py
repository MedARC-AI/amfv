"""Canonical retrieval verdict, rubric, agreement, and export coverage."""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app import crud
from app.core.config import settings
from app.models import (
    Assignment,
    AssignmentKind,
    AssignmentMode,
    Chunk,
    Dataset,
    EvalItem,
    EvalType,
    ItemSource,
    ItemStatus,
    ItemVerdict,
    PooledCandidate,
    RetrievalQAReview,
    User,
)
from app.services.agreement import dataset_judgments_for_all
from app.services.assignment import (
    available_relevance_candidates,
    item_accepted_for_relevance,
)
from app.services.documents import create_document_with_chunks
from tests.utils.user import authentication_token_from_email


def test_normal_retrieval_verdicts_control_relevance_eligibility(
    client: TestClient,
    db: Session,
) -> None:
    reviewer, reviewer_headers = _reviewer(client, db, "canonical-reviewer")
    labeler, _ = _reviewer(client, db, "canonical-labeler")
    author = User(email=f"canonical-author-{uuid4()}@example.com", hashed_password="x")
    dataset = Dataset(
        name=f"canonical-review-{uuid4()}",
        display_name="Canonical retrieval review",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add_all([author, dataset])
    db.flush()
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Canonical review source",
        content="The canonical answer is Amber.",
        external_id=f"canonical-document-{uuid4()}",
    )
    chunk = db.exec(select(Chunk).where(col(Chunk.document_id) == document.id)).one()
    accepted_item = _retrieval_item(db, dataset, author, "Accepted item")
    rejected_item = _retrieval_item(db, dataset, author, "Rejected item")
    accepted_assignment = _assignment(db, dataset, reviewer, accepted_item, slot=0)
    rejected_assignment = _assignment(db, dataset, reviewer, rejected_item, slot=0)
    accepted_candidate = PooledCandidate(
        dataset_id=dataset.id,
        item_id=accepted_item.id,
        chunk_id=chunk.id,
    )
    rejected_candidate = PooledCandidate(
        dataset_id=dataset.id,
        item_id=rejected_item.id,
        chunk_id=chunk.id,
    )
    db.add_all([accepted_candidate, rejected_candidate])
    db.commit()

    accepted = client.post(
        f"{settings.API_V1_STR}/review/retrieval/{accepted_assignment.id}",
        headers=reviewer_headers,
        json=_retrieval_submission(accept_as_gold=True),
    )
    rejected = client.post(
        f"{settings.API_V1_STR}/review/retrieval/{rejected_assignment.id}",
        headers=reviewer_headers,
        json=_retrieval_submission(accept_as_gold=False),
    )
    assert accepted.status_code == 200
    assert rejected.status_code == 200

    accepted_review = db.exec(
        select(RetrievalQAReview).where(
            col(RetrievalQAReview.assignment_id) == accepted_assignment.id
        )
    ).one()
    rejected_review = db.exec(
        select(RetrievalQAReview).where(
            col(RetrievalQAReview.assignment_id) == rejected_assignment.id
        )
    ).one()
    assert accepted_review.verdict == ItemVerdict.ACCEPT
    assert rejected_review.verdict == ItemVerdict.REJECT
    assert item_accepted_for_relevance(db, accepted_item)
    assert not item_accepted_for_relevance(db, rejected_item)
    assert [
        candidate.id
        for candidate in available_relevance_candidates(db, labeler, dataset)
    ] == [accepted_candidate.id]


def test_retrieval_submission_enforces_rubric_and_canonical_skip_shape(
    client: TestClient,
    db: Session,
) -> None:
    reviewer, reviewer_headers = _reviewer(client, db, "skip-shape")
    author = User(email=f"skip-author-{uuid4()}@example.com", hashed_password="x")
    dataset = Dataset(
        name=f"skip-dataset-{uuid4()}",
        display_name="Skip shape dataset",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add_all([author, dataset])
    db.flush()
    item = _retrieval_item(db, dataset, author, "Skip shape item")
    assignment = _assignment(db, dataset, reviewer, item, slot=0)
    db.commit()

    invalid_score = client.post(
        f"{settings.API_V1_STR}/review/retrieval/{assignment.id}",
        headers=reviewer_headers,
        json={
            **_retrieval_submission(accept_as_gold=True),
            "question_validity": 5,
        },
    )
    invalid_skip = client.post(
        f"{settings.API_V1_STR}/review/retrieval/{assignment.id}",
        headers=reviewer_headers,
        json={
            "skipped": True,
            "skip_reason": "Evidence cannot be assessed",
            "question_validity": 4,
        },
    )
    assert invalid_score.status_code == 422
    assert invalid_skip.status_code == 422

    skipped = client.post(
        f"{settings.API_V1_STR}/review/retrieval/{assignment.id}",
        headers=reviewer_headers,
        json={
            "skipped": True,
            "skip_reason": "Evidence cannot be assessed",
            "notes": "Canonical skip state",
        },
    )
    assert skipped.status_code == 200
    review = db.exec(
        select(RetrievalQAReview).where(
            col(RetrievalQAReview.assignment_id) == assignment.id
        )
    ).one()
    assert review.skipped
    assert review.skip_reason == "Evidence cannot be assessed"
    assert review.question_validity is None
    assert review.evidence_quality is None
    assert review.answer_correctness is None
    assert review.answer_faithfulness is None
    assert review.verdict is None


def test_agreement_and_export_use_canonical_retrieval_columns(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    first_reviewer, _ = _reviewer(client, db, "agreement-first")
    second_reviewer, _ = _reviewer(client, db, "agreement-second")
    author = User(email=f"agreement-author-{uuid4()}@example.com", hashed_password="x")
    dataset = Dataset(
        name=f"agreement-dataset-{uuid4()}",
        display_name="Agreement dataset",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add_all([author, dataset])
    db.flush()
    item = _retrieval_item(db, dataset, author, "Agreement item")
    first_assignment = _assignment(db, dataset, first_reviewer, item, slot=0)
    second_assignment = _assignment(db, dataset, second_reviewer, item, slot=1)
    db.add_all(
        [
            RetrievalQAReview(
                assignment_id=first_assignment.id,
                item_id=item.id,
                user_id=first_reviewer.id,
                question_validity=4,
                evidence_quality=3,
                answer_correctness=4,
                answer_faithfulness=3,
                verdict=ItemVerdict.ACCEPT,
            ),
            RetrievalQAReview(
                assignment_id=second_assignment.id,
                item_id=item.id,
                user_id=second_reviewer.id,
                question_validity=4,
                evidence_quality=3,
                answer_correctness=4,
                answer_faithfulness=3,
                verdict=ItemVerdict.ACCEPT,
            ),
        ]
    )
    db.commit()

    judgments = dataset_judgments_for_all(db, dataset_id=dataset.id)
    assert {
        "item_question_validity",
        "item_evidence_quality",
        "item_answer_correctness",
        "item_answer_faithfulness",
        "item_verdict",
    }.issubset(judgments)
    assert all(
        len(labels[f"item:{item.id}"]) == 2
        for labels in judgments.values()
        if f"item:{item.id}" in labels
    )
    assert judgments["item_question_validity"][f"item:{item.id}"] == {
        first_reviewer.id: "4",
        second_reviewer.id: "4",
    }

    metrics = client.get(
        f"{settings.API_V1_STR}/admin/metrics/agreement?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    )
    assert metrics.status_code == 200
    assert {metric["dimension"] for metric in metrics.json()} >= {
        "item_question_validity",
        "item_evidence_quality",
        "item_answer_correctness",
        "item_answer_faithfulness",
        "item_verdict",
    }

    exported = client.get(
        f"{settings.API_V1_STR}/admin/export?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    )
    assert exported.status_code == 200
    review = exported.json()["items"][0]["retrieval_reviews"][0]
    assert review["question_validity"] == 4
    assert review["verdict"] == "ACCEPT"
    assert "checks" not in review


def _reviewer(
    client: TestClient, db: Session, prefix: str
) -> tuple[User, dict[str, str]]:
    email = f"{prefix}-{uuid4()}@example.com"
    headers = authentication_token_from_email(client=client, email=email, db=db)
    user = crud.get_user_by_email(session=db, email=email)
    assert user is not None
    return user, headers


def _retrieval_item(
    db: Session,
    dataset: Dataset,
    author: User,
    prompt_text: str,
) -> EvalItem:
    assert dataset.id is not None
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text=prompt_text,
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.flush()
    return item


def _assignment(
    db: Session,
    dataset: Dataset,
    user: User,
    item: EvalItem,
    *,
    slot: int,
) -> Assignment:
    assert dataset.id is not None
    assert user.id is not None
    assert item.id is not None
    assignment = Assignment(
        dataset_id=dataset.id,
        user_id=user.id,
        mode=AssignmentMode.ITEM_AUDIT,
        target_id=item.id,
        kind=AssignmentKind.DOUBLE if slot == 1 else AssignmentKind.REGULAR,
        slot=slot,
    )
    db.add(assignment)
    db.flush()
    return assignment


def _retrieval_submission(*, accept_as_gold: bool) -> dict[str, int | bool | str]:
    return {
        "question_validity": 4,
        "evidence_quality": 3,
        "answer_correctness": 4,
        "answer_faithfulness": 3,
        "accept_as_gold": accept_as_gold,
        "notes": "Canonical typed review",
    }
