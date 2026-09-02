import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine

from app.models import (
    Dataset,
    EvalFact,
    EvalItem,
    EvalType,
    FactPolarity,
    ItemSource,
    ItemStatus,
)
from app.services.rubrics import validate_fact_decomp_ratings


def test_fact_decomp_requires_call_for_every_fact() -> None:
    item = EvalItem(
        dataset_id=1,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        prompt_text="Statement.",
        status=ItemStatus.ACTIVE,
    )
    facts = [
        EvalFact(
            item_id=1,
            fact_text="A",
            polarity=FactPolarity.SHOULD_LIST,
            position=0,
        ),
        EvalFact(
            item_id=1,
            fact_text="B",
            polarity=FactPolarity.SHOULD_NOT_LIST,
            position=1,
        ),
    ]

    with pytest.raises(ValueError, match="exactly one call per fact"):
        validate_fact_decomp_ratings(
            item,
            facts,
            fact_calls=["SHOULD_LIST"],
            values={
                "independently_verifiable": "pass",
                "noise_removed": "pass",
                "deduplicated_ordered": "pass",
            },
        )


def test_fact_decomp_derives_fact_agreement() -> None:
    item = EvalItem(
        dataset_id=1,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        prompt_text="Statement.",
        status=ItemStatus.ACTIVE,
    )
    facts = [
        EvalFact(
            item_id=1,
            fact_text="A",
            polarity=FactPolarity.SHOULD_LIST,
            position=0,
        ),
        EvalFact(
            item_id=1,
            fact_text="B",
            polarity=FactPolarity.SHOULD_NOT_LIST,
            position=1,
        ),
    ]

    ratings = validate_fact_decomp_ratings(
        item,
        facts,
        fact_calls=["SHOULD_LIST", "SHOULD_LIST"],
        values={
            "independently_verifiable": "pass",
            "noise_removed": "minor_issue",
            "deduplicated_ordered": "fail",
        },
    )

    assert ratings["fact_agreement"] == ["agree", "disagree"]


def test_eval_fact_positions_are_unique_per_item() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        dataset = Dataset(
            name="fact-position-unique",
            display_name="Fact Position Unique",
            eval_type=EvalType.FACT_DECOMP,
        )
        item = EvalItem(
            dataset_id=1,
            eval_type=EvalType.FACT_DECOMP,
            source=ItemSource.HUMAN,
            prompt_text="Statement.",
            status=ItemStatus.ACTIVE,
        )
        session.add(dataset)
        session.flush()
        item.dataset_id = dataset.id
        session.add(item)
        session.flush()
        session.add_all(
            [
                EvalFact(
                    item_id=item.id,
                    fact_text="A",
                    polarity=FactPolarity.SHOULD_LIST,
                    position=0,
                ),
                EvalFact(
                    item_id=item.id,
                    fact_text="B",
                    polarity=FactPolarity.SHOULD_NOT_LIST,
                    position=0,
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            session.flush()
