"""Fixture-backed coverage for the versioned source-document import boundary."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, col, create_engine, func, select

from app import crud
from app.api.routes import admin as admin_routes
from app.core.config import settings
from app.models import Chunk, Dataset, Document, EvalType, UserRole

FIXTURE_PATH = (
    Path(__file__).resolve().parents[5]
    / "datasets/test/fixtures/scraping/nice_document_v1.jsonl"
)


def _fixture_row() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _jsonl(*rows: object) -> bytes:
    return (
        b"\n".join(json.dumps(row, ensure_ascii=False).encode("utf-8") for row in rows)
        + b"\n"
    )


def _retrieval_dataset(db: Session, *, active: bool = True) -> Dataset:
    dataset = Dataset(
        name=f"document-import-{uuid4()}",
        display_name="Document import",
        eval_type=EvalType.RETRIEVAL,
        is_active=active,
    )
    db.add(dataset)
    db.commit()
    assert dataset.id is not None
    return dataset


def _post_import(
    client: TestClient,
    headers: dict[str, str],
    dataset: Dataset,
    data: bytes,
    *,
    dry_run: bool = False,
) -> Response:
    assert dataset.id is not None
    return client.post(
        f"{settings.API_V1_STR}/admin/documents/import",
        headers=headers,
        data={"dataset_id": str(dataset.id), "dry_run": str(dry_run).lower()},
        files={"file": ("documents.jsonl", data, "application/x-ndjson")},
    )


def _documents_for_dataset(db: Session, dataset: Dataset) -> list[Document]:
    assert dataset.id is not None
    db.expire_all()
    return list(
        db.exec(
            select(Document)
            .where(col(Document.dataset_id) == dataset.id)
            .order_by(col(Document.id))
        ).all()
    )


def test_import_transaction_setup_does_not_rebegin_an_embedded_sqlite_transaction() -> (
    None
):
    engine = create_engine("sqlite://")
    try:
        with Session(engine) as session:
            connection = session.connection()
            connection.exec_driver_sql("BEGIN")
            admin_routes._ensure_import_transaction(session)
            session.rollback()
    finally:
        engine.dispose()


def test_imports_frozen_fixture_with_provenance_single_chunk_and_idempotency(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = _retrieval_dataset(db)
    row = _fixture_row()

    response = _post_import(
        client,
        superuser_token_headers,
        dataset,
        _jsonl(row),
    )
    assert response.status_code == 200
    assert response.json() == {
        "created": 1,
        "unchanged": 0,
        "rejected": 0,
        "errors": [],
        "dry_run": False,
    }

    documents = _documents_for_dataset(db, dataset)
    assert len(documents) == 1
    document = documents[0]
    assert document.external_id == row["external_id"]
    assert document.content == row["content"]
    assert document.source == row["source"]
    assert document.source_url == row["url"]
    assert document.source_metadata == {
        **row["metadata"],
        "section_count": row["section_count"],
    }
    assert document.source_content_hash
    assert document.paragraphs == [{"idx": 0, "text": row["content"]}]
    chunks = list(
        db.exec(
            select(Chunk)
            .where(col(Chunk.document_id) == document.id)
            .order_by(col(Chunk.position))
        ).all()
    )
    assert [(chunk.position, chunk.text) for chunk in chunks] == [(0, row["content"])]

    assert document.id is not None
    detail = client.get(
        f"{settings.API_V1_STR}/admin/documents/{document.id}",
        headers=superuser_token_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["source_url"] == row["url"]

    advice_row = {
        **row,
        "external_id": f"{row['external_id']}-advice-{uuid4()}",
        "url": f"{row['url']}/advice/why-this-is-important",
    }
    advice = _post_import(client, superuser_token_headers, dataset, _jsonl(advice_row))
    assert advice.status_code == 200
    advice_document = next(
        candidate
        for candidate in _documents_for_dataset(db, dataset)
        if candidate.external_id == advice_row["external_id"]
    )
    assert advice_document.id is not None
    advice_detail = client.get(
        f"{settings.API_V1_STR}/admin/documents/{advice_document.id}",
        headers=superuser_token_headers,
    )
    assert advice_detail.status_code == 200
    assert advice_detail.json()["source_url"] == advice_row["url"]

    replay = _post_import(client, superuser_token_headers, dataset, _jsonl(row))
    assert replay.status_code == 200
    assert replay.json()["created"] == 0
    assert replay.json()["unchanged"] == 1
    assert len(_documents_for_dataset(db, dataset)) == 2


def test_import_rejects_unknown_versions_extra_fields_and_changed_content(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = _retrieval_dataset(db)
    row = _fixture_row()
    row["external_id"] = f"{row['external_id']}-{uuid4()}"
    unknown_version = {**row, "schema_version": 2}
    unexpected_field = {**row, "external_id": f"extra-{uuid4()}", "unexpected": True}

    response = _post_import(
        client,
        superuser_token_headers,
        dataset,
        _jsonl(unknown_version, unexpected_field, row),
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["created"], body["unchanged"], body["rejected"]) == (1, 0, 2)
    assert "unsupported schema_version 2" in body["errors"][0]["message"]
    assert body["errors"][1]["line"] == 2

    changed = {**row, "content": f"{row['content']}\nChanged."}
    conflict = _post_import(client, superuser_token_headers, dataset, _jsonl(changed))
    assert conflict.status_code == 200
    assert conflict.json()["rejected"] == 1
    assert "different content" in conflict.json()["errors"][0]["message"]
    assert _documents_for_dataset(db, dataset)[0].content == row["content"]

    provenance_changes = [
        {**row, "title": f"{row['title']} corrected"},
        {**row, "source": "corrected-source"},
        {**row, "url": f"{row['url']}/corrected"},
        {**row, "metadata": {**row["metadata"], "corrected": True}},
        {**row, "section_count": row["section_count"] + 1},
    ]
    provenance_conflicts = _post_import(
        client,
        superuser_token_headers,
        dataset,
        _jsonl(*provenance_changes),
    )
    assert provenance_conflicts.status_code == 200
    assert provenance_conflicts.json()["unchanged"] == 0
    assert provenance_conflicts.json()["rejected"] == len(provenance_changes)
    assert all(
        "different content or provenance" in error["message"]
        for error in provenance_conflicts.json()["errors"]
    )
    persisted = _documents_for_dataset(db, dataset)[0]
    assert persisted.title == row["title"]
    assert persisted.source_url == row["url"]
    assert persisted.source_metadata == {
        **row["metadata"],
        "section_count": row["section_count"],
    }


def test_import_isolates_bad_rows_and_dry_run_rolls_back_provisional_documents(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = _retrieval_dataset(db)
    first = _fixture_row()
    first["external_id"] = f"dry-run-first-{uuid4()}"
    second = {**first, "external_id": f"dry-run-second-{uuid4()}"}

    isolated = _post_import(
        client,
        superuser_token_headers,
        dataset,
        _jsonl(first, {"schema_version": 1}, second),
    )
    assert isolated.status_code == 200
    assert isolated.json()["created"] == 2
    assert isolated.json()["rejected"] == 1
    assert len(_documents_for_dataset(db, dataset)) == 2

    dry_first = {**first, "external_id": f"dry-run-conflict-{uuid4()}"}
    dry_conflict = {**dry_first, "content": "a changed source document"}
    before_documents = db.exec(select(func.count(col(Document.id)))).one()
    before_chunks = db.exec(select(func.count(col(Chunk.id)))).one()
    dry_run = _post_import(
        client,
        superuser_token_headers,
        dataset,
        _jsonl(dry_first, dry_first, dry_conflict),
        dry_run=True,
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["dry_run"] is True
    assert (
        dry_run.json()["created"],
        dry_run.json()["unchanged"],
        dry_run.json()["rejected"],
    ) == (1, 1, 1)
    db.expire_all()
    assert db.exec(select(func.count(col(Document.id)))).one() == before_documents
    assert db.exec(select(func.count(col(Chunk.id)))).one() == before_chunks


def test_import_requires_superuser_and_active_retrieval_dataset(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    data = _jsonl(_fixture_row())
    retrieval = _retrieval_dataset(db)
    forbidden = _post_import(client, normal_user_token_headers, retrieval, data)
    assert forbidden.status_code == 403

    db_user = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert db_user is not None
    db_user.role = UserRole.data_admin
    db.add(db_user)
    db.commit()
    try:
        data_admin_denied = _post_import(
            client, normal_user_token_headers, retrieval, data
        )
        assert data_admin_denied.status_code == 403
    finally:
        db_user.role = UserRole.user
        db.add(db_user)
        db.commit()

    inactive = _retrieval_dataset(db, active=False)
    unavailable = _post_import(client, superuser_token_headers, inactive, data)
    assert unavailable.status_code == 404

    fact_dataset = Dataset(
        name=f"document-import-fact-{uuid4()}",
        display_name="Document import facts",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(fact_dataset)
    db.commit()
    unavailable = _post_import(client, superuser_token_headers, fact_dataset, data)
    assert unavailable.status_code == 404


def test_import_enforces_configured_size_ceilings_and_rolls_back_oversized_artifact(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _retrieval_dataset(db)
    first = _fixture_row()
    first["external_id"] = f"artifact-first-{uuid4()}"
    second = {**first, "external_id": f"artifact-second-{uuid4()}"}
    first_line = _jsonl(first)
    payload = first_line + _jsonl(second)
    monkeypatch.setattr(admin_routes, "IMPORT_READ_CHUNK_BYTES", len(first_line) + 1)
    monkeypatch.setattr(
        settings, "DOCUMENT_IMPORT_MAX_ARTIFACT_BYTES", len(first_line) + 1
    )

    oversized = _post_import(client, superuser_token_headers, dataset, payload)
    assert oversized.status_code == 413
    assert _documents_for_dataset(db, dataset) == []

    monkeypatch.setattr(settings, "DOCUMENT_IMPORT_MAX_ARTIFACT_BYTES", 100_000)
    monkeypatch.setattr(settings, "DOCUMENT_IMPORT_MAX_CONTENT_BYTES", 4)
    content_too_large = {
        **first,
        "external_id": f"content-limit-{uuid4()}",
        "content": "five!",
    }
    content_limited = _post_import(
        client,
        superuser_token_headers,
        dataset,
        _jsonl(content_too_large),
    )
    assert content_limited.status_code == 200
    assert content_limited.json()["rejected"] == 1
    assert "content exceeds" in content_limited.json()["errors"][0]["message"]

    monkeypatch.setattr(settings, "DOCUMENT_IMPORT_MAX_CONTENT_BYTES", 100_000)
    monkeypatch.setattr(settings, "DOCUMENT_IMPORT_MAX_METADATA_BYTES", 4)
    metadata_too_large = {
        **first,
        "external_id": f"metadata-limit-{uuid4()}",
        "metadata": {"key": "value"},
    }
    metadata_limited = _post_import(
        client,
        superuser_token_headers,
        dataset,
        _jsonl(metadata_too_large),
    )
    assert metadata_limited.status_code == 200
    assert metadata_limited.json()["rejected"] == 1
    assert "metadata exceeds" in metadata_limited.json()["errors"][0]["message"]
    assert _documents_for_dataset(db, dataset) == []
