from __future__ import annotations

import json

import pytest

from amfv_verifier.llm_verifier import (
    LiteLLMClaimVerifier,
    VerifierParseError,
    build_messages,
    format_evidence,
    parse_verification,
)


class FakeMessage:
    content = json.dumps(
        {
            "verdict": "strongly supported",
            "medv1_score": 2,
            "score": 1.0,
            "confidence": "high",
            "supported_evidence_ids": ["e1"],
            "contradicted_evidence_ids": [],
            "missing_context": [],
            "explanation": (
                "The evidence directly supports the claim."
            ),
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


def test_format_evidence_assigns_evidence_ids() -> None:
    evidence = [
        {
            "chunk_id": "chunk_1",
            "title": "Asthma guideline",
            "url": "https://example.com",
            "text": "Asthma treatment includes inhaled corticosteroids.",
        }
    ]

    formatted = format_evidence(evidence)

    assert formatted[0]["evidence_id"] == "e1"
    assert formatted[0]["chunk_id"] == "chunk_1"
    assert "inhaled corticosteroids" in formatted[0]["text"]


def test_build_messages_contains_claim_and_evidence() -> None:
    messages = build_messages(
        claim="Asthma is treated with inhaled corticosteroids.",
        evidence=[
            {
                "chunk_id": "chunk_1",
                "title": "Asthma guideline",
                "text": "Asthma treatment includes inhaled corticosteroids.",
            }
        ],
    )

    assert messages[0]["role"] == "system"
    assert len(messages) == 8
    assert "Metformin is contraindicated" in messages[1]["content"]
    assert "Antibiotics are routinely recommended" in messages[3]["content"]
    assert "PSA of 4.2" in messages[5]["content"]
    assert "Asthma is treated" in messages[7]["content"]


def test_parse_verification_returns_normalized_scores() -> None:
    verification = parse_verification(
        json.dumps(
            {
                "verdict": "weakly supported",
                "medv1_score": 999,
                "score": 999,
                "confidence": "medium",
                "supported_evidence_ids": ["e1"],
                "contradicted_evidence_ids": [],
                "missing_context": ["adult population"],
                "explanation": "The evidence partly supports the claim.",
            }
        )
    )

    assert verification["verdict"] == "weakly supported"
    assert verification["medv1_score"] == 1
    assert verification["score"] == 0.75
    assert verification["confidence"] == "medium"


def test_parse_verification_rejects_invalid_verdict() -> None:
    with pytest.raises(VerifierParseError):
        parse_verification(
            json.dumps(
                {
                    "verdict": "supported",
                    "medv1_score": 2,
                    "score": 1.0,
                    "confidence": "high",
                    "supported_evidence_ids": [],
                    "contradicted_evidence_ids": [],
                    "missing_context": [],
                    "explanation": "Invalid verdict.",
                }
            )
        )


def test_litellm_verifier_calls_completion_function() -> None:
    fake_completion = FakeCompletion()

    verifier = LiteLLMClaimVerifier(
        model="test-verifier-model",
        completion_fn=fake_completion,
    )

    verification = verifier.verify_claim(
        claim="Asthma is treated with inhaled corticosteroids.",
        evidence=[
            {
                "chunk_id": "chunk_1",
                "title": "Asthma guideline",
                "text": "Asthma treatment includes inhaled corticosteroids.",
            }
        ],
    )

    assert verification["verdict"] == "strongly supported"
    assert verification["score"] == 1.0
    assert verification["verifier_name"] == "llm_medv1_style_verifier"
    assert verification["verifier_model"] == "test-verifier-model"
    assert fake_completion.kwargs is not None
    assert fake_completion.kwargs["model"] == "test-verifier-model"
    assert fake_completion.kwargs["temperature"] == 0.0
    assert fake_completion.kwargs["max_tokens"] == 1200