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
    dataset_judgments_for_all,
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


def test_fact_call_agreement_observations_are_task_scoped() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        dataset = Dataset(
            name="task-scoped-facts",
            display_name="Task Scoped Facts",
            eval_type=EvalType.FACT_DECOMP,
        )
        session.add(dataset)
        session.flush()
        first_item = EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.FACT_DECOMP,
            source=ItemSource.LLM,
            prompt_text="First statement.",
            status=ItemStatus.ACTIVE,
        )
        second_item = EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.FACT_DECOMP,
            source=ItemSource.LLM,
            prompt_text="Second statement.",
            status=ItemStatus.ACTIVE,
        )
        first_user = User(email="task-scoped-first@example.com", hashed_password="x")
        second_user = User(email="task-scoped-second@example.com", hashed_password="x")
        session.add_all([first_item, second_item, first_user, second_user])
        session.flush()
        first_task = ReviewTask(dataset_id=dataset.id, item_a_id=first_item.id)
        second_task = ReviewTask(dataset_id=dataset.id, item_a_id=second_item.id)
        session.add_all([first_task, second_task])
        session.flush()
        first_task_id = first_task.id
        second_task_id = second_task.id
        first_user_id = first_user.id
        second_user_id = second_user.id
        session.add_all(
            [
                FactDecompReview(
                    task_id=first_task.id,
                    user_id=first_user.id,
                    item_revision=first_item.revision,
                    reviewer_kind=ReviewerKind.human,
                    ratings={"fact_calls": ["SHOULD_LIST"]},
                ),
                FactDecompReview(
                    task_id=second_task.id,
                    user_id=second_user.id,
                    item_revision=second_item.revision,
                    reviewer_kind=ReviewerKind.human,
                    ratings={"fact_calls": ["SHOULD_NOT_LIST"]},
                ),
            ]
        )
        session.commit()

        judgments = dataset_judgments_for_all(session, dataset_id=dataset.id)

    observations = judgments["fact_call"]
    assert observations == {
        f"task:{first_task_id}:fact:0": {first_user_id: "SHOULD_LIST"},
        f"task:{second_task_id}:fact:0": {second_user_id: "SHOULD_NOT_LIST"},
    }


def test_dataset_agreement_excludes_model_correction_reviews() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        dataset = Dataset(
            name="model-correction-agreement",
            display_name="Model correction agreement",
            eval_type=EvalType.FACT_DECOMP,
        )
        reviewers = [
            User(email=f"correction-reviewer-{index}@example.com", hashed_password="x")
            for index in range(2)
        ]
        item = EvalItem(
            dataset_id=1,
            eval_type=EvalType.FACT_DECOMP,
            source=ItemSource.LLM,
            prompt_text="Alpha.",
            item_metadata={
                "schema_version": 1,
                "review_mode": "MODEL_LABEL_CORRECTION",
                "case_id": "case-1",
                "arm_id": "arm-1",
                "canonical_row_sha256": "a" * 64,
                "generator": {
                    "model_id": "model",
                    "prompt_id": "prompt-1",
                    "prompt_hash": "b" * 64,
                    "pydantic_ai_version": "2.33.0",
                    "generation": {},
                },
                "ordered_claim_annotations": [],
            },
            status=ItemStatus.ACTIVE,
        )
        session.add_all([dataset, *reviewers])
        session.flush()
        item.dataset_id = dataset.id
        session.add(item)
        session.flush()
        task = ReviewTask(dataset_id=dataset.id, item_a_id=item.id)
        session.add(task)
        session.flush()
        session.add_all(
            [
                FactDecompReview(
                    task_id=task.id,
                    user_id=reviewer.id,
                    item_revision=item.revision,
                    reviewer_kind=ReviewerKind.human,
                    source="web_model_eval",
                    ratings={
                        "review_mode": "MODEL_LABEL_CORRECTION",
                        "proposed_labels": [],
                        "final_claims": [],
                    },
                )
                for reviewer in reviewers
            ]
        )
        session.commit()

        judgments = dataset_judgments_for_all(
            session, dataset_id=dataset.id, max_rows=1
        )
        assert judgments == {}

        authored_item = EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.FACT_DECOMP,
            source=ItemSource.HUMAN,
            prompt_text="Authored response.",
            status=ItemStatus.ACTIVE,
        )
        session.add(authored_item)
        session.flush()
        authored_task = ReviewTask(dataset_id=dataset.id, item_a_id=authored_item.id)
        session.add(authored_task)
        session.flush()
        assert authored_task.id is not None
        authored_task_id = authored_task.id
        reviewer_id = reviewers[0].id
        session.add(
            FactDecompReview(
                task_id=authored_task.id,
                user_id=reviewers[0].id,
                item_revision=authored_item.revision,
                reviewer_kind=ReviewerKind.human,
                source="web",
                ratings={"atomicity": "pass"},
            )
        )
        session.commit()

        judgments = dataset_judgments_for_all(
            session, dataset_id=dataset.id, max_rows=1
        )

    assert judgments == {
        "atomicity": {f"task:{authored_task_id}": {reviewer_id: "pass"}}
    }
