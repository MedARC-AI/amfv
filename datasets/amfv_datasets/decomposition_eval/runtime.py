"""Owned OpenAI-compatible Responses API runtime."""

# Runtime construction pattern adapted from MedARC-AI/pistachio at
# e8ca53df565d81d478664a7aec504c6a739f6b75. The referenced repository states
# no license file; copyright belongs to its contributors.

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from openai import AsyncOpenAI
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.profiles import ModelProfile, merge_profile
from pydantic_ai.profiles.harmony import harmony_model_profile
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.profiles.qwen import qwen_model_profile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from amfv_datasets.decomposition_eval.models import GenerationSettings

__all__ = ["ModelFamily", "OwnedModelRuntime", "RuntimeConfig", "build_model_runtime"]

_LOCAL_RESPONSES_PROFILE = OpenAIModelProfile(
    openai_supports_tool_choice_required=False,
    openai_supports_strict_tool_definition=False,
    supported_native_tools=frozenset(),
)


class ModelFamily(StrEnum):
    """Explicit profile family for an OpenAI-compatible model."""

    GPT_OSS = "gpt-oss"
    QWEN = "qwen"
    GENERIC = "generic"


class RuntimeConfig(BaseModel):
    """Validated provider and generation configuration for one arm."""

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=500)
    model_revision: str | None = Field(default=None, min_length=1, max_length=500)
    base_url: AnyHttpUrl
    family: ModelFamily
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=200)
    timeout_seconds: float = Field(default=120, gt=0, le=3600)
    output_retries: int = Field(default=2, ge=0, le=10)
    generation: GenerationSettings = Field(default_factory=GenerationSettings)

    @model_validator(mode="after")
    def validate_values(self) -> Self:
        """Reject blank model IDs and non-Responses base paths."""
        if not self.model_id.strip():
            raise ValueError("model_id must not be blank")
        if not str(self.base_url).rstrip("/").endswith("/v1"):
            raise ValueError("base_url must end in /v1")
        return self


@dataclass
class OwnedModelRuntime:
    """Model, settings, and the client that must be closed by the caller."""

    model: Model[Any]
    settings: ModelSettings
    client: Any

    async def aclose(self) -> None:
        """Close the owned provider client."""
        await self.client.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()


def _profile_for(config: RuntimeConfig, provider_profile: ModelProfile | None) -> ModelProfile:
    if config.family is ModelFamily.GENERIC:
        return provider_profile or ModelProfile()
    if config.family is ModelFamily.GPT_OSS:
        base = harmony_model_profile("gpt-oss") or ModelProfile()
    elif config.family is ModelFamily.QWEN:
        base = qwen_model_profile("qwen3") or ModelProfile()
    else:
        base = ModelProfile()
    return merge_profile(base, _LOCAL_RESPONSES_PROFILE)


def _settings_for(config: RuntimeConfig) -> OpenAIResponsesModelSettings:
    settings = OpenAIResponsesModelSettings(parallel_tool_calls=False, openai_store=False)
    generation = config.generation
    if generation.max_tokens is not None:
        settings["max_tokens"] = generation.max_tokens
    if generation.temperature is not None:
        settings["temperature"] = generation.temperature
    if generation.top_p is not None:
        settings["top_p"] = generation.top_p
    if generation.reasoning_effort is not None:
        settings["openai_reasoning_effort"] = generation.reasoning_effort
    return settings


def build_model_runtime(config: RuntimeConfig) -> OwnedModelRuntime:
    """Build one owned Responses model with retries disabled at the HTTP layer."""
    if config.api_key_env is None:
        api_key = "not-needed"
    else:
        api_key = os.environ.get(config.api_key_env)
        if api_key is None:
            raise ValueError(f"API key environment variable is not set: {config.api_key_env}")
    client = AsyncOpenAI(
        base_url=str(config.base_url),
        api_key=api_key,
        timeout=config.timeout_seconds,
        max_retries=0,
    )
    model = OpenAIResponsesModel(
        config.model_id,
        provider=OpenAIProvider(openai_client=client),
        profile=lambda provider_profile: _profile_for(config, provider_profile),
    )
    return OwnedModelRuntime(model=model, settings=_settings_for(config), client=client)
