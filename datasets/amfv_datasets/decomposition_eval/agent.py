"""Reusable one-case PydanticAI decomposition loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, ModelRetry, NativeOutput, RunContext, UsageLimits
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from amfv_datasets.decomposition_eval.models import (
    DecompositionCase,
    DecompositionPrediction,
    resolve_prediction,
)
from amfv_datasets.decomposition_eval.prompt_io import render_case

__all__ = ["CaseDeps", "create_agent", "run_case"]


@dataclass(frozen=True)
class CaseDeps:
    """Trusted response text used for output validation."""

    assistant_response: str


def create_agent(
    model: Model[Any],
    *,
    instructions: str,
    model_settings: ModelSettings,
    output_retries: int,
) -> Agent[CaseDeps, DecompositionPrediction]:
    """Create a native-schema agent with exact caller-supplied instructions."""
    agent = Agent(
        model,
        deps_type=CaseDeps,
        instructions=instructions,
        output_type=NativeOutput(
            DecompositionPrediction,
            name="decomposition_prediction",
            description="Return the validated decomposition prediction.",
        ),
        model_settings=model_settings,
        retries=output_retries,
    )

    @agent.output_validator
    def validate_output(ctx: RunContext[CaseDeps], output: DecompositionPrediction) -> DecompositionPrediction:
        try:
            resolve_prediction(ctx.deps.assistant_response, output)
            return output
        except ValueError as error:
            raise ModelRetry(str(error)[:500]) from error

    return agent


async def run_case(
    agent: Agent[CaseDeps, DecompositionPrediction],
    case: DecompositionCase,
    *,
    timeout_seconds: float,
) -> DecompositionPrediction:
    """Run one case through the shared agent and bounded request budget."""
    async with asyncio.timeout(timeout_seconds):
        result = await agent.run(
            render_case(case),
            deps=CaseDeps(assistant_response=case.assistant_response),
            usage_limits=UsageLimits(request_limit=20, tool_calls_limit=0),
        )
    return result.output
