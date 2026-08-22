import pytest
from sqlmodel import Session, col, func, select

from app.models import User


@pytest.mark.parametrize("marker", ["first", "second"])
def test_database_state_is_fresh_for_every_test(db: Session, marker: str) -> None:
    """Prove one test's commit cannot enter the next test database."""
    assert db.exec(select(func.count(col(User.id)))).one() == 1
    db.add(User(email=f"isolation-{marker}@example.com", hashed_password="not-used"))
    db.commit()
    assert db.exec(select(func.count(col(User.id)))).one() == 2
