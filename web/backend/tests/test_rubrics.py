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
            duplicate_flags=[False, False],
            looks_good=[True, True],
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
        duplicate_flags=[False, False],
        looks_good=[True, True],
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


@pytest.mark.parametrize(
    ("calls", "duplicates", "accepted"),
    [
        (["SHOULD_LIST"], [False], [False]),
        (["SHOULD_LIST"], [], [True]),
        (["SHOULD_LIST"], [True], [True]),
        (["MALFORMED"], [False], [True]),
    ],
    ids=["untouched", "missing-flag", "conflicting-decision", "malformed-accepted"],
)
def test_fact_decomp_rejects_incomplete_decisions(calls, duplicates, accepted):
    item = EvalItem(
        dataset_id=1,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        prompt_text="A",
    )
    facts = [
        EvalFact(
            item_id=1, fact_text="A", polarity=FactPolarity.SHOULD_LIST, position=0
        )
    ]
    with pytest.raises(ValueError):
        validate_fact_decomp_ratings(
            item,
            facts,
            fact_calls=calls,
            duplicate_flags=duplicates,
            looks_good=accepted,
            values={
                "independently_verifiable": "pass",
                "noise_removed": "pass",
                "deduplicated_ordered": "pass",
            },
        )


@pytest.mark.parametrize(
    "flags, accepted, valid",
    [([True], [False], True), ([True], [True], False), ([], [False], False)],
)
def test_multiple_facts_decision(flags, accepted, valid):
    item = EvalItem(
        dataset_id=1,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        prompt_text="A and B",
    )
    facts = [
        EvalFact(
            item_id=1,
            fact_text="A and B",
            polarity=FactPolarity.SHOULD_LIST,
            position=0,
        )
    ]
    kwargs = {
        "fact_calls": ["SHOULD_LIST"],
        "duplicate_flags": [False],
        "multiple_facts_flags": flags,
        "looks_good": accepted,
        "values": {
            "independently_verifiable": "pass",
            "noise_removed": "pass",
            "deduplicated_ordered": "pass",
        },
    }
    if valid:
        assert validate_fact_decomp_ratings(item, facts, **kwargs)[
            "multiple_facts_flags"
        ] == [True]
    else:
        with pytest.raises(ValueError):
            validate_fact_decomp_ratings(item, facts, **kwargs)
