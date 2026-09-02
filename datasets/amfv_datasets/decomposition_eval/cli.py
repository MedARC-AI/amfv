"""Command-line interface for prompt preview and one-arm generation."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from amfv_datasets.decomposition_eval.models import GenerationSettings
from amfv_datasets.decomposition_eval.prompt_io import build_preview, load_cases, load_prompt, preview_json
from amfv_datasets.decomposition_eval.run import generate_artifact
from amfv_datasets.decomposition_eval.runtime import ModelFamily, RuntimeConfig

__all__ = ["main"]


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--prompt-id", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-family", choices=[family.value for family in ModelFamily], required=True)
    parser.add_argument("--model-revision")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"])
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--top-p", type=float)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amfv-decomposition-eval")
    commands = parser.add_subparsers(dest="command", required=True)
    preview = commands.add_parser("preview", help="show the exact no-network request contract")
    _common_arguments(preview)
    preview.add_argument("--case-id")
    preview.add_argument("--output", type=Path)

    run = commands.add_parser("run", help="generate one atomic model-arm artifact")
    _common_arguments(run)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--arm-id", required=True)
    run.add_argument("--base-url", required=True)
    run.add_argument("--api-key-env")
    run.add_argument("--concurrency", type=int, default=1)
    run.add_argument("--timeout", type=float, default=120)
    run.add_argument("--output-retries", type=int, default=2)
    return parser


def _generation(arguments: argparse.Namespace) -> GenerationSettings:
    return GenerationSettings(
        max_tokens=arguments.max_tokens,
        temperature=arguments.temperature,
        top_p=arguments.top_p,
        reasoning_effort=arguments.reasoning_effort,
    )


def _preview(arguments: argparse.Namespace) -> None:
    cases = load_cases(arguments.input)
    if arguments.case_id is None:
        case = cases[0]
    else:
        matches = [case for case in cases if case.case_id == arguments.case_id]
        if not matches:
            raise ValueError(f"case_id is not present in input: {arguments.case_id}")
        case = matches[0]
    instructions = load_prompt(arguments.prompt_file)
    preview = build_preview(
        case,
        instructions=instructions,
        prompt_id=arguments.prompt_id,
        model_id=arguments.model_id,
        model_revision=arguments.model_revision,
        model_family=arguments.model_family,
        generation=_generation(arguments),
    )
    rendered = preview_json(preview)
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8", newline="\n")


async def _run(arguments: argparse.Namespace) -> None:
    config = RuntimeConfig(
        model_id=arguments.model_id,
        model_revision=arguments.model_revision,
        base_url=arguments.base_url,
        family=arguments.model_family,
        api_key_env=arguments.api_key_env,
        timeout_seconds=arguments.timeout,
        output_retries=arguments.output_retries,
        generation=_generation(arguments),
    )
    await generate_artifact(
        arguments.input,
        arguments.output,
        arguments.prompt_file,
        prompt_id=arguments.prompt_id,
        arm_id=arguments.arm_id,
        config=config,
        concurrency=arguments.concurrency,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Run the selected decomposition-evaluation command."""
    arguments = _parser().parse_args(argv)
    if arguments.command == "preview":
        _preview(arguments)
    else:
        asyncio.run(_run(arguments))
