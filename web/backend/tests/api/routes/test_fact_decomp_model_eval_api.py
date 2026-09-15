from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.middleware.model_eval_body_limit import MAX_MODEL_EVAL_REQUEST_BYTES
from app.models import (
    Dataset,
    EvalFact,
    EvalItem,
    EvalType,
    FactDecompReview,
    ItemSource,
    ItemStatus,
    ReviewTask,
    User,
)


def _imported_task(
    db: Session, *, claims: int = 2, query: str | None = "What is true?"
) -> tuple[EvalItem, ReviewTask]:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    author = User(email=f"model-eval-author-{claims}@example.com", hashed_password="x")
    dataset = Dataset(
        name=f"model-eval-{claims}",
        display_name="Model Eval",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add_all([author, dataset])
    db.flush()
    reviewer.fact_decomp_dataset_id = dataset.id
    db.add(reviewer)
    annotations = [
        {
            "claim": "Alpha is true.",
            "label": "vital",
            "spans": [{"start": 0, "end": 14, "text": "Alpha is true."}],
        },
        {
            "claim": "Beta is false.",
            "label": "semi-important",
            "spans": [{"start": 15, "end": 29, "text": "Beta is false."}],
        },
    ][:claims]
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.LLM,
        author_user_id=author.id,
        prompt_text="Alpha is true. Beta is false.",
        lazy_query=query,
        item_metadata={
            "schema_version": 2,
            "review_mode": "MODEL_LABEL_CORRECTION",
            "case_id": f"case-{claims}",
            "arm_id": "arm-1",
            "canonical_row_sha256": "b" * 64,
            "generator": {
                "model_id": "secret-model",
                "prompt_text": "Exact instructions.\r\nUnicode 😀\n",
                "pydantic_ai_version": "2.33.0",
                "generation": {},
            },
            "ordered_claim_annotations": annotations,
        },
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.flush()
    for position, annotation in enumerate(annotations):
        db.add(
            EvalFact(
                item_id=item.id,
                fact_text=annotation["claim"],
                position=position,
                polarity="SHOULD_LIST",
            )
        )
    task = ReviewTask(dataset_id=dataset.id, item_a_id=item.id)
    db.add(task)
    db.commit()
    return item, task


def _valid_submission() -> dict:
    return {
        "item_revision": 1,
        "rubric_id": "importance-v1",
        "claim_reviews": [
            {"position": 0, "label": "unimportant", "issue": None},
            {"position": 1, "label": None, "issue": "The subject is ambiguous."},
        ],
        "human_claims": [
            {
                "claim_text": "The response mentions alpha.",
                "label": "semi-important",
                "response_spans": [{"start": 0, "end": 5, "text": "Alpha"}],
            }
        ],
        "coverage_checked": True,
    }


def test_payload_exposes_guide_and_redacts_generator(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    item, task = _imported_task(db)
    response = client.get(
        f"{settings.API_V1_STR}/review/fact-decomp/{task.id}",
        headers=normal_user_token_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["guide"]["rubric_id"] == "importance-v1"
    assert [label["value"] for label in payload["guide"]["labels"]] == [
        "vital",
        "semi-important",
        "unimportant",
    ]
    assert [claim["proposed_label"] for claim in payload["claims"]] == [
        "vital",
        "semi-important",
    ]
    assert payload["assistant_response"] == item.prompt_text
    assert "secret-model" not in response.text
    assert "Exact instructions" not in response.text


def test_valid_labeled_flagged_review_survives_reload_without_changing_prediction(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    item, task = _imported_task(db)
    original_metadata = deepcopy(item.item_metadata)
    url = f"{settings.API_V1_STR}/review/fact-decomp/{task.id}"
    response = client.post(
        f"{url}/model-eval", headers=normal_user_token_headers, json=_valid_submission()
    )
    assert response.status_code == 200
    review = db.exec(
        select(FactDecompReview).where(FactDecompReview.task_id == task.id)
    ).one()
    assert review.ratings == {
        "schema_version": 2,
        "review_mode": "MODEL_LABEL_CORRECTION",
        **{
            key: value
            for key, value in _valid_submission().items()
            if key != "item_revision"
        },
    }
    readback = client.get(url, headers=normal_user_token_headers)
    assert readback.status_code == 200
    assert readback.json()["existing_review"] == {
        key: value
        for key, value in _valid_submission().items()
        if key != "item_revision"
    }
    db.refresh(item)
    assert item.item_metadata == original_metadata


@pytest.mark.parametrize(
    ("update", "status"),
    [
        ({"claim_reviews": [{"position": 0, "label": "vital", "issue": None}]}, 400),
        (
            {
                "claim_reviews": [
                    {"position": 1, "label": "vital", "issue": None},
                    {"position": 1, "label": "vital", "issue": None},
                ]
            },
            400,
        ),
        (
            {
                "claim_reviews": [
                    {"position": 0, "label": None, "issue": None},
                    {"position": 1, "label": "vital", "issue": None},
                ]
            },
            422,
        ),
        (
            {
                "claim_reviews": [
                    {"position": 0, "label": None, "issue": " "},
                    {"position": 1, "label": "vital", "issue": None},
                ]
            },
            422,
        ),
        (
            {
                "claim_reviews": [
                    {"position": 0, "label": None, "issue": "x" * 501},
                    {"position": 1, "label": "vital", "issue": None},
                ]
            },
            422,
        ),
        (
            {
                "claim_reviews": [
                    {"position": True, "label": "vital", "issue": None},
                    {"position": 1, "label": "vital", "issue": None},
                ]
            },
            422,
        ),
        (
            {
                "claim_reviews": [
                    {"position": 1, "label": "vital", "issue": None},
                    {"position": 0, "label": "vital", "issue": None},
                ]
            },
            400,
        ),
        (
            {
                "claim_reviews": [
                    {"position": 0, "label": "vital", "issue": None},
                    {"position": 2, "label": "vital", "issue": None},
                ]
            },
            400,
        ),
        (
            {
                "claim_reviews": [
                    {"position": 0, "label": "invalid", "issue": None},
                    {"position": 1, "label": "vital", "issue": None},
                ]
            },
            422,
        ),
        ({"coverage_checked": False}, 422),
        ({"coverage_checked": 1}, 422),
        ({"coverage_checked": "true"}, 422),
        ({"rubric_id": "importance-old"}, 409),
        ({"item_revision": 2}, 409),
    ],
)
def test_invalid_review_is_rejected_without_mutation(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
    update: dict,
    status: int,
) -> None:
    _item, task = _imported_task(db)
    body = {**_valid_submission(), **update}
    response = client.post(
        f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval",
        headers=normal_user_token_headers,
        json=body,
    )
    assert response.status_code == status
    assert (
        db.exec(
            select(FactDecompReview).where(FactDecompReview.task_id == task.id)
        ).first()
        is None
    )
    db.refresh(task)
    assert task.labels_count == 0


def test_invalid_added_span_and_duplicate_submission_are_rejected(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    _item, task = _imported_task(db)
    body = _valid_submission()
    body["human_claims"][0]["response_spans"][0]["text"] = "Wrong"
    url = f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval"
    assert (
        client.post(url, headers=normal_user_token_headers, json=body).status_code
        == 400
    )
    assert (
        client.post(
            url, headers=normal_user_token_headers, json=_valid_submission()
        ).status_code
        == 200
    )
    db.refresh(task)
    assert task.labels_count == 1
    assert (
        client.post(
            url, headers=normal_user_token_headers, json=_valid_submission()
        ).status_code
        == 409
    )
    db.refresh(task)
    assert task.labels_count == 1


def test_missing_coverage_and_oversized_body_create_no_review(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    _item, task = _imported_task(db)
    url = f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval"
    missing = _valid_submission()
    del missing["coverage_checked"]
    assert (
        client.post(url, headers=normal_user_token_headers, json=missing).status_code
        == 422
    )
    response = client.post(
        url,
        headers={**normal_user_token_headers, "content-type": "application/json"},
        content=b"x" * (MAX_MODEL_EVAL_REQUEST_BYTES + 1),
    )
    assert response.status_code == 413
    assert (
        db.exec(
            select(FactDecompReview).where(FactDecompReview.task_id == task.id)
        ).first()
        is None
    )
    db.refresh(task)
    assert task.labels_count == 0


def test_bounded_human_claim_fields_are_rejected_without_mutation(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    _item, task = _imported_task(db)
    url = f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval"
    valid_human_claim = {
        "claim_text": "Alpha",
        "label": "vital",
        "response_spans": [{"start": 0, "end": 5, "text": "Alpha"}],
    }
    bodies = [
        {**_valid_submission(), "human_claims": [valid_human_claim] * 10_001},
        {
            **_valid_submission(),
            "human_claims": [{**valid_human_claim, "claim_text": "x" * 20_001}],
        },
        {
            **_valid_submission(),
            "human_claims": [
                {
                    **valid_human_claim,
                    "response_spans": valid_human_claim["response_spans"] * 101,
                }
            ],
        },
    ]

    for body in bodies:
        response = client.post(url, headers=normal_user_token_headers, json=body)
        assert response.status_code == 422

    assert (
        db.exec(
            select(FactDecompReview).where(FactDecompReview.task_id == task.id)
        ).first()
        is None
    )
    db.refresh(task)
    assert task.labels_count == 0


def test_zero_claim_review_requires_coverage_and_saves(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    _item, task = _imported_task(db, claims=0, query=None)
    body = {
        "item_revision": 1,
        "rubric_id": "importance-v1",
        "claim_reviews": [],
        "human_claims": [],
        "coverage_checked": True,
    }
    url = f"{settings.API_V1_STR}/review/fact-decomp/{task.id}"
    payload = client.get(url, headers=normal_user_token_headers)
    assert payload.status_code == 200
    assert payload.json()["user_prompt"] is None

    response = client.post(
        f"{url}/model-eval",
        headers=normal_user_token_headers,
        json=body,
    )
    assert response.status_code == 200
    assert (
        client.get(
            url,
            headers=normal_user_token_headers,
        ).json()["existing_review"]["coverage_checked"]
        is True
    )
