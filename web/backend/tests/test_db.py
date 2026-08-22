from app.core.db import engine


def test_sqlite_connections_use_required_pragmas() -> None:
    with engine.connect() as connection:
        if connection.dialect.name != "sqlite":
            return

        journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
        busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
        foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()

    assert str(journal_mode).lower() == "wal"
    assert busy_timeout == 5000
    assert foreign_keys == 1
