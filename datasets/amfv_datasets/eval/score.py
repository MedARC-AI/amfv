"""Deterministic stage-separated scorer for AMFV-Bench.

The scorer never calls a network or an LLM. Claim matching uses normalised
token Jaccard. Retrieval is scored at document and section level, not chunk id.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from amfv_datasets.eval.schema import (
    CoarseVerdict,
    EvalCase,
    GoldClaim,
    PredictedClaim,
    PredictedEvidence,
    Prediction,
    Stratum,
    coarse_verdict,
    load_gold_jsonl,
    load_gold_v0,
    load_predictions_jsonl,
    requires_abstention,
)

DEFAULT_SIMILARITY_THRESHOLD = 0.45
DEFAULT_SECTION_THRESHOLD = 0.5

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "people",
        "person",
        "that",
        "the",
        "this",
        "to",
        "with",
        "without",
    }
)
# Standalone numbers only. Digits inside identifiers (QRISK3, NG136) must not count.
_NUMBER_RE = re.compile(r"(?<![a-z0-9])-?\d+(?:\.\d+)?(?![a-z0-9])")
_NON_TOKEN_RE = re.compile(r"[^a-z0-9.\s%/-]+")
# Keep 0.6; drop the trailing dot on "ABPM." so it does not become a distinct token.
_NON_DECIMAL_DOT_RE = re.compile(r"(?<!\d)\.|\.(?!\d)")
_WHITESPACE_RE = re.compile(r"\s+")
_BP_RE = re.compile(r"\bbp\b")
_NEGATION_RE = re.compile(r"\b(?:not|never|no|dont|without|avoid)\b")
_COUNTER_NAMES = frozenset(
    {
        "coverage_hits",
        "coverage_total",
        "precision_hits",
        "precision_total",
        "faithful_hits",
        "faithful_total",
        "atomicity_hits",
        "atomicity_total",
        "document_hits",
        "document_total",
        "section_hits",
        "section_total",
        "recency_hits",
        "recency_total",
        "tier_hits",
        "tier_total",
        "exact_hits",
        "exact_total",
        "coarse_hits",
        "coarse_total",
        "abstention_hits",
        "abstention_total",
    }
)


@dataclass(frozen=True)
class Rate:
    """A numerator/denominator rate.

    Args:
        hits: Count of successes.
        total: Count of scored items.
    """

    hits: int
    total: int

    @property
    def value(self) -> float | None:
        """Return hits/total, or None when nothing was scored."""
        if self.total == 0:
            return None
        return self.hits / self.total


@dataclass(frozen=True)
class StageScores:
    """Decomposer, retrieval, and verdict rates for one run."""

    coverage: Rate
    precision: Rate
    faithfulness: Rate
    atomicity: Rate
    document_hit: Rate
    section_hit: Rate
    recency_hit: Rate
    tier_hit: Rate
    exact_5way: Rate
    coarse_3way: Rate
    scope_abstention: Rate


@dataclass(frozen=True)
class ScoreReport:
    """Full AMFV-Bench score.

    Args:
        n_cases: Gold cases that had a prediction.
        n_missing_predictions: Gold cases with no prediction row.
        n_gold_claims: Gold claims in scored cases.
        n_predicted_claims: Predicted claims in scored cases.
        overall: Pooled stage scores.
        by_stratum: Stage scores keyed by stratum value.
    """

    n_cases: int
    n_missing_predictions: int
    n_gold_claims: int
    n_predicted_claims: int
    overall: StageScores
    by_stratum: dict[str, StageScores]


def score_predictions(
    cases: Sequence[EvalCase],
    predictions: Sequence[Prediction],
    *,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    section_threshold: float = DEFAULT_SECTION_THRESHOLD,
) -> ScoreReport:
    """Score pipeline predictions against gold cases.

    Args:
        cases: Gold cases.
        predictions: Pipeline outputs. Extra case ids are ignored; missing
            case ids count as empty predictions.
        similarity_threshold: Minimum token Jaccard to match a predicted claim
            to a gold claim. Polar negation or letter-digit identifier swaps
            never match (default: 0.45).
        section_threshold: Minimum token Jaccard to count a section hit
            (default: 0.5).
    """
    if not 0.0 <= similarity_threshold <= 1.0:
        raise ValueError(f"similarity_threshold must be in [0, 1]; got {similarity_threshold}")
    if not 0.0 <= section_threshold <= 1.0:
        raise ValueError(f"section_threshold must be in [0, 1]; got {section_threshold}")

    by_id = {prediction.case_id: prediction for prediction in predictions}
    counters = _Counters()
    stratum_counters: dict[str, _Counters] = {}
    missing = 0
    predicted_claim_count = 0
    gold_claim_count = 0

    for case in cases:
        prediction = by_id.get(case.case_id)
        if prediction is None:
            missing += 1
            prediction = Prediction(case_id=case.case_id, predicted_claims=())
        predicted_claim_count += len(prediction.predicted_claims)
        gold_claim_count += len(case.gold_claims)
        bucket = stratum_counters.setdefault(case.stratum.value, _Counters())
        _score_case(
            case,
            prediction,
            overall=counters,
            stratum=bucket,
            similarity_threshold=similarity_threshold,
            section_threshold=section_threshold,
        )

    return ScoreReport(
        n_cases=len(cases) - missing,
        n_missing_predictions=missing,
        n_gold_claims=gold_claim_count,
        n_predicted_claims=predicted_claim_count,
        overall=counters.to_scores(),
        by_stratum={name: bucket.to_scores() for name, bucket in sorted(stratum_counters.items())},
    )


def report_to_dict(report: ScoreReport) -> dict[str, object]:
    """Serialize a score report to JSON-ready rates.

    Args:
        report: Output of :func:`score_predictions`.
    """
    return {
        "n_cases": report.n_cases,
        "n_missing_predictions": report.n_missing_predictions,
        "n_gold_claims": report.n_gold_claims,
        "n_predicted_claims": report.n_predicted_claims,
        "overall": _stage_to_dict(report.overall),
        "by_stratum": {name: _stage_to_dict(scores) for name, scores in report.by_stratum.items()},
    }


def _score_cli(
    predictions: Annotated[
        Path,
        typer.Option("--predictions", "-p", help="JSONL of pipeline predictions."),
    ],
    gold: Annotated[
        Path | None,
        typer.Option("--gold", "-g", help="Gold JSONL. Defaults to AMFV-Bench v0."),
    ] = None,
    similarity_threshold: Annotated[
        float,
        typer.Option("--similarity-threshold", help="Minimum token Jaccard for claim matching."),
    ] = DEFAULT_SIMILARITY_THRESHOLD,
) -> None:
    """Score a predictions JSONL against AMFV-Bench gold."""
    cases = load_gold_jsonl(gold) if gold is not None else load_gold_v0()
    parsed = load_predictions_jsonl(predictions)
    report = score_predictions(cases, parsed, similarity_threshold=similarity_threshold)
    typer.echo(json.dumps(report_to_dict(report), indent=2, sort_keys=True))


def main() -> None:
    """Run the AMFV-Bench scoring CLI."""
    typer.run(_score_cli)


class _Counters:
    def __init__(self) -> None:
        self.coverage_hits = 0
        self.coverage_total = 0
        self.precision_hits = 0
        self.precision_total = 0
        self.faithful_hits = 0
        self.faithful_total = 0
        self.atomicity_hits = 0
        self.atomicity_total = 0
        self.document_hits = 0
        self.document_total = 0
        self.section_hits = 0
        self.section_total = 0
        self.recency_hits = 0
        self.recency_total = 0
        self.tier_hits = 0
        self.tier_total = 0
        self.exact_hits = 0
        self.exact_total = 0
        self.coarse_hits = 0
        self.coarse_total = 0
        self.abstention_hits = 0
        self.abstention_total = 0

    def to_scores(self) -> StageScores:
        return StageScores(
            coverage=Rate(self.coverage_hits, self.coverage_total),
            precision=Rate(self.precision_hits, self.precision_total),
            faithfulness=Rate(self.faithful_hits, self.faithful_total),
            atomicity=Rate(self.atomicity_hits, self.atomicity_total),
            document_hit=Rate(self.document_hits, self.document_total),
            section_hit=Rate(self.section_hits, self.section_total),
            recency_hit=Rate(self.recency_hits, self.recency_total),
            tier_hit=Rate(self.tier_hits, self.tier_total),
            exact_5way=Rate(self.exact_hits, self.exact_total),
            coarse_3way=Rate(self.coarse_hits, self.coarse_total),
            scope_abstention=Rate(self.abstention_hits, self.abstention_total),
        )


def _score_case(
    case: EvalCase,
    prediction: Prediction,
    *,
    overall: _Counters,
    stratum: _Counters,
    similarity_threshold: float,
    section_threshold: float,
) -> None:
    pairs = _match_claims(case.gold_claims, prediction.predicted_claims, similarity_threshold)
    matched_gold = {gold.claim_id for gold, _predicted in pairs}
    matched_predicted = {id(predicted) for _gold, predicted in pairs}

    for gold in case.gold_claims:
        _add(overall, stratum, "coverage_total", 1)
        if gold.claim_id in matched_gold:
            _add(overall, stratum, "coverage_hits", 1)

    for predicted in prediction.predicted_claims:
        _add(overall, stratum, "precision_total", 1)
        if id(predicted) in matched_predicted:
            _add(overall, stratum, "precision_hits", 1)
        _add(overall, stratum, "faithful_total", 1)
        if _is_faithful(predicted.text, case.input_text):
            _add(overall, stratum, "faithful_hits", 1)

    gold_by_predicted = _golds_covered_by_predicted(case.gold_claims, prediction.predicted_claims, similarity_threshold)
    for predicted in prediction.predicted_claims:
        covered = gold_by_predicted.get(id(predicted), ())
        if not covered:
            continue
        _add(overall, stratum, "atomicity_total", 1)
        if len(covered) == 1:
            _add(overall, stratum, "atomicity_hits", 1)

    for gold, predicted in pairs:
        if case.stratum is not Stratum.INSUFFICIENT:
            _score_retrieval(
                gold,
                predicted,
                overall=overall,
                stratum=stratum,
                section_threshold=section_threshold,
            )
        _add(overall, stratum, "exact_total", 1)
        _add(overall, stratum, "coarse_total", 1)
        if predicted.verdict is gold.verdict:
            _add(overall, stratum, "exact_hits", 1)
        if predicted.verdict is not None and coarse_verdict(predicted.verdict) is coarse_verdict(gold.verdict):
            _add(overall, stratum, "coarse_hits", 1)
        if requires_abstention(gold):
            _add(overall, stratum, "abstention_total", 1)
            if predicted.verdict is not None and coarse_verdict(predicted.verdict) is CoarseVerdict.NEI:
                _add(overall, stratum, "abstention_hits", 1)

    for gold in case.gold_claims:
        if gold.claim_id not in matched_gold and requires_abstention(gold):
            _add(overall, stratum, "abstention_total", 1)


def _score_retrieval(
    gold: GoldClaim,
    predicted: PredictedClaim,
    *,
    overall: _Counters,
    stratum: _Counters,
    section_threshold: float,
) -> None:
    gold_source_ids = {ref.source_id for ref in gold.evidence}
    gold_sections = [ref.section for ref in gold.evidence]
    replaced_ids = {ref.replaces for ref in gold.evidence if ref.replaces}
    predicted_source_ids = {item.source_id for item in predicted.retrieved_evidence}

    _add(overall, stratum, "document_total", 1)
    if predicted_source_ids & gold_source_ids:
        _add(overall, stratum, "document_hits", 1)

    _add(overall, stratum, "section_total", 1)
    if _section_hit(gold_sections, predicted.retrieved_evidence, section_threshold):
        _add(overall, stratum, "section_hits", 1)

    if replaced_ids:
        _add(overall, stratum, "recency_total", 1)
        if predicted_source_ids & gold_source_ids:
            _add(overall, stratum, "recency_hits", 1)

    predicted_tiers = [item.pyramid_tier for item in predicted.retrieved_evidence if item.pyramid_tier is not None]
    if predicted_tiers:
        gold_tiers = {ref.pyramid_tier for ref in gold.evidence}
        _add(overall, stratum, "tier_total", 1)
        if gold_tiers.intersection(predicted_tiers):
            _add(overall, stratum, "tier_hits", 1)


def _match_claims(
    gold_claims: Sequence[GoldClaim],
    predicted_claims: Sequence[PredictedClaim],
    similarity_threshold: float,
) -> list[tuple[GoldClaim, PredictedClaim]]:
    scored: list[tuple[float, int, int]] = []
    for gold_index, gold in enumerate(gold_claims):
        for predicted_index, predicted in enumerate(predicted_claims):
            score = _claim_similarity(gold.text, predicted.text)
            if score >= similarity_threshold:
                scored.append((score, gold_index, predicted_index))
    scored.sort(reverse=True)
    used_gold: set[int] = set()
    used_predicted: set[int] = set()
    pairs: list[tuple[GoldClaim, PredictedClaim]] = []
    for _score, gold_index, predicted_index in scored:
        if gold_index in used_gold or predicted_index in used_predicted:
            continue
        used_gold.add(gold_index)
        used_predicted.add(predicted_index)
        pairs.append((gold_claims[gold_index], predicted_claims[predicted_index]))
    return pairs


def _golds_covered_by_predicted(
    gold_claims: Sequence[GoldClaim],
    predicted_claims: Sequence[PredictedClaim],
    similarity_threshold: float,
) -> dict[int, tuple[GoldClaim, ...]]:
    covered: dict[int, list[GoldClaim]] = {id(predicted): [] for predicted in predicted_claims}
    for predicted in predicted_claims:
        for gold in gold_claims:
            if _claim_similarity(predicted.text, gold.text) >= similarity_threshold:
                covered[id(predicted)].append(gold)
    return {key: tuple(value) for key, value in covered.items()}


def _section_hit(
    gold_sections: Sequence[str],
    predicted_evidence: Sequence[PredictedEvidence],
    section_threshold: float,
) -> bool:
    predicted_sections = [item.section for item in predicted_evidence if item.section]
    if not predicted_sections:
        return False
    for gold_section in gold_sections:
        gold_tokens = _tokenize(gold_section)
        for predicted_section in predicted_sections:
            if _jaccard(gold_tokens, _tokenize(predicted_section)) >= section_threshold:
                return True
    return False


def _claim_similarity(left: str, right: str) -> float:
    # Character overlap treats "offer aspirin" and "do not offer aspirin" as
    # near-duplicates. Require matching polarity and letter-digit codes first.
    if _is_negated(left) != _is_negated(right):
        return 0.0
    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)
    if _code_tokens(left_tokens) != _code_tokens(right_tokens):
        return 0.0
    return _jaccard(left_tokens, right_tokens)


def _is_faithful(predicted_text: str, input_text: str) -> bool:
    predicted_numbers = set(_NUMBER_RE.findall(_normalize(predicted_text)))
    if not predicted_numbers:
        return True
    input_numbers = set(_NUMBER_RE.findall(_normalize(input_text)))
    return predicted_numbers <= input_numbers


def _tokenize(value: str) -> frozenset[str]:
    return frozenset(token for token in _normalize(value).split() if token and token not in _STOPWORDS)


def _code_tokens(tokens: frozenset[str]) -> frozenset[str]:
    return frozenset(
        token for token in tokens if any(char.isalpha() for char in token) and any(char.isdigit() for char in token)
    )


def _is_negated(value: str) -> bool:
    return _NEGATION_RE.search(_normalize(value)) is not None


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    text = text.replace("mm hg", "mmhg")
    text = _BP_RE.sub("blood pressure", text)
    text = _NON_DECIMAL_DOT_RE.sub(" ", text)
    text = _NON_TOKEN_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _add(overall: _Counters, stratum: _Counters, name: str, amount: int) -> None:
    if name not in _COUNTER_NAMES:
        raise AttributeError(name)
    setattr(overall, name, getattr(overall, name) + amount)
    setattr(stratum, name, getattr(stratum, name) + amount)


def _stage_to_dict(scores: StageScores) -> dict[str, dict[str, float | int | None]]:
    return {
        "coverage": _rate_to_dict(scores.coverage),
        "precision": _rate_to_dict(scores.precision),
        "faithfulness": _rate_to_dict(scores.faithfulness),
        "atomicity": _rate_to_dict(scores.atomicity),
        "document_hit": _rate_to_dict(scores.document_hit),
        "section_hit": _rate_to_dict(scores.section_hit),
        "recency_hit": _rate_to_dict(scores.recency_hit),
        "tier_hit": _rate_to_dict(scores.tier_hit),
        "exact_5way": _rate_to_dict(scores.exact_5way),
        "coarse_3way": _rate_to_dict(scores.coarse_3way),
        "scope_abstention": _rate_to_dict(scores.scope_abstention),
    }


def _rate_to_dict(rate: Rate) -> dict[str, float | int | None]:
    return {"hits": rate.hits, "total": rate.total, "value": rate.value}


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_SECTION_THRESHOLD",
    "DEFAULT_SIMILARITY_THRESHOLD",
    "Rate",
    "ScoreReport",
    "StageScores",
    "main",
    "report_to_dict",
    "score_predictions",
]
