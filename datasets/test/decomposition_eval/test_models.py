"""Behavior tests for the decomposition-evaluation contracts."""

import hashlib
import json

import pytest
from pydantic import ValidationError

from amfv_datasets.decomposition_eval import (
    ClaimPrediction,
    DecompositionCase,
    DecompositionPrediction,
    ExtractedClaim,
    GenerationSettings,
    GeneratorProvenance,
    SourceSpan,
    canonical_json,
    external_id_for,
    project_prediction,
    prompt_hash_for,
    resolve_prediction,
)


def _generator() -> GeneratorProvenance:
    return GeneratorProvenance(
        model_id="openai/gpt-oss-20b",
        prompt_id="prompt-v1",
        prompt_hash="a" * 64,
        pydantic_ai_version="2.33.0",
        generation=GenerationSettings(max_tokens=512, temperature=0.2, top_p=0.9, reasoning_effort="medium"),
    )


def test_prediction_projects_losslessly_to_exact_ingest_row() -> None:
    """Preserve all four labels, Unicode, and cross-claim overlap."""
    response = "Café helps. Café helps twice. Extra context. Café helps."
    case = DecompositionCase(
        schema_version=1, case_id="case-unicode", user_prompt="What helps?", assistant_response=response
    )
    prediction = DecompositionPrediction(
        claims=[
            ExtractedClaim(
                claim="Café helps twice.",
                source_texts=["Café helps twice."],
                label="vital",
            ),
            ExtractedClaim(claim="Extra context.", source_texts=["Extra context."], label="supporting"),
            ExtractedClaim(claim="Context is extra.", source_texts=["Extra c"], label="peripheral"),
            ExtractedClaim(claim="Café helps.", source_texts=["Café helps."], label="duplicate"),
        ]
    )

    row = project_prediction(case, prediction, arm_id="arm-a", generator=_generator())
    reparsed = type(row).model_validate_json(canonical_json(row))

    assert reparsed == row
    assert [(span.start, span.end) for claim in reparsed.claims for span in claim.spans] == [
        (12, 29),
        (30, 44),
        (30, 37),
        (45, 56),
    ]
    assert json.loads(canonical_json(row)) == row.model_dump(mode="json")
    assert json.loads(canonical_json(row))["generator"]["model_revision"] is None
    assert json.loads(canonical_json(row))["generator"]["generation"]["reasoning_effort"] == "medium"
    assert row.external_id == hashlib.sha256(b"case-unicode\0arm-a").hexdigest()


def test_zero_claims_and_exact_prompt_bytes_are_valid() -> None:
    """Accept extraction failure and hash bytes without normalization."""
    assert DecompositionPrediction(claims=[]).claims == []
    assert prompt_hash_for("﻿line\r\n".encode()) == hashlib.sha256("﻿line\r\n".encode()).hexdigest()
    assert GenerationSettings().model_dump() == {}
    with pytest.raises(ValidationError, match="Field required"):
        DecompositionPrediction.model_validate({})


def test_response_only_case_projects_null_query() -> None:
    """Preserve the absence of a query in the import artifact."""
    case = DecompositionCase(schema_version=1, case_id="document-a", assistant_response="Alpha.")
    prediction = DecompositionPrediction(
        claims=[ExtractedClaim(claim="Alpha.", source_texts=["Alpha."], label="vital")]
    )

    row = project_prediction(case, prediction, arm_id="arm-a", generator=_generator())

    assert row.user_prompt is None
    assert json.loads(canonical_json(row))["user_prompt"] is None


def test_model_schema_asks_for_quotes_and_not_offsets() -> None:
    """Keep deterministic character offsets outside the model contract."""
    schema = json.dumps(DecompositionPrediction.model_json_schema())

    assert "source_texts" in schema
    assert '"start"' not in schema
    assert '"end"' not in schema


def test_resolver_uses_python_code_points_and_ordered_exact_quotes() -> None:
    """Resolve Unicode and repeated quotations without model-supplied arithmetic."""
    response = "😀 Alpha and beta. Then omega."
    prediction = DecompositionPrediction(
        claims=[
            ExtractedClaim(
                claim="Alpha and beta, then omega.",
                source_texts=["Alpha and beta.", "omega."],
                label="vital",
            ),
        ]
    )

    resolved = resolve_prediction(response, prediction)

    assert [(span.start, span.end, span.text) for span in resolved[0].spans] == [
        (2, 17, "Alpha and beta."),
        (23, 29, "omega."),
    ]


def test_resolver_rejects_nonexact_or_out_of_order_quotes() -> None:
    """Require quotations that code can locate deterministically in source order."""
    nonexact = DecompositionPrediction(claims=[ExtractedClaim(claim="Alpha.", source_texts=["alpha."], label="vital")])
    out_of_order = DecompositionPrediction(
        claims=[ExtractedClaim(claim="Alpha beta.", source_texts=["beta", "Alpha"], label="vital")]
    )

    with pytest.raises(ValueError, match="exact quotation"):
        resolve_prediction("Alpha.", nonexact)
    with pytest.raises(ValueError, match="source order"):
        resolve_prediction("Alpha beta.", out_of_order)


def test_resolver_rejects_ambiguous_repeated_quote() -> None:
    """Never guess which repeated source occurrence the model intended."""
    prediction = DecompositionPrediction(
        claims=[ExtractedClaim(claim="Alpha.", source_texts=["Alpha."], label="vital")]
    )

    with pytest.raises(ValueError, match="matches more than once"):
        resolve_prediction("Alpha. Alpha.", prediction)


def test_resolver_accepts_unique_context_for_repeated_text() -> None:
    """Let exact surrounding text distinguish otherwise repeated assertions."""
    prediction = DecompositionPrediction(
        claims=[
            ExtractedClaim(claim="First Alpha.", source_texts=["First Alpha."], label="vital"),
            ExtractedClaim(claim="Then Alpha.", source_texts=["Then Alpha."], label="duplicate"),
        ]
    )

    resolved = resolve_prediction("First Alpha. Then Alpha.", prediction)

    assert [(claim.spans[0].start, claim.spans[0].end) for claim in resolved] == [(0, 12), (13, 24)]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"claim": " ", "spans": [{"start": 0, "end": 1, "text": "x"}], "label": "vital"}, "claim must not be blank"),
        ({"claim": "x", "spans": [], "label": "vital"}, "at least 1 item"),
        ({"claim": "x", "spans": [{"start": 2, "end": 2, "text": "x"}], "label": "vital"}, "end must be greater"),
        (
            {
                "claim": "x",
                "spans": [{"start": 2, "end": 4, "text": "xx"}, {"start": 3, "end": 5, "text": "xx"}],
                "label": "vital",
            },
            "nonoverlapping",
        ),
        ({"claim": "x", "spans": [{"start": 0, "end": 1, "text": "x"}], "label": "critical"}, "Input should be"),
    ],
    ids=["blank", "no-spans", "empty-span", "within-claim-overlap", "extra-label"],
)
def test_claim_rejects_invalid_shapes(payload: dict[str, object], message: str) -> None:
    """Reject invalid claim content, spans, and labels."""
    with pytest.raises(ValidationError, match=message):
        ClaimPrediction.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"start": True, "end": 1, "text": "x"},
        {"start": "0", "end": 1, "text": "x"},
    ],
    ids=["boolean-offset", "string-offset"],
)
def test_span_offsets_are_strict_integers(payload: dict[str, object]) -> None:
    """Reject coerced boolean and string source offsets."""
    with pytest.raises(ValidationError, match="valid integer"):
        SourceSpan.model_validate(payload)


@pytest.mark.parametrize("value", [float("nan"), float("inf")], ids=["nan", "infinity"])
def test_generation_settings_require_finite_numbers(value: float) -> None:
    """Reject non-finite generation values before preview or provider work."""
    with pytest.raises(ValidationError, match="finite number"):
        GenerationSettings(temperature=value)

    with pytest.raises(ValidationError, match="valid integer"):
        GenerationSettings(max_tokens=True)


def test_unknown_fields_and_unsafe_ids_are_rejected() -> None:
    """Reject extensible metadata and path-like identifiers."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DecompositionCase.model_validate(
            {"schema_version": 1, "case_id": "case", "user_prompt": "u", "assistant_response": "a", "metadata": {}}
        )
    with pytest.raises(ValidationError, match="String should match pattern"):
        external_id_for("case", "../unsafe")


@pytest.mark.parametrize("schema_version", [True, 1.0], ids=["boolean", "float"])
def test_schema_version_requires_integer_one(schema_version: object) -> None:
    """Reject values that compare equal to one but are not an integer."""
    with pytest.raises(ValidationError, match="integer 1"):
        DecompositionCase(
            schema_version=schema_version,
            case_id="case-a",
            user_prompt="u",
            assistant_response="a",
        )


def test_external_id_is_stable_and_changes_by_arm() -> None:
    """Derive stable identities from the case and arm together."""
    assert external_id_for("case-a", "arm-a") == external_id_for("case-a", "arm-a")
    assert external_id_for("case-a", "arm-a") != external_id_for("case-a", "arm-b")


def test_ingest_row_revalidates_spans_against_its_response() -> None:
    """Reject an ingest row whose provenance contradicts its response."""
    case = DecompositionCase(schema_version=1, case_id="case-a", user_prompt="u", assistant_response="Alpha.")
    prediction = DecompositionPrediction(
        claims=[ExtractedClaim(claim="Alpha.", source_texts=["Alpha."], label="vital")]
    )
    payload = project_prediction(case, prediction, arm_id="arm-a", generator=_generator()).model_dump(mode="json")
    payload["claims"][0]["spans"][0]["text"] = "secret mismatch"

    with pytest.raises(ValidationError, match="does not match"):
        type(project_prediction(case, prediction, arm_id="arm-a", generator=_generator())).model_validate(payload)

    for field in ("user_prompt", "assistant_response"):
        blank_payload = project_prediction(case, prediction, arm_id="arm-a", generator=_generator()).model_dump(
            mode="json"
        )
        blank_payload[field] = "   "
        with pytest.raises(ValidationError, match="must not be blank"):
            type(project_prediction(case, prediction, arm_id="arm-a", generator=_generator())).model_validate(
                blank_payload
            )
