"""Bounded one-arm generation and atomic JSONL publication."""

from __future__ import annotations

import asyncio
import importlib.metadata
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

from amfv_datasets.decomposition_eval.agent import create_agent, run_case
from amfv_datasets.decomposition_eval.models import (
    DecompositionCase,
    FactDecompRow,
    GeneratorProvenance,
    canonical_json,
    project_prediction,
    prompt_hash_for,
    validate_identifier,
)
from amfv_datasets.decomposition_eval.prompt_io import load_cases, load_prompt
from amfv_datasets.decomposition_eval.runtime import OwnedModelRuntime, RuntimeConfig, build_model_runtime

__all__ = ["generate_artifact", "run_arm"]


async def run_arm(
    cases: list[DecompositionCase],
    *,
    instructions: str,
    prompt_id: str,
    arm_id: str,
    config: RuntimeConfig,
    runtime: OwnedModelRuntime,
    concurrency: int,
) -> list[FactDecompRow]:
    """Run one model arm with bounded concurrency and stable input ordering."""
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    validate_identifier(prompt_id)
    validate_identifier(arm_id)
    agent = create_agent(
        runtime.model,
        instructions=instructions,
        model_settings=runtime.settings,
        output_retries=config.output_retries,
    )
    generator = GeneratorProvenance(
        model_id=config.model_id,
        model_revision=config.model_revision,
        prompt_id=prompt_id,
        prompt_hash=prompt_hash_for(instructions.encode("utf-8")),
        pydantic_ai_version=importlib.metadata.version("pydantic-ai-slim"),
        generation=config.generation,
    )
    queue: asyncio.Queue[tuple[int, DecompositionCase]] = asyncio.Queue()
    for item in enumerate(cases):
        queue.put_nowait(item)
    results: list[FactDecompRow | None] = [None] * len(cases)

    async def worker() -> None:
        while not queue.empty():
            index, case = queue.get_nowait()
            prediction = await run_case(agent, case, timeout_seconds=config.timeout_seconds)
            results[index] = project_prediction(case, prediction, arm_id=arm_id, generator=generator)

    tasks = [asyncio.create_task(worker()) for _ in range(min(concurrency, len(cases)))]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    assert all(row is not None for row in results)
    return cast(list[FactDecompRow], results)


async def generate_artifact(
    input_path: Path,
    output_path: Path,
    prompt_path: Path,
    *,
    prompt_id: str,
    arm_id: str,
    config: RuntimeConfig,
    concurrency: int = 1,
    runtime_factory: Callable[[RuntimeConfig], OwnedModelRuntime] = build_model_runtime,
) -> list[FactDecompRow]:
    """Generate all rows and atomically replace the output only after success."""
    cases = load_cases(input_path)
    instructions = load_prompt(prompt_path)
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    validate_identifier(prompt_id)
    validate_identifier(arm_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    runtime = runtime_factory(config)
    try:
        async with runtime:
            rows = await run_arm(
                cases,
                instructions=instructions,
                prompt_id=prompt_id,
                arm_id=arm_id,
                config=config,
                runtime=runtime,
                concurrency=concurrency,
            )
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            for row in rows:
                temporary.write(canonical_json(row))
                temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, output_path)
        temporary_path = None
        return rows
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
