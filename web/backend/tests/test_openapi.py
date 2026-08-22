"""Verify that runtime mode cannot change the public API contract."""

from typing import Literal

from app.core.config import Settings
from app.main import create_app


def _settings(environment: Literal["local", "production"]) -> Settings:
    return Settings.model_validate(
        {
            "ENVIRONMENT": environment,
            "FIRST_SUPERUSER": "openapi@example.com",
            "FIRST_SUPERUSER_PASSWORD": "openapi-test-password",
            "PROJECT_NAME": "AMFV Web",
            "SECRET_KEY": "openapi-test-secret-key-at-least-32-bytes",
            "SQLITE_DATABASE_URL": "sqlite:////tmp/amfv-openapi-unused.db",
        }
    )


def test_public_openapi_is_environment_independent() -> None:
    local_schema = create_app(_settings("local")).openapi()
    production_schema = create_app(_settings("production")).openapi()

    assert local_schema == production_schema
    assert "/api/v1/private/users/" not in local_schema["paths"]
