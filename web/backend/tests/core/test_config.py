"""Security-sensitive configuration tests."""

from typing import Literal

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings


def _settings_data(
    environment: Literal["local", "staging", "production"],
) -> dict[str, str]:
    return {
        "ENVIRONMENT": environment,
        "FIRST_SUPERUSER": "config-test@example.com",
        "FIRST_SUPERUSER_PASSWORD": "unique-config-test-password",
        "PROJECT_NAME": "AMFV Web",
        "SECRET_KEY": "unique-config-test-secret-key-at-least-32-bytes",
        "SQLITE_DATABASE_URL": "sqlite:////tmp/amfv-config-unused.db",
    }


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize(
    ("field", "example_value"),
    [
        ("SECRET_KEY", "changethis"),
        ("SECRET_KEY", "dev-only-secret-key-at-least-32-bytes"),
        ("FIRST_SUPERUSER_PASSWORD", "changethis"),
    ],
)
def test_non_local_environment_rejects_known_example_secrets(
    environment: Literal["staging", "production"],
    field: str,
    example_value: str,
) -> None:
    data = _settings_data(environment)
    data[field] = example_value

    with pytest.raises(ValidationError, match="known example value"):
        Settings.model_validate(data)


def test_signing_secret_is_redacted_by_settings_representation() -> None:
    secret = "unique-secret-that-must-not-appear-in-repr"
    password = "unique-password-that-must-not-appear-in-repr"
    data = _settings_data("production")
    data["SECRET_KEY"] = secret
    data["FIRST_SUPERUSER_PASSWORD"] = password

    configured = Settings.model_validate(data)

    assert isinstance(configured.SECRET_KEY, SecretStr)
    assert isinstance(configured.FIRST_SUPERUSER_PASSWORD, SecretStr)
    assert configured.SECRET_KEY.get_secret_value() == secret
    assert configured.FIRST_SUPERUSER_PASSWORD.get_secret_value() == password
    assert secret not in repr(configured)
    assert password not in repr(configured)


def test_secret_validation_error_does_not_render_configuration_inputs() -> None:
    password = "password-that-must-not-appear-in-validation-error"
    data = _settings_data("production")
    data["SECRET_KEY"] = "changethis"
    data["FIRST_SUPERUSER_PASSWORD"] = password

    with pytest.raises(ValidationError) as exc_info:
        Settings.model_validate(data)

    assert "changethis" not in str(exc_info.value)
    assert password not in str(exc_info.value)
