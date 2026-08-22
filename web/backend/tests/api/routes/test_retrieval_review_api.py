from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session, func, select

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
    RetrievalQAReview,
    User,
)
from app.services.documents import create_document_with_chunks


def test_retrieval_get_next_is_read_only_and_claim_is_idempotent(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    author = User(email="retrieval-author@example.com", hashed_password="x")
    dataset = Dataset(
        name="review-retrieval-next",
        display_name="Review Retrieval Next",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(author)
    db.add(dataset)
    db.flush()
    reviewer.retrieval_dataset_id = dataset.id
    db.add(reviewer)
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Review Retrieval Doc",
        content="The answer is Falcon.",
        external_id="review-retrieval-doc",
    )
    chunk = db.exec(select(Chunk).where(Chunk.document_id == document.id)).first()
    assert chunk is not None
    start = chunk.text.index("Falcon")
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        category="VERBATIM",
        document_id=document.id,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Which answer appears?",
        expected_answer="Falcon",
        evidence_spans=[
            {
                "kind": "gold",
                "chunk_id": chunk.id,
                "start": start,
                "end": start + len("Falcon"),
                "text": "Falcon",
            }
        ],
        gold_chunk_ids=[chunk.id],
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.commit()

    assignment_count_before = db.exec(select(func.count(Assignment.id))).one()
    read_before_claim = client.get(
        f"{settings.API_V1_STR}/review/next?eval_type=RETRIEVAL",
        headers=normal_user_token_headers,
    )
    assert read_before_claim.status_code == 404
    assert db.exec(select(func.count(Assignment.id))).one() == assignment_count_before

    first = client.post(
        f"{settings.API_V1_STR}/review/claim",
        headers=normal_user_token_headers,
        json={"eval_type": "RETRIEVAL", "mode": "ITEM_AUDIT"},
    )
    assert first.status_code == 200
    recommendation = first.json()
    assert recommendation["kind"] == "retrieval_audit"
    assert recommendation["reservation_state"] == "created"
    assert (
        recommendation["review_url"]
        == f"/review/retrieval/{recommendation['assignment_id']}"
    )

    second = client.get(
        f"{settings.API_V1_STR}/review/next?eval_type=RETRIEVAL",
        headers=normal_user_token_headers,
    )
    assert second.status_code == 200
    assert second.json()["assignment_id"] == recommendation["assignment_id"]
    assert second.json()["reservation_state"] == "existing"
    assert (
        db.exec(select(func.count(Assignment.id))).one() == assignment_count_before + 1
    )

    payload = client.get(
        f"{settings.API_V1_STR}/review/retrieval/{recommendation['assignment_id']}",
        headers=normal_user_token_headers,
    )
    assert payload.status_code == 200
    body = payload.json()
    assert body["kind"] == "retrieval_audit"
    assert body["dataset"]["id"] == dataset.id
    assert body["item"]["id"] == item.id
    assert body["gold_evidence_spans"][0]["text"] == "Falcon"
    assert body["documents"][0]["chunks"][0]["text"] == "The answer is Falcon."
    assert body["allowed_actions"] == ["accept", "reject"]


def test_retrieval_review_rejects_wrong_user_and_completed_assignment(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    other_user = User(email="assignment-other@example.com", hashed_password="x")
    dataset = Dataset(
        name="review-retrieval-access",
        display_name="Review Retrieval Access",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(other_user)
    db.add(dataset)
    db.flush()
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        prompt_text="Question?",
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.flush()
    wrong_user_assignment = Assignment(
        dataset_id=dataset.id,
        user_id=other_user.id,
        mode=AssignmentMode.ITEM_AUDIT,
        target_id=item.id,
        kind=AssignmentKind.REGULAR,
        released_at=datetime.now(timezone.utc),
        release_reason="Fixture assignment is no longer live",
    )
    completed_assignment = Assignment(
        dataset_id=dataset.id,
        user_id=reviewer.id,
        mode=AssignmentMode.ITEM_AUDIT,
        target_id=item.id,
        kind=AssignmentKind.REGULAR,
    )
    db.add(wrong_user_assignment)
    db.add(completed_assignment)
    db.flush()
    db.add(
        RetrievalQAReview(
            assignment_id=completed_assignment.id,
            item_id=item.id,
            user_id=reviewer.id,
            question_validity=4,
            evidence_quality=4,
            answer_correctness=4,
            answer_faithfulness=4,
            verdict=ItemVerdict.ACCEPT,
        )
    )
    completed_assignment.completed_at = datetime.now(timezone.utc)
    db.add(completed_assignment)
    db.commit()

    wrong_user = client.get(
        f"{settings.API_V1_STR}/review/retrieval/{wrong_user_assignment.id}",
        headers=normal_user_token_headers,
    )
    assert wrong_user.status_code == 404

    completed = client.get(
        f"{settings.API_V1_STR}/review/retrieval/{completed_assignment.id}",
        headers=normal_user_token_headers,
    )
    assert completed.status_code == 409


def test_retrieval_review_rejects_mismatched_assignment_dataset(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    assignment_dataset = Dataset(
        name="review-retrieval-mismatch-assignment",
        display_name="Review Retrieval Mismatch Assignment",
        eval_type=EvalType.RETRIEVAL,
    )
    item_dataset = Dataset(
        name="review-retrieval-mismatch-item",
        display_name="Review Retrieval Mismatch Item",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(assignment_dataset)
    db.add(item_dataset)
    db.flush()
    item = EvalItem(
        dataset_id=item_dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        prompt_text="Mismatched question?",
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.flush()
    assignment = Assignment(
        dataset_id=assignment_dataset.id,
        user_id=reviewer.id,
        mode=AssignmentMode.ITEM_AUDIT,
        target_id=item.id,
        kind=AssignmentKind.REGULAR,
    )
    db.add(assignment)
    db.commit()

    response = client.get(
        f"{settings.API_V1_STR}/review/retrieval/{assignment.id}",
        headers=normal_user_token_headers,
    )
    assert response.status_code == 404


def test_retrieval_get_next_does_not_mutate_stale_assignments(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    for assignment in db.exec(
        select(Assignment).where(
            Assignment.user_id == reviewer.id,
            Assignment.completed_at.is_(None),
        )
    ).all():
        assignment.completed_at = datetime.now(timezone.utc)
        db.add(assignment)
    author = User(email="retrieval-stale-author@example.com", hashed_password="x")
    dataset = Dataset(
        name="review-retrieval-stale-next",
        display_name="Review Retrieval Stale Next",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(author)
    db.add(dataset)
    db.flush()
    reviewer.retrieval_dataset_id = dataset.id
    db.add(reviewer)
    stale_item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Stale question?",
        status=ItemStatus.REJECTED,
    )
    active_item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Active question?",
        status=ItemStatus.ACTIVE,
    )
    db.add(stale_item)
    db.add(active_item)
    db.flush()
    stale_assignment = Assignment(
        dataset_id=dataset.id,
        user_id=reviewer.id,
        mode=AssignmentMode.ITEM_AUDIT,
        target_id=stale_item.id,
        kind=AssignmentKind.REGULAR,
    )
    db.add(stale_assignment)
    db.commit()

    response = client.get(
        f"{settings.API_V1_STR}/review/next?eval_type=RETRIEVAL",
        headers=normal_user_token_headers,
    )
    assert response.status_code == 404
    db.refresh(stale_assignment)
    assert stale_assignment.completed_at is None

    claimed = client.post(
        f"{settings.API_V1_STR}/review/claim",
        headers=normal_user_token_headers,
        json={"eval_type": "RETRIEVAL", "mode": "ITEM_AUDIT"},
    )
    assert claimed.status_code == 200
    body = claimed.json()
    assert body["reservation_state"] == "created"
    assert body["item_id"] == active_item.id
    assert body["assignment_id"] != stale_assignment.id


def test_retrieval_review_submit_persists_judgment_and_completes_assignment(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    author = User(email="retrieval-submit-author@example.com", hashed_password="x")
    dataset = Dataset(
        name="review-retrieval-submit",
        display_name="Review Retrieval Submit",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(author)
    db.add(dataset)
    db.flush()
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Retrieval Submit Doc",
        content="The review span is Granite.",
        external_id="retrieval-submit-doc",
    )
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Which review span appears?",
        status=ItemStatus.ACTIVE,
        document_id=document.id,
    )
    db.add(item)
    db.flush()
    assignment = Assignment(
        dataset_id=dataset.id,
        user_id=reviewer.id,
        mode=AssignmentMode.ITEM_AUDIT,
        target_id=item.id,
        kind=AssignmentKind.REGULAR,
    )
    db.add(assignment)
    db.commit()

    response = client.post(
        f"{settings.API_V1_STR}/review/retrieval/{assignment.id}",
        headers=normal_user_token_headers,
        json={
            "question_validity": 4,
            "evidence_quality": 4,
            "answer_correctness": 3,
            "answer_faithfulness": 4,
            "accept_as_gold": True,
            "notes": "Looks good.",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "retrieval_audit"
    assert body["assignment_id"] == assignment.id
    assert body["item_id"] == item.id

    judgment = db.exec(
        select(RetrievalQAReview).where(
            RetrievalQAReview.assignment_id == assignment.id
        )
    ).first()
    assert judgment is not None
    assert judgment.verdict == ItemVerdict.ACCEPT
    assert judgment.confidence is None
    assert judgment.question_validity == 4
    assert judgment.evidence_quality == 4
    assert judgment.answer_correctness == 3
    assert judgment.answer_faithfulness == 4
    assert judgment.notes == "Looks good."
    assert judgment.span is None
    db.refresh(assignment)
    assert assignment.completed_at is not None

    duplicate = client.post(
        f"{settings.API_V1_STR}/review/retrieval/{assignment.id}",
        headers=normal_user_token_headers,
        json={
            "question_validity": 4,
            "evidence_quality": 4,
            "answer_correctness": 4,
            "answer_faithfulness": 4,
            "accept_as_gold": True,
            "notes": None,
        },
    )
    assert duplicate.status_code == 409


def test_retrieval_review_submit_rejects_invalid_rubric_score(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    author = User(
        email="retrieval-submit-invalid-author@example.com", hashed_password="x"
    )
    dataset = Dataset(
        name="review-retrieval-submit-invalid",
        display_name="Review Retrieval Submit Invalid",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(author)
    db.add(dataset)
    db.flush()
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Retrieval Submit Invalid Doc",
        content="Correct span text.",
        external_id="retrieval-submit-invalid-doc",
    )
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Submit invalid?",
        status=ItemStatus.ACTIVE,
        document_id=document.id,
        revision=4,
    )
    db.add(item)
    db.flush()
    assignment = Assignment(
        dataset_id=dataset.id,
        user_id=reviewer.id,
        mode=AssignmentMode.ITEM_AUDIT,
        target_id=item.id,
        kind=AssignmentKind.REGULAR,
    )
    db.add(assignment)
    db.commit()

    response = client.post(
        f"{settings.API_V1_STR}/review/retrieval/{assignment.id}",
        headers=normal_user_token_headers,
        json={
            "question_validity": 5,
            "evidence_quality": 4,
            "answer_correctness": 4,
            "answer_faithfulness": 4,
            "accept_as_gold": True,
            "notes": None,
        },
    )
    assert response.status_code == 422
    assert (
        db.exec(
            select(RetrievalQAReview).where(
                RetrievalQAReview.assignment_id == assignment.id
            )
        ).first()
        is None
    )
