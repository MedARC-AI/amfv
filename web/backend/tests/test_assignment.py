from sqlmodel import Session, SQLModel, create_engine

from app.models import (
    Assignment,
    AssignmentMode,
    Dataset,
    EvalItem,
    EvalType,
    ItemSource,
    ItemStatus,
    User,
)
from app.services.assignment import complete_assignment, select_assignment


def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_audit_calibration_completion_uses_uuid_user_id() -> None:
    with session() as db:
        user = User(email="user@example.com", hashed_password="x")
        dataset = Dataset(
            name="retrieval",
            display_name="Retrieval",
            eval_type=EvalType.RETRIEVAL,
        )
        db.add(user)
        db.add(dataset)
        db.flush()
        calibration_item = EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.RETRIEVAL,
            source=ItemSource.LLM,
            prompt_text="Calibration?",
            status=ItemStatus.ACTIVE,
            is_calibration=True,
        )
        regular_item = EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.RETRIEVAL,
            source=ItemSource.LLM,
            prompt_text="Regular?",
            status=ItemStatus.ACTIVE,
            is_calibration=False,
        )
        db.add(calibration_item)
        db.add(regular_item)
        db.flush()
        completed = Assignment(
            dataset_id=dataset.id,
            mode=AssignmentMode.ITEM_AUDIT,
            target_id=calibration_item.id,
            user_id=user.id,
        )
        db.add(completed)
        db.flush()
        complete_assignment(db, completed)
        db.commit()

        selected = select_assignment(db, user, AssignmentMode.ITEM_AUDIT)

        assert selected is not None
        assert selected.target_id == regular_item.id
