from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    Dataset,
    EvalFact,
    EvalItem,
    EvalType,
    FactDecompReview,
    FactPolarity,
    ItemSource,
    ItemStatus,
    ReviewerKind,
    ReviewTask,
    User,
)
from app.services.documents import create_document_with_chunks


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
    assert body["facts"][0] == {
        "fact_text": "Alpha is true.",
        "polarity": "SHOULD_LIST",
        "position": 1,
    }
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
        fact_text="Alpha is true.",
        polarity=FactPolarity.SHOULD_LIST,
        position=0,
    )
    should_not_fact = EvalFact(
        item_id=item.id,
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
            "fact_calls": ["SHOULD_LIST", "SHOULD_NOT_LIST"],
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
    assert review.ratings["fact_agreement"] == ["agree", "agree"]
    db.refresh(task)
    assert task.labels_count == 2

    duplicate = client.post(
        f"{settings.API_V1_STR}/review/fact-decomp/{task.id}",
        headers=normal_user_token_headers,
        json={
            "fact_calls": ["SHOULD_LIST", "SHOULD_NOT_LIST"],
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
            fact_text="Gamma is true.",
            polarity=FactPolarity.SHOULD_LIST,
            position=0,
        )
    )
    db.add(
        EvalFact(
            item_id=invalid_item.id,
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
            "fact_calls": ["SHOULD_LIST"],
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
            "fact_calls": [],
            "values": {
                "independently_verifiable": "pass",
                "noise_removed": "pass",
                "deduplicated_ordered": "pass",
            },
            "item_revision": invalid_item.revision,
        },
    )
    assert invalid.status_code == 400
    assert "exactly one call per fact" in invalid.json()["detail"][0]["message"]

    self_review = client.post(
        f"{settings.API_V1_STR}/review/fact-decomp/{self_task.id}",
        headers=normal_user_token_headers,
        json={
            "fact_calls": [],
            "values": {
                "independently_verifiable": "pass",
                "noise_removed": "pass",
                "deduplicated_ordered": "pass",
            },
            "item_revision": self_item.revision,
        },
    )
    assert self_review.status_code == 403
