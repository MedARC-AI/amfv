"""vLLM inference client via the OpenAI-compatible API server.

run_eval.sh starts `vllm serve` before invoking evaluate.py.
Set VLLM_BASE_URL to override the default server address.
"""

from __future__ import annotations

import os

from openai import OpenAI

QWEN3_8B = "Qwen/Qwen3-8B"
VERISCORE_MISTRAL = "SYX/mistral_based_claim_extractor"

_client = OpenAI(
    base_url=os.environ.get("VLLM_BASE_URL", "http://localhost:8000/v1"),
    api_key="none",
)


def chat_generate(
    messages_batch: list[list[dict]],
    model: str = QWEN3_8B,
) -> list[str]:
    """Batch chat completions via the vLLM OpenAI-compatible API."""
    results = []
    for messages in messages_batch:
        resp = _client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.0,
            max_tokens=1024,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        results.append((resp.choices[0].message.content or "").strip())
    return results


def completion_generate(
    prompts: list[str],
    model: str = VERISCORE_MISTRAL,
) -> list[str]:
    """Batch completions via the vLLM OpenAI-compatible API."""
    results = []
    for prompt in prompts:
        resp = _client.completions.create(
            model=model,
            prompt=prompt,
            temperature=0.0,
            max_tokens=1024,
        )
        results.append(resp.choices[0].text.strip().replace("</s>", "").strip())
    return results
