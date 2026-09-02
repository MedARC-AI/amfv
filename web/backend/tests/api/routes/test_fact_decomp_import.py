"""Coverage for the strict FACT_DECOMP artifact import boundary."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

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
        "schema_version": 1,
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
            "prompt_id": "prompt-v1",
            "prompt_hash": "a" * 64,
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
                "label": "supporting",
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
