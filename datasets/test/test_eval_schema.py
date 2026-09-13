"""Tests for AMFV-Bench schema and the v0 gold split."""

from __future__ import annotations

import json

import pytest

from amfv_datasets.eval.schema import (
    EXPECTED_CASE_COUNT,
    EXPECTED_CASES_PER_STRATUM,
    MAX_QUOTED_SPAN_CHARS,
    FailureMode,
    Stratum,
    Verdict,
    case_from_dict,
    case_to_dict,
    load_gold_v0,
    validate_case,
    validate_gold_set,
)


def test_v0_gold_set_is_balanced() -> None:
    """v0 ships 48 cases, eight per stratum, with unique ids."""
    cases = load_gold_v0()

    assert len(cases) == EXPECTED_CASE_COUNT
    counts = dict.fromkeys(Stratum, 0)
    for case in cases:
        counts[case.stratum] += 1
    assert counts == dict.fromkeys(Stratum, EXPECTED_CASES_PER_STRATUM)
    assert len({case.case_id for case in cases}) == EXPECTED_CASE_COUNT


def test_v0_quoted_spans_stay_short() -> None:
    """Gold quotes a span, not a chapter."""
    for case in load_gold_v0():
        for claim in case.gold_claims:
            for ref in claim.evidence:
                assert 0 < len(ref.quoted_span) <= MAX_QUOTED_SPAN_CHARS


@pytest.mark.parametrize(
    ("stratum", "verdict", "mode"),
    [
        (Stratum.POPULATION_MISMATCH, Verdict.NEUTRAL, FailureMode.POPULATION_MISMATCH),
        (Stratum.TEMPORAL, Verdict.STRONG_CONTRADICTION, FailureMode.GUIDELINE_UPDATE),
        (Stratum.STRONGLY_SUPPORTED, Verdict.STRONG_AGREEMENT, None),
        (Stratum.WEAKLY_SUPPORTED, Verdict.PARTIAL_AGREEMENT, None),
        (Stratum.REFUTED, Verdict.STRONG_CONTRADICTION, None),
        (Stratum.INSUFFICIENT, Verdict.NEUTRAL, None),
    ],
    ids=["population_mismatch", "temporal", "strongly_supported", "weakly_supported", "refuted", "insufficient"],
)
def test_v0_stratum_labels_match_contract(
    stratum: Stratum,
    verdict: Verdict,
    mode: FailureMode | None,
) -> None:
    """Each stratum uses the verdict and failure-mode tags the rubric requires."""
    matched = [case for case in load_gold_v0() if case.stratum is stratum]
    assert matched
    for case in matched:
        for claim in case.gold_claims:
            assert claim.verdict is verdict
            if mode is not None:
                assert mode in claim.failure_modes


def test_v0_generator_matches_shipped_jsonl() -> None:
    """Regenerating from `_v0.py` must equal the committed JSONL."""
    from amfv_datasets.eval.gold._v0 import cases

    generated = [case_to_dict(case) for case in validate_gold_set(cases())]
    shipped = [case_to_dict(case) for case in load_gold_v0()]

    assert generated == shipped


def test_case_json_roundtrip() -> None:
    """JSONL serialization preserves a gold case."""
    original = load_gold_v0()[0]
    restored = case_from_dict(json.loads(json.dumps(case_to_dict(original))))

    assert restored == original


def test_validate_case_rejects_long_quotes() -> None:
    """Oversized quoted spans fail validation with an actionable message."""
    case = load_gold_v0()[0]
    payload = case_to_dict(case)
    payload["gold_claims"][0]["evidence"][0]["quoted_span"] = "x" * (MAX_QUOTED_SPAN_CHARS + 1)
    bloated = case_from_dict(payload)

    with pytest.raises(ValueError, match="quoted_span"):
        validate_case(bloated)
