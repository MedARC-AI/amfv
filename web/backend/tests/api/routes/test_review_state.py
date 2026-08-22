from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session, col, func, select

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
    ReviewTask,
    User,
)
from app.services.agreement import dataset_judgments_for_all
from app.services.assignment import (
    available_item_audit_targets,
    available_relevance_candidates,
    claim_assignment,
    complete_assignment,
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
    assert accepted_review.checks is None
    assert item_accepted_for_relevance(db, accepted_item)
    assert not item_accepted_for_relevance(db, rejected_item)
    assert [candidate.id for candidate in available_relevance_candidates(db, labeler, dataset)] == [
        accepted_candidate.id
    ]


def test_retrieval_get_is_read_only_and_post_claim_is_idempotent(
    client: TestClient,
    db: Session,
) -> None:
    reviewer, reviewer_headers = _reviewer(client, db, "read-only-claim")
    author = User(email=f"read-only-author-{uuid4()}@example.com", hashed_password="x")
    dataset = Dataset(
        name=f"read-only-dataset-{uuid4()}",
        display_name="Read-only retrieval dataset",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add_all([author, dataset])
    db.flush()
    item = _retrieval_item(db, dataset, author, "Only POST may reserve me")
    db.commit()

    before = db.exec(select(func.count(col(Assignment.id)))).one()
    read = client.get(
        f"{settings.API_V1_STR}/review/next?eval_type=RETRIEVAL",
        headers=reviewer_headers,
    )
    assert read.status_code == 404
    assert db.exec(select(func.count(col(Assignment.id)))).one() == before
    db.refresh(reviewer)
    assert reviewer.retrieval_dataset_id is None

    first_claim = client.post(
        f"{settings.API_V1_STR}/review/claim",
        headers=reviewer_headers,
        json={
            "eval_type": "RETRIEVAL",
            "mode": "ITEM_AUDIT",
            "dataset_id": dataset.id,
        },
    )
    second_claim = client.post(
        f"{settings.API_V1_STR}/review/claim",
        headers=reviewer_headers,
        json={
            "eval_type": "RETRIEVAL",
            "mode": "ITEM_AUDIT",
            "dataset_id": dataset.id,
        },
    )
    assert first_claim.status_code == 200
    assert second_claim.status_code == 200
    assert first_claim.json()["item_id"] == item.id
    assert second_claim.json()["assignment_id"] == first_claim.json()["assignment_id"]
    assert second_claim.json()["reservation_state"] == "existing"
    assert db.exec(select(func.count(col(Assignment.id)))).one() == before + 1

    read_existing = client.get(
        f"{settings.API_V1_STR}/review/next?eval_type=RETRIEVAL",
        headers=reviewer_headers,
    )
    assert read_existing.status_code == 200
    assert read_existing.json()["assignment_id"] == first_claim.json()["assignment_id"]


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
    assert review.checks is None


def test_home_hides_full_item_slots_and_shows_released_slot(
    client: TestClient,
    db: Session,
) -> None:
    reviewer, reviewer_headers = _reviewer(client, db, "home-item-capacity")
    author = User(email=f"home-item-author-{uuid4()}@example.com", hashed_password="x")
    slot_owner = User(email=f"home-item-owner-{uuid4()}@example.com", hashed_password="x")
    dataset = Dataset(
        name=f"home-item-dataset-{uuid4()}",
        display_name="Home item capacity dataset",
        eval_type=EvalType.RETRIEVAL,
        double_rate=0,
    )
    quiet_fact_dataset = Dataset(
        name=f"home-item-facts-{uuid4()}",
        display_name="Home item empty fact dataset",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add_all([author, slot_owner, dataset, quiet_fact_dataset])
    db.flush()
    assert dataset.id is not None
    assert quiet_fact_dataset.id is not None
    reviewer.retrieval_dataset_id = dataset.id
    reviewer.fact_decomp_dataset_id = quiet_fact_dataset.id
    db.add(reviewer)

    completed_item = _retrieval_item(db, dataset, author, "Completed slot stays occupied")
    releasable_item = _retrieval_item(db, dataset, author, "Released slot becomes visible")
    completed_assignment = _assignment(db, dataset, slot_owner, completed_item, slot=0)
    releasable_assignment = _assignment(db, dataset, slot_owner, releasable_item, slot=0)
    complete_assignment(db, completed_assignment)
    db.commit()

    assert available_item_audit_targets(db, reviewer, dataset) == []
    full = client.get(
        f"{settings.API_V1_STR}/home/summary",
        headers=reviewer_headers,
    )
    assert full.status_code == 200
    assert full.json()["outstanding_counts"]["retrieval_reviews"] == 0
    assert full.json()["recommended_task"] is None
    no_work = client.post(
        f"{settings.API_V1_STR}/review/claim",
        headers=reviewer_headers,
        json={
            "eval_type": "RETRIEVAL",
            "mode": "ITEM_AUDIT",
            "dataset_id": dataset.id,
        },
    )
    assert no_work.status_code == 404

    releasable_assignment.released_at = datetime.now(timezone.utc)
    releasable_assignment.release_reason = "Return this label slot"
    db.add(releasable_assignment)
    db.commit()

    assert [item.id for item in available_item_audit_targets(db, reviewer, dataset)] == [
        releasable_item.id
    ]
    released = client.get(
        f"{settings.API_V1_STR}/home/summary",
        headers=reviewer_headers,
    )
    assert released.status_code == 200
    assert released.json()["outstanding_counts"]["retrieval_reviews"] == 1
    assert released.json()["recommended_task"] == {
        "kind": "retrieval_audit",
        "eval_type": "RETRIEVAL",
        "dataset_id": dataset.id,
        "title": "Review retrieval item",
        "reason": "Next available retrieval review",
        "assignment_id": None,
        "task_id": None,
    }
    claim = client.post(
        f"{settings.API_V1_STR}/review/claim",
        headers=reviewer_headers,
        json={
            "eval_type": "RETRIEVAL",
            "mode": "ITEM_AUDIT",
            "dataset_id": dataset.id,
        },
    )
    assert claim.status_code == 200
    assert claim.json()["item_id"] == releasable_item.id


def test_home_hides_full_relevance_slots_and_shows_released_slot(
    client: TestClient,
    db: Session,
) -> None:
    reviewer, reviewer_headers = _reviewer(client, db, "home-relevance-capacity")
    author = User(email=f"home-relevance-author-{uuid4()}@example.com", hashed_password="x")
    slot_owner = User(
        email=f"home-relevance-owner-{uuid4()}@example.com",
        hashed_password="x",
    )
    dataset = Dataset(
        name=f"home-relevance-dataset-{uuid4()}",
        display_name="Home relevance capacity dataset",
        eval_type=EvalType.RETRIEVAL,
        double_rate=0,
    )
    quiet_fact_dataset = Dataset(
        name=f"home-relevance-facts-{uuid4()}",
        display_name="Home relevance empty fact dataset",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add_all([author, slot_owner, dataset, quiet_fact_dataset])
    db.flush()
    assert dataset.id is not None
    assert quiet_fact_dataset.id is not None
    reviewer.retrieval_dataset_id = dataset.id
    reviewer.fact_decomp_dataset_id = quiet_fact_dataset.id
    db.add(reviewer)
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Home relevance source",
        content="The relevant evidence is in this document.",
        external_id=f"home-relevance-document-{uuid4()}",
    )
    chunk = db.exec(select(Chunk).where(col(Chunk.document_id) == document.id)).one()
    item = _retrieval_item(db, dataset, author, "Canonical relevance item")
    audit_assignment = _assignment(db, dataset, slot_owner, item, slot=0)
    complete_assignment(db, audit_assignment)
    db.add(
        RetrievalQAReview(
            assignment_id=audit_assignment.id,
            item_id=item.id,
            user_id=slot_owner.id,
            question_validity=4,
            evidence_quality=4,
            answer_correctness=4,
            answer_faithfulness=4,
            verdict=ItemVerdict.ACCEPT,
        )
    )
    candidate = PooledCandidate(
        dataset_id=dataset.id,
        item_id=item.id,
        chunk_id=chunk.id,
    )
    db.add(candidate)
    db.flush()
    relevance_assignment = Assignment(
        dataset_id=dataset.id,
        user_id=slot_owner.id,
        mode=AssignmentMode.RELEVANCE,
        target_id=candidate.id,
        kind=AssignmentKind.REGULAR,
        slot=0,
    )
    db.add(relevance_assignment)
    db.commit()

    assert available_item_audit_targets(db, reviewer, dataset) == []
    assert available_relevance_candidates(db, reviewer, dataset) == []
    full = client.get(
        f"{settings.API_V1_STR}/home/summary",
        headers=reviewer_headers,
    )
    assert full.status_code == 200
    assert full.json()["outstanding_counts"]["retrieval_reviews"] == 0
    assert full.json()["outstanding_counts"]["relevance_reviews"] == 0
    assert full.json()["recommended_task"] is None

    relevance_assignment.released_at = datetime.now(timezone.utc)
    relevance_assignment.release_reason = "Return relevance slot"
    db.add(relevance_assignment)
    db.commit()

    assert [candidate_row.id for candidate_row in available_relevance_candidates(db, reviewer, dataset)] == [
        candidate.id
    ]
    released = client.get(
        f"{settings.API_V1_STR}/home/summary",
        headers=reviewer_headers,
    )
    assert released.status_code == 200
    assert released.json()["outstanding_counts"]["retrieval_reviews"] == 0
    assert released.json()["outstanding_counts"]["relevance_reviews"] == 1
    assert released.json()["recommended_task"] == {
        "kind": "relevance",
        "eval_type": "RETRIEVAL",
        "dataset_id": dataset.id,
        "title": "Review retrieved passage relevance",
        "reason": "Next available relevance review",
        "assignment_id": None,
        "task_id": None,
    }
    claim = client.post(
        f"{settings.API_V1_STR}/review/claim",
        headers=reviewer_headers,
        json={
            "eval_type": "RETRIEVAL",
            "mode": "RELEVANCE",
            "dataset_id": dataset.id,
        },
    )
    assert claim.status_code == 200
    assert claim.json()["kind"] == "relevance"
    assert claim.json()["item_id"] == item.id


def test_release_endpoint_authorizes_then_reclaims_the_same_slot(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    owner, owner_headers = _reviewer(client, db, "release-owner")
    _other_user, other_headers = _reviewer(client, db, "release-other")
    author = User(email=f"release-author-{uuid4()}@example.com", hashed_password="x")
    dataset = Dataset(
        name=f"release-dataset-{uuid4()}",
        display_name="Release dataset",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add_all([author, dataset])
    db.flush()
    item = _retrieval_item(db, dataset, author, "Release me")
    assignment = _assignment(db, dataset, owner, item, slot=0)
    db.commit()

    forbidden = client.post(
        f"{settings.API_V1_STR}/review/assignments/{assignment.id}/release",
        headers=other_headers,
        json={"reason": "Not my assignment"},
    )
    assert forbidden.status_code == 403

    released = client.post(
        f"{settings.API_V1_STR}/review/assignments/{assignment.id}/release",
        headers=owner_headers,
        json={"reason": "Need to switch tasks"},
    )
    assert released.status_code == 200
    db.refresh(assignment)
    assert assignment.released_at is not None
    assert assignment.release_reason == "Need to switch tasks"

    released_read = client.get(
        f"{settings.API_V1_STR}/review/retrieval/{assignment.id}",
        headers=owner_headers,
    )
    assert released_read.status_code == 409

    db.refresh(owner)
    reclaimed, created = claim_assignment(
        db,
        owner,
        AssignmentMode.ITEM_AUDIT,
        dataset_id=dataset.id,
    )
    assert created
    assert reclaimed is not None
    assert reclaimed.id != assignment.id
    assert reclaimed.slot == 0
    db.commit()

    complete_assignment(db, reclaimed)
    db.commit()
    completed_release = client.post(
        f"{settings.API_V1_STR}/review/assignments/{reclaimed.id}/release",
        headers=owner_headers,
        json={"reason": "Too late"},
    )
    assert completed_release.status_code == 409

    admin_item = _retrieval_item(db, dataset, author, "Admin release")
    admin_assignment = _assignment(db, dataset, owner, admin_item, slot=0)
    db.commit()
    admin_release = client.post(
        f"{settings.API_V1_STR}/review/assignments/{admin_assignment.id}/release",
        headers=superuser_token_headers,
        json={"reason": "Administrator returned the task"},
    )
    assert admin_release.status_code == 200


def test_claim_keeps_dataset_preferences_separate_by_evaluation_type(
    client: TestClient,
    db: Session,
) -> None:
    reviewer, reviewer_headers = _reviewer(client, db, "preference-reviewer")
    author = User(email=f"preference-author-{uuid4()}@example.com", hashed_password="x")
    first_retrieval = Dataset(
        name=f"preference-a-{uuid4()}",
        display_name="A first retrieval dataset",
        eval_type=EvalType.RETRIEVAL,
    )
    selected_retrieval = Dataset(
        name=f"preference-b-{uuid4()}",
        display_name="B selected retrieval dataset",
        eval_type=EvalType.RETRIEVAL,
    )
    fact_dataset = Dataset(
        name=f"preference-facts-{uuid4()}",
        display_name="Fact preference dataset",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add_all([author, first_retrieval, selected_retrieval, fact_dataset])
    db.flush()
    selected_item = _retrieval_item(db, selected_retrieval, author, "Selected retrieval")
    fact_item = EvalItem(
        dataset_id=fact_dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Fact-decomposition review",
        status=ItemStatus.ACTIVE,
    )
    db.add(fact_item)
    db.flush()
    task = ReviewTask(dataset_id=fact_dataset.id, item_a_id=fact_item.id)
    db.add(task)
    db.commit()

    retrieval_claim = client.post(
        f"{settings.API_V1_STR}/review/claim",
        headers=reviewer_headers,
        json={
            "eval_type": "RETRIEVAL",
            "mode": "ITEM_AUDIT",
            "dataset_id": selected_retrieval.id,
        },
    )
    fact_claim = client.post(
        f"{settings.API_V1_STR}/review/claim",
        headers=reviewer_headers,
        json={"eval_type": "FACT_DECOMP", "dataset_id": fact_dataset.id},
    )
    assert retrieval_claim.status_code == 200
    assert fact_claim.status_code == 200
    assert retrieval_claim.json()["item_id"] == selected_item.id
    assert fact_claim.json()["task_id"] == task.id

    db.refresh(reviewer)
    assert reviewer.retrieval_dataset_id == selected_retrieval.id
    assert reviewer.fact_decomp_dataset_id == fact_dataset.id
    assert first_retrieval.id != reviewer.retrieval_dataset_id

    retrieval_read = client.get(
        f"{settings.API_V1_STR}/review/next?eval_type=RETRIEVAL",
        headers=reviewer_headers,
    )
    fact_read = client.get(
        f"{settings.API_V1_STR}/review/next?eval_type=FACT_DECOMP",
        headers=reviewer_headers,
    )
    assert retrieval_read.status_code == 200
    assert fact_read.status_code == 200
    assert retrieval_read.json()["dataset_id"] == selected_retrieval.id
    assert fact_read.json()["dataset_id"] == fact_dataset.id


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
                checks={
                    "question_validity": 1,
                    "evidence_quality": 1,
                    "answer_correctness": 1,
                    "answer_faithfulness": 1,
                    "accept_as_gold": False,
                },
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
                checks={
                    "question_validity": 1,
                    "evidence_quality": 1,
                    "answer_correctness": 1,
                    "answer_faithfulness": 1,
                    "accept_as_gold": False,
                },
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
    assert {
        metric["dimension"] for metric in metrics.json()
    } >= {
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


def test_task_generation_only_materializes_fact_decomposition_work(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    author = User(email=f"task-author-{uuid4()}@example.com", hashed_password="x")
    retrieval_dataset = Dataset(
        name=f"task-retrieval-{uuid4()}",
        display_name="Task retrieval",
        eval_type=EvalType.RETRIEVAL,
    )
    fact_dataset = Dataset(
        name=f"task-fact-{uuid4()}",
        display_name="Task fact",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add_all([author, retrieval_dataset, fact_dataset])
    db.flush()
    _retrieval_item(db, retrieval_dataset, author, "No retrieval task")
    fact_item = EvalItem(
        dataset_id=fact_dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text="Create one fact task",
        status=ItemStatus.ACTIVE,
    )
    db.add(fact_item)
    db.commit()

    retrieval_generation = client.post(
        f"{settings.API_V1_STR}/admin/datasets/{retrieval_dataset.id}/generate-tasks",
        headers=superuser_token_headers,
    )
    fact_generation = client.post(
        f"{settings.API_V1_STR}/admin/datasets/{fact_dataset.id}/generate-tasks",
        headers=superuser_token_headers,
    )
    assert retrieval_generation.json()["created"] == 0
    assert fact_generation.json()["created"] == 1
    assert db.exec(select(ReviewTask).where(col(ReviewTask.item_a_id) == fact_item.id)).one()


def _reviewer(client: TestClient, db: Session, prefix: str) -> tuple[User, dict[str, str]]:
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
