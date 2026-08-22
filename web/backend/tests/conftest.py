# ruff: noqa: E402
# Isolate the test suite onto a throwaway database so running pytest never
# touches the dev database (data/app.db). This must run before any app module
# imports `settings`, since os.environ overrides the .env value.
import os
import tempfile

_TEST_DB_PATH = os.path.join(tempfile.gettempdir(), "amfv_test_app.db")
os.environ["SQLITE_DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"

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


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session, None, None]:
    # Start each run from a clean throwaway database.
    if os.path.exists(_TEST_DB_PATH):
        os.remove(_TEST_DB_PATH)

    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")

    with Session(engine) as session:
        init_db(session)
        yield session
        session.rollback()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )
