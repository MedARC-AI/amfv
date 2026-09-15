"""PydanticAI tests for the shared one-case decomposition path."""

import asyncio
import json

import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from pydantic_ai.messages import ModelResponse, TextPart  # noqa: E402
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402

from amfv_datasets.decomposition_eval.agent import create_agent, run_case  # noqa: E402
from amfv_datasets.decomposition_eval.models import (  # noqa: E402
    ClaimPrediction,
    DecompositionCase,
    SourceSpan,
    validate_claim_spans_against_response,
)


def _case(response: str = "Café helps.") -> DecompositionCase:
    return DecompositionCase(schema_version=1, case_id="case-a", user_prompt="What helps?", assistant_response=response)


def _response(payload: dict[str, object]) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=json.dumps(payload))])


def test_function_model_receives_exact_instructions_and_structured_output() -> None:
    """Use exact instructions and parse the declared native output."""
    observed: dict[str, object] = {}

    async def function(_messages: object, info: AgentInfo) -> ModelResponse:
        observed["instructions"] = info.instructions
        observed["output_mode"] = info.model_request_parameters.output_mode
        observed["output_schema"] = info.model_request_parameters.output_object
        return _response(
            {
                "claims": [
                    {
                        "claim": "Café helps.",
                        "source_texts": ["Café helps."],
                        "label": "vital",
                    }
                ]
            },
        )

    agent = create_agent(
        FunctionModel(function), instructions="Exact\r\ninstructions", model_settings={}, output_retries=1
    )
    result = asyncio.run(run_case(agent, _case(), timeout_seconds=2))

    assert observed["instructions"] == "Exact\r\ninstructions"
    assert observed["output_mode"] == "native"
    assert observed["output_schema"] is not None
    native_schema = observed["output_schema"].json_schema
    assert '"start"' not in json.dumps(native_schema)
    assert '"end"' not in json.dumps(native_schema)
    assert result.claims[0].label.value == "vital"


def test_response_mismatch_asks_model_to_retry() -> None:
    """Retry a source span whose text does not match the response."""
    attempts = 0

    async def function(_messages: object, _info: AgentInfo) -> ModelResponse:
        nonlocal attempts
        attempts += 1
        source_text = "wrong" if attempts == 1 else "Café helps."
        return _response(
            {
                "claims": [
                    {
                        "claim": "Café helps.",
                        "source_texts": [source_text],
                        "label": "vital",
                    }
                ]
            },
        )

    agent = create_agent(FunctionModel(function), instructions="test", model_settings={}, output_retries=2)
    result = asyncio.run(run_case(agent, _case(), timeout_seconds=2))

    assert attempts == 2
    assert result.claims[0].source_texts == ["Café helps."]


def test_zero_claims_are_valid() -> None:
    """Accept a model response with no extracted claims."""

    async def function(_messages: object, _info: AgentInfo) -> ModelResponse:
        return _response({"claims": []})

    agent = create_agent(FunctionModel(function), instructions="test", model_settings={}, output_retries=1)
    assert asyncio.run(run_case(agent, _case("Hello!"), timeout_seconds=2)).claims == []


@pytest.mark.parametrize(
    ("prediction", "message"),
    [
        (
            [
                ClaimPrediction(claim="second", spans=[SourceSpan(start=4, end=6, text="ef")], label="vital"),
                ClaimPrediction(claim="first", spans=[SourceSpan(start=0, end=2, text="ab")], label="vital"),
            ],
            "starts before",
        ),
        (
            [ClaimPrediction(claim="outside", spans=[SourceSpan(start=4, end=8, text="efgh")], label="vital")],
            "ends outside",
        ),
    ],
    ids=["global-order", "out-of-range"],
)
def test_response_dependent_validation_rejects_invalid_spans(prediction: list[ClaimPrediction], message: str) -> None:
    """Reject globally unordered claims and out-of-range spans."""
    with pytest.raises(ValueError, match=message):
        validate_claim_spans_against_response("abcdef", prediction)
