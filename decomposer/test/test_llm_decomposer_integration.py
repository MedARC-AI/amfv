from __future__ import annotations

import os

import pytest

from amfv_decomposer.llm_decomposer import (
    PROVIDER_EXAMPLES,
    LiteLLMClaimDecomposer,
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
def test_real_llm_decomposer_provider_returns_structured_claims(
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

    decomposer = LiteLLMClaimDecomposer(
        model=str(provider_config["model"]),
        api_key=api_key,
        api_base=api_base,
    )

    claims = decomposer.decompose_text(
        "Asthma is commonly treated with inhaled corticosteroids. "
        "Oral corticosteroids can be used during severe asthma exacerbations."
    )

    assert len(claims) >= 2

    for claim in claims:
        assert isinstance(claim["claim"], str)
        assert claim["claim"]
        assert claim["claim_type"] in {
            "diagnosis",
            "treatment",
            "epidemiology",
            "prognosis",
            "safety",
            "pathophysiology",
            "screening",
            "other",
        }
        assert claim["certainty"] in {
            "asserted",
            "hedged",
            "negated",
        }
        assert isinstance(claim["requires_context"], bool)