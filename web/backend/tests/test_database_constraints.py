"""Check database invariants formerly covered by historical migration tests."""

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import (
    Assignment,
    AssignmentMode,
    Dataset,
    EvalItem,
    EvalType,
    FactDecompSaveReceipt,
    ItemSource,
    ItemStatus,
    User,
)


def test_database_enforces_receipt_identity_and_single_terminal_state(
    db: Session,
) -> None:
    user = db.exec(select(User)).one()
    dataset = Dataset(
        name="constraints", display_name="Constraints", eval_type=EvalType.FACT_DECOMP
    )
    db.add(dataset)
    db.flush()
    item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=user.id,
        prompt_text="Source text",
        status=ItemStatus.DRAFT,
    )
    db.add(item)
    db.flush()
    assignment = Assignment(
        dataset_id=dataset.id,
        mode=AssignmentMode.ITEM_AUDIT,
        target_id=item.id,
        user_id=user.id,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(assignment)
    receipt_fields = {
        "author_user_id": user.id,
        "request_id": "saved-request",
        "request_hash": "a" * 64,
        "command": "draft",
        "item_id": item.id,
        "request_payload": {"source_text": "Source text"},
        "response_payload": {"id": item.id},
    }
    db.add(FactDecompSaveReceipt(**receipt_fields))
    db.commit()

    db.add(FactDecompSaveReceipt(**receipt_fields))
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
        db.flush()
    db.rollback()

    assignment.released_at = datetime.now(timezone.utc)
    db.add(assignment)
    with pytest.raises(IntegrityError, match="ck_assignment_single_terminal_state"):
        db.flush()
    db.rollback()
    assert assignment.completed_at is not None
    assert assignment.released_at is None
