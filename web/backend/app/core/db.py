from sqlalchemy import event
from sqlmodel import Session, create_engine, select

from app import crud
from app.core.config import settings
from app.models import User, UserCreate

engine = create_engine(str(settings.SQLALCHEMY_DATABASE_URI))


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001, ARG001
    if engine.url.get_backend_name() != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


# Make sure all SQLModel models are imported before initializing DB metadata.


def init_db(session: Session) -> None:
    # Tables should be created with Alembic migrations
    # But if you don't want to use migrations, create
    # the tables un-commenting the next lines
    # from sqlmodel import SQLModel

    # This works because the models are already imported and registered from app.models
    # SQLModel.metadata.create_all(engine)

    user = session.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).first()
    if not user:
        user_in = UserCreate(
            email=settings.FIRST_SUPERUSER,
            password=settings.FIRST_SUPERUSER_PASSWORD.get_secret_value(),
            is_superuser=True,
        )
        user = crud.create_user(session=session, user_create=user_in)

    # Prepopulate the shared dataset that NICE webscrape documents land in.
    from app.services.nice import get_or_create_nice_dataset

    get_or_create_nice_dataset(session)
    session.commit()
