from unittest.mock import MagicMock, patch

from sqlmodel import select

from app.scripts import bootstrap


def test_wait_for_database_executes_probe_query() -> None:
    engine_mock = MagicMock()
    session_mock = MagicMock()
    session_mock.__enter__.return_value = session_mock
    select1 = select(1)

    with (
        patch.object(bootstrap, "Session", return_value=session_mock),
        patch.object(bootstrap, "select", return_value=select1),
    ):
        bootstrap.wait_for_database(engine_mock)

    session_mock.exec.assert_called_once_with(select1)


def test_seed_initial_data_initializes_database() -> None:
    session_mock = MagicMock()
    session_mock.__enter__.return_value = session_mock

    with (
        patch.object(bootstrap, "Session", return_value=session_mock),
        patch.object(bootstrap, "init_db") as init_db,
    ):
        bootstrap.seed_initial_data()

    init_db.assert_called_once_with(session_mock)
