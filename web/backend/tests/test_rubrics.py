import pytest

from app.models import (
    EvalFact,
    EvalItem,
    EvalType,
    FactPolarity,
    ItemSource,
    ItemStatus,
)
from app.services.rubrics import validate_fact_decomp_ratings


def test_fact_decomp_requires_call_for_every_fact_uuid() -> None:
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
            fact_uuid="a",
            fact_text="A",
            polarity=FactPolarity.SHOULD_LIST,
            position=0,
        ),
        EvalFact(
            item_id=1,
            fact_uuid="b",
            fact_text="B",
            polarity=FactPolarity.SHOULD_NOT_LIST,
            position=1,
        ),
    ]

    with pytest.raises(ValueError, match="missing calls"):
        validate_fact_decomp_ratings(
            item,
            facts,
            fact_calls={"a": "SHOULD_LIST"},
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
            fact_uuid="a",
            fact_text="A",
            polarity=FactPolarity.SHOULD_LIST,
            position=0,
        ),
        EvalFact(
            item_id=1,
            fact_uuid="b",
            fact_text="B",
            polarity=FactPolarity.SHOULD_NOT_LIST,
            position=1,
        ),
    ]

    ratings = validate_fact_decomp_ratings(
        item,
        facts,
        fact_calls={"a": "SHOULD_LIST", "b": "SHOULD_LIST"},
        values={
            "independently_verifiable": "pass",
            "noise_removed": "minor_issue",
            "deduplicated_ordered": "fail",
        },
    )

    assert ratings["fact_agreement"] == {"a": "agree", "b": "disagree"}
