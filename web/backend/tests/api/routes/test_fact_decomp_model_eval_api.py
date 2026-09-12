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
    db: Session, *, query: str | None = "What is true?"
) -> tuple[EvalItem, ReviewTask]:
    from app import crud

    reviewer = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert reviewer is not None
    author = User(email="model-eval-author@example.com", hashed_password="x")
    dataset = Dataset(
        name="model-eval-dataset",
        display_name="Model Eval Dataset",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(author)
    db.add(dataset)
    db.flush()
    reviewer.fact_decomp_dataset_id = dataset.id
    db.add(reviewer)
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.LLM,
        author_user_id=author.id,
        prompt_text="Alpha is true. Beta is false.",
        lazy_query=query,
        item_metadata={
            "schema_version": 1,
            "review_mode": "MODEL_LABEL_CORRECTION",
            "case_id": "case-1",
            "arm_id": "arm-1",
            "canonical_row_sha256": "b" * 64,
            "generator": {
                "model_id": "secret-model",
                "prompt_id": "prompt-v1",
                "prompt_hash": "a" * 64,
                "pydantic_ai_version": "2.33.0",
                "generation": {},
            },
            "ordered_claim_annotations": [
                {
                    "claim": "Alpha is true.",
                    "label": "substantive",
                    "spans": [{"start": 0, "end": 14, "text": "Alpha is true."}],
                },
                {
                    "claim": "Beta is false.",
                    "label": "substantive",
                    "spans": [{"start": 15, "end": 29, "text": "Beta is false."}],
                },
            ],
        },
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.flush()
    db.add_all(
        [
            EvalFact(
                item_id=item.id,
                fact_text="Alpha is true.",
                position=0,
                polarity="SHOULD_LIST",
            ),
            EvalFact(
                item_id=item.id,
                fact_text="Beta is false.",
                position=1,
                polarity="SHOULD_LIST",
            ),
        ]
    )
    task = ReviewTask(dataset_id=dataset.id, item_a_id=item.id)
    db.add(task)
    db.commit()
    return item, task


def test_model_eval_payload_redacts_generator_and_exposes_claim_provenance(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    item, task = _imported_task(db)

    response = client.get(
        f"{settings.API_V1_STR}/review/fact-decomp/{task.id}",
        headers=normal_user_token_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["review_mode"] == "MODEL_LABEL_CORRECTION"
    assert payload["allowed_actions"] == ["save_model_eval"]
    assert payload["user_prompt"] == "What is true?"
    assert payload["assistant_response"] == item.prompt_text
    assert [claim["proposed_label"] for claim in payload["claims"]] == [
        "substantive",
        "substantive",
    ]
    assert "secret-model" not in response.text
    assert "case-1" not in response.text
    assert "arm-1" not in response.text
    assert all("id" not in claim for claim in payload["claims"])


def test_model_eval_accepts_relabel_and_missing_claim_with_unicode_span(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    _item, task = _imported_task(db)
    response = client.post(
        f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval",
        headers=normal_user_token_headers,
        json={
            "item_revision": 1,
            "model_labels": ["incidental", "substantive"],
            "human_claims": [
                {
                    "claim_text": "The response mentions alpha.",
                    "label": "substantive",
                    "response_spans": [{"start": 0, "end": 5, "text": "Alpha"}],
                }
            ],
        },
    )

    assert response.status_code == 200
    review = db.exec(
        select(FactDecompReview).where(FactDecompReview.task_id == task.id)
    ).one()
    assert review.ratings == {
        "review_mode": "MODEL_LABEL_CORRECTION",
        "proposed_labels": ["substantive", "substantive"],
        "model_labels": ["incidental", "substantive"],
        "human_claims": [
            {
                "claim_text": "The response mentions alpha.",
                "response_spans": [{"start": 0, "end": 5, "text": "Alpha"}],
                "label": "substantive",
            }
        ],
    }


def test_model_eval_rejects_bad_spans_lengths_stale_and_duplicate(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    _item, task = _imported_task(db)
    base = {
        "item_revision": 1,
        "model_labels": ["incidental", "substantive"],
        "human_claims": [],
    }
    bad_span = {
        **base,
        "model_labels": ["incidental", "substantive"],
        "human_claims": [
            {
                "claim_text": "bad",
                "label": "substantive",
                "response_spans": [{"start": 0, "end": 5, "text": "Wrong"}],
            }
        ],
    }
    assert (
        client.post(
            f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval",
            headers=normal_user_token_headers,
            json=bad_span,
        ).status_code
        == 400
    )
    bool_offset = {
        **base,
        "model_labels": ["incidental", "substantive"],
        "human_claims": [
            {
                "claim_text": "bad",
                "label": "substantive",
                "response_spans": [{"start": False, "end": 5, "text": "Alpha"}],
            }
        ],
    }
    assert (
        client.post(
            f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval",
            headers=normal_user_token_headers,
            json=bool_offset,
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval",
            headers=normal_user_token_headers,
            json={**base, "model_labels": []},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval",
            headers=normal_user_token_headers,
            json={**base, "item_revision": 2},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval",
            headers=normal_user_token_headers,
            json=base,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval",
            headers=normal_user_token_headers,
            json=base,
        ).status_code
        == 409
    )


def test_response_only_model_eval_hides_null_query(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    _item, task = _imported_task(db, query=None)
    response = client.get(
        f"{settings.API_V1_STR}/review/fact-decomp/{task.id}",
        headers=normal_user_token_headers,
    )
    assert response.status_code == 200
    assert response.json()["user_prompt"] is None


def test_model_eval_rejects_bounded_collection_and_text_overflows_without_writing(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    _item, task = _imported_task(db)
    url = f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval"
    valid_missing = {
        "claim_text": "Alpha",
        "label": "substantive",
        "response_spans": [{"start": 0, "end": 5, "text": "Alpha"}],
    }
    requests = [
        {
            "item_revision": 1,
            "model_labels": ["incidental", "substantive"],
            "human_claims": [valid_missing] * 10_001,
        },
        {
            "item_revision": 1,
            "model_labels": ["incidental", "substantive"],
            "human_claims": [{**valid_missing, "claim_text": "x" * 20_001}],
        },
        {
            "item_revision": 1,
            "model_labels": ["incidental", "substantive"],
            "human_claims": [
                {
                    **valid_missing,
                    "response_spans": valid_missing["response_spans"] * 101,
                }
            ],
        },
    ]

    for request in requests:
        assert (
            client.post(
                url, headers=normal_user_token_headers, json=request
            ).status_code
            == 422
        )

    assert (
        db.exec(
            select(FactDecompReview).where(FactDecompReview.task_id == task.id)
        ).first()
        is None
    )


def test_model_eval_rejects_oversized_body_before_validation(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    _item, task = _imported_task(db)

    response = client.post(
        f"{settings.API_V1_STR}/review/fact-decomp/{task.id}/model-eval",
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


def test_human_claims_and_model_grades_preserve_originals_and_readback(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    item, task = _imported_task(db)
    original_metadata = item.item_metadata.copy()
    original_facts = [
        (fact.position, fact.fact_text)
        for fact in db.exec(select(EvalFact).where(EvalFact.item_id == item.id)).all()
    ]
    url = f"{settings.API_V1_STR}/review/fact-decomp/{task.id}"
    # Human claims may overlap model spans without replacing any original claim.
    finals = [
        {
            "claim_text": "Alpha is asserted.",
            "label": "borderline",
            "response_spans": [{"start": 0, "end": 5, "text": "Alpha"}],
        },
        {
            "claim_text": "Alpha is described as true.",
            "label": "substantive",
            "response_spans": [{"start": 0, "end": 14, "text": "Alpha is true."}],
        },
        {
            "claim_text": "The response mentions Beta.",
            "label": "incidental",
            "response_spans": [{"start": 15, "end": 19, "text": "Beta"}],
        },
    ]
    result = client.post(
        f"{url}/model-eval",
        headers=normal_user_token_headers,
        json={
            "item_revision": 1,
            "model_labels": ["incidental", "substantive"],
            "human_claims": finals,
        },
    )
    assert result.status_code == 200
    readback = client.get(url, headers=normal_user_token_headers)
    assert readback.status_code == 200
    assert readback.json()["existing_review"] == {
        "model_labels": ["incidental", "substantive"],
        "human_claims": finals,
    }
    db.refresh(item)
    assert item.item_metadata == original_metadata
    assert [
        (fact.position, fact.fact_text)
        for fact in db.exec(select(EvalFact).where(EvalFact.item_id == item.id)).all()
    ] == original_facts
