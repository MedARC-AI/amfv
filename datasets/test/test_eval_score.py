"""Tests for the deterministic AMFV-Bench scorer."""

from __future__ import annotations

import json
from pathlib import Path

from amfv_datasets.eval.schema import (
    EvalCase,
    EvidenceRef,
    FailureMode,
    GoldClaim,
    InputKind,
    PredictedClaim,
    PredictedEvidence,
    Prediction,
    PyramidTier,
    Scope,
    Stratum,
    Verdict,
    load_gold_v0,
    load_predictions_jsonl,
    prediction_from_dict,
)
from amfv_datasets.eval.score import score_predictions

_SCOPE = Scope("adults", "hypertension", "primary care")
_EVIDENCE = EvidenceRef(
    source_id="nice-ng136",
    url="https://www.nice.org.uk/guidance/ng136",
    title="Hypertension in adults",
    section="Diagnosing hypertension",
    quoted_span="offer ambulatory blood pressure monitoring (ABPM)",
    pyramid_tier=PyramidTier.GUIDELINE,
    replaces="nice-cg127",
)


def _case(*, case_id: str = "case-1", stratum: Stratum = Stratum.STRONGLY_SUPPORTED, **overrides: object) -> EvalCase:
    claim = GoldClaim(
        claim_id="c1",
        text="Adults with clinic BP 148/92 mmHg should be offered ABPM.",
        atomicity_ok=True,
        verdict=Verdict.STRONG_AGREEMENT,
        scope=_SCOPE,
        as_of="2026-08-13",
        evidence=(_EVIDENCE,),
        failure_modes=(),
    )
    values: dict[str, object] = {
        "case_id": case_id,
        "stratum": stratum,
        "input_kind": InputKind.MODEL_OUTPUT,
        "input_text": "Offer ABPM when clinic BP is 148/92 mmHg.",
        "gold_claims": (claim,),
        "annotator": "test",
        "notes": "",
    }
    values.update(overrides)
    return EvalCase(**values)  # type: ignore[arg-type]


def _prediction(
    text: str,
    *,
    case_id: str = "case-1",
    verdict: Verdict | None = Verdict.STRONG_AGREEMENT,
    source_id: str = "nice-ng136",
    section: str = "Diagnosing hypertension",
    atomicity_ok: bool | None = True,
) -> Prediction:
    return Prediction(
        case_id=case_id,
        predicted_claims=(
            PredictedClaim(
                text=text,
                atomicity_ok=atomicity_ok,
                verdict=verdict,
                retrieved_evidence=(
                    PredictedEvidence(
                        source_id=source_id,
                        section=section,
                        pyramid_tier=PyramidTier.GUIDELINE,
                    ),
                ),
            ),
        ),
    )


def test_matched_paraphrase_counts_as_coverage() -> None:
    """Token overlap, not string equality, matches a gold claim."""
    report = score_predictions(
        [_case()],
        [_prediction("Offer ABPM to adults with clinic blood pressure 148/92 mmHg.")],
    )

    assert report.overall.coverage.value == 1.0
    assert report.overall.precision.value == 1.0


def test_extra_predicted_claim_lowers_precision() -> None:
    """Unmatched extra claims are precision errors, not coverage errors."""
    extra = PredictedClaim(text="Start immediate dual antiplatelet therapy after every nosebleed.")
    matched = _prediction("Adults with clinic BP 148/92 mmHg should be offered ABPM.")
    prediction = Prediction(case_id="case-1", predicted_claims=matched.predicted_claims + (extra,))

    report = score_predictions([_case()], [prediction])

    assert report.overall.coverage.value == 1.0
    assert report.overall.precision.value == 0.5


def test_invented_number_fails_faithfulness() -> None:
    """Numbers that never appear in the input are unfaithful."""
    report = score_predictions(
        [_case()],
        [_prediction("Adults with clinic BP 148/92 mmHg should be offered ABPM at 999 mg.")],
    )

    assert report.overall.faithfulness.value == 0.0


def test_document_hit_does_not_require_chunk_id() -> None:
    """Retrieval scores source id plus section, not a chunk hash."""
    report = score_predictions(
        [_case()],
        [_prediction("Adults with clinic BP 148/92 mmHg should be offered ABPM.")],
    )

    assert report.overall.document_hit.value == 1.0
    assert report.overall.section_hit.value == 1.0


def test_wrong_section_is_document_hit_without_section_hit() -> None:
    """Same guideline, wrong heading, is a section miss."""
    report = score_predictions(
        [_case()],
        [
            _prediction(
                "Adults with clinic BP 148/92 mmHg should be offered ABPM.",
                section="Lifestyle interventions",
            )
        ],
    )

    assert report.overall.document_hit.value == 1.0
    assert report.overall.section_hit.value == 0.0


def test_superseded_source_fails_recency() -> None:
    """Retrieving only a withdrawn guideline is a recency miss."""
    report = score_predictions(
        [_case()],
        [
            _prediction(
                "Adults with clinic BP 148/92 mmHg should be offered ABPM.",
                source_id="nice-cg127",
            )
        ],
    )

    assert report.overall.recency_hit.value == 0.0
    assert report.overall.document_hit.value == 0.0


def test_five_way_miss_can_still_match_coarse_verdict() -> None:
    """Partial vs strong agreement is a 5-way miss and a 3-way hit."""
    report = score_predictions(
        [_case()],
        [
            _prediction(
                "Adults with clinic BP 148/92 mmHg should be offered ABPM.",
                verdict=Verdict.PARTIAL_AGREEMENT,
            )
        ],
    )

    assert report.overall.exact_5way.value == 0.0
    assert report.overall.coarse_3way.value == 1.0


def _mismatch_case() -> EvalCase:
    claim = GoldClaim(
        claim_id="c1",
        text="A 7-year-old with clinic BP 142/90 mmHg should use the adult ABPM pathway.",
        atomicity_ok=True,
        verdict=Verdict.NEUTRAL,
        scope=Scope("children aged 7 years", "hypertension", "paediatric care"),
        as_of="2026-08-13",
        evidence=(_EVIDENCE,),
        failure_modes=(FailureMode.POPULATION_MISMATCH, FailureMode.PEDIATRICS),
    )
    return _case(
        stratum=Stratum.POPULATION_MISMATCH,
        input_text="A 7-year-old with clinic BP 142/90 mmHg should use the adult ABPM pathway.",
        gold_claims=(claim,),
    )


def _oracle_predictions(cases: list[EvalCase] | tuple[EvalCase, ...]) -> list[Prediction]:
    predictions = []
    for case in cases:
        predicted = tuple(
            PredictedClaim(
                text=claim.text,
                atomicity_ok=True,
                verdict=claim.verdict,
                scope=claim.scope,
                as_of=claim.as_of,
                retrieved_evidence=tuple(
                    PredictedEvidence(
                        source_id=ref.source_id,
                        section=ref.section,
                        pyramid_tier=ref.pyramid_tier,
                    )
                    for ref in claim.evidence
                ),
            )
            for claim in case.gold_claims
        )
        predictions.append(Prediction(case_id=case.case_id, predicted_claims=predicted))
    return predictions


def test_population_mismatch_must_abstain() -> None:
    """Supporting an out-of-scope paediatric claim is an abstention failure."""
    case = _mismatch_case()
    supported = _prediction(
        case.gold_claims[0].text,
        verdict=Verdict.STRONG_AGREEMENT,
    )
    abstained = _prediction(case.gold_claims[0].text, verdict=Verdict.NEUTRAL)

    failed = score_predictions([case], [supported])
    passed = score_predictions([case], [abstained])

    assert failed.overall.scope_abstention.value == 0.0
    assert passed.overall.scope_abstention.value == 1.0


def test_oracle_on_v0_is_perfect() -> None:
    """Copying gold claims into predictions scores 1.0 on every v0 rate."""
    cases = load_gold_v0()
    report = score_predictions(cases, _oracle_predictions(cases))

    for rate in (
        report.overall.coverage,
        report.overall.precision,
        report.overall.faithfulness,
        report.overall.atomicity,
        report.overall.document_hit,
        report.overall.section_hit,
        report.overall.recency_hit,
        report.overall.tier_hit,
        report.overall.exact_5way,
        report.overall.coarse_3way,
        report.overall.scope_abstention,
    ):
        assert rate.value == 1.0


def test_demo_fixture_flags_supported_population_mismatch() -> None:
    """The checked-in demo predictions file is enough to exercise the scorer."""
    fixture = Path(__file__).parent / "fixtures" / "eval" / "predictions_demo.jsonl"
    keep = {"amfv-v0-ss-01", "amfv-v0-pm-01"}
    cases = [case for case in load_gold_v0() if case.case_id in keep]
    report = score_predictions(cases, load_predictions_jsonl(fixture))

    assert report.overall.coverage.value == 1.0
    assert report.overall.scope_abstention.value == 0.0
    assert report.by_stratum["strongly_supported"].exact_5way.value == 1.0


def test_prediction_parser_accepts_fixture(tmp_path: Path) -> None:
    """A predictions JSONL fixture loads without a network or LLM."""
    path = tmp_path / "pred.jsonl"
    payload = {
        "case_id": "demo",
        "predicted_claims": [
            {"text": "Offer ABPM.", "verdict": 2, "retrieved_evidence": [{"source_id": "nice-ng136"}]}
        ],
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    loaded = load_predictions_jsonl(path)

    assert loaded[0] == prediction_from_dict(payload)


def test_negated_claim_does_not_match() -> None:
    """Offer vs do-not-offer is a coverage miss, not a paraphrase hit."""
    report = score_predictions(
        [_case()],
        [_prediction("Do not offer ABPM to adults with clinic BP 148/92 mmHg.")],
    )

    assert report.overall.coverage.value == 0.0


def test_identifier_swap_does_not_match() -> None:
    """QRISK3 vs QRISK2 must not pair as the same claim."""
    claim = GoldClaim(
        claim_id="c1",
        text="Offer atorvastatin 20 mg when QRISK3 is 10% or more.",
        atomicity_ok=True,
        verdict=Verdict.STRONG_AGREEMENT,
        scope=_SCOPE,
        as_of="2026-08-13",
        evidence=(_EVIDENCE,),
    )
    case = _case(
        input_text="Offer atorvastatin 20 mg when QRISK3 is 10% or more.",
        gold_claims=(claim,),
    )
    report = score_predictions(
        [case],
        [_prediction("Offer atorvastatin 20 mg when QRISK2 is 10% or more.")],
    )

    assert report.overall.coverage.value == 0.0


def test_missing_verdict_counts_as_five_way_miss() -> None:
    """A matched claim with verdict omitted still sits in the verifier denominator."""
    report = score_predictions(
        [_case()],
        [_prediction("Adults with clinic BP 148/92 mmHg should be offered ABPM.", verdict=None)],
    )

    assert report.overall.coverage.value == 1.0
    assert report.overall.exact_5way == report.overall.coarse_3way
    assert report.overall.exact_5way.hits == 0
    assert report.overall.exact_5way.total == 1


def test_omitted_population_mismatch_claim_is_abstention_miss() -> None:
    """Skipping a population-mismatch claim is an abstention failure, not a skip."""
    case = _mismatch_case()
    report = score_predictions([case], [])

    assert report.overall.scope_abstention.hits == 0
    assert report.overall.scope_abstention.total == 1


def test_insufficient_decoy_retrieval_is_not_scored() -> None:
    """Retrieving planted off-topic evidence on an insufficient case is not a hit."""
    claim = GoldClaim(
        claim_id="c1",
        text="Laparoscopic TEP mesh repair is preferred for inguinal hernia.",
        atomicity_ok=True,
        verdict=Verdict.NEUTRAL,
        scope=_SCOPE,
        as_of="2026-08-13",
        evidence=(_EVIDENCE,),
    )
    case = _case(stratum=Stratum.INSUFFICIENT, gold_claims=(claim,))
    report = score_predictions(
        [case],
        [
            _prediction(
                "Laparoscopic TEP mesh repair is preferred for inguinal hernia.",
                verdict=Verdict.NEUTRAL,
            )
        ],
    )

    assert report.overall.document_hit.total == 0
    assert report.overall.section_hit.total == 0
    assert report.overall.exact_5way.value == 1.0


def test_unmatched_extra_claim_does_not_count_in_atomicity() -> None:
    """Atomicity is scored only on predicted claims that cover gold."""
    extra = PredictedClaim(text="Start immediate dual antiplatelet therapy after every nosebleed.")
    matched = _prediction("Adults with clinic BP 148/92 mmHg should be offered ABPM.")
    prediction = Prediction(case_id="case-1", predicted_claims=matched.predicted_claims + (extra,))

    report = score_predictions([_case()], [prediction])

    assert report.overall.atomicity.hits == 1
    assert report.overall.atomicity.total == 1


def test_merged_golds_fail_atomicity() -> None:
    """One predicted claim that covers two gold claims is not atomic."""
    first = GoldClaim(
        claim_id="c1",
        text="Adults with clinic BP 148/92 mmHg should be offered ABPM.",
        atomicity_ok=True,
        verdict=Verdict.STRONG_AGREEMENT,
        scope=_SCOPE,
        as_of="2026-08-13",
        evidence=(_EVIDENCE,),
    )
    second = GoldClaim(
        claim_id="c2",
        text="Adults with clinic BP 148/92 mmHg should be offered HBPM.",
        atomicity_ok=True,
        verdict=Verdict.STRONG_AGREEMENT,
        scope=_SCOPE,
        as_of="2026-08-13",
        evidence=(_EVIDENCE,),
    )
    case = _case(gold_claims=(first, second))
    report = score_predictions(
        [case],
        [
            _prediction(
                "Adults with clinic BP 148/92 mmHg should be offered ABPM and HBPM.",
            )
        ],
    )

    assert report.overall.atomicity.value == 0.0


def test_identifier_digits_are_not_input_numbers() -> None:
    """Digits inside QRISK3 are not a licence to emit a standalone 3."""
    case = _case(input_text="Discuss QRISK3 before offering ABPM.")
    report = score_predictions([case], [_prediction("Offer ABPM if the score is 3.")])

    assert report.overall.faithfulness.value == 0.0
