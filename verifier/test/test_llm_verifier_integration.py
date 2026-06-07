from __future__ import annotations

import os

import pytest

from amfv_verifier.llm_verifier import (
    PROVIDER_EXAMPLES,
    LiteLLMClaimVerifier,
)


SELECTED_PROVIDERS = [
    provider.strip()
    for provider in os.getenv("AMFV_LLM_TEST_PROVIDERS", "openai").split(",")
    if provider.strip()
]


def get_provider_config(provider_name: str) -> dict[str, str | None]:
    if provider_name not in PROVIDER_EXAMPLES:
        supported = ", ".join(sorted(PROVIDER_EXAMPLES))
        raise KeyError(
            f"Unknown provider '{provider_name}'. Supported providers: {supported}"
        )

    config = PROVIDER_EXAMPLES[provider_name]

    model = os.getenv(config["model_env"], config["default_model"])
    api_key_env = config.get("api_key_env")
    api_key = os.getenv(api_key_env) if api_key_env else None

    api_base_env = config.get("api_base_env")
    api_base = os.getenv(api_base_env) if api_base_env else None

    return {
        "model": model,
        "api_key_env": api_key_env,
        "api_key": api_key,
        "api_base_env": api_base_env,
        "api_base": api_base,
    }


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("AMFV_RUN_LLM_TESTS") != "1",
    reason="Set AMFV_RUN_LLM_TESTS=1 to run real LLM API tests.",
)
@pytest.mark.parametrize("provider_name", SELECTED_PROVIDERS)
def test_real_llm_verifier_provider_returns_structured_verdict(
    provider_name: str,
) -> None:
    provider_config = get_provider_config(provider_name)

    api_key_env = provider_config["api_key_env"]
    api_key = provider_config["api_key"]

    if api_key_env and not api_key:
        pytest.skip(f"Set {api_key_env} to test provider '{provider_name}'.")

    api_base_env = provider_config["api_base_env"]
    api_base = provider_config["api_base"]

    if provider_name == "local_openai" and not api_base:
        pytest.skip(f"Set {api_base_env} to test local OpenAI-compatible server.")

    verifier = LiteLLMClaimVerifier(
        model=str(provider_config["model"]),
        api_key=api_key,
        api_base=api_base,
    )

    verification = verifier.verify_claim(
        claim="Asthma is commonly treated with inhaled corticosteroids.",
        evidence=[
            {
                "chunk_id": "chunk_1",
                "title": "Asthma guideline",
                "url": "https://example.com/asthma",
                "text": (
                    "Asthma maintenance treatment commonly includes inhaled "
                    "corticosteroids."
                ),
            }
        ],
    )

    assert verification["verdict"] in {
        "strongly supported",
        "weakly supported",
        "unclear",
        "weakly unsubstantiated",
        "strongly unsubstantiated",
    }
    assert verification["medv1_score"] in {-2, -1, 0, 1, 2}
    assert 0.0 <= verification["score"] <= 1.0
    assert verification["confidence"] in {"high", "medium", "low"}
    assert isinstance(verification["explanation"], str)
    assert verification["explanation"]