"""Shared Qwen3-8B vLLM instance used by all decomposers."""

from __future__ import annotations

from vllm import LLM, SamplingParams

MODEL_NAME = "Qwen/Qwen3-8B"

SAMPLING_PARAMS = SamplingParams(
    temperature=0.0,
    max_tokens=1024,
)

_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = LLM(model=MODEL_NAME, tensor_parallel_size=1)
    return _llm


def generate(messages_batch: list[list[dict]]) -> list[str]:
    """Run a batch of chat conversations through Qwen3-8B with thinking disabled."""
    llm = get_llm()
    outputs = llm.chat(
        messages_batch,
        sampling_params=SAMPLING_PARAMS,
        chat_template_kwargs={"enable_thinking": False},
    )
    return [out.outputs[0].text.strip() for out in outputs]
