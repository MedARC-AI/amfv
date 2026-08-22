from __future__ import annotations

from dataclasses import dataclass

from app.models import EvalFact, EvalItem, EvalType, FactPolarity

FACT_CALL_OPTIONS = {"SHOULD_LIST", "SHOULD_NOT_LIST", "MALFORMED"}


@dataclass(frozen=True)
class RubricDimension:
    id: str
    label: str
    options: tuple[tuple[str, str], ...]


FACT_DECOMP_DIMENSIONS = (
    RubricDimension(
        "independently_verifiable",
        "Claims are independently verifiable without outside context",
        (("pass", "Pass"), ("minor_issue", "Minor issue"), ("fail", "Fail")),
    ),
    RubricDimension(
        "noise_removed",
        "Distracting or non-claim noise has been removed",
        (("pass", "Pass"), ("minor_issue", "Minor issue"), ("fail", "Fail")),
    ),
    RubricDimension(
        "deduplicated_ordered",
        "Semantically redundant claims are removed while preserving logical order",
        (("pass", "Pass"), ("minor_issue", "Minor issue"), ("fail", "Fail")),
    ),
)


def validate_fact_decomp_ratings(
    item: EvalItem,
    facts: list[EvalFact],
    *,
    fact_calls: dict[str, str],
    values: dict[str, str],
) -> dict:
    if item.eval_type != EvalType.FACT_DECOMP:
        raise ValueError(
            "Fact-decomposition ratings can only be submitted for FACT_DECOMP items."
        )
    required = {fact.fact_uuid for fact in facts}
    submitted = set(fact_calls)
    if submitted != required:
        missing = sorted(required - submitted)
        extra = sorted(submitted - required)
        pieces = []
        if missing:
            pieces.append(f"missing calls for {', '.join(missing)}")
        if extra:
            pieces.append(f"unknown fact ids {', '.join(extra)}")
        raise ValueError("; ".join(pieces))
    unknown_calls = {
        call for call in fact_calls.values() if call not in FACT_CALL_OPTIONS
    }
    if unknown_calls:
        raise ValueError(f"Unknown fact call: {', '.join(sorted(unknown_calls))}")
    _validate_options(values, _allowed_options(FACT_DECOMP_DIMENSIONS))
    polarity_by_uuid = {fact.fact_uuid: fact.polarity.value for fact in facts}
    return {
        **values,
        "fact_calls": fact_calls,
        "fact_agreement": {
            fact_uuid: _fact_agreement(call, polarity_by_uuid[fact_uuid])
            for fact_uuid, call in fact_calls.items()
        },
    }


def _fact_agreement(call: str, polarity: str) -> str:
    if call == "MALFORMED":
        return "malformed"
    if (
        call == FactPolarity.SHOULD_LIST.value
        and polarity == FactPolarity.SHOULD_LIST.value
    ):
        return "agree"
    if (
        call == FactPolarity.SHOULD_NOT_LIST.value
        and polarity == FactPolarity.SHOULD_NOT_LIST.value
    ):
        return "agree"
    return "disagree"


def _allowed_options(dimensions: tuple[RubricDimension, ...]) -> dict[str, set[str]]:
    return {
        dimension.id: {value for value, _ in dimension.options}
        for dimension in dimensions
    }


def _validate_options(values: dict[str, str], allowed: dict[str, set[str]]) -> None:
    missing = sorted(set(allowed) - set(values))
    unknown = sorted(set(values) - set(allowed))
    invalid = sorted(
        key
        for key, value in values.items()
        if key in allowed and value not in allowed[key]
    )
    if missing:
        raise ValueError(f"Missing rubric dimensions: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"Unknown rubric dimensions: {', '.join(unknown)}")
    if invalid:
        raise ValueError(f"Invalid rubric options: {', '.join(invalid)}")
