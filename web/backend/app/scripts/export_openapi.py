"""Export the public OpenAPI contract with deterministic test settings."""

import argparse
import json
import os
from pathlib import Path

_EXPORT_ENVIRONMENT = {
    "ENVIRONMENT": "production",
    "FIRST_SUPERUSER": "openapi@example.com",
    "FIRST_SUPERUSER_PASSWORD": "openapi-test-password",
    "PROJECT_NAME": "AMFV Web",
    "SECRET_KEY": "openapi-test-secret-key-at-least-32-bytes",
    "SQLITE_DATABASE_URL": "sqlite:////tmp/amfv-openapi-unused.db",
}

os.environ.update(_EXPORT_ENVIRONMENT)

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def export_openapi(output: Path) -> None:
    """Write a stable public OpenAPI document to ``output``."""
    export_settings = Settings.model_validate(_EXPORT_ENVIRONMENT)
    schema = create_app(export_settings).openapi()
    output.write_text(
        json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """Parse command-line arguments and export the OpenAPI document."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export_openapi(args.output)


if __name__ == "__main__":
    main()
