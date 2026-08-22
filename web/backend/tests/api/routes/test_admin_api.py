from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    Chunk,
    Dataset,
    Document,
    EvalItem,
    EvalType,
    FactDecompReview,
    ItemSource,
    ItemStatus,
    ReviewerKind,
    ReviewTask,
    User,
    UserRole,
)
from app.services.documents import create_document_with_chunks


def test_admin_dataset_list_requires_data_role(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    superuser_token_headers: dict[str, str],
) -> None:
    denied = client.get(
        f"{settings.API_V1_STR}/admin/datasets",
        headers=normal_user_token_headers,
    )
    assert denied.status_code == 403

    allowed = client.get(
        f"{settings.API_V1_STR}/admin/datasets",
        headers=superuser_token_headers,
    )
    assert allowed.status_code == 200

    items = client.get(
        f"{settings.API_V1_STR}/admin/items",
        headers=superuser_token_headers,
    )
    assert items.status_code == 200
    assert isinstance(items.json(), list)


def test_admin_dataset_list_accepts_data_role_user(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    # Mutate the fixture user directly so the existing token remains valid.
    from app import crud

    db_user = crud.get_user_by_email(session=db, email=settings.EMAIL_TEST_USER)
    assert db_user is not None
    db_user.role = UserRole.data_admin
    db.add(db_user)
    db.commit()

    allowed = client.get(
        f"{settings.API_V1_STR}/admin/datasets",
        headers=normal_user_token_headers,
    )
    assert allowed.status_code == 200

    db_user.role = UserRole.user
    db.add(db_user)
    db.commit()


def test_admin_can_create_dataset_document_and_toggle_document(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset_response = client.post(
        f"{settings.API_V1_STR}/admin/datasets",
        headers=superuser_token_headers,
        json={
            "name": "admin-core-dataset",
            "display_name": "Admin Core Dataset",
            "eval_type": "RETRIEVAL",
        },
    )
    assert dataset_response.status_code == 200
    dataset_id = dataset_response.json()["id"]

    document_response = client.post(
        f"{settings.API_V1_STR}/admin/documents",
        headers=superuser_token_headers,
        json={
            "dataset_id": dataset_id,
            "external_id": "admin-core-doc",
            "title": "Admin Core Doc",
            "content": "First paragraph.\n\nSecond paragraph.",
        },
    )
    assert document_response.status_code == 200
    document = document_response.json()
    assert document["external_id"] == "admin-core-doc"
    assert [chunk["external_id"] for chunk in document["chunks"]] == [
        "admin-core-doc-chunk-0",
    ]
    assert document["chunks"][0]["text"] == "First paragraph.\n\nSecond paragraph."

    toggled = client.post(
        f"{settings.API_V1_STR}/admin/documents/{document['id']}/toggle",
        headers=superuser_token_headers,
    )
    assert toggled.status_code == 200
    assert toggled.json()["is_active"] is False

    db_document = db.get(Document, document["id"])
    assert db_document is not None
    assert db_document.is_active is False


def test_admin_moderation_keeps_retrieval_task_generation_empty_and_exports_evidence(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="admin-core-moderation",
        display_name="Admin Core Moderation",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(dataset)
    db.flush()
    document = create_document_with_chunks(
        db,
        dataset_id=dataset.id,
        title="Moderation Doc",
        content="The answer is Delta.",
        external_id="doc-moderation",
    )
    db.flush()
    chunk = db.exec(select(Chunk).where(Chunk.document_id == document.id)).first()
    assert chunk is not None
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        category="VERBATIM",
        document_id=document.id,
        source=ItemSource.HUMAN,
        prompt_text="Which answer appears?",
        expected_answer="Delta",
        evidence_spans=[
            {
                "chunk_id": chunk.id,
                "start": chunk.text.index("Delta"),
                "end": chunk.text.index("Delta") + len("Delta"),
                "text": "Delta",
                "kind": "gold",
            }
        ],
        gold_chunk_ids=[chunk.id],
        status=ItemStatus.SUBMITTED,
    )
    db.add(item)
    db.commit()

    listed = client.get(
        f"{settings.API_V1_STR}/admin/items?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    )
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "SUBMITTED"

    approved = client.post(
        f"{settings.API_V1_STR}/admin/items/{item.id}/approve",
        headers=superuser_token_headers,
        json={
            "action": "approve",
            "dataset_id": dataset.id,
            "expected_item_revision": item.revision,
        },
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "ACTIVE"
    approved_revision = approved.json()["revision"]

    generated = client.post(
        f"{settings.API_V1_STR}/admin/datasets/{dataset.id}/generate-tasks",
        headers=superuser_token_headers,
    )
    assert generated.status_code == 200
    assert generated.json()["created"] == 0
    assert (
        db.exec(select(ReviewTask).where(ReviewTask.item_a_id == item.id)).first()
        is None
    )

    regenerated = client.post(
        f"{settings.API_V1_STR}/admin/datasets/{dataset.id}/generate-tasks",
        headers=superuser_token_headers,
    )
    assert regenerated.status_code == 200
    assert regenerated.json()["created"] == 0

    exported = client.get(
        f"{settings.API_V1_STR}/admin/export?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    )
    assert exported.status_code == 200
    export_item = exported.json()["items"][0]
    assert export_item["evidence_chunks"][0]["text"] == "The answer is Delta."
    assert export_item["evidence_documents"][0]["external_id"] == "doc-moderation"
    assert export_item["review_task_count"] == 0

    returned_to_draft = client.post(
        f"{settings.API_V1_STR}/admin/items/{item.id}/reject",
        headers=superuser_token_headers,
        json={
            "action": "return_to_draft",
            "dataset_id": dataset.id,
            "expected_item_revision": approved_revision,
            "reason": "Needs edit",
        },
    )
    assert returned_to_draft.status_code == 400
    assert returned_to_draft.json()["detail"] == "Only submitted items can be moderated"

    queue_after_approval = client.get(
        f"{settings.API_V1_STR}/admin/items?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    )
    assert queue_after_approval.status_code == 200
    assert queue_after_approval.json() == []

    all_items = client.get(
        f"{settings.API_V1_STR}/admin/items?dataset_id={dataset.id}&status=ACTIVE",
        headers=superuser_token_headers,
    )
    assert all_items.status_code == 200
    assert all_items.json()[0]["status"] == "ACTIVE"


def test_admin_moderation_rejects_revision_mismatch(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="admin-core-revision",
        display_name="Admin Core Revision",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.flush()
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        prompt_text="Statement.",
        status=ItemStatus.SUBMITTED,
        revision=3,
    )
    db.add(item)
    db.commit()

    response = client.post(
        f"{settings.API_V1_STR}/admin/items/{item.id}/approve",
        headers=superuser_token_headers,
        json={
            "action": "approve",
            "dataset_id": dataset.id,
            "expected_item_revision": 2,
        },
    )

    assert response.status_code == 409


def test_admin_moderation_rejects_draft_item(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="admin-core-draft-transition",
        display_name="Admin Core Draft Transition",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.flush()
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        prompt_text="Draft statement.",
        status=ItemStatus.DRAFT,
    )
    db.add(item)
    db.commit()

    response = client.post(
        f"{settings.API_V1_STR}/admin/items/{item.id}/approve",
        headers=superuser_token_headers,
        json={
            "action": "approve",
            "dataset_id": dataset.id,
            "expected_item_revision": item.revision,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only submitted items can be moderated"


def test_admin_agreement_and_inter_user_metrics(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    first_user = User(
        email="metrics-first@example.com",
        hashed_password="x",
        reviewer_kind=ReviewerKind.human,
    )
    second_user = User(
        email="metrics-second@example.com",
        hashed_password="x",
        reviewer_kind=ReviewerKind.human,
    )
    dataset = Dataset(
        name="admin-metrics-agreement",
        display_name="Admin Metrics Agreement",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(first_user)
    db.add(second_user)
    db.add(dataset)
    db.flush()
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=first_user.id,
        prompt_text="Statement.",
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.flush()
    task = ReviewTask(dataset_id=dataset.id, item_a_id=item.id)
    db.add(task)
    db.flush()
    db.add(
        FactDecompReview(
            task_id=task.id,
            user_id=first_user.id,
            item_revision=item.revision,
            reviewer_kind=ReviewerKind.human,
            ratings={"verdict": "keep"},
        )
    )
    db.add(
        FactDecompReview(
            task_id=task.id,
            user_id=second_user.id,
            item_revision=item.revision,
            reviewer_kind=ReviewerKind.human,
            ratings={"verdict": "keep"},
        )
    )
    db.commit()

    agreement = client.get(
        f"{settings.API_V1_STR}/admin/metrics/agreement?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    )
    assert agreement.status_code == 200
    assert agreement.json()[0]["dimension"] == "verdict"
    assert agreement.json()[0]["alpha"] == 1.0

    inter_user = client.get(
        f"{settings.API_V1_STR}/admin/metrics/inter-user-agreement"
        f"?dataset_id={dataset.id}&left_user_id={first_user.id}"
        f"&right_user_id={second_user.id}",
        headers=superuser_token_headers,
    )
    assert inter_user.status_code == 200
    assert inter_user.json()[0]["dimension"] == "verdict"
    assert inter_user.json()[0]["kappa"] == 1.0
    assert inter_user.json()[0]["overlap"] == 1

    invalid_overlap = client.get(
        f"{settings.API_V1_STR}/admin/metrics/inter-user-agreement"
        f"?dataset_id={dataset.id}&left_user_id={first_user.id}"
        f"&right_user_id={second_user.id}&min_overlap=0",
        headers=superuser_token_headers,
    )
    assert invalid_overlap.status_code == 422


def test_admin_user_metrics_count_dataset_activity(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    user = User(
        email="metrics-user@example.com",
        hashed_password="x",
        reviewer_kind=ReviewerKind.human,
    )
    dataset = Dataset(
        name="admin-metrics-user",
        display_name="Admin Metrics User",
        eval_type=EvalType.RETRIEVAL,
    )
    other_dataset = Dataset(
        name="admin-metrics-user-other",
        display_name="Admin Metrics User Other",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(user)
    db.add(dataset)
    db.add(other_dataset)
    db.flush()
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=user.id,
        prompt_text="Question?",
        status=ItemStatus.ACTIVE,
    )
    other_item = EvalItem(
        dataset_id=other_dataset.id,
        eval_type=EvalType.RETRIEVAL,
        source=ItemSource.HUMAN,
        author_user_id=user.id,
        prompt_text="Other question?",
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.add(other_item)
    db.flush()
    task = ReviewTask(dataset_id=dataset.id, item_a_id=item.id)
    db.add(task)
    db.flush()
    db.add(
        FactDecompReview(
            task_id=task.id,
            user_id=user.id,
            item_revision=item.revision,
            reviewer_kind=ReviewerKind.human,
            ratings={"verdict": "keep"},
        )
    )
    db.commit()

    metrics = client.get(
        f"{settings.API_V1_STR}/admin/metrics/users?dataset_id={dataset.id}",
        headers=superuser_token_headers,
    )
    assert metrics.status_code == 200
    payload = metrics.json()
    assert payload["total"] >= 2
    row = next(
        row for row in payload["items"] if row["email"] == "metrics-user@example.com"
    )
    assert row["authored_items"] == 1
    assert row["fact_decomp_reviews"] == 1
