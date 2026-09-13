"""Schema for AMFV-Bench: stage-separated, scope- and time-aware gold cases.

Each case is a long-form input plus clinician-graded atomic claims. Verdicts use
the Med-V1 five-point Likert scale. Retrieval gold is a source id plus section
heading, not a brittle chunk hash. Quoted evidence spans are short excerpts
from redistributable NICE pages (Apache-2.0 project; NICE content remains NICE's).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any, TextIO

GOLD_V0_PATH = Path(__file__).resolve().parent / "gold" / "v0.jsonl"
MAX_QUOTED_SPAN_CHARS = 280
EXPECTED_CASE_COUNT = 48
EXPECTED_CASES_PER_STRATUM = 8
ANNOTATOR_SEED = "seed-v0-clinician"


class InputKind(StrEnum):
    """Kind of long-form text the decomposer sees."""

    DOCUMENT = "document"
    REASONING_TRACE = "reasoning_trace"
    MODEL_OUTPUT = "model_output"


class Verdict(IntEnum):
    """Med-V1 five-level claim–evidence agreement.

    Values:
        STRONG_CONTRADICTION: evidence clearly refutes the claim.
        PARTIAL_CONTRADICTION: mixed or indirect evidence against the claim.
        NEUTRAL: evidence does not address the claim, is insufficient, or the
            claim is out of scope for the retrieved source.
        PARTIAL_AGREEMENT: mixed or hedged evidence in favour of the claim.
        STRONG_AGREEMENT: evidence clearly and directly supports the claim.
    """

    STRONG_CONTRADICTION = -2
    PARTIAL_CONTRADICTION = -1
    NEUTRAL = 0
    PARTIAL_AGREEMENT = 1
    STRONG_AGREEMENT = 2


class CoarseVerdict(StrEnum):
    """Three-way collapse of :class:`Verdict` used by SciFact-style reporting."""

    REFUTE = "refute"
    NEI = "nei"
    SUPPORT = "support"


class PyramidTier(StrEnum):
    """Baichuan-M4-style evidence pyramid tier."""

    PRIMARY = "primary"
    REVIEW = "review"
    GUIDELINE = "guideline"
    PRACTICAL = "practical"
    PUBLIC_HEALTH = "public_health"
    REGULATORY = "regulatory"


class FailureMode(StrEnum):
    """High-stakes error class the case is designed to probe."""

    DOSAGE = "dosage"
    CONTRAINDICATION = "contraindication"
    PREGNANCY = "pregnancy"
    PEDIATRICS = "pediatrics"
    RENAL_DOSING = "renal_dosing"
    GUIDELINE_UPDATE = "guideline_update"
    POPULATION_MISMATCH = "population_mismatch"
    HEDGE = "hedge"
    MCQ_DISTRACTOR = "mcq_distractor"
    UNIT = "unit"


class Stratum(StrEnum):
    """Balanced v0 slice. Each stratum has eight gold cases."""

    STRONGLY_SUPPORTED = "strongly_supported"
    WEAKLY_SUPPORTED = "weakly_supported"
    REFUTED = "refuted"
    INSUFFICIENT = "insufficient"
    POPULATION_MISMATCH = "population_mismatch"
    TEMPORAL = "temporal"


@dataclass(frozen=True)
class Scope:
    """Clinical scope Z in the AMFV cache formula.

    Args:
        population: Who the claim is about (age band, pregnancy, ethnicity).
        condition: Disease or indication.
        setting: Care setting (default: "").
    """

    population: str
    condition: str
    setting: str = ""


@dataclass(frozen=True)
class EvidenceRef:
    """Gold evidence pointer: document plus section, not a chunk id.

    Args:
        source_id: Stable id such as ``nice-ng136``.
        url: Canonical source URL.
        title: Guideline or document title.
        section: Section heading the quote lives under.
        quoted_span: Short supporting excerpt. Must be at most
            ``MAX_QUOTED_SPAN_CHARS`` characters.
        pyramid_tier: Evidence pyramid tier (default: PyramidTier.GUIDELINE).
        published_or_updated: ISO date of the cited version (default: None).
        replaces: Withdrawn source id this document supersedes (default: None).
    """

    source_id: str
    url: str
    title: str
    section: str
    quoted_span: str
    pyramid_tier: PyramidTier = PyramidTier.GUIDELINE
    published_or_updated: str | None = None
    replaces: str | None = None


@dataclass(frozen=True)
class GoldClaim:
    """One atomic, independently verifiable medical claim with gold labels.

    Args:
        claim_id: Unique id within the case.
        text: Coreference-resolved claim text written by the annotator.
        atomicity_ok: Whether the claim is a single verifiable fact.
        verdict: Med-V1 Likert verdict against ``evidence``.
        scope: Population, condition, and setting the verdict is valid under.
        as_of: ISO date the verdict is valid (cache key T).
        evidence: Gold evidence pointers.
        failure_modes: High-stakes tags for this claim (default: ()).
    """

    claim_id: str
    text: str
    atomicity_ok: bool
    verdict: Verdict
    scope: Scope
    as_of: str
    evidence: tuple[EvidenceRef, ...]
    failure_modes: tuple[FailureMode, ...] = ()


@dataclass(frozen=True)
class EvalCase:
    """One AMFV-Bench case.

    Args:
        case_id: Stable case id, unique in the gold set.
        stratum: Evaluation slice this case belongs to.
        input_kind: Kind of long-form input.
        input_text: Text the decomposer receives.
        gold_claims: Clinician-graded atomic claims.
        annotator: Annotator id (default: ANNOTATOR_SEED).
        notes: Optional grading notes (default: "").
    """

    case_id: str
    stratum: Stratum
    input_kind: InputKind
    input_text: str
    gold_claims: tuple[GoldClaim, ...]
    annotator: str = ANNOTATOR_SEED
    notes: str = ""


@dataclass(frozen=True)
class PredictedEvidence:
    """Evidence a pipeline retrieved for a predicted claim.

    Args:
        source_id: Retrieved document id.
        section: Retrieved section heading (default: "").
        pyramid_tier: Predicted pyramid tier (default: None).
        as_of: ISO date associated with the hit (default: None).
    """

    source_id: str
    section: str = ""
    pyramid_tier: PyramidTier | None = None
    as_of: str | None = None


@dataclass(frozen=True)
class PredictedClaim:
    """One claim emitted by a pipeline under evaluation.

    Args:
        text: Predicted atomic claim.
        atomicity_ok: Model's atomicity flag (default: None).
        verdict: Predicted Med-V1 verdict (default: None).
        scope: Predicted scope (default: None).
        as_of: Predicted as-of date (default: None).
        retrieved_evidence: Retrieved evidence for this claim (default: ()).
    """

    text: str
    atomicity_ok: bool | None = None
    verdict: Verdict | None = None
    scope: Scope | None = None
    as_of: str | None = None
    retrieved_evidence: tuple[PredictedEvidence, ...] = ()


@dataclass(frozen=True)
class Prediction:
    """Pipeline output for one gold case.

    Args:
        case_id: Must match :attr:`EvalCase.case_id`.
        predicted_claims: Claims the pipeline produced.
    """

    case_id: str
    predicted_claims: tuple[PredictedClaim, ...] = ()


def coarse_verdict(verdict: Verdict) -> CoarseVerdict:
    """Collapse a five-level verdict into support, refute, or NEI.

    Args:
        verdict: Med-V1 Likert value.
    """
    if verdict >= Verdict.PARTIAL_AGREEMENT:
        return CoarseVerdict.SUPPORT
    if verdict <= Verdict.PARTIAL_CONTRADICTION:
        return CoarseVerdict.REFUTE
    return CoarseVerdict.NEI


def requires_abstention(claim: GoldClaim) -> bool:
    """Return whether the verifier must abstain (NEI) rather than support.

    Population-mismatch claims are labelled NEUTRAL. A pipeline that marks them
    supported because a disease name matched has failed the AMFV scope contract.

    Args:
        claim: Gold claim.
    """
    return FailureMode.POPULATION_MISMATCH in claim.failure_modes


def validate_case(case: EvalCase) -> None:
    """Raise ``ValueError`` if a case violates the v0 contract.

    Args:
        case: Case to validate.
    """
    if not case.case_id.strip():
        raise ValueError("case_id must be a non-empty string")
    if not case.input_text.strip():
        raise ValueError(f"{case.case_id}: input_text must be non-empty")
    if not case.gold_claims:
        raise ValueError(f"{case.case_id}: gold_claims must contain at least one claim")
    claim_ids: set[str] = set()
    for claim in case.gold_claims:
        if claim.claim_id in claim_ids:
            raise ValueError(f"{case.case_id}: duplicate claim_id {claim.claim_id!r}")
        claim_ids.add(claim.claim_id)
        if not claim.text.strip():
            raise ValueError(f"{case.case_id}/{claim.claim_id}: claim text must be non-empty")
        if not claim.evidence:
            raise ValueError(f"{case.case_id}/{claim.claim_id}: evidence must be non-empty")
        if not _ISO_DATE_RE.fullmatch(claim.as_of):
            raise ValueError(f"{case.case_id}/{claim.claim_id}: as_of must be YYYY-MM-DD; got {claim.as_of!r}")
        if case.stratum is Stratum.POPULATION_MISMATCH:
            if claim.verdict is not Verdict.NEUTRAL:
                raise ValueError(
                    f"{case.case_id}/{claim.claim_id}: population_mismatch claims must use "
                    f"verdict 0 (abstain); got {int(claim.verdict)}"
                )
            if FailureMode.POPULATION_MISMATCH not in claim.failure_modes:
                raise ValueError(f"{case.case_id}/{claim.claim_id}: tag failure mode population_mismatch")
        if case.stratum is Stratum.TEMPORAL:
            if FailureMode.GUIDELINE_UPDATE not in claim.failure_modes:
                raise ValueError(f"{case.case_id}/{claim.claim_id}: temporal claims must tag guideline_update")
        if case.stratum is Stratum.STRONGLY_SUPPORTED and claim.verdict is not Verdict.STRONG_AGREEMENT:
            raise ValueError(f"{case.case_id}/{claim.claim_id}: strongly_supported claims must use verdict +2")
        if case.stratum is Stratum.WEAKLY_SUPPORTED and claim.verdict is not Verdict.PARTIAL_AGREEMENT:
            raise ValueError(f"{case.case_id}/{claim.claim_id}: weakly_supported claims must use verdict +1")
        if case.stratum is Stratum.REFUTED and claim.verdict is not Verdict.STRONG_CONTRADICTION:
            raise ValueError(f"{case.case_id}/{claim.claim_id}: refuted claims must use verdict -2")
        if case.stratum is Stratum.INSUFFICIENT and claim.verdict is not Verdict.NEUTRAL:
            raise ValueError(f"{case.case_id}/{claim.claim_id}: insufficient claims must use verdict 0")
        if case.stratum is Stratum.TEMPORAL and claim.verdict is not Verdict.STRONG_CONTRADICTION:
            raise ValueError(f"{case.case_id}/{claim.claim_id}: temporal claims must use verdict -2")
        for ref in claim.evidence:
            if len(ref.quoted_span) > MAX_QUOTED_SPAN_CHARS:
                raise ValueError(
                    f"{case.case_id}/{claim.claim_id}: quoted_span is {len(ref.quoted_span)} chars; "
                    f"keep it at most {MAX_QUOTED_SPAN_CHARS}. Quote a heading-sized span, not a chapter."
                )
            if not ref.source_id.strip() or not ref.url.strip() or not ref.section.strip():
                raise ValueError(f"{case.case_id}/{claim.claim_id}: evidence needs source_id, url, and section")


def validate_gold_set(cases: Iterable[EvalCase]) -> tuple[EvalCase, ...]:
    """Validate a full gold split and return it as a tuple.

    Args:
        cases: Cases to validate. v0 must contain exactly
            ``EXPECTED_CASE_COUNT`` cases, ``EXPECTED_CASES_PER_STRATUM`` per
            stratum, with unique case ids.
    """
    loaded = tuple(cases)
    if len(loaded) != EXPECTED_CASE_COUNT:
        raise ValueError(f"gold set must contain {EXPECTED_CASE_COUNT} cases; got {len(loaded)}")
    seen: set[str] = set()
    counts = dict.fromkeys(Stratum, 0)
    for case in loaded:
        validate_case(case)
        if case.case_id in seen:
            raise ValueError(f"duplicate case_id {case.case_id!r}")
        seen.add(case.case_id)
        counts[case.stratum] += 1
    for stratum, count in counts.items():
        if count != EXPECTED_CASES_PER_STRATUM:
            raise ValueError(f"stratum {stratum.value} must have {EXPECTED_CASES_PER_STRATUM} cases; got {count}")
    return loaded


def case_from_dict(payload: Mapping[str, Any]) -> EvalCase:
    """Parse one JSON object into an :class:`EvalCase`.

    Args:
        payload: Mapping produced by :func:`case_to_dict` or gold JSONL.
    """
    try:
        claims = tuple(_gold_claim_from_dict(item) for item in payload["gold_claims"])
        return EvalCase(
            case_id=str(payload["case_id"]),
            stratum=Stratum(payload["stratum"]),
            input_kind=InputKind(payload["input_kind"]),
            input_text=str(payload["input_text"]),
            gold_claims=claims,
            annotator=str(payload.get("annotator", ANNOTATOR_SEED)),
            notes=str(payload.get("notes", "")),
        )
    except KeyError as exc:
        raise ValueError(f"case is missing required field {exc.args[0]!r}") from exc


def case_to_dict(case: EvalCase) -> dict[str, Any]:
    """Serialize a case to a JSON-ready dict.

    Args:
        case: Case to serialize.
    """
    payload = asdict(case)
    payload["stratum"] = case.stratum.value
    payload["input_kind"] = case.input_kind.value
    payload["gold_claims"] = [_gold_claim_to_dict(claim) for claim in case.gold_claims]
    return payload


def prediction_from_dict(payload: Mapping[str, Any]) -> Prediction:
    """Parse one JSON object into a :class:`Prediction`.

    Args:
        payload: Mapping with ``case_id`` and ``predicted_claims``.
    """
    try:
        claims = tuple(_predicted_claim_from_dict(item) for item in payload.get("predicted_claims", []))
        return Prediction(case_id=str(payload["case_id"]), predicted_claims=claims)
    except KeyError as exc:
        raise ValueError(f"prediction is missing required field {exc.args[0]!r}") from exc


def load_gold_jsonl(path: Path, *, validate_v0_split: bool = False) -> tuple[EvalCase, ...]:
    """Load gold cases from JSONL and validate each case.

    Args:
        path: JSONL file of :func:`case_to_dict` objects.
        validate_v0_split: If True, also require the v0 size and stratum
            balance (default: False).
    """
    cases = tuple(_read_jsonl(path, case_from_dict))
    if validate_v0_split:
        return validate_gold_set(cases)
    seen: set[str] = set()
    for case in cases:
        validate_case(case)
        if case.case_id in seen:
            raise ValueError(f"duplicate case_id {case.case_id!r}")
        seen.add(case.case_id)
    return cases


def load_gold_v0() -> tuple[EvalCase, ...]:
    """Load the shipped AMFV-Bench v0 gold set."""
    if not GOLD_V0_PATH.is_file():
        raise FileNotFoundError(f"missing gold file {GOLD_V0_PATH}; add datasets/amfv_datasets/eval/gold/v0.jsonl")
    return load_gold_jsonl(GOLD_V0_PATH, validate_v0_split=True)


def load_predictions_jsonl(path: Path) -> tuple[Prediction, ...]:
    """Load pipeline predictions from JSONL.

    Args:
        path: JSONL file of :func:`prediction_from_dict` objects.
    """
    predictions = tuple(_read_jsonl(path, prediction_from_dict))
    seen: set[str] = set()
    for prediction in predictions:
        if prediction.case_id in seen:
            raise ValueError(f"duplicate prediction for case_id {prediction.case_id!r}")
        seen.add(prediction.case_id)
    return predictions


def dump_jsonl(rows: Iterable[Mapping[str, Any]], output: TextIO) -> int:
    """Write mappings as JSON Lines.

    Args:
        rows: JSON-ready mappings.
        output: Writable text stream.
    """
    count = 0
    for row in rows:
        output.write(json.dumps(row, sort_keys=True, ensure_ascii=False))
        output.write("\n")
        count += 1
    return count


def _read_jsonl(path: Path, parse: Any) -> list[Any]:
    rows: list[Any] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            try:
                rows.append(parse(payload))
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return rows


def _gold_claim_from_dict(payload: Mapping[str, Any]) -> GoldClaim:
    evidence = tuple(_evidence_from_dict(item) for item in payload["evidence"])
    failure_modes = tuple(FailureMode(item) for item in payload.get("failure_modes", ()))
    scope_payload = payload["scope"]
    return GoldClaim(
        claim_id=str(payload["claim_id"]),
        text=str(payload["text"]),
        atomicity_ok=bool(payload["atomicity_ok"]),
        verdict=Verdict(payload["verdict"]),
        scope=Scope(
            population=str(scope_payload["population"]),
            condition=str(scope_payload["condition"]),
            setting=str(scope_payload.get("setting", "")),
        ),
        as_of=str(payload["as_of"]),
        evidence=evidence,
        failure_modes=failure_modes,
    )


def _gold_claim_to_dict(claim: GoldClaim) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "text": claim.text,
        "atomicity_ok": claim.atomicity_ok,
        "verdict": int(claim.verdict),
        "scope": asdict(claim.scope),
        "as_of": claim.as_of,
        "evidence": [_evidence_to_dict(ref) for ref in claim.evidence],
        "failure_modes": [mode.value for mode in claim.failure_modes],
    }


def _evidence_from_dict(payload: Mapping[str, Any]) -> EvidenceRef:
    return EvidenceRef(
        source_id=str(payload["source_id"]),
        url=str(payload["url"]),
        title=str(payload["title"]),
        section=str(payload["section"]),
        quoted_span=str(payload["quoted_span"]),
        pyramid_tier=PyramidTier(payload.get("pyramid_tier", PyramidTier.GUIDELINE)),
        published_or_updated=payload.get("published_or_updated"),
        replaces=payload.get("replaces"),
    )


def _evidence_to_dict(ref: EvidenceRef) -> dict[str, Any]:
    return {
        "source_id": ref.source_id,
        "url": ref.url,
        "title": ref.title,
        "section": ref.section,
        "quoted_span": ref.quoted_span,
        "pyramid_tier": ref.pyramid_tier.value,
        "published_or_updated": ref.published_or_updated,
        "replaces": ref.replaces,
    }


def _predicted_claim_from_dict(payload: Mapping[str, Any]) -> PredictedClaim:
    scope = None
    if payload.get("scope") is not None:
        scope_payload = payload["scope"]
        scope = Scope(
            population=str(scope_payload["population"]),
            condition=str(scope_payload["condition"]),
            setting=str(scope_payload.get("setting", "")),
        )
    verdict = None if payload.get("verdict") is None else Verdict(payload["verdict"])
    evidence = tuple(_predicted_evidence_from_dict(item) for item in payload.get("retrieved_evidence", ()))
    atomicity = payload.get("atomicity_ok")
    return PredictedClaim(
        text=str(payload["text"]),
        atomicity_ok=None if atomicity is None else bool(atomicity),
        verdict=verdict,
        scope=scope,
        as_of=payload.get("as_of"),
        retrieved_evidence=evidence,
    )


def _predicted_evidence_from_dict(payload: Mapping[str, Any]) -> PredictedEvidence:
    tier = payload.get("pyramid_tier")
    return PredictedEvidence(
        source_id=str(payload["source_id"]),
        section=str(payload.get("section", "")),
        pyramid_tier=None if tier is None else PyramidTier(tier),
        as_of=payload.get("as_of"),
    )


_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

__all__ = [
    "ANNOTATOR_SEED",
    "CoarseVerdict",
    "EXPECTED_CASE_COUNT",
    "EXPECTED_CASES_PER_STRATUM",
    "EvalCase",
    "EvidenceRef",
    "FailureMode",
    "GOLD_V0_PATH",
    "GoldClaim",
    "InputKind",
    "MAX_QUOTED_SPAN_CHARS",
    "PredictedClaim",
    "PredictedEvidence",
    "Prediction",
    "PyramidTier",
    "Scope",
    "Stratum",
    "Verdict",
    "case_from_dict",
    "case_to_dict",
    "coarse_verdict",
    "dump_jsonl",
    "load_gold_jsonl",
    "load_gold_v0",
    "load_predictions_jsonl",
    "prediction_from_dict",
    "requires_abstention",
    "validate_case",
    "validate_gold_set",
]
