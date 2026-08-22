from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app import crud
from app.core.config import settings
from app.models import NiceImportJob, UserCreate, UserRole
from app.services import nice_import_scheduler
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_email, random_lower_string


def _role_headers(client: TestClient, db: Session, role: UserRole) -> dict[str, str]:
    password = random_lower_string()
    user = crud.create_user(
        session=db,
        user_create=UserCreate(email=random_email(), password=password, role=role),
    )
    return user_authentication_headers(client=client, email=user.email, password=password)


def _clear_jobs(db: Session) -> None:
    db.execute(delete(NiceImportJob))
    db.commit()


def test_admin_can_start_read_and_cancel_nice_import(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
) -> None:
    _clear_jobs(db)
    nudges: list[str] = []
    monkeypatch.setattr(nice_import_scheduler, "ensure_started", lambda: nudges.append("start"))
    monkeypatch.setattr(
        nice_import_scheduler,
        "nudge_if_unfinished",
        lambda session: nudges.append("status"),
    )

    start = client.post(
        f"{settings.API_V1_STR}/admin/nice-imports",
        headers=superuser_token_headers,
        json={"limit": 10},
    )
    assert start.status_code == 200
    data = start.json()
    assert data["status"] == "pending"
    assert data["requested_limit"] == "10"
    assert data["target_count"] == 10
    assert nudges == ["start"]

    current = client.get(
        f"{settings.API_V1_STR}/admin/nice-imports/current",
        headers=superuser_token_headers,
    )
    assert current.status_code == 200
    assert current.json()["id"] == data["id"]
    assert nudges == ["start", "status"]

    cancel = client.post(
        f"{settings.API_V1_STR}/admin/nice-imports/{data['id']}/cancel",
        headers=superuser_token_headers,
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    job = db.exec(select(NiceImportJob)).one()
    assert job.active_slot is None


def test_nice_import_denies_data_admin(
    client: TestClient,
    db: Session,
) -> None:
    _clear_jobs(db)
    headers = _role_headers(client, db, UserRole.data_admin)

    response = client.post(
        f"{settings.API_V1_STR}/admin/nice-imports",
        headers=headers,
        json={"limit": 10},
    )
    assert response.status_code == 403

    current = client.get(
        f"{settings.API_V1_STR}/admin/nice-imports/current",
        headers=headers,
    )
    assert current.status_code == 403

    cancel = client.post(
        f"{settings.API_V1_STR}/admin/nice-imports/1/cancel",
        headers=headers,
    )
    assert cancel.status_code == 403


def test_role_admin_can_start_nice_import(
    client: TestClient,
    db: Session,
    monkeypatch,
) -> None:
    _clear_jobs(db)
    monkeypatch.setattr(nice_import_scheduler, "ensure_started", lambda: None)
    headers = _role_headers(client, db, UserRole.admin)

    response = client.post(
        f"{settings.API_V1_STR}/admin/nice-imports",
        headers=headers,
        json={"limit": "all"},
    )
    assert response.status_code == 200
    assert response.json()["requested_limit"] == "all"
    assert response.json()["target_count"] is None


def test_start_nice_import_rejects_second_active_job(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
) -> None:
    _clear_jobs(db)
    monkeypatch.setattr(nice_import_scheduler, "ensure_started", lambda: None)

    first = client.post(
        f"{settings.API_V1_STR}/admin/nice-imports",
        headers=superuser_token_headers,
        json={"limit": 10},
    )
    assert first.status_code == 200

    second = client.post(
        f"{settings.API_V1_STR}/admin/nice-imports",
        headers=superuser_token_headers,
        json={"limit": 20},
    )
    assert second.status_code == 409
