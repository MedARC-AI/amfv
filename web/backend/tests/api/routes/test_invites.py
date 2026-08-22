from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.api.deps import get_current_data_user
from app.core.config import settings
from app.models import SignupInvite, User, UserRole
from app.services.rate_limit import rate_limiter
from tests.utils.utils import random_email, random_lower_string


def test_create_preview_and_redeem_invite(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    invite_data = {"role": "data_admin", "max_redemptions": 1}
    r = client.post(
        f"{settings.API_V1_STR}/auth/invites",
        headers=superuser_token_headers,
        json=invite_data,
    )
    assert r.status_code == 200
    created_invite = r.json()
    assert created_invite["token"]
    assert "token_hash" not in created_invite

    preview = client.get(
        f"{settings.API_V1_STR}/auth/invites/{created_invite['token']}"
    )
    assert preview.status_code == 404
    preview = client.post(
        f"{settings.API_V1_STR}/auth/invites/preview",
        json={"token": created_invite["token"]},
    )
    assert preview.status_code == 200
    assert preview.json()["role"] == "data_admin"
    assert preview.json()["remaining_redemptions"] == 1

    password = random_lower_string()
    signup = client.post(
        f"{settings.API_V1_STR}/auth/signup",
        json={
            "invite_token": created_invite["token"],
            "email": random_email(),
            "password": password,
            "full_name": "Invited User",
            "discord_handle": "invited-user",
            "medical_profession": "Nurse practitioner",
        },
    )
    assert signup.status_code == 200
    token = signup.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get(f"{settings.API_V1_STR}/users/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["role"] == "data_admin"
    assert me.json()["reviewer_kind"] == "human"
    assert me.json()["discord_handle"] == "invited-user"
    assert me.json()["medical_profession"] == "Nurse practitioner"

    invite = db.exec(
        select(SignupInvite).where(SignupInvite.id == UUID(created_invite["id"]))
    ).one()
    assert invite.redeemed_count == 1


def test_invite_preview_rate_limit_by_ip(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    created_invite = client.post(
        f"{settings.API_V1_STR}/auth/invites",
        headers=superuser_token_headers,
        json={},
    ).json()
    rate_limiter.clear()
    try:
        with (
            patch("app.core.config.settings.AUTH_RATE_LIMIT_WINDOW_SECONDS", 60),
            patch("app.core.config.settings.AUTH_RATE_LIMIT_INVITE_IP_ATTEMPTS", 2),
        ):
            first = client.post(
                f"{settings.API_V1_STR}/auth/invites/preview",
                json={"token": created_invite["token"]},
            )
            second = client.post(
                f"{settings.API_V1_STR}/auth/invites/preview",
                json={"token": created_invite["token"]},
            )
            limited = client.post(
                f"{settings.API_V1_STR}/auth/invites/preview",
                json={"token": created_invite["token"]},
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert limited.status_code == 429
        assert "Retry-After" in limited.headers
    finally:
        rate_limiter.clear()


def test_public_signup_endpoint_disabled(client: TestClient) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/users/signup",
        json={
            "invite_token": "not-a-real-invite-token",
            "email": random_email(),
            "password": random_lower_string(),
            "full_name": "No Invite",
        },
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "Signup requires an invite"}


def test_invite_cannot_be_reused_after_redemption(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.post(
        f"{settings.API_V1_STR}/auth/invites",
        headers=superuser_token_headers,
        json={},
    )
    token = r.json()["token"]
    first = client.post(
        f"{settings.API_V1_STR}/auth/signup",
        json={
            "invite_token": token,
            "email": random_email(),
            "password": random_lower_string(),
        },
    )
    assert first.status_code == 200

    second = client.post(
        f"{settings.API_V1_STR}/auth/signup",
        json={
            "invite_token": token,
            "email": random_email(),
            "password": random_lower_string(),
        },
    )
    assert second.status_code == 404
    assert second.json() == {"detail": "Invite unavailable"}


def test_disabled_and_expired_invites_are_not_redeemable(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    disabled = client.post(
        f"{settings.API_V1_STR}/auth/invites",
        headers=superuser_token_headers,
        json={},
    ).json()
    r = client.post(
        f"{settings.API_V1_STR}/auth/invites/{disabled['id']}/disable",
        headers=superuser_token_headers,
    )
    assert r.status_code == 200

    preview = client.post(
        f"{settings.API_V1_STR}/auth/invites/preview",
        json={"token": disabled["token"]},
    )
    assert preview.status_code == 404

    expired = client.post(
        f"{settings.API_V1_STR}/auth/invites",
        headers=superuser_token_headers,
        json={
            "expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        },
    ).json()
    signup = client.post(
        f"{settings.API_V1_STR}/auth/signup",
        json={
            "invite_token": expired["token"],
            "email": random_email(),
            "password": random_lower_string(),
        },
    )
    assert signup.status_code == 404
    assert signup.json() == {"detail": "Invite unavailable"}


def test_all_unavailable_invite_states_have_the_same_public_result(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    disabled = client.post(
        f"{settings.API_V1_STR}/auth/invites",
        headers=superuser_token_headers,
        json={},
    ).json()
    client.post(
        f"{settings.API_V1_STR}/auth/invites/{disabled['id']}/disable",
        headers=superuser_token_headers,
    )
    expired = client.post(
        f"{settings.API_V1_STR}/auth/invites",
        headers=superuser_token_headers,
        json={
            "expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        },
    ).json()
    exhausted = client.post(
        f"{settings.API_V1_STR}/auth/invites",
        headers=superuser_token_headers,
        json={},
    ).json()
    first_redemption = client.post(
        f"{settings.API_V1_STR}/auth/signup",
        json={
            "invite_token": exhausted["token"],
            "email": random_email(),
            "password": random_lower_string(),
        },
    )
    assert first_redemption.status_code == 200

    tokens = [
        "invalid",
        disabled["token"],
        expired["token"],
        exhausted["token"],
    ]
    for token in tokens:
        preview = client.post(
            f"{settings.API_V1_STR}/auth/invites/preview", json={"token": token}
        )
        signup = client.post(
            f"{settings.API_V1_STR}/auth/signup",
            json={
                "invite_token": token,
                "email": random_email(),
                "password": random_lower_string(),
            },
        )
        assert preview.status_code == HTTPStatus.NOT_FOUND
        assert signup.status_code == HTTPStatus.NOT_FOUND
        assert preview.json() == signup.json() == {"detail": "Invite unavailable"}


@pytest.mark.parametrize("max_redemptions", [1, 7], ids=["single-use", "multi-use"])
def test_twenty_concurrent_redemptions_never_exceed_limit(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    max_redemptions: int,
) -> None:
    created = client.post(
        f"{settings.API_V1_STR}/auth/invites",
        headers=superuser_token_headers,
        json={"max_redemptions": max_redemptions},
    ).json()
    emails = [random_email() for _ in range(20)]

    def redeem(email: str) -> int:
        response = client.post(
            f"{settings.API_V1_STR}/auth/signup",
            json={
                "invite_token": created["token"],
                "email": email,
                "password": "concurrent-test-password",
            },
        )
        return response.status_code

    with (
        patch("app.services.invites.get_password_hash", return_value="test-only-hash"),
        ThreadPoolExecutor(max_workers=20) as executor,
    ):
        statuses = list(executor.map(redeem, emails))

    db.expire_all()
    invite = db.get(SignupInvite, UUID(created["id"]))
    user_count = len(db.exec(select(User).where(col(User.email).in_(emails))).all())
    assert invite is not None
    assert invite.redeemed_count == max_redemptions
    assert user_count == max_redemptions
    assert statuses.count(HTTPStatus.OK) == max_redemptions
    assert statuses.count(HTTPStatus.NOT_FOUND) == 20 - max_redemptions


def test_duplicate_user_rollback_does_not_consume_invite_capacity(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    created = client.post(
        f"{settings.API_V1_STR}/auth/invites",
        headers=superuser_token_headers,
        json={},
    ).json()
    duplicate = client.post(
        f"{settings.API_V1_STR}/auth/signup",
        json={
            "invite_token": created["token"],
            "email": settings.FIRST_SUPERUSER,
            "password": random_lower_string(),
        },
    )
    assert duplicate.status_code == HTTPStatus.BAD_REQUEST

    db.expire_all()
    invite = db.get(SignupInvite, UUID(created["id"]))
    assert invite is not None
    assert invite.redeemed_count == 0

    successful = client.post(
        f"{settings.API_V1_STR}/auth/signup",
        json={
            "invite_token": created["token"],
            "email": random_email(),
            "password": random_lower_string(),
        },
    )
    assert successful.status_code == HTTPStatus.OK

    db.expire_all()
    redeemed_invite = db.get(SignupInvite, UUID(created["id"]))
    assert redeemed_invite is not None
    assert redeemed_invite.redeemed_count == 1


def test_list_invites_returns_created_invites_newest_first(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    first = client.post(
        f"{settings.API_V1_STR}/auth/invites",
        headers=superuser_token_headers,
        json={"role": "user"},
    ).json()
    second = client.post(
        f"{settings.API_V1_STR}/auth/invites",
        headers=superuser_token_headers,
        json={"role": "data_admin", "max_redemptions": 3},
    ).json()

    r = client.get(
        f"{settings.API_V1_STR}/auth/invites",
        headers=superuser_token_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 2
    ids = [item["id"] for item in body["data"]]
    assert first["id"] in ids
    assert second["id"] in ids
    # Newest first: second was created after first.
    assert ids.index(second["id"]) < ids.index(first["id"])
    # The stored token hash is never exposed.
    assert all(
        "token" not in item and "token_hash" not in item for item in body["data"]
    )


def test_list_invites_requires_superuser(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.get(
        f"{settings.API_V1_STR}/auth/invites",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 403


def test_capability_helpers_accept_amfv_roles() -> None:
    data_user = User(
        email=random_email(), hashed_password="x", role=UserRole.data_admin
    )
    regular_user = User(email=random_email(), hashed_password="x", role=UserRole.user)

    assert get_current_data_user(data_user) == data_user
    with pytest.raises(HTTPException):
        get_current_data_user(regular_user)
