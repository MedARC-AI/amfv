"""Cross-dataset fact queue and duplicate review recovery contracts."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, func, select

from app import crud
from app.api.routes import review_fact_decomp
from app.core.config import settings
from app.core.db import engine
from app.models import (
    Assignment,
    Dataset,
    EvalItem,
    EvalType,
    FactDecompReview,
    ItemSource,
    ItemStatus,
    ReviewTask,
    User,
)
from tests.api.routes.test_fact_decomp_model_eval_api import (
    _imported_task,
    _valid_submission,
)


def _dataset(db: Session, name: str, *, active: bool = True) -> Dataset:
    dataset = Dataset(
        name=f"queue-{uuid4()}",
        display_name=name,
        eval_type=EvalType.FACT_DECOMP,
        is_active=active,
    )
    db.add(dataset)
    db.flush()
    return dataset


def _task(
    db: Session,
    dataset: Dataset,
    author: User,
    *,
    status: ItemStatus = ItemStatus.ACTIVE,
    active: bool = True,
    item_active: bool = True,
    model_metadata: dict | None = None,
) -> ReviewTask:
    assert dataset.id is not None
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.LLM if model_metadata else ItemSource.HUMAN,
        author_user_id=author.id,
        prompt_text=f"Queue item {uuid4()}",
        status=status,
        is_active=item_active,
        item_metadata=model_metadata,
    )
    db.add(item)
    db.flush()
    assert item.id is not None
    task = ReviewTask(dataset_id=dataset.id, item_a_id=item.id, is_active=active)
    db.add(task)
    db.flush()
    return task


def _reviewer(db: Session) -> User:
    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    return reviewer


def _next(client: TestClient, headers: dict[str, str]):
    return client.get(
        f"{settings.API_V1_STR}/review/next?eval_type=FACT_DECOMP", headers=headers
    )


def _home(client: TestClient, headers: dict[str, str]):
    return client.get(f"{settings.API_V1_STR}/home/summary", headers=headers)


def test_fact_queue_preferred_first_then_name_and_id_with_home_consistency(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    reviewer = _reviewer(db)
    author = User(email=f"queue-author-{uuid4()}@example.com", hashed_password="x")
    db.add(author)
    db.flush()
    alphabetical = _dataset(db, "A facts")
    same_name_later = _dataset(db, "A facts")
    preferred = _dataset(db, "Z preferred")
    reviewer.fact_decomp_dataset_id = preferred.id
    db.add(reviewer)
    a_task = _task(db, alphabetical, author)
    same_name_task = _task(db, same_name_later, author)
    preferred_task = _task(db, preferred, author)
    db.commit()
    assert alphabetical.id is not None and same_name_later.id is not None
    assert alphabetical.id < same_name_later.id

    assignment_count = db.exec(select(func.count(col(Assignment.id)))).one()
    first = _next(client, normal_user_token_headers)
    home = _home(client, normal_user_token_headers)
    assert first.status_code == 200 and home.status_code == 200
    assert first.json()["task_id"] == preferred_task.id
    assert first.json()["dataset_id"] == preferred.id
    assert home.json()["outstanding_counts"]["fact_decomp_reviews"] == 3
    assert home.json()["recommended_task"]["dataset_id"] == preferred.id
    db.refresh(reviewer)
    assert reviewer.fact_decomp_dataset_id == preferred.id
    assert db.exec(select(func.count(col(Assignment.id)))).one() == assignment_count
    assert db.exec(select(func.count(col(FactDecompReview.id)))).one() == 0

    # A completed review removes the preferred task; remaining datasets retain name/ID order.
    assert preferred_task.id is not None and reviewer.id is not None
    db.add(
        FactDecompReview(
            task_id=preferred_task.id, user_id=reviewer.id, item_revision=1, ratings={}
        )
    )
    db.commit()
    assert _next(client, normal_user_token_headers).json()["task_id"] == a_task.id
    assert (
        _home(client, normal_user_token_headers).json()["outstanding_counts"][
            "fact_decomp_reviews"
        ]
        == 2
    )
    assert a_task.id is not None
    db.add(
        FactDecompReview(
            task_id=a_task.id, user_id=reviewer.id, item_revision=1, ratings={}
        )
    )
    db.commit()
    assert (
        _next(client, normal_user_token_headers).json()["task_id"] == same_name_task.id
    )

    # Explicit claim stays scoped to its requested dataset, even while another has work.
    explicit = client.post(
        f"{settings.API_V1_STR}/review/claim",
        headers=normal_user_token_headers,
        json={"eval_type": "FACT_DECOMP", "dataset_id": preferred.id},
    )
    assert explicit.status_code == 404
    db.refresh(reviewer)
    assert reviewer.fact_decomp_dataset_id == preferred.id


def test_fact_queue_excludes_inactive_self_reviewed_and_unloadable_tasks(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    reviewer = _reviewer(db)
    author = User(email=f"queue-exclusions-{uuid4()}@example.com", hashed_password="x")
    db.add(author)
    db.flush()
    preferred = _dataset(db, "A preferred")
    fallback = _dataset(db, "B fallback")
    inactive_dataset = _dataset(db, "C inactive", active=False)
    reviewer.fact_decomp_dataset_id = preferred.id
    db.add(reviewer)
    _task(db, preferred, reviewer)
    _task(db, preferred, author, status=ItemStatus.DRAFT)
    _task(db, preferred, author, active=False)
    _task(db, preferred, author, item_active=False)
    _task(db, inactive_dataset, author)
    _task(
        db, preferred, author, model_metadata={"review_mode": "MODEL_LABEL_CORRECTION"}
    )
    reviewed = _task(db, preferred, author)
    assert reviewed.id is not None and reviewer.id is not None
    db.add(
        FactDecompReview(
            task_id=reviewed.id, user_id=reviewer.id, item_revision=1, ratings={}
        )
    )
    eligible = _task(db, fallback, author)
    db.commit()

    result = _next(client, normal_user_token_headers)
    home = _home(client, normal_user_token_headers)
    assert result.status_code == 200
    assert result.json()["task_id"] == eligible.id
    assert home.json()["outstanding_counts"]["fact_decomp_reviews"] == 1
    assert home.json()["recommended_task"]["dataset_id"] == fallback.id
    db.refresh(reviewer)
    assert reviewer.fact_decomp_dataset_id == preferred.id


@pytest.mark.parametrize("mode", ["authored", "model"])
def test_saved_fact_review_is_read_only_and_duplicate_keeps_one_count(
    mode: str,
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    if mode == "model":
        item, task = _imported_task(db)
        url = f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval"
        body = _valid_submission()
    else:
        author = User(email=f"review-author-{uuid4()}@example.com", hashed_password="x")
        db.add(author)
        db.flush()
        dataset = _dataset(db, "Authored")
        task = _task(db, dataset, author)
        item = db.get(EvalItem, task.item_a_id)
        assert item is not None
        db.commit()
        url = f"{settings.API_V1_STR}/review/fact-decomp/{task.id}"
        body = {
            "item_revision": item.revision,
            "fact_calls": [],
            "duplicate_flags": [],
            "looks_good": [],
            "values": {
                "independently_verifiable": "pass",
                "noise_removed": "pass",
                "deduplicated_ordered": "pass",
            },
        }
    read_url = f"{settings.API_V1_STR}/review/fact-decomp/{task.id}"
    before = client.get(read_url, headers=normal_user_token_headers)
    assert before.status_code == 200
    assert before.json()["allowed_actions"] == (
        ["save_model_eval"] if mode == "model" else ["save_review"]
    )
    first = client.post(url, headers=normal_user_token_headers, json=body)
    assert first.status_code == 200, first.text
    duplicate = client.post(url, headers=normal_user_token_headers, json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Review task already submitted"
    after = client.get(read_url, headers=normal_user_token_headers)
    assert after.status_code == 200
    assert after.json()["allowed_actions"] == []
    assert after.json()["existing_review"] is not None
    db.refresh(task)
    assert task.labels_count == 1
    assert (
        len(
            db.exec(
                select(FactDecompReview).where(col(FactDecompReview.task_id) == task.id)
            ).all()
        )
        == 1
    )


@pytest.mark.parametrize("mode", ["authored", "model"])
def test_uniqueness_race_returns_duplicate_contract_without_extra_count(
    mode: str,
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviewer = _reviewer(db)
    if mode == "model":
        item, task = _imported_task(db)
        url = f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval"
        body = _valid_submission()
        original = review_fact_decomp._model_claims
        patch_target = "_model_claims"
    else:
        author = User(email=f"race-author-{uuid4()}@example.com", hashed_password="x")
        db.add(author)
        db.flush()
        dataset = _dataset(db, "Race authored")
        task = _task(db, dataset, author)
        item = db.get(EvalItem, task.item_a_id)
        assert item is not None
        db.commit()
        url = f"{settings.API_V1_STR}/review/fact-decomp/{task.id}"
        body = {
            "item_revision": item.revision,
            "fact_calls": [],
            "duplicate_flags": [],
            "looks_good": [],
            "values": {
                "independently_verifiable": "pass",
                "noise_removed": "pass",
                "deduplicated_ordered": "pass",
            },
        }
        original = review_fact_decomp.validate_fact_decomp_ratings
        patch_target = "validate_fact_decomp_ratings"
    assert task.id is not None and reviewer.id is not None
    injected = False

    def insert_competing_review(*args, **kwargs):
        nonlocal injected
        result = original(*args, **kwargs)
        if not injected:
            injected = True
            with Session(engine) as concurrent:
                competing_task = concurrent.get(ReviewTask, task.id)
                assert competing_task is not None
                competing_task.labels_count += 1
                concurrent.add(competing_task)
                concurrent.add(
                    FactDecompReview(
                        task_id=task.id,
                        user_id=reviewer.id,
                        item_revision=item.revision,
                        ratings={"winner": True},
                    )
                )
                concurrent.commit()
        return result

    monkeypatch.setattr(review_fact_decomp, patch_target, insert_competing_review)
    response = client.post(url, headers=normal_user_token_headers, json=body)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Review task already submitted"
    db.expire_all()
    persisted_task = db.get(ReviewTask, task.id)
    assert persisted_task is not None
    assert persisted_task.labels_count == 1
    reviews = db.exec(
        select(FactDecompReview).where(col(FactDecompReview.task_id) == task.id)
    ).all()
    assert len(reviews) == 1
    assert reviews[0].ratings == {"winner": True}
