"""Regression coverage for the consolidated initial schema."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.core.config import settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_fresh_migration_head_matches_model_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'alembic-drift.db'}"
    monkeypatch.setattr(settings, "SQLITE_DATABASE_URL", database_url)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))

    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, "base")
    engine = sa.create_engine(database_url)
    try:
        assert set(sa.inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()
    command.upgrade(config, "head")
    command.check(config)
