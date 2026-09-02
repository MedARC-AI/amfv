"""Tests for bounded one-arm generation and atomic publication."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from pydantic_ai.messages import ModelResponse, TextPart  # noqa: E402
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402

from amfv_datasets.decomposition_eval.models import FactDecompRow  # noqa: E402
from amfv_datasets.decomposition_eval.run import generate_artifact  # noqa: E402
from amfv_datasets.decomposition_eval.runtime import OwnedModelRuntime, RuntimeConfig  # noqa: E402


class _CloseTracker:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    input_path = tmp_path / "cases.jsonl"
    input_path.write_text(
        "\n".join(
            [
                '{"schema_version":1,"case_id":"case-a","user_prompt":"u1","assistant_response":"Alpha."}',
                '{"schema_version":1,"case_id":"case-b","user_prompt":"u2","assistant_response":"Beta."}',
                '{"schema_version":1,"case_id":"case-c","user_prompt":"u3","assistant_response":"Gamma."}',
                '{"schema_version":1,"case_id":"case-d","user_prompt":"u4","assistant_response":"Delta."}',
                '{"schema_version":1,"case_id":"case-e","user_prompt":"u5","assistant_response":"Epsilon."}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("Smoke instructions.", encoding="utf-8")
    return input_path, tmp_path / "output.jsonl", prompt_path


def _config() -> RuntimeConfig:
    return RuntimeConfig(
        model_id="function-model",
        base_url="http://example.test/v1",
        family="generic",
        timeout_seconds=2,
        generation={"max_tokens": 50, "temperature": 0.1},
    )


def test_batch_is_bounded_ordered_and_closes_client(tmp_path: Path) -> None:
    """Bound concurrency, preserve input order, and close the client."""
    input_path, output_path, prompt_path = _paths(tmp_path)
    tracker = _CloseTracker()
    active = 0
    maximum_active = 0

    async def function(messages: object, _info: AgentInfo) -> ModelResponse:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        rendered = str(messages)
        text = next(value for value in ["Alpha.", "Beta.", "Gamma.", "Delta.", "Epsilon."] if value in rendered)
        await asyncio.sleep((6 - len(text)) * 0.005 + 0.01)
        active -= 1
        label = "vital" if text == "Alpha." else "duplicate"
        payload = {"claims": [{"claim": text, "source_texts": [text], "label": label}]}
        return ModelResponse(parts=[TextPart(content=json.dumps(payload))])

    runtime = OwnedModelRuntime(model=FunctionModel(function), settings={}, client=tracker)
    rows = asyncio.run(
        generate_artifact(
            input_path,
            output_path,
            prompt_path,
            prompt_id="smoke-local",
            arm_id="arm-a",
            config=_config(),
            concurrency=2,
            runtime_factory=lambda _config: runtime,
        )
    )

    parsed = [FactDecompRow.model_validate_json(line) for line in output_path.read_text().splitlines()]
    assert [row.case_id for row in rows] == ["case-a", "case-b", "case-c", "case-d", "case-e"]
    assert parsed == rows
    assert maximum_active == 2
    assert tracker.closed
    assert [row.claims[0].label.value for row in rows[:2]] == ["vital", "duplicate"]


@pytest.mark.parametrize(
    "failure", [RuntimeError("forced failure"), asyncio.CancelledError()], ids=["failure", "cancellation"]
)
def test_failure_preserves_prior_output_and_leaves_no_temporary_file(tmp_path: Path, failure: BaseException) -> None:
    """Keep prior output intact after failure or cancellation."""
    input_path, output_path, prompt_path = _paths(tmp_path)
    output_path.write_text("prior\n", encoding="utf-8")
    tracker = _CloseTracker()

    async def function(_messages: object, _info: AgentInfo) -> ModelResponse:
        raise failure

    runtime = OwnedModelRuntime(model=FunctionModel(function), settings={}, client=tracker)
    with pytest.raises(type(failure)):
        asyncio.run(
            generate_artifact(
                input_path,
                output_path,
                prompt_path,
                prompt_id="smoke-local",
                arm_id="arm-a",
                config=_config(),
                concurrency=1,
                runtime_factory=lambda _config: runtime,
            )
        )

    assert output_path.read_text() == "prior\n"
    assert list(tmp_path.glob(f".{output_path.name}.*.tmp")) == []
    assert tracker.closed


def test_duplicate_cases_fail_before_runtime_construction(tmp_path: Path) -> None:
    """Parse and validate all cases before opening a provider runtime."""
    input_path, output_path, prompt_path = _paths(tmp_path)
    first = input_path.read_text().splitlines()[0]
    input_path.write_text(first + "\n" + first + "\n", encoding="utf-8")
    constructed = False

    def factory(_config: RuntimeConfig) -> Any:
        nonlocal constructed
        constructed = True
        raise AssertionError("runtime must not be constructed")

    with pytest.raises(ValueError, match="duplicate case_id"):
        asyncio.run(
            generate_artifact(
                input_path,
                output_path,
                prompt_path,
                prompt_id="prompt-a",
                arm_id="arm-a",
                config=_config(),
                runtime_factory=factory,
            )
        )
    assert not constructed
    assert not output_path.exists()


def test_invalid_arm_fails_before_runtime_construction(tmp_path: Path) -> None:
    """Validate run identifiers before opening a provider runtime."""
    input_path, output_path, prompt_path = _paths(tmp_path)
    constructed = False

    def factory(_config: RuntimeConfig) -> Any:
        nonlocal constructed
        constructed = True
        raise AssertionError("runtime must not be constructed")

    with pytest.raises(ValueError, match="String should match pattern"):
        asyncio.run(
            generate_artifact(
                input_path,
                output_path,
                prompt_path,
                prompt_id="prompt-a",
                arm_id="../unsafe",
                config=_config(),
                runtime_factory=factory,
            )
        )
    assert not constructed
    assert not output_path.exists()


def test_rows_exclude_private_and_volatile_fields(tmp_path: Path) -> None:
    """Keep credentials, messages, reasoning, and run data out of rows."""
    input_path, output_path, prompt_path = _paths(tmp_path)
    tracker = _CloseTracker()

    async def function(_messages: object, _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content='{"claims": []}')])

    runtime = OwnedModelRuntime(model=FunctionModel(function), settings={}, client=tracker)
    asyncio.run(
        generate_artifact(
            input_path,
            output_path,
            prompt_path,
            prompt_id="prompt-a",
            arm_id="arm-a",
            config=_config(),
            runtime_factory=lambda _config: runtime,
        )
    )
    payload = json.loads(output_path.read_text().splitlines()[0])
    forbidden = {"api_key", "messages", "reasoning", "run_id", "timestamp", "metadata"}
    assert forbidden.isdisjoint(payload)
    assert forbidden.isdisjoint(payload["generator"])


def test_worker_failure_cancels_and_awaits_concurrent_sibling(tmp_path: Path) -> None:
    """Cancel and await an active sibling before closing the client."""
    input_path, output_path, prompt_path = _paths(tmp_path)
    output_path.write_text("prior\n", encoding="utf-8")
    tracker = _CloseTracker()
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()

    async def function(messages: object, _info: AgentInfo) -> ModelResponse:
        rendered = str(messages)
        if "Alpha." in rendered:
            await sibling_started.wait()
            raise RuntimeError("forced worker failure")
        if "Beta." in rendered:
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                sibling_cancelled.set()
                raise
        raise AssertionError("later cases must not start")

    runtime = OwnedModelRuntime(model=FunctionModel(function), settings={}, client=tracker)
    with pytest.raises(RuntimeError, match="forced worker failure"):
        asyncio.run(
            generate_artifact(
                input_path,
                output_path,
                prompt_path,
                prompt_id="smoke-local",
                arm_id="arm-a",
                config=_config(),
                concurrency=2,
                runtime_factory=lambda _config: runtime,
            )
        )

    assert sibling_cancelled.is_set()
    assert tracker.closed
    assert output_path.read_text() == "prior\n"
    assert list(tmp_path.glob(f".{output_path.name}.*.tmp")) == []
