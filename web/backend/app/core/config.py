import warnings
from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    BeforeValidator,
    EmailStr,
    Field,
    HttpUrl,
    SecretStr,
    computed_field,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self

EXAMPLE_SECRET_KEYS = frozenset(
    {
        "changethis",
        "dev-only-secret-key-at-least-32-bytes",
    }
)
EXAMPLE_PASSWORDS = frozenset({"changethis"})


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: SecretStr
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS] + [
            self.FRONTEND_HOST
        ]

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    SQLITE_DATABASE_URL: str = "sqlite:///../data/app.db"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        return self.SQLITE_DATABASE_URL

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: SecretStr | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: SecretStr
    AUTH_RATE_LIMIT_ENABLED: bool = True
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = Field(default=300, ge=1)
    AUTH_RATE_LIMIT_LOGIN_IP_ATTEMPTS: int = Field(default=100, ge=1)
    AUTH_RATE_LIMIT_LOGIN_ACCOUNT_FAILURES: int = Field(default=20, ge=1)
    AUTH_RATE_LIMIT_PASSWORD_RECOVERY_IP_ATTEMPTS: int = Field(default=30, ge=1)
    AUTH_RATE_LIMIT_PASSWORD_RECOVERY_EMAIL_ATTEMPTS: int = Field(default=5, ge=1)
    AUTH_RATE_LIMIT_INVITE_IP_ATTEMPTS: int = Field(default=100, ge=1)
    AUTH_RATE_LIMIT_MAX_BUCKETS: int = Field(default=10_000, ge=100)
    AUTH_RATE_LIMIT_IDENTITY_MAX_LENGTH: int = Field(default=256, ge=32)
    AUTH_RATE_LIMIT_TRUSTED_PROXY_IPS: str = "127.0.0.1,::1"
    AUTH_RATE_LIMIT_CLIENT_IP_HEADER: str = ""

    def _check_example_secret(
        self,
        var_name: str,
        value: str | SecretStr | None,
        example_values: frozenset[str],
    ) -> None:
        secret_value = (
            value.get_secret_value() if isinstance(value, SecretStr) else value
        )
        if secret_value in example_values:
            message = f"{var_name} uses a known example value; configure a unique deployment secret."
            if self.ENVIRONMENT == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_example_secret("SECRET_KEY", self.SECRET_KEY, EXAMPLE_SECRET_KEYS)
        self._check_example_secret(
            "FIRST_SUPERUSER_PASSWORD",
            self.FIRST_SUPERUSER_PASSWORD,
            EXAMPLE_PASSWORDS,
        )

        return self


settings = Settings()
