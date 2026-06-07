from __future__ import annotations

import json

import pytest

from amfv_decomposer.llm_decomposer import (
    DecomposerParseError,
    LiteLLMClaimDecomposer,
    build_messages,
    parse_decomposition,
)


class FakeMessage:
    content = json.dumps(
        {
            "claims": [
                {
                    "claim": (
                        "Asthma is commonly treated with inhaled "
                        "corticosteroids."
                    ),
                    "claim_type": "treatment",
                    "certainty": "asserted",
                    "requires_context": True,
                }
            ]
        }
    )


class FakeChoice:
    message = FakeMessage()


class FakeResponse:
    choices = [FakeChoice()]


class FakeCompletion:
    def __init__(self) -> None:
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse()


def test_build_messages_contains_few_shot_examples() -> None:
    messages = build_messages("Asthma is treated with inhaled corticosteroids.")

    assert messages[0]["role"] == "system"
    assert len(messages) == 8
    assert "Metformin" in messages[1]["content"]
    assert "PSA" in messages[3]["content"]
    assert "Pneumonia" in messages[5]["content"]
    assert "Asthma is treated" in messages[7]["content"]

def test_parse_decomposition_returns_structured_claims() -> None:
    claims = parse_decomposition(
        json.dumps(
            {
                "claims": [
                    {
                        "claim": "Hypertension increases stroke risk.",
                        "claim_type": "prognosis",
                        "certainty": "asserted",
                        "requires_context": True,
                    }
                ]
            }
        )
    )

    assert len(claims) == 1
    assert claims[0]["claim"] == "Hypertension increases stroke risk."
    assert claims[0]["claim_type"] == "prognosis"
    assert claims[0]["certainty"] == "asserted"
    assert claims[0]["requires_context"] is True


def test_parse_decomposition_rejects_unstructured_string_list() -> None:
    with pytest.raises(DecomposerParseError):
        parse_decomposition(
            json.dumps(
                [
                    "Asthma is treated with inhaled corticosteroids.",
                ]
            )
        )


def test_litellm_decomposer_calls_completion_function() -> None:
    fake_completion = FakeCompletion()

    decomposer = LiteLLMClaimDecomposer(
        model="test-model",
        completion_fn=fake_completion,
    )

    claims = decomposer.decompose_text(
        "Asthma is commonly treated with inhaled corticosteroids."
    )

    assert claims[0]["claim"] == (
        "Asthma is commonly treated with inhaled corticosteroids."
    )
    assert fake_completion.kwargs is not None
    assert fake_completion.kwargs["model"] == "test-model"
    assert fake_completion.kwargs["temperature"] == 0.0
    assert fake_completion.kwargs["max_tokens"] == 1600
    assert fake_completion.kwargs["messages"][0]["role"] == "system"