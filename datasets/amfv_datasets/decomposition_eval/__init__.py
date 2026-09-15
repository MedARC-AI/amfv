"""Strict contracts for decomposition evaluation."""

from amfv_datasets.decomposition_eval.models import (
    ClaimLabel,
    ClaimPrediction,
    DecompositionCase,
    DecompositionPrediction,
    ExtractedClaim,
    FactDecompRow,
    GenerationSettings,
    GeneratorProvenance,
    SourceSpan,
    canonical_json,
    external_id_for,
    project_prediction,
    resolve_prediction,
    validate_claim_spans_against_response,
    validate_identifier,
)

__all__ = [
    "ClaimLabel",
    "ClaimPrediction",
    "DecompositionCase",
    "DecompositionPrediction",
    "ExtractedClaim",
    "FactDecompRow",
    "GenerationSettings",
    "GeneratorProvenance",
    "SourceSpan",
    "canonical_json",
    "external_id_for",
    "project_prediction",
    "resolve_prediction",
    "validate_claim_spans_against_response",
    "validate_identifier",
]
