"""Generic vLLM client supporting both chat and completion models."""

from __future__ import annotations

from vllm import LLM, SamplingParams

QWEN3_8B = "Qwen/Qwen3-8B"
VERISCORE_MISTRAL = "SYX/mistral_based_claim_extractor"

_instances: dict[str, LLM] = {}

_CHAT_PARAMS = SamplingParams(temperature=0.0, max_tokens=1024)
_COMPLETION_PARAMS = SamplingParams(temperature=0.0, max_tokens=1024)


def get_llm(model: str) -> LLM:
    if model not in _instances:
        _instances[model] = LLM(model=model, tensor_parallel_size=1)
    return _instances[model]


def chat_generate(
    messages_batch: list[list[dict]],
    model: str = QWEN3_8B,
) -> list[str]:
    """Batch inference for instruction-tuned chat models."""
    llm = get_llm(model)
    outputs = llm.chat(
        messages_batch,
        sampling_params=_CHAT_PARAMS,
        chat_template_kwargs={"enable_thinking": False},
    )
    return [out.outputs[0].text.strip() for out in outputs]


def completion_generate(
    prompts: list[str],
    model: str = VERISCORE_MISTRAL,
) -> list[str]:
    """Batch inference for completion/fine-tuned models (Alpaca format)."""
    llm = get_llm(model)
    outputs = llm.generate(prompts, sampling_params=_COMPLETION_PARAMS)
    return [out.outputs[0].text.strip().replace("</s>", "").strip() for out in outputs]
