from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import event
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.models import (
    Dataset,
    EvalFact,
    EvalItem,
    EvalType,
    FactPolarity,
    ItemSource,
    ItemStatus,
    ReviewTask,
    User,
)


@pytest.fixture
def representative_admin_data(db: Session) -> Generator[Dataset, None, None]:
    dataset = Dataset(
        name="bounded-admin-volume",
        display_name="Bounded admin volume",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.flush()
    assert dataset.id is not None

    users = [
        User(
            email=f"bounded-user-{index:03d}@example.com",
            hashed_password="not-used",
        )
        for index in range(125)
    ]
    db.add_all(users)
    db.flush()
    items = [
        EvalItem(
            dataset_id=dataset.id,
            external_id=f"bounded-item-{index:03d}",
            eval_type=EvalType.FACT_DECOMP,
            source=ItemSource.HUMAN,
            author_user_id=user.id,
            prompt_text=f"Representative item {index}",
            status=ItemStatus.ACTIVE,
        )
        for index, user in enumerate(users)
    ]
    db.add_all(items)
    db.flush()
    for index, item in enumerate(items):
        assert item.id is not None
        db.add(
            EvalFact(
                item_id=item.id,
                fact_uuid=f"bounded-fact-{index:03d}",
                fact_text=f"Representative fact {index}",
                polarity=FactPolarity.SHOULD_LIST,
                position=0,
            )
        )
        db.add(ReviewTask(dataset_id=dataset.id, item_a_id=item.id))
    db.commit()
    yield dataset


def test_user_metrics_are_paginated_with_constant_query_count(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    representative_admin_data: Dataset,
) -> None:
    dataset_id = representative_admin_data.id
    assert dataset_id is not None

    small, small_queries = _get_with_select_count(
        client,
        f"{settings.API_V1_STR}/admin/metrics/users"
        f"?dataset_id={dataset_id}&offset=0&limit=1",
        superuser_token_headers,
    )
    volume, volume_queries = _get_with_select_count(
        client,
        f"{settings.API_V1_STR}/admin/metrics/users"
        f"?dataset_id={dataset_id}&offset=0&limit=100",
        superuser_token_headers,
    )

    assert small.status_code == 200
    assert volume.status_code == 200
    assert small.json()["limit"] == 1
    assert len(small.json()["items"]) == 1
    assert volume.json()["total"] == 126
    assert len(volume.json()["items"]) == 100
    assert volume.json()["next_offset"] == 100
    assert small_queries == volume_queries
    assert volume_queries == 11

    too_large = client.get(
        f"{settings.API_V1_STR}/admin/metrics/users?limit=501",
        headers=superuser_token_headers,
    )
    assert too_large.status_code == 422


def test_export_is_paginated_with_constant_query_count(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    representative_admin_data: Dataset,
) -> None:
    dataset_id = representative_admin_data.id
    assert dataset_id is not None

    small, small_queries = _get_with_select_count(
        client,
        f"{settings.API_V1_STR}/admin/export?dataset_id={dataset_id}&offset=0&limit=1",
        superuser_token_headers,
    )
    volume, volume_queries = _get_with_select_count(
        client,
        f"{settings.API_V1_STR}/admin/export"
        f"?dataset_id={dataset_id}&offset=25&limit=100",
        superuser_token_headers,
    )

    assert small.status_code == 200
    assert volume.status_code == 200
    assert small.json()["total"] == 125
    assert len(small.json()["items"]) == 1
    assert volume.json()["offset"] == 25
    assert len(volume.json()["items"]) == 100
    assert volume.json()["next_offset"] is None
    assert volume.json()["items"][0]["facts"][0]["fact_text"]
    assert volume.json()["items"][0]["review_task_count"] == 1
    assert small_queries == volume_queries
    assert volume_queries == 7

    too_large = client.get(
        f"{settings.API_V1_STR}/admin/export?limit=1001",
        headers=superuser_token_headers,
    )
    assert too_large.status_code == 422


def _get_with_select_count(
    client: TestClient,
    url: str,
    headers: dict[str, str],
) -> tuple[Response, int]:
    statements: list[str] = []

    def record_statement(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        _ = connection, cursor, parameters, context, executemany
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        response = client.get(url, headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
    return response, len(statements)
