from sqlmodel import Session, SQLModel, create_engine

from app.models import (
    Dataset,
    EvalItem,
    EvalType,
    FactDecompReview,
    ItemSource,
    ItemStatus,
    ReviewerKind,
    ReviewTask,
    User,
)
from app.services.agreement import (
    cohen_kappa,
    dataset_agreement,
    krippendorff_alpha_nominal,
)


def test_cohen_kappa_hand_computed_fixture() -> None:
    left = {"a": "yes", "b": "yes", "c": "no", "d": "no"}
    right = {"a": "yes", "b": "no", "c": "no", "d": "no"}

    kappa, n = cohen_kappa(left, right)

    assert n == 4
    assert round(kappa, 3) == 0.5


def test_krippendorff_alpha_nominal_perfect_agreement() -> None:
    alpha, n = krippendorff_alpha_nominal(
        {
            "item-1": {1: "keep", 2: "keep"},
            "item-2": {1: "reject", 2: "reject"},
        }
    )

    assert n == 2
    assert alpha == 1.0


def test_dataset_agreement_excludes_stale_reviews() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        dataset = Dataset(name="d", display_name="D", eval_type=EvalType.RETRIEVAL)
        session.add(dataset)
        session.flush()
        item = EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.RETRIEVAL,
            source=ItemSource.LLM,
            prompt_text="Question?",
            status=ItemStatus.ACTIVE,
            revision=2,
        )
        session.add(item)
        session.flush()
        task = ReviewTask(dataset_id=dataset.id, item_a_id=item.id)
        session.add(task)
        session.flush()
        first_user = User(email="first@example.com", hashed_password="x")
        second_user = User(email="second@example.com", hashed_password="x")
        session.add(first_user)
        session.add(second_user)
        session.flush()
        session.add(
            FactDecompReview(
                task_id=task.id,
                user_id=first_user.id,
                item_revision=2,
                reviewer_kind=ReviewerKind.human,
                ratings={"verdict": "keep"},
            )
        )
        session.add(
            FactDecompReview(
                task_id=task.id,
                user_id=second_user.id,
                item_revision=1,
                reviewer_kind=ReviewerKind.human,
                ratings={"verdict": "reject"},
            )
        )
        session.commit()

        summaries = dataset_agreement(session, dataset.id)

    assert summaries == []
