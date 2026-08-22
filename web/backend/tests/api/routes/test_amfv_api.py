from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session, func, select

from app.core.config import settings
from app.models import (
    Assignment,
    AssignmentKind,
    AssignmentMode,
    Chunk,
    Dataset,
    Document,
    EvalFact,
    EvalItem,
    EvalType,
    FactDecompReview,
    FactPolarity,
    ItemSource,
    ItemStatus,
    ItemVerdict,
    PooledCandidate,
    RelevanceJudgment,
    RetrievalCategory,
    RetrievalQAReview,
    ReviewerKind,
    ReviewTask,
    User,
    UserRole,
)
from app.schemas import EvidenceSpan
from app.services.documents import create_document_with_chunks
from tests.utils.user import authentication_token_from_email


def test_home_summary_returns_counts_and_does_not_reserve(
    client: TestClient,
    db: Session,
) -> None:
    from app import crud

    reviewer_email = f"home-reviewer-{uuid4()}@example.com"
    reviewer_headers = authentication_token_from_email(
        client=client,
        email=reviewer_email,
        db=db,
    )
    reviewer = crud.get_user_by_email(session=db, email=reviewer_email)
    assert reviewer is not None
    author = User(email=f"home-author-{uuid4()}@example.com", hashed_password="x")
    retrieval_dataset = Dataset(
        name=f"home-retrieval-{uuid4()}",
        display_name="000 Home Retrieval",
        eval_type=EvalType.RETRIEVAL,
    )
    fact_dataset = Dataset(
        name=f"home-fact-{uuid4()}",
        display_name="000 Home Fact",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(author)
    db.add(retrieval_dataset)
    db.add(fact_dataset)
    db.flush()
    reviewer.retrieval_dataset_id = retrieval_dataset.id
    db.add(reviewer)

    document = create_document_with_chunks(
        db,
        dataset_id=retrieval_dataset.id,
        title="Home Retrieval Doc",
        content="The answer is Home.",
        external_id=f"home-doc-{uuid4()}",
    )
    chunk = db.exec(select(Chunk).where(Chunk.document_id == document.id)).first()
    assert chunk is not None
    retrieval_item = EvalItem(
        dataset_id=retrieval_dataset.id,
        eval_type=EvalType.RETRIEVAL,
        category="VERBATIM",
        document_id=document.id,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Which answer appears?",
        expected_answer="Home",
        evidence_spans=[
            {
                "kind": "gold",
                "chunk_id": chunk.id,
                "start": chunk.text.index("Home"),
                "end": chunk.text.index("Home") + len("Home"),
                "text": "Home",
            }
        ],
        gold_chunk_ids=[chunk.id],
        status=ItemStatus.ACTIVE,
    )
    fact_item = EvalItem(
        dataset_id=fact_dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Home facts are independently verifiable.",
        status=ItemStatus.ACTIVE,
    )
    authored_draft = EvalItem(
        dataset_id=retrieval_dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=reviewer.id,
        prompt_text="Reviewer draft",
        status=ItemStatus.DRAFT,
    )
    authored_submitted = EvalItem(
        dataset_id=retrieval_dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=reviewer.id,
        prompt_text="Reviewer submitted",
        status=ItemStatus.SUBMITTED,
    )
    db.add(retrieval_item)
    db.add(fact_item)
    db.add(authored_draft)
    db.add(authored_submitted)
    db.flush()
    db.add(
        ReviewTask(
            dataset_id=fact_dataset.id,
            item_a_id=fact_item.id,
            priority_score=2.0,
        )
    )
    db.commit()

    assignment_count_before = db.exec(select(func.count(Assignment.id))).one()
    r = client.get(
        f"{settings.API_V1_STR}/home/summary",
        headers=reviewer_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == reviewer_email
    assert body["outstanding_counts"]["retrieval_reviews"] >= 1
    assert body["outstanding_counts"]["fact_decomp_reviews"] >= 1
    assert body["outstanding_counts"]["draft_items"] == 1
    assert body["outstanding_counts"]["submitted_items"] == 1
    assert body["outstanding_counts"]["authored_items"] == 2
    assert body["authored_total"] == 2
    assert body["reviewed_total"] == 0
    assert body["recommended_task"]["kind"] == "retrieval_audit"
    assert body["recommended_task"]["assignment_id"] is None
    assignment_count_after = db.exec(select(func.count(Assignment.id))).one()
    assert assignment_count_after == assignment_count_before


def test_home_summary_prefers_existing_incomplete_assignment(
    client: TestClient,
    db: Session,
) -> None:
    from app import crud

    reviewer_email = f"home-existing-reviewer-{uuid4()}@example.com"
    reviewer_headers = authentication_token_from_email(
        client=client,
        email=reviewer_email,
        db=db,
    )
    reviewer = crud.get_user_by_email(session=db, email=reviewer_email)
    assert reviewer is not None
    author = User(
        email=f"home-existing-author-{uuid4()}@example.com", hashed_password="x"
    )
    dataset = Dataset(
        name=f"home-existing-retrieval-{uuid4()}",
        display_name="Home Existing Retrieval",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(author)
    db.add(dataset)
    db.flush()
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Existing assignment question?",
        status=ItemStatus.ACTIVE,
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
    db.refresh(assignment)

    r = client.get(
        f"{settings.API_V1_STR}/home/summary",
        headers=reviewer_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["recommended_task"]["kind"] == "retrieval_audit"
    assert body["recommended_task"]["reason"] == "Existing assignment"
    assert body["recommended_task"]["assignment_id"] == assignment.id


def test_home_summary_matches_assignment_gating_without_reserving(
    client: TestClient,
    db: Session,
) -> None:
    from app import crud

    reviewer_email = f"home-gated-reviewer-{uuid4()}@example.com"
    reviewer_headers = authentication_token_from_email(
        client=client,
        email=reviewer_email,
        db=db,
    )
    reviewer = crud.get_user_by_email(session=db, email=reviewer_email)
    assert reviewer is not None
    author = User(email=f"home-gated-author-{uuid4()}@example.com", hashed_password="x")
    other_reviewer = User(
        email=f"home-gated-other-{uuid4()}@example.com",
        hashed_password="x",
    )
    dataset = Dataset(
        name=f"home-gated-retrieval-{uuid4()}",
        display_name="000 Home Gated Retrieval",
        eval_type=EvalType.RETRIEVAL,
        double_rate=0,
        trap_rate=0,
    )
    db.add(author)
    db.add(other_reviewer)
    db.add(dataset)
    db.flush()
    reviewer.retrieval_dataset_id = dataset.id
    db.add(reviewer)

    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Home Gated Doc",
        content="The gated answer is Labelled.",
        external_id=f"home-gated-doc-{uuid4()}",
    )
    chunk = db.exec(select(Chunk).where(Chunk.document_id == document.id)).first()
    assert chunk is not None
    regular_item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        category="VERBATIM",
        document_id=document.id,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Which gated answer appears?",
        expected_answer="Labelled",
        status=ItemStatus.ACTIVE,
    )
    self_authored_calibration = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        category="VERBATIM",
        document_id=document.id,
        source=ItemSource.HUMAN,
        author_user_id=reviewer.id,
        prompt_text="Calibration should be skipped because it is self-authored.",
        expected_answer="Labelled",
        status=ItemStatus.ACTIVE,
        is_calibration=True,
    )
    db.add(regular_item)
    db.add(self_authored_calibration)
    db.flush()
    completed_assignment = Assignment(
        dataset_id=dataset.id,
        user_id=other_reviewer.id,
        mode=AssignmentMode.ITEM_AUDIT,
        target_id=regular_item.id,
        kind=AssignmentKind.REGULAR,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(completed_assignment)
    db.flush()
    db.add(
        RetrievalQAReview(
            assignment_id=completed_assignment.id,
            item_id=regular_item.id,
            user_id=other_reviewer.id,
            question_validity=4,
            evidence_quality=4,
            answer_correctness=4,
            answer_faithfulness=4,
            verdict=ItemVerdict.ACCEPT,
        )
    )
    candidate = PooledCandidate(
        dataset_id=dataset.id,
        item_id=regular_item.id,
        chunk_id=chunk.id,
    )
    db.add(candidate)
    db.flush()
    relevance_assignment = Assignment(
        dataset_id=dataset.id,
        user_id=other_reviewer.id,
        mode=AssignmentMode.RELEVANCE,
        target_id=candidate.id,
        kind=AssignmentKind.REGULAR,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(relevance_assignment)
    db.flush()
    db.add(
        RelevanceJudgment(
            assignment_id=relevance_assignment.id,
            candidate_id=candidate.id,
            user_id=other_reviewer.id,
            grade=3,
        )
    )
    db.commit()

    assignment_count_before = db.exec(select(func.count(Assignment.id))).one()
    r = client.get(
        f"{settings.API_V1_STR}/home/summary",
        headers=reviewer_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["outstanding_counts"]["retrieval_reviews"] == 0
    assert body["outstanding_counts"]["relevance_reviews"] == 0
    assignment_count_after = db.exec(select(func.count(Assignment.id))).one()
    assert assignment_count_after == assignment_count_before


def test_home_summary_skips_stale_existing_relevance_assignment(
    client: TestClient,
    db: Session,
) -> None:
    from app import crud

    reviewer_email = f"home-stale-relevance-reviewer-{uuid4()}@example.com"
    reviewer_headers = authentication_token_from_email(
        client=client,
        email=reviewer_email,
        db=db,
    )
    reviewer = crud.get_user_by_email(session=db, email=reviewer_email)
    assert reviewer is not None
    author = User(
        email=f"home-stale-relevance-author-{uuid4()}@example.com",
        hashed_password="x",
    )
    dataset = Dataset(
        name=f"home-stale-relevance-{uuid4()}",
        display_name="000 Home Stale Relevance",
        eval_type=EvalType.RETRIEVAL,
        double_rate=0,
        trap_rate=0,
    )
    db.add(author)
    db.add(dataset)
    db.flush()
    reviewer.retrieval_dataset_id = dataset.id
    db.add(reviewer)
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Home Stale Relevance Doc",
        content="The stale assignment still has a valid chunk.",
        external_id=f"home-stale-relevance-doc-{uuid4()}",
    )
    chunk = db.exec(select(Chunk).where(Chunk.document_id == document.id)).first()
    assert chunk is not None
    stale_item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        category="VERBATIM",
        document_id=document.id,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="This inactive item should make the assignment stale.",
        expected_answer="valid chunk",
        status=ItemStatus.ACTIVE,
        is_active=False,
    )
    db.add(stale_item)
    db.flush()
    candidate = PooledCandidate(
        dataset_id=dataset.id,
        item_id=stale_item.id,
        chunk_id=chunk.id,
    )
    db.add(candidate)
    db.flush()
    assignment = Assignment(
        dataset_id=dataset.id,
        user_id=reviewer.id,
        mode=AssignmentMode.RELEVANCE,
        target_id=candidate.id,
        kind=AssignmentKind.REGULAR,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)

    assignment_count_before = db.exec(select(func.count(Assignment.id))).one()
    r = client.get(
        f"{settings.API_V1_STR}/home/summary",
        headers=reviewer_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["outstanding_counts"]["relevance_reviews"] == 0
    if body["recommended_task"] is not None:
        assert body["recommended_task"].get("assignment_id") != assignment.id
    db.refresh(assignment)
    assert assignment.completed_at is None
    assignment_count_after = db.exec(select(func.count(Assignment.id))).one()
    assert assignment_count_after == assignment_count_before


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
    assert judgment.checks is None
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


def test_fact_decomp_next_selects_task_without_placeholder_review(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    author = User(email="fact-review-author@example.com", hashed_password="x")
    dataset = Dataset(
        name="review-fact-next",
        display_name="Review Fact Next",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(author)
    db.add(dataset)
    db.flush()
    reviewer.fact_decomp_dataset_id = dataset.id
    db.add(reviewer)
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Fact Source",
        content="Alpha is true.",
        external_id="fact-source-review",
    )
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        document_id=document.id,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Alpha is true.",
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.flush()
    db.add(
        EvalFact(
            item_id=item.id,
            fact_uuid="fact-1",
            fact_text="Alpha is true.",
            polarity=FactPolarity.SHOULD_LIST,
            position=1,
        )
    )
    task = ReviewTask(dataset_id=dataset.id, item_a_id=item.id, priority_score=10)
    db.add(task)
    db.commit()

    response = client.get(
        f"{settings.API_V1_STR}/review/next?eval_type=FACT_DECOMP",
        headers=normal_user_token_headers,
    )
    assert response.status_code == 200
    recommendation = response.json()
    assert recommendation["kind"] == "fact_decomp"
    assert recommendation["reservation_state"] == "selected"
    assert recommendation["task_id"] == task.id
    assert (
        db.exec(
            select(FactDecompReview).where(FactDecompReview.task_id == task.id)
        ).first()
        is None
    )

    payload = client.get(
        f"{settings.API_V1_STR}/review/fact-decomp/{task.id}",
        headers=normal_user_token_headers,
    )
    assert payload.status_code == 200
    body = payload.json()
    assert body["kind"] == "fact_decomp"
    assert body["facts"][0]["fact_uuid"] == "fact-1"
    assert [row["key"] for row in body["rubric_dimensions"]] == [
        "independently_verifiable",
        "noise_removed",
        "deduplicated_ordered",
    ]


def test_fact_decomp_review_rejects_mismatched_task_dataset(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    task_dataset = Dataset(
        name="review-fact-mismatch-task",
        display_name="Review Fact Mismatch Task",
        eval_type=EvalType.FACT_DECOMP,
    )
    item_dataset = Dataset(
        name="review-fact-mismatch-item",
        display_name="Review Fact Mismatch Item",
        eval_type=EvalType.FACT_DECOMP,
    )
    author = User(email="fact-mismatch-author@example.com", hashed_password="x")
    db.add(task_dataset)
    db.add(item_dataset)
    db.add(author)
    db.flush()
    item = EvalItem(
        dataset_id=item_dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Mismatched fact.",
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.flush()
    task = ReviewTask(dataset_id=task_dataset.id, item_a_id=item.id)
    db.add(task)
    db.commit()

    response = client.get(
        f"{settings.API_V1_STR}/review/fact-decomp/{task.id}",
        headers=normal_user_token_headers,
    )
    assert response.status_code == 404


def test_fact_decomp_next_skips_self_and_previously_reviewed_tasks(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    other_author = User(email="fact-other-author@example.com", hashed_password="x")
    dataset = Dataset(
        name="review-fact-skip",
        display_name="Review Fact Skip",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(other_author)
    db.add(dataset)
    db.flush()
    reviewer.fact_decomp_dataset_id = dataset.id
    db.add(reviewer)
    self_item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=reviewer.id,
        prompt_text="Self item.",
        status=ItemStatus.ACTIVE,
    )
    reviewed_item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=other_author.id,
        prompt_text="Reviewed item.",
        status=ItemStatus.ACTIVE,
    )
    next_item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=other_author.id,
        prompt_text="Next item.",
        status=ItemStatus.ACTIVE,
    )
    db.add(self_item)
    db.add(reviewed_item)
    db.add(next_item)
    db.flush()
    self_task = ReviewTask(dataset_id=dataset.id, item_a_id=self_item.id)
    reviewed_task = ReviewTask(dataset_id=dataset.id, item_a_id=reviewed_item.id)
    next_task = ReviewTask(dataset_id=dataset.id, item_a_id=next_item.id)
    db.add(self_task)
    db.add(reviewed_task)
    db.add(next_task)
    db.flush()
    db.add(
        FactDecompReview(
            task_id=reviewed_task.id,
            user_id=reviewer.id,
            item_revision=reviewed_item.revision,
            reviewer_kind=ReviewerKind.human,
            ratings={"independently_verifiable": "pass"},
        )
    )
    db.commit()

    response = client.get(
        f"{settings.API_V1_STR}/review/next?eval_type=FACT_DECOMP",
        headers=normal_user_token_headers,
    )
    assert response.status_code == 200
    assert response.json()["task_id"] == next_task.id


def test_fact_decomp_review_submit_persists_review_and_updates_labels(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    author = User(email="fact-submit-author@example.com", hashed_password="x")
    dataset = Dataset(
        name="review-fact-submit",
        display_name="Review Fact Submit",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(author)
    db.add(dataset)
    db.flush()
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Alpha is true.",
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.flush()
    should_fact = EvalFact(
        item_id=item.id,
        fact_uuid="fact-should",
        fact_text="Alpha is true.",
        polarity=FactPolarity.SHOULD_LIST,
        position=0,
    )
    should_not_fact = EvalFact(
        item_id=item.id,
        fact_uuid="fact-should-not",
        fact_text="Beta is true.",
        polarity=FactPolarity.SHOULD_NOT_LIST,
        position=1,
    )
    task = ReviewTask(dataset_id=dataset.id, item_a_id=item.id, labels_count=1)
    db.add(should_fact)
    db.add(should_not_fact)
    db.add(task)
    db.commit()

    response = client.post(
        f"{settings.API_V1_STR}/review/fact-decomp/{task.id}",
        headers=normal_user_token_headers,
        json={
            "fact_calls": {
                "fact-should": "SHOULD_LIST",
                "fact-should-not": "SHOULD_NOT_LIST",
            },
            "values": {
                "independently_verifiable": "pass",
                "noise_removed": "pass",
                "deduplicated_ordered": "pass",
            },
            "comments": "Ready.",
            "confidence": "EASY_CALL",
            "item_revision": item.revision,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == task.id
    assert body["item_id"] == item.id
    assert body["labels_count"] == 2

    review = db.exec(
        select(FactDecompReview).where(
            FactDecompReview.task_id == task.id, FactDecompReview.user_id == reviewer.id
        )
    ).first()
    assert review is not None
    assert review.comment == "Ready."
    assert review.flags == {"confidence": "EASY_CALL"}
    assert review.ratings["independently_verifiable"] == "pass"
    assert review.ratings["fact_agreement"] == {
        "fact-should": "agree",
        "fact-should-not": "agree",
    }
    db.refresh(task)
    assert task.labels_count == 2

    duplicate = client.post(
        f"{settings.API_V1_STR}/review/fact-decomp/{task.id}",
        headers=normal_user_token_headers,
        json={
            "fact_calls": {
                "fact-should": "SHOULD_LIST",
                "fact-should-not": "SHOULD_NOT_LIST",
            },
            "values": {
                "independently_verifiable": "pass",
                "noise_removed": "pass",
                "deduplicated_ordered": "pass",
            },
            "item_revision": item.revision,
        },
    )
    assert duplicate.status_code == 409


def test_fact_decomp_review_submit_rejects_stale_invalid_and_self_review(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    author = User(email="fact-submit-invalid-author@example.com", hashed_password="x")
    dataset = Dataset(
        name="review-fact-submit-invalid",
        display_name="Review Fact Submit Invalid",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(author)
    db.add(dataset)
    db.flush()
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Gamma is true.",
        status=ItemStatus.ACTIVE,
        revision=5,
    )
    invalid_item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Delta is true.",
        status=ItemStatus.ACTIVE,
        revision=5,
    )
    self_item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=reviewer.id,
        prompt_text="Self review item.",
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.add(invalid_item)
    db.add(self_item)
    db.flush()
    db.add(
        EvalFact(
            item_id=item.id,
            fact_uuid="fact-gamma",
            fact_text="Gamma is true.",
            polarity=FactPolarity.SHOULD_LIST,
            position=0,
        )
    )
    db.add(
        EvalFact(
            item_id=invalid_item.id,
            fact_uuid="fact-delta",
            fact_text="Delta is true.",
            polarity=FactPolarity.SHOULD_LIST,
            position=0,
        )
    )
    stale_task = ReviewTask(dataset_id=dataset.id, item_a_id=item.id)
    invalid_task = ReviewTask(dataset_id=dataset.id, item_a_id=invalid_item.id)
    self_task = ReviewTask(dataset_id=dataset.id, item_a_id=self_item.id)
    db.add(stale_task)
    db.add(invalid_task)
    db.add(self_task)
    db.commit()

    stale = client.post(
        f"{settings.API_V1_STR}/review/fact-decomp/{stale_task.id}",
        headers=normal_user_token_headers,
        json={
            "fact_calls": {"fact-gamma": "SHOULD_LIST"},
            "values": {
                "independently_verifiable": "pass",
                "noise_removed": "pass",
                "deduplicated_ordered": "pass",
            },
            "item_revision": 4,
        },
    )
    assert stale.status_code == 409

    invalid = client.post(
        f"{settings.API_V1_STR}/review/fact-decomp/{invalid_task.id}",
        headers=normal_user_token_headers,
        json={
            "fact_calls": {},
            "values": {
                "independently_verifiable": "pass",
                "noise_removed": "pass",
                "deduplicated_ordered": "pass",
            },
            "item_revision": invalid_item.revision,
        },
    )
    assert invalid.status_code == 400
    assert "missing calls" in invalid.json()["detail"][0]["message"]

    self_review = client.post(
        f"{settings.API_V1_STR}/review/fact-decomp/{self_task.id}",
        headers=normal_user_token_headers,
        json={
            "fact_calls": {},
            "values": {
                "independently_verifiable": "pass",
                "noise_removed": "pass",
                "deduplicated_ordered": "pass",
            },
            "item_revision": self_item.revision,
        },
    )
    assert self_review.status_code == 403


def test_relevance_next_and_payload_use_pooled_candidate(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    author = User(email="relevance-author@example.com", hashed_password="x")
    dataset = Dataset(
        name="review-relevance-next",
        display_name="Review Relevance Next",
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
        title="Relevance Doc",
        content="Candidate passage.",
        external_id="relevance-doc",
    )
    chunk = db.exec(select(Chunk).where(Chunk.document_id == document.id)).first()
    assert chunk is not None
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Find passage.",
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.flush()
    candidate = PooledCandidate(
        dataset_id=dataset.id,
        item_id=item.id,
        chunk_id=chunk.id,
        is_calibration=True,
        systems=["seed"],
        ranks={"seed": 1},
    )
    db.add(candidate)
    db.commit()

    response = client.post(
        f"{settings.API_V1_STR}/review/claim",
        headers=normal_user_token_headers,
        json={"eval_type": "RETRIEVAL", "mode": "RELEVANCE"},
    )
    assert response.status_code == 200
    recommendation = response.json()
    assert recommendation["kind"] == "relevance"
    assert recommendation["item_id"] == item.id

    payload = client.get(
        f"{settings.API_V1_STR}/review/relevance/{recommendation['assignment_id']}",
        headers=normal_user_token_headers,
    )
    assert payload.status_code == 200
    body = payload.json()
    assert body["kind"] == "relevance"
    assert body["candidate"]["id"] == candidate.id
    assert body["chunk"]["text"] == "Candidate passage."


def test_relevance_review_rejects_mismatched_candidate_dataset(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    assignment_dataset = Dataset(
        name="review-relevance-mismatch-assignment",
        display_name="Review Relevance Mismatch Assignment",
        eval_type=EvalType.RETRIEVAL,
    )
    candidate_dataset = Dataset(
        name="review-relevance-mismatch-candidate",
        display_name="Review Relevance Mismatch Candidate",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(assignment_dataset)
    db.add(candidate_dataset)
    db.flush()
    document = create_document_with_chunks(
        db,
        dataset_id=candidate_dataset.id,
        title="Candidate Mismatch Doc",
        content="Candidate mismatch passage.",
        external_id="candidate-mismatch-doc",
    )
    chunk = db.exec(select(Chunk).where(Chunk.document_id == document.id)).first()
    assert chunk is not None
    item = EvalItem(
        dataset_id=candidate_dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        prompt_text="Candidate mismatch?",
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.flush()
    candidate = PooledCandidate(
        dataset_id=candidate_dataset.id,
        item_id=item.id,
        chunk_id=chunk.id,
    )
    db.add(candidate)
    db.flush()
    assignment = Assignment(
        dataset_id=assignment_dataset.id,
        user_id=reviewer.id,
        mode=AssignmentMode.RELEVANCE,
        target_id=candidate.id,
        kind=AssignmentKind.REGULAR,
    )
    db.add(assignment)
    db.commit()

    response = client.get(
        f"{settings.API_V1_STR}/review/relevance/{assignment.id}",
        headers=normal_user_token_headers,
    )
    assert response.status_code == 404


def test_relevance_review_submit_persists_judgment_and_completes_assignment(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    author = User(email="relevance-submit-author@example.com", hashed_password="x")
    dataset = Dataset(
        name="review-relevance-submit",
        display_name="Review Relevance Submit",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(author)
    db.add(dataset)
    db.flush()
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Relevance Submit Doc",
        content="Relevant passage.",
        external_id="relevance-submit-doc",
    )
    chunk = db.exec(select(Chunk).where(Chunk.document_id == document.id)).first()
    assert chunk is not None
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Find relevant passage.",
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.flush()
    candidate = PooledCandidate(
        dataset_id=dataset.id, item_id=item.id, chunk_id=chunk.id
    )
    db.add(candidate)
    db.flush()
    assignment = Assignment(
        dataset_id=dataset.id,
        user_id=reviewer.id,
        mode=AssignmentMode.RELEVANCE,
        target_id=candidate.id,
        kind=AssignmentKind.REGULAR,
    )
    db.add(assignment)
    db.commit()

    response = client.post(
        f"{settings.API_V1_STR}/review/relevance/{assignment.id}",
        headers=normal_user_token_headers,
        json={"grade": 3, "confidence": "DELIBERATED", "item_revision": item.revision},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "relevance"
    assert body["assignment_id"] == assignment.id
    assert body["item_id"] == item.id

    judgment = db.exec(
        select(RelevanceJudgment).where(
            RelevanceJudgment.assignment_id == assignment.id
        )
    ).first()
    assert judgment is not None
    assert judgment.candidate_id == candidate.id
    assert judgment.grade == 3
    assert judgment.confidence == "DELIBERATED"
    db.refresh(assignment)
    assert assignment.completed_at is not None

    duplicate = client.post(
        f"{settings.API_V1_STR}/review/relevance/{assignment.id}",
        headers=normal_user_token_headers,
        json={"grade": 3, "item_revision": item.revision},
    )
    assert duplicate.status_code == 409


def test_relevance_review_submit_rejects_stale_revision_and_invalid_grade(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    author = User(
        email="relevance-submit-stale-author@example.com", hashed_password="x"
    )
    dataset = Dataset(
        name="review-relevance-submit-stale",
        display_name="Review Relevance Submit Stale",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(author)
    db.add(dataset)
    db.flush()
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Relevance Submit Stale Doc",
        content="Relevant stale passage.",
        external_id="relevance-submit-stale-doc",
    )
    chunk = db.exec(select(Chunk).where(Chunk.document_id == document.id)).first()
    assert chunk is not None
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Find stale relevant passage.",
        status=ItemStatus.ACTIVE,
        revision=2,
    )
    db.add(item)
    db.flush()
    candidate = PooledCandidate(
        dataset_id=dataset.id, item_id=item.id, chunk_id=chunk.id
    )
    db.add(candidate)
    db.flush()
    assignment = Assignment(
        dataset_id=dataset.id,
        user_id=reviewer.id,
        mode=AssignmentMode.RELEVANCE,
        target_id=candidate.id,
        kind=AssignmentKind.REGULAR,
    )
    db.add(assignment)
    db.commit()

    stale = client.post(
        f"{settings.API_V1_STR}/review/relevance/{assignment.id}",
        headers=normal_user_token_headers,
        json={"grade": 2, "item_revision": 1},
    )
    assert stale.status_code == 409
    assert (
        db.exec(
            select(RelevanceJudgment).where(
                RelevanceJudgment.assignment_id == assignment.id
            )
        ).first()
        is None
    )

    invalid_grade = client.post(
        f"{settings.API_V1_STR}/review/relevance/{assignment.id}",
        headers=normal_user_token_headers,
        json={"grade": 4, "item_revision": item.revision},
    )
    assert invalid_grade.status_code == 422


def test_create_options_and_source_documents(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="api-retrieval",
        display_name="API Retrieval",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(dataset)
    db.flush()
    document = Document(
        dataset_id=dataset.id,
        external_id="doc-api",
        title="API Doc",
        content="Document text",
        paragraphs=[],
    )
    db.add(document)
    db.commit()

    options = client.get(
        f"{settings.API_V1_STR}/create/options",
        headers=normal_user_token_headers,
    )
    assert options.status_code == 200
    assert any(row["name"] == "api-retrieval" for row in options.json())

    documents = client.get(
        f"{settings.API_V1_STR}/create/source-documents"
        "?dataset_id={dataset_id}&eval_type=RETRIEVAL".format(dataset_id=dataset.id),
        headers=normal_user_token_headers,
    )
    assert documents.status_code == 200
    assert documents.json()[0]["external_id"] == "doc-api"


def test_source_documents_hide_current_users_used_retrieval_documents(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    user = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert user is not None
    dataset = Dataset(
        name=f"api-retrieval-used-{uuid4()}",
        display_name="API Retrieval Used",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(dataset)
    db.flush()
    used_document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        external_id="used-doc-api",
        title="Used API Doc",
        content="Used evidence text.",
    )
    create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        external_id="unused-doc-api",
        title="Unused API Doc",
        content="Unused evidence text.",
    )
    used_chunk = db.exec(
        select(Chunk).where(Chunk.document_id == used_document.id)
    ).first()
    assert used_chunk is not None
    db.add(
        EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.RETRIEVAL,
            category=RetrievalCategory.MULTI_CHUNK,
            source=ItemSource.HUMAN,
            author_user_id=user.id,
            prompt_text="Used prompt",
            gold_chunk_ids=[used_chunk.id],
            status=ItemStatus.SUBMITTED,
        )
    )
    db.commit()

    filtered = client.get(
        f"{settings.API_V1_STR}/create/source-documents"
        "?dataset_id={dataset_id}&eval_type=RETRIEVAL".format(dataset_id=dataset.id),
        headers=normal_user_token_headers,
    )
    assert filtered.status_code == 200
    assert [row["external_id"] for row in filtered.json()] == ["unused-doc-api"]

    included = client.get(
        f"{settings.API_V1_STR}/create/source-documents"
        "?dataset_id={dataset_id}&eval_type=RETRIEVAL&include_used=true".format(
            dataset_id=dataset.id
        ),
        headers=normal_user_token_headers,
    )
    assert included.status_code == 200
    assert {row["external_id"] for row in included.json()} == {
        "used-doc-api",
        "unused-doc-api",
    }


def test_create_source_document_detail_returns_chunks_and_enforces_scope(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    retrieval_dataset = Dataset(
        name="detail-retrieval",
        display_name="Detail Retrieval",
        eval_type=EvalType.RETRIEVAL,
    )
    fact_dataset = Dataset(
        name="detail-fact",
        display_name="Detail Fact",
        eval_type=EvalType.FACT_DECOMP,
    )
    inactive_dataset = Dataset(
        name="detail-inactive",
        display_name="Detail Inactive",
        eval_type=EvalType.RETRIEVAL,
        is_active=False,
    )
    db.add(retrieval_dataset)
    db.add(fact_dataset)
    db.add(inactive_dataset)
    db.flush()
    document = create_document_with_chunks(
        db,
        dataset_id=retrieval_dataset.id,
        title="Detail Doc",
        content="First paragraph.\n\nSecond paragraph with emoji 😀.",
        external_id="detail-doc",
        source_url="https://www.nice.org.uk/guidance/ng235/advice/why-this-is-important",
    )
    inactive_document = create_document_with_chunks(
        db,
        dataset_id=retrieval_dataset.id,
        title="Inactive Detail Doc",
        content="Hidden paragraph.",
        external_id="inactive-detail-doc",
    )
    inactive_document.is_active = False
    inactive_dataset_document = create_document_with_chunks(
        db,
        dataset_id=inactive_dataset.id,
        title="Inactive Dataset Detail Doc",
        content="Hidden dataset paragraph.",
        external_id="inactive-dataset-detail-doc",
    )
    db.add(inactive_document)
    db.commit()

    detail = client.get(
        f"{settings.API_V1_STR}/create/source-documents/{document.id}"
        "?dataset_id={dataset_id}&eval_type=RETRIEVAL".format(
            dataset_id=retrieval_dataset.id
        ),
        headers=normal_user_token_headers,
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["external_id"] == "detail-doc"
    assert body["source_url"] == "https://www.nice.org.uk/guidance/ng235/advice/why-this-is-important"
    assert body["content"] == "First paragraph.\n\nSecond paragraph with emoji 😀."
    assert [chunk["position"] for chunk in body["chunks"]] == [0]
    assert body["chunks"][0]["text"] == body["content"]

    wrong_eval_type = client.get(
        f"{settings.API_V1_STR}/create/source-documents/{document.id}"
        "?dataset_id={dataset_id}&eval_type=FACT_DECOMP".format(
            dataset_id=retrieval_dataset.id
        ),
        headers=normal_user_token_headers,
    )
    assert wrong_eval_type.status_code == 404

    wrong_dataset = client.get(
        f"{settings.API_V1_STR}/create/source-documents/{document.id}"
        "?dataset_id={dataset_id}&eval_type=FACT_DECOMP".format(
            dataset_id=fact_dataset.id
        ),
        headers=normal_user_token_headers,
    )
    assert wrong_dataset.status_code == 404

    inactive_doc = client.get(
        f"{settings.API_V1_STR}/create/source-documents/{inactive_document.id}"
        "?dataset_id={dataset_id}&eval_type=RETRIEVAL".format(
            dataset_id=retrieval_dataset.id
        ),
        headers=normal_user_token_headers,
    )
    assert inactive_doc.status_code == 404

    inactive_dataset_response = client.get(
        f"{settings.API_V1_STR}/create/source-documents/{inactive_dataset_document.id}"
        "?dataset_id={dataset_id}&eval_type=RETRIEVAL".format(
            dataset_id=inactive_dataset.id
        ),
        headers=normal_user_token_headers,
    )
    assert inactive_dataset_response.status_code == 404


def test_admin_dataset_list_requires_data_role(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    superuser_token_headers: dict[str, str],
) -> None:
    denied = client.get(
        f"{settings.API_V1_STR}/admin/datasets",
        headers=normal_user_token_headers,
    )
    assert denied.status_code == 403

    allowed = client.get(
        f"{settings.API_V1_STR}/admin/datasets",
        headers=superuser_token_headers,
    )
    assert allowed.status_code == 200

    items = client.get(
        f"{settings.API_V1_STR}/admin/items",
        headers=superuser_token_headers,
    )
    assert items.status_code == 200
    assert isinstance(items.json(), list)


def test_admin_dataset_list_accepts_data_role_user(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    # Mutate the fixture user directly so the existing token remains valid.
    from app import crud

    db_user = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert db_user is not None
    db_user.role = UserRole.data_admin
    db.add(db_user)
    db.commit()

    allowed = client.get(
        f"{settings.API_V1_STR}/admin/datasets",
        headers=normal_user_token_headers,
    )
    assert allowed.status_code == 200

    db_user.role = UserRole.user
    db.add(db_user)
    db.commit()


def test_admin_can_create_dataset_document_and_toggle_document(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset_response = client.post(
        f"{settings.API_V1_STR}/admin/datasets",
        headers=superuser_token_headers,
        json={
            "name": "admin-core-dataset",
            "display_name": "Admin Core Dataset",
            "eval_type": "RETRIEVAL",
        },
    )
    assert dataset_response.status_code == 200
    dataset_id = dataset_response.json()["id"]

    document_response = client.post(
        f"{settings.API_V1_STR}/admin/documents",
        headers=superuser_token_headers,
        json={
            "dataset_id": dataset_id,
            "external_id": "admin-core-doc",
            "title": "Admin Core Doc",
            "content": "First paragraph.\n\nSecond paragraph.",
        },
    )
    assert document_response.status_code == 200
    document = document_response.json()
    assert document["external_id"] == "admin-core-doc"
    assert [chunk["external_id"] for chunk in document["chunks"]] == [
        "admin-core-doc-chunk-0",
    ]
    assert document["chunks"][0]["text"] == "First paragraph.\n\nSecond paragraph."

    toggled = client.post(
        f"{settings.API_V1_STR}/admin/documents/{document['id']}/toggle",
        headers=superuser_token_headers,
    )
    assert toggled.status_code == 200
    assert toggled.json()["is_active"] is False

    db_document = db.get(Document, document["id"])
    assert db_document is not None
    assert db_document.is_active is False


def test_admin_moderation_keeps_retrieval_task_generation_empty_and_exports_evidence(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="admin-core-moderation",
        display_name="Admin Core Moderation",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(dataset)
    db.flush()
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Moderation Doc",
        content="The answer is Delta.",
        external_id="doc-moderation",
    )
    db.flush()
    chunk = db.exec(select(Chunk).where(Chunk.document_id == document.id)).first()
    assert chunk is not None
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        category="VERBATIM",
        document_id=document.id,
        source=ItemSource.HUMAN,
        prompt_text="Which answer appears?",
        expected_answer="Delta",
        evidence_spans=[
            {
                "chunk_id": chunk.id,
                "start": chunk.text.index("Delta"),
                "end": chunk.text.index("Delta") + len("Delta"),
                "text": "Delta",
                "kind": "gold",
            }
        ],
        gold_chunk_ids=[chunk.id],
        status=ItemStatus.SUBMITTED,
    )
    db.add(item)
    db.commit()

    listed = client.get(
        f"{settings.API_V1_STR}/admin/items?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    )
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "SUBMITTED"

    approved = client.post(
        f"{settings.API_V1_STR}/admin/items/{item.id}/approve",
        headers=superuser_token_headers,
        json={
            "action": "approve",
            "dataset_id": dataset.id,
            "expected_item_revision": item.revision,
        },
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "ACTIVE"
    approved_revision = approved.json()["revision"]

    generated = client.post(
        f"{settings.API_V1_STR}/admin/datasets/{dataset.id}/generate-tasks",
        headers=superuser_token_headers,
    )
    assert generated.status_code == 200
    assert generated.json()["created"] == 0
    assert (
        db.exec(select(ReviewTask).where(ReviewTask.item_a_id == item.id)).first()
        is None
    )

    regenerated = client.post(
        f"{settings.API_V1_STR}/admin/datasets/{dataset.id}/generate-tasks",
        headers=superuser_token_headers,
    )
    assert regenerated.status_code == 200
    assert regenerated.json()["created"] == 0

    exported = client.get(
        f"{settings.API_V1_STR}/admin/export?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    )
    assert exported.status_code == 200
    export_item = exported.json()["items"][0]
    assert export_item["evidence_chunks"][0]["text"] == "The answer is Delta."
    assert export_item["evidence_documents"][0]["external_id"] == "doc-moderation"
    assert export_item["review_task_count"] == 0

    returned_to_draft = client.post(
        f"{settings.API_V1_STR}/admin/items/{item.id}/reject",
        headers=superuser_token_headers,
        json={
            "action": "return_to_draft",
            "dataset_id": dataset.id,
            "expected_item_revision": approved_revision,
            "reason": "Needs edit",
        },
    )
    assert returned_to_draft.status_code == 400
    assert returned_to_draft.json()["detail"] == "Only submitted items can be moderated"

    queue_after_approval = client.get(
        f"{settings.API_V1_STR}/admin/items?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    )
    assert queue_after_approval.status_code == 200
    assert queue_after_approval.json() == []

    all_items = client.get(
        f"{settings.API_V1_STR}/admin/items?dataset_id={dataset.id}&status=ACTIVE",
        headers=superuser_token_headers,
    )
    assert all_items.status_code == 200
    assert all_items.json()[0]["status"] == "ACTIVE"


def test_admin_moderation_rejects_revision_mismatch(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="admin-core-revision",
        display_name="Admin Core Revision",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.flush()
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        prompt_text="Statement.",
        status=ItemStatus.SUBMITTED,
        revision=3,
    )
    db.add(item)
    db.commit()

    response = client.post(
        f"{settings.API_V1_STR}/admin/items/{item.id}/approve",
        headers=superuser_token_headers,
        json={
            "action": "approve",
            "dataset_id": dataset.id,
            "expected_item_revision": 2,
        },
    )

    assert response.status_code == 409


def test_admin_moderation_rejects_draft_item(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="admin-core-draft-transition",
        display_name="Admin Core Draft Transition",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.flush()
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        prompt_text="Draft statement.",
        status=ItemStatus.DRAFT,
    )
    db.add(item)
    db.commit()

    response = client.post(
        f"{settings.API_V1_STR}/admin/items/{item.id}/approve",
        headers=superuser_token_headers,
        json={
            "action": "approve",
            "dataset_id": dataset.id,
            "expected_item_revision": item.revision,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only submitted items can be moderated"


def test_admin_agreement_and_inter_user_metrics(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    first_user = User(
        email="metrics-first@example.com",
        hashed_password="x",
        reviewer_kind=ReviewerKind.human,
    )
    second_user = User(
        email="metrics-second@example.com",
        hashed_password="x",
        reviewer_kind=ReviewerKind.human,
    )
    dataset = Dataset(
        name="admin-metrics-agreement",
        display_name="Admin Metrics Agreement",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(first_user)
    db.add(second_user)
    db.add(dataset)
    db.flush()
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=first_user.id,
        prompt_text="Statement.",
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.flush()
    task = ReviewTask(dataset_id=dataset.id, item_a_id=item.id)
    db.add(task)
    db.flush()
    db.add(
        FactDecompReview(
            task_id=task.id,
            user_id=first_user.id,
            item_revision=item.revision,
            reviewer_kind=ReviewerKind.human,
            ratings={"verdict": "keep"},
        )
    )
    db.add(
        FactDecompReview(
            task_id=task.id,
            user_id=second_user.id,
            item_revision=item.revision,
            reviewer_kind=ReviewerKind.human,
            ratings={"verdict": "keep"},
        )
    )
    db.commit()

    agreement = client.get(
        f"{settings.API_V1_STR}/admin/metrics/agreement?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    )
    assert agreement.status_code == 200
    assert agreement.json()[0]["dimension"] == "verdict"
    assert agreement.json()[0]["alpha"] == 1.0

    inter_user = client.get(
        f"{settings.API_V1_STR}/admin/metrics/inter-user-agreement?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    )
    assert inter_user.status_code == 200
    assert inter_user.json()[0]["dimension"] == "verdict"
    assert inter_user.json()[0]["kappa"] == 1.0
    assert inter_user.json()[0]["overlap"] == 1

    invalid_overlap = client.get(
        f"{settings.API_V1_STR}/admin/metrics/inter-user-agreement"
        f"?dataset_id={dataset.id}&min_overlap=0",
        headers=superuser_token_headers,
    )
    assert invalid_overlap.status_code == 422


def test_admin_user_metrics_count_dataset_activity(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    user = User(
        email="metrics-user@example.com",
        hashed_password="x",
        reviewer_kind=ReviewerKind.human,
    )
    dataset = Dataset(
        name="admin-metrics-user",
        display_name="Admin Metrics User",
        eval_type=EvalType.RETRIEVAL,
    )
    other_dataset = Dataset(
        name="admin-metrics-user-other",
        display_name="Admin Metrics User Other",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(user)
    db.add(dataset)
    db.add(other_dataset)
    db.flush()
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=user.id,
        prompt_text="Question?",
        status=ItemStatus.ACTIVE,
    )
    other_item = EvalItem(
        dataset_id=other_dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=user.id,
        prompt_text="Other question?",
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.add(other_item)
    db.flush()
    task = ReviewTask(dataset_id=dataset.id, item_a_id=item.id)
    db.add(task)
    db.flush()
    db.add(
        FactDecompReview(
            task_id=task.id,
            user_id=user.id,
            item_revision=item.revision,
            reviewer_kind=ReviewerKind.human,
            ratings={"verdict": "keep"},
        )
    )
    db.commit()

    metrics = client.get(
        f"{settings.API_V1_STR}/admin/metrics/users?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    )
    assert metrics.status_code == 200
    row = next(
        row for row in metrics.json() if row["email"] == "metrics-user@example.com"
    )
    assert row["authored_items"] == 1
    assert row["fact_decomp_reviews"] == 1


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
                "fact_uuid": "fact-b",
                "fact_text": "The distractor answer choice should not be listed.",
                "polarity": "SHOULD_NOT_LIST",
                "position": 1,
                "provenance_spans": [],
            },
            {
                "fact_uuid": "fact-a",
                "fact_text": "Aspirin reduced fever.",
                "polarity": "SHOULD_LIST",
                "position": 0,
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
        json=payload,
    )
    assert draft.status_code == 200
    draft_data = draft.json()
    created = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/submit",
        headers=normal_user_token_headers,
        json={
            **payload,
            "item_id": draft_data["id"],
            "expected_item_revision": draft_data["item_revision"],
        },
    )
    assert created.status_code == 200
    data = created.json()
    assert data["status"] == "SUBMITTED"
    assert [fact["fact_uuid"] for fact in data["facts"]] == ["fact-a", "fact-b"]
    assert data["validation"]["ok"] is True

    item = db.get(EvalItem, data["id"])
    assert item is not None
    assert item.document_id == document.id
    assert item.evidence_spans[0]["fact_uuid"] == "fact-a"
    facts = db.exec(
        select(EvalFact).where(EvalFact.item_id == item.id).order_by(EvalFact.position)
    ).all()
    assert [fact.fact_uuid for fact in facts] == ["fact-a", "fact-b"]
    assert facts[0].polarity == FactPolarity.SHOULD_LIST


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
                "fact_uuid": "fact-a",
                "fact_text": "Aspirin reduced fever.",
                "polarity": "SHOULD_LIST",
                "position": 0,
                "provenance_spans": [],
            }
        ],
    }
    draft = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json=payload,
    )
    assert draft.status_code == 200
    rejected = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/submit",
        headers=normal_user_token_headers,
        json={
            **payload,
            "item_id": draft.json()["id"],
            "expected_item_revision": draft.json()["item_revision"],
        },
    )

    assert rejected.status_code == 400
    assert any("SHOULD_NOT_LIST" in row["message"] for row in rejected.json()["detail"])


def test_fact_decomp_submit_rejects_duplicate_fact_uuid(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="api-fact-duplicate-uuid",
        display_name="API Fact Duplicate UUID",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.commit()

    payload = {
        "dataset_id": dataset.id,
        "source_text": "Aspirin reduced fever.",
        "facts": [
            {
                "fact_uuid": "fact-a",
                "fact_text": "Aspirin reduced fever.",
                "polarity": "SHOULD_LIST",
                "position": 0,
                "provenance_spans": [],
            },
            {
                "fact_uuid": "fact-a",
                "fact_text": "Noise fact.",
                "polarity": "SHOULD_NOT_LIST",
                "position": 1,
                "provenance_spans": [],
            },
        ],
    }
    draft = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/draft",
        headers=normal_user_token_headers,
        json=payload,
    )
    assert draft.status_code == 200
    rejected = client.post(
        f"{settings.API_V1_STR}/create/fact-decomp/submit",
        headers=normal_user_token_headers,
        json={
            **payload,
            "item_id": draft.json()["id"],
            "expected_item_revision": draft.json()["item_revision"],
        },
    )

    assert rejected.status_code == 400
    assert any(
        "UUIDs must be unique" in row["message"] for row in rejected.json()["detail"]
    )


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
            "dataset_id": dataset.id,
            "source_text": "Should not persist.",
            "facts": [
                {
                    "fact_uuid": "fact-a",
                    "fact_text": "Should not persist.",
                    "polarity": "SHOULD_LIST",
                    "position": 0,
                    "provenance_spans": [],
                },
                {
                    "fact_uuid": "fact-b",
                    "fact_text": "Noise should not persist.",
                    "polarity": "SHOULD_NOT_LIST",
                    "position": 1,
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
            "dataset_id": dataset.id,
            "document_id": document.id,
            "source_text": "Current provenance text.",
            "facts": [
                {
                    "fact_uuid": "fact-a",
                    "fact_text": "Current provenance text.",
                    "polarity": "SHOULD_LIST",
                    "position": 0,
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
                    "fact_uuid": "fact-b",
                    "fact_text": "Noise fact.",
                    "polarity": "SHOULD_NOT_LIST",
                    "position": 1,
                    "provenance_spans": [],
                },
            ],
        },
    )

    assert rejected.status_code == 400
    assert any("does not match" in row["message"] for row in rejected.json()["detail"])
