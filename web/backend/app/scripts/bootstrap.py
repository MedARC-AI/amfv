"""Small startup commands for waiting on and seeding the application database."""

import argparse
import logging
from collections.abc import Sequence

from sqlalchemy import Engine
from sqlmodel import Session, select
from tenacity import after_log, before_log, retry, stop_after_attempt, wait_fixed

from app.core.db import engine, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_TRIES = 60 * 5  # 5 minutes
WAIT_SECONDS = 1


@retry(
    stop=stop_after_attempt(MAX_TRIES),
    wait=wait_fixed(WAIT_SECONDS),
    before=before_log(logger, logging.INFO),
    after=after_log(logger, logging.WARNING),
)
def wait_for_database(db_engine: Engine) -> None:
    """Wait until a database connection can execute a trivial query."""
    try:
        with Session(db_engine) as session:
            session.exec(select(1))
    except Exception as exc:  # noqa: BLE001
        logger.error(exc)
        raise


def seed_initial_data() -> None:
    """Create the initial application data when it is not present."""
    with Session(engine) as session:
        init_db(session)


def main(argv: Sequence[str] | None = None) -> None:
    """Run one of the narrow database bootstrap commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("wait-for-database", "seed-initial-data"),
    )
    args = parser.parse_args(argv)

    if args.command == "wait-for-database":
        logger.info("Initializing service")
        wait_for_database(engine)
        logger.info("Service finished initializing")
    else:
        logger.info("Creating initial data")
        seed_initial_data()
        logger.info("Initial data created")


if __name__ == "__main__":
    main()
