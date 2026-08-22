from fastapi.testclient import TestClient
from sqlmodel import Session, select

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
    PooledCandidate,
    RelevanceJudgment,
    User,
)
from app.services.documents import create_document_with_chunks


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
