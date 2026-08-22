from datetime import datetime, timezone
from uuid import uuid4

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
    PooledCandidate,
    RelevanceJudgment,
    RetrievalQAReview,
    ReviewTask,
    User,
)
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
