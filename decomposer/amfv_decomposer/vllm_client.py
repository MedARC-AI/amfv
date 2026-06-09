"""vLLM inference client via the OpenAI-compatible API server.

run_eval.sh starts `vllm serve` before invoking evaluate.py.
Set VLLM_BASE_URL to override the default server address.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from openai import OpenAI

QWEN3_8B = "Qwen/Qwen3-8B"

# VLLM_MODEL overrides the default model name sent in API requests.
# Must match the model passed to --model in vllm serve (run_eval.sh).
_DEFAULT_MODEL = os.environ.get("VLLM_MODEL", QWEN3_8B)

_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 2.0


@lru_cache(maxsize=1)
def _get_client() -> OpenAI:
    return OpenAI(
        base_url=os.environ.get("VLLM_BASE_URL", "http://localhost:8000/v1"),
        api_key="none",
    )


def chat_generate(
    messages_batch: list[list[dict]],
    model: str = _DEFAULT_MODEL,
) -> list[str]:
    """Batch chat completions via the vLLM OpenAI-compatible API."""

    def _call(messages: list[dict]) -> str:
        last_exc: Exception | None = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                resp = _get_client().chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=0.0,
                    max_tokens=1024,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
                return (resp.choices[0].message.content or "").strip()
            except Exception as exc:
                last_exc = exc
                if attempt < _RETRY_ATTEMPTS - 1:
                    time.sleep(_RETRY_DELAY)
        raise RuntimeError(f"chat_generate failed after {_RETRY_ATTEMPTS} attempts") from last_exc

    with ThreadPoolExecutor() as pool:
        return list(pool.map(_call, messages_batch))
