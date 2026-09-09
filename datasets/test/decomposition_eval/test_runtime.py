"""Tests for explicit provider configuration and owned client closure."""

import asyncio

import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from amfv_datasets.decomposition_eval import runtime as runtime_module  # noqa: E402
from amfv_datasets.decomposition_eval.runtime import RuntimeConfig, build_model_runtime  # noqa: E402


class _FakeClient:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_runtime_propagates_provider_and_generation_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass the configured URL, model, profile, and settings to PydanticAI."""
    captured: dict[str, object] = {}

    class FakeProvider:
        def __init__(self, *, openai_client: _FakeClient) -> None:
            captured["provider_client"] = openai_client

    def fake_model(model_id: str, *, provider: object, profile: object) -> object:
        captured.update(model_id=model_id, provider=provider, profile=profile)
        return object()

    monkeypatch.setenv("SMOKE_API_KEY", "secret-value")
    monkeypatch.setattr(runtime_module, "AsyncOpenAI", _FakeClient)
    monkeypatch.setattr(runtime_module, "OpenAIProvider", FakeProvider)
    monkeypatch.setattr(runtime_module, "OpenAIResponsesModel", fake_model)
    config = RuntimeConfig(
        model_id="served-model",
        base_url="http://host.docker.internal:8010/v1",
        family="gpt-oss",
        api_key_env="SMOKE_API_KEY",
        timeout_seconds=45,
        generation={"max_tokens": 123, "temperature": 0.25, "top_p": 0.8, "reasoning_effort": "medium"},
    )

    runtime = build_model_runtime(config)
    client = captured["provider_client"]

    assert isinstance(client, _FakeClient)
    assert client.kwargs == {
        "base_url": "http://host.docker.internal:8010/v1",
        "api_key": "secret-value",
        "timeout": 45.0,
        "max_retries": 0,
    }
    assert captured["model_id"] == "served-model"
    assert runtime.settings == {
        "max_tokens": 123,
        "temperature": 0.25,
        "top_p": 0.8,
        "parallel_tool_calls": False,
        "openai_store": False,
        "openai_reasoning_effort": "medium",
    }
    assert "secret-value" not in config.model_dump_json()
    asyncio.run(runtime.aclose())
    assert client.closed


def test_named_api_key_must_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail before client construction when an explicit key variable is absent."""
    monkeypatch.delenv("MISSING_API_KEY", raising=False)
    config = RuntimeConfig(
        model_id="served-model",
        base_url="http://host.docker.internal:8010/v1",
        family="generic",
        api_key_env="MISSING_API_KEY",
    )

    with pytest.raises(ValueError, match="MISSING_API_KEY"):
        build_model_runtime(config)


def test_generic_openai_runtime_supports_native_output_without_local_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserve provider schema support for hosted OpenAI models."""
    from amfv_datasets.decomposition_eval.agent import create_agent

    monkeypatch.setenv("SMOKE_API_KEY", "test-only-key")
    runtime = build_model_runtime(
        RuntimeConfig(
            model_id="gpt-5.6-terra",
            base_url="https://api.openai.com/v1",
            family="generic",
            api_key_env="SMOKE_API_KEY",
        )
    )
    try:
        create_agent(runtime.model, instructions="Extract claims.", model_settings=runtime.settings, output_retries=0)
        assert runtime.model.profile.get("supports_json_schema_output") is True
        assert runtime.model.profile.get("openai_supports_strict_tool_definition", True) is True
    finally:
        asyncio.run(runtime.aclose())
