"""Coverage for the strict FACT_DECOMP artifact import boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, col, func, select

from app.core.config import settings
from app.models import Dataset, EvalFact, EvalItem, EvalType, ReviewTask


def _jsonl(*rows: object) -> bytes:
    return (
        b"\n".join(json.dumps(row, ensure_ascii=False).encode("utf-8") for row in rows)
        + b"\n"
    )


def _dataset(db: Session) -> Dataset:
    dataset = Dataset(
        name=f"fact-import-{uuid4()}",
        display_name="Fact import",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.commit()
    assert dataset.id is not None
    return dataset


def _row(*, case_id: str = "case-a", arm_id: str = "arm-a") -> dict:
    response = "Alpha is true. Beta is useful."
    return {
        "schema_version": 2,
        "eval_type": "FACT_DECOMP",
        "external_id": hashlib.sha256(f"{case_id}\0{arm_id}".encode()).hexdigest(),
        "case_id": case_id,
        "source": "LLM",
        "user_prompt": "What does the response assert?",
        "assistant_response": response,
        "arm_id": arm_id,
        "generator": {
            "model_id": "openai/gpt-oss-20b",
            "model_revision": None,
            "prompt_text": "Exact instructions.\r\nUnicode 😀\n",
            "pydantic_ai_version": "2.33.0",
            "generation": {"reasoning_effort": "medium"},
        },
        "claims": [
            {
                "claim": "Alpha is true.",
                "spans": [{"start": 0, "end": 14, "text": "Alpha is true."}],
                "label": "vital",
            },
            {
                "claim": "Beta is useful.",
                "spans": [{"start": 15, "end": 30, "text": "Beta is useful."}],
                "label": "semi-important",
            },
        ],
    }


def _post(
    client: TestClient,
    headers: dict[str, str],
    dataset: Dataset,
    payload: bytes,
    *,
    dry_run: bool = False,
) -> Response:
    assert dataset.id is not None
    return client.post(
        f"{settings.API_V1_STR}/admin/ingest",
        headers=headers,
        data={"dataset_id": str(dataset.id), "dry_run": str(dry_run).lower()},
        files={"file": ("facts.jsonl", payload, "application/x-ndjson")},
    )


def test_imports_response_only_zero_claim_and_replays_identically(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = _dataset(db)
    row = _row(case_id="response-only")
    row["user_prompt"] = None
    row["claims"] = []

    first = _post(client, superuser_token_headers, dataset, _jsonl(row))
    assert first.status_code == 200
    assert first.json()["created"] == 1

    item = db.exec(select(EvalItem)).one()
    assert item.prompt_text == row["assistant_response"]
    assert item.lazy_query is None
    assert item.author_kind.value == "LLM_GENERATED"
    assert item.item_metadata["review_mode"] == "MODEL_LABEL_CORRECTION"
    assert db.exec(select(EvalFact)).all() == []
    assert db.exec(select(ReviewTask)).one().item_a_id == item.id

    replay = _post(client, superuser_token_headers, dataset, _jsonl(row))
    assert replay.status_code == 200
    assert replay.json()["unchanged"] == 1
    assert db.exec(select(func.count(col(EvalItem.id)))).one() == 1


def test_import_rejects_bad_spans_extra_fields_and_conflicts(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = _dataset(db)
    row = _row(case_id="validation")
    bad_span = json.loads(json.dumps(row))
    bad_span["claims"][0]["spans"][0]["text"] = "not in response"
    extra = json.loads(json.dumps(row))
    extra["extra"] = True

    response = _post(
        client,
        superuser_token_headers,
        dataset,
        _jsonl(bad_span, extra, row),
    )
    assert response.status_code == 200
    assert response.json()["created"] == 1
    assert response.json()["rejected"] == 2

    conflict = json.loads(json.dumps(row))
    conflict["user_prompt"] = "A different query."
    conflict_result = _post(client, superuser_token_headers, dataset, _jsonl(conflict))
    assert conflict_result.status_code == 200
    assert conflict_result.json()["rejected"] == 1
    assert "different content" in conflict_result.json()["errors"][0]["message"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda row: row.update(schema_version=1),
            "unsupported schema_version 1; expected 2",
        ),
        (
            lambda row: row["generator"].update(prompt_id="old", prompt_hash="a" * 64),
            "prompt_id",
        ),
        (lambda row: row["claims"][0].update(label="substantive"), "label"),
    ],
    ids=["old-version", "old-prompt-fields", "old-label"],
)
def test_import_rejects_old_contracts_explicitly(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    mutate: Callable[[dict], None],
    message: str,
) -> None:
    dataset = _dataset(db)
    row = _row(case_id=f"old-{uuid4()}")
    mutate(row)
    result = _post(client, superuser_token_headers, dataset, _jsonl(row))
    assert result.json()["rejected"] == 1
    assert message in result.json()["errors"][0]["message"]


def test_import_dry_run_and_oversized_artifact_do_not_persist(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch,
) -> None:
    dataset = _dataset(db)
    row = _row(case_id="dry-run")
    dry_run = _post(
        client, superuser_token_headers, dataset, _jsonl(row, row), dry_run=True
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["created"] == 1
    assert dry_run.json()["unchanged"] == 1
    assert db.exec(select(EvalItem)).all() == []

    monkeypatch.setattr(settings, "DOCUMENT_IMPORT_MAX_ARTIFACT_BYTES", 10)
    oversized = _post(client, superuser_token_headers, dataset, _jsonl(row))
    assert oversized.status_code == 413
    assert db.exec(select(EvalItem)).all() == []


def test_import_rejects_malformed_json_line_without_rolling_back_valid_rows(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = _dataset(db)
    response = _post(
        client,
        superuser_token_headers,
        dataset,
        b"{not-json}\n" + _jsonl(_row(case_id="valid-after-malformed")),
    )

    assert response.status_code == 200
    assert response.json()["created"] == 1
    assert response.json()["rejected"] == 1
    assert "invalid JSON" in response.json()["errors"][0]["message"]
    assert len(db.exec(select(EvalItem)).all()) == 1


@pytest.mark.parametrize("query", ["What is asserted?", None], ids=["qa", "document"])
def test_generated_import_review_and_export_preserve_exact_contract(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    query: str | None,
) -> None:
    from amfv_datasets.decomposition_eval import (
        DecompositionCase,
        DecompositionPrediction,
        GeneratorProvenance,
        project_prediction,
    )

    dataset = _dataset(db)
    case = DecompositionCase(
        schema_version=1,
        case_id="relevance-contract",
        user_prompt=query,
        assistant_response="First Alpha. Context. Unclear. Then Alpha.",
    )
    labels = ["vital", "unimportant"]
    prediction = DecompositionPrediction.model_validate(
        {
            "claims": [
                {"claim": claim, "source_texts": [quote], "label": label}
                for claim, quote, label in zip(
                    ["Alpha.", "Context."],
                    ["First Alpha.", "Context."],
                    labels,
                    strict=True,
                )
            ]
        }
    )
    row = project_prediction(
        case,
        prediction,
        arm_id="relevance-arm",
        generator=GeneratorProvenance(
            model_id="test",
            prompt_text="Instructions with CRLF.\r\nUnicode 😀\n",
            pydantic_ai_version="test",
        ),
    )
    imported = _post(
        client, superuser_token_headers, dataset, _jsonl(row.model_dump(mode="json"))
    )
    assert imported.json()["created"] == 1
    task = db.exec(select(ReviewTask)).one()
    review_url = f"{settings.API_V1_STR}/review/fact-decomp/{task.id}"
    review = client.get(review_url, headers=superuser_token_headers)
    assert review.status_code == 200
    assert review.json()["user_prompt"] == query
    assert [claim["proposed_label"] for claim in review.json()["claims"]] == labels
    saved = client.post(
        f"{review_url}/model-eval",
        headers=superuser_token_headers,
        json={
            "item_revision": review.json()["item_revision"],
            "rubric_id": "importance-v1",
            "claim_reviews": [
                {"position": 0, "label": "unimportant", "issue": None},
                {
                    "position": 1,
                    "label": "semi-important",
                    "issue": "Grouped too broadly.",
                },
            ],
            "human_claims": [],
            "coverage_checked": True,
        },
    )
    assert saved.status_code == 200
    exported = client.get(
        f"{settings.API_V1_STR}/admin/export?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    )
    assert exported.status_code == 200
    item = exported.json()["items"][0]
    assert item["schema_version"] == 2
    assert item["generator"]["prompt_text"] == "Instructions with CRLF.\r\nUnicode 😀\n"
    assert [claim["proposed_label"] for claim in item["claims"]] == labels
    assert item["correction_reviews"][0]["claim_reviews"][0]["label"] == "unimportant"
    assert item["correction_reviews"][0]["coverage_checked"] is True


def test_two_arms_preserve_distinct_prompt_text_and_same_identity_conflicts(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = _dataset(db)
    first = _row(case_id="history", arm_id="arm-one")
    second = _row(case_id="history", arm_id="arm-two")
    second["generator"]["prompt_text"] = "Second prompt.\n"
    result = _post(client, superuser_token_headers, dataset, _jsonl(first, second))
    assert result.json()["created"] == 2
    conflict = deepcopy(first)
    conflict["generator"]["prompt_text"] = "Changed prompt.\n"
    assert (
        _post(client, superuser_token_headers, dataset, _jsonl(conflict)).json()[
            "rejected"
        ]
        == 1
    )
    exported = client.get(
        f"{settings.API_V1_STR}/admin/export?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    ).json()["items"]
    assert {item["arm_id"]: item["generator"]["prompt_text"] for item in exported} == {
        "arm-one": "Exact instructions.\r\nUnicode 😀\n",
        "arm-two": "Second prompt.\n",
    }
