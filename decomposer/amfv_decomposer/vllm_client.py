"""vLLM inference client via the OpenAI-compatible API server.

run_eval.sh starts `vllm serve` before invoking evaluate.py.
Set VLLM_BASE_URL to override the default server address.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI

QWEN3_8B = "Qwen/Qwen3-8B"

_client = OpenAI(
    base_url=os.environ.get("VLLM_BASE_URL", "http://localhost:8000/v1"),
    api_key="none",
)


def chat_generate(
    messages_batch: list[list[dict]],
    model: str = QWEN3_8B,
) -> list[str]:
    """Batch chat completions via the vLLM OpenAI-compatible API."""

    def _call(messages: list[dict]) -> str:
        resp = _client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.0,
            max_tokens=1024,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        return (resp.choices[0].message.content or "").strip()

    with ThreadPoolExecutor() as pool:
        return list(pool.map(_call, messages_batch))
