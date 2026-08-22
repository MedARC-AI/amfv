from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    Chunk,
    Dataset,
    Document,
    EvalItem,
    EvalType,
    ItemSource,
    ItemStatus,
    RetrievalCategory,
)
from app.services.documents import create_document_with_chunks


def test_create_options_and_source_documents(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="api-retrieval",
        display_name="API Retrieval",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(dataset)
    db.flush()
    document = Document(
        dataset_id=dataset.id,
        external_id="doc-api",
        title="API Doc",
        content="Document text",
        paragraphs=[],
    )
    db.add(document)
    db.commit()

    options = client.get(
        f"{settings.API_V1_STR}/create/options",
        headers=normal_user_token_headers,
    )
    assert options.status_code == 200
    assert any(row["name"] == "api-retrieval" for row in options.json())

    documents = client.get(
        f"{settings.API_V1_STR}/create/source-documents"
        "?dataset_id={dataset_id}&eval_type=RETRIEVAL".format(dataset_id=dataset.id),
        headers=normal_user_token_headers,
    )
    assert documents.status_code == 200
    assert documents.json()[0]["external_id"] == "doc-api"


def test_source_documents_hide_current_users_used_retrieval_documents(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    from app import crud

    user = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert user is not None
    dataset = Dataset(
        name=f"api-retrieval-used-{uuid4()}",
        display_name="API Retrieval Used",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(dataset)
    db.flush()
    used_document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        external_id="used-doc-api",
        title="Used API Doc",
        content="Used evidence text.",
    )
    create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        external_id="unused-doc-api",
        title="Unused API Doc",
        content="Unused evidence text.",
    )
    used_chunk = db.exec(
        select(Chunk).where(Chunk.document_id == used_document.id)
    ).first()
    assert used_chunk is not None
    db.add(
        EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.RETRIEVAL,
            category=RetrievalCategory.MULTI_CHUNK,
            source=ItemSource.HUMAN,
            author_user_id=user.id,
            prompt_text="Used prompt",
            gold_chunk_ids=[used_chunk.id],
            status=ItemStatus.SUBMITTED,
        )
    )
    db.commit()

    filtered = client.get(
        f"{settings.API_V1_STR}/create/source-documents"
        "?dataset_id={dataset_id}&eval_type=RETRIEVAL".format(dataset_id=dataset.id),
        headers=normal_user_token_headers,
    )
    assert filtered.status_code == 200
    assert [row["external_id"] for row in filtered.json()] == ["unused-doc-api"]

    included = client.get(
        f"{settings.API_V1_STR}/create/source-documents"
        "?dataset_id={dataset_id}&eval_type=RETRIEVAL&include_used=true".format(
            dataset_id=dataset.id
        ),
        headers=normal_user_token_headers,
    )
    assert included.status_code == 200
    assert {row["external_id"] for row in included.json()} == {
        "used-doc-api",
        "unused-doc-api",
    }


def test_create_source_document_detail_returns_chunks_and_enforces_scope(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    retrieval_dataset = Dataset(
        name="detail-retrieval",
        display_name="Detail Retrieval",
        eval_type=EvalType.RETRIEVAL,
    )
    fact_dataset = Dataset(
        name="detail-fact",
        display_name="Detail Fact",
        eval_type=EvalType.FACT_DECOMP,
    )
    inactive_dataset = Dataset(
        name="detail-inactive",
        display_name="Detail Inactive",
        eval_type=EvalType.RETRIEVAL,
        is_active=False,
    )
    db.add(retrieval_dataset)
    db.add(fact_dataset)
    db.add(inactive_dataset)
    db.flush()
    document = create_document_with_chunks(
        db,
        dataset_id=retrieval_dataset.id,
        title="Detail Doc",
        content="First paragraph.\n\nSecond paragraph with emoji 😀.",
        external_id="detail-doc",
        source_url="https://www.nice.org.uk/guidance/ng235/advice/why-this-is-important",
    )
    inactive_document = create_document_with_chunks(
        db,
        dataset_id=retrieval_dataset.id,
        title="Inactive Detail Doc",
        content="Hidden paragraph.",
        external_id="inactive-detail-doc",
    )
    inactive_document.is_active = False
    inactive_dataset_document = create_document_with_chunks(
        db,
        dataset_id=inactive_dataset.id,
        title="Inactive Dataset Detail Doc",
        content="Hidden dataset paragraph.",
        external_id="inactive-dataset-detail-doc",
    )
    db.add(inactive_document)
    db.commit()

    detail = client.get(
        f"{settings.API_V1_STR}/create/source-documents/{document.id}"
        "?dataset_id={dataset_id}&eval_type=RETRIEVAL".format(
            dataset_id=retrieval_dataset.id
        ),
        headers=normal_user_token_headers,
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["external_id"] == "detail-doc"
    assert (
        body["source_url"]
        == "https://www.nice.org.uk/guidance/ng235/advice/why-this-is-important"
    )
    assert body["content"] == "First paragraph.\n\nSecond paragraph with emoji 😀."
    assert [chunk["position"] for chunk in body["chunks"]] == [0]
    assert body["chunks"][0]["text"] == body["content"]

    wrong_eval_type = client.get(
        f"{settings.API_V1_STR}/create/source-documents/{document.id}"
        "?dataset_id={dataset_id}&eval_type=FACT_DECOMP".format(
            dataset_id=retrieval_dataset.id
        ),
        headers=normal_user_token_headers,
    )
    assert wrong_eval_type.status_code == 404

    wrong_dataset = client.get(
        f"{settings.API_V1_STR}/create/source-documents/{document.id}"
        "?dataset_id={dataset_id}&eval_type=FACT_DECOMP".format(
            dataset_id=fact_dataset.id
        ),
        headers=normal_user_token_headers,
    )
    assert wrong_dataset.status_code == 404

    inactive_doc = client.get(
        f"{settings.API_V1_STR}/create/source-documents/{inactive_document.id}"
        "?dataset_id={dataset_id}&eval_type=RETRIEVAL".format(
            dataset_id=retrieval_dataset.id
        ),
        headers=normal_user_token_headers,
    )
    assert inactive_doc.status_code == 404

    inactive_dataset_response = client.get(
        f"{settings.API_V1_STR}/create/source-documents/{inactive_dataset_document.id}"
        "?dataset_id={dataset_id}&eval_type=RETRIEVAL".format(
            dataset_id=inactive_dataset.id
        ),
        headers=normal_user_token_headers,
    )
    assert inactive_dataset_response.status_code == 404
