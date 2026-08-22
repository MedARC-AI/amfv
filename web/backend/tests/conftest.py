# ruff: noqa: E402
# Isolate the test suite onto a throwaway database so running pytest never
# touches the dev database (data/app.db). This must run before any app module
# imports `settings`, since os.environ overrides the .env value.
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

_TEST_DB_DIRECTORY = Path(tempfile.mkdtemp(prefix=f"amfv-pytest-{os.getpid()}-"))
_TEST_DB_PATH = _TEST_DB_DIRECTORY / "test.db"
_TEST_DB_TEMPLATE_PATH = _TEST_DB_DIRECTORY / "template.db"
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
os.environ["SQLITE_DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
os.environ["PROJECT_NAME"] = "AMFV Web Tests"
os.environ["SECRET_KEY"] = "test-only-secret-key-at-least-32-bytes"
os.environ["FIRST_SUPERUSER"] = "admin@example.com"
os.environ["FIRST_SUPERUSER_PASSWORD"] = "test-only-superuser-password"
os.environ["ENVIRONMENT"] = "local"

from collections.abc import Generator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine, init_db
from app.main import app
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers


@pytest.fixture(scope="session")
def _database_template() -> Generator[Path, None, None]:
    """Build one migrated template for this pytest process."""
    alembic_cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")
    with Session(engine) as session:
        init_db(session)

    engine.dispose()
    with sqlite3.connect(_TEST_DB_PATH) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copyfile(_TEST_DB_PATH, _TEST_DB_TEMPLATE_PATH)
    yield _TEST_DB_TEMPLATE_PATH
    engine.dispose()
    shutil.rmtree(_TEST_DB_DIRECTORY)


@pytest.fixture(autouse=True)
def db(_database_template: Path) -> Generator[Session, None, None]:
    """Give every test a fresh database and direct session."""
    engine.dispose()
    for suffix in ("", "-shm", "-wal"):
        Path(f"{_TEST_DB_PATH}{suffix}").unlink(missing_ok=True)
    shutil.copyfile(_database_template, _TEST_DB_PATH)

    with Session(engine) as session:
        yield session
        session.rollback()
    engine.dispose()


@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    _ = db
    with TestClient(app) as c:
        yield c


@pytest.fixture
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )
