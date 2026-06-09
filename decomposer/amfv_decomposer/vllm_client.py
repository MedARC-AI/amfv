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

_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 2.0
# vLLM v1 has a race condition when hundreds of concurrent requests arrive at once.
# Cap in-flight requests to avoid triggering it.
_MAX_CONCURRENT = int(os.environ.get("VLLM_MAX_CONCURRENT", "32"))

_enable_thinking: bool = False


def configure(*, enable_thinking: bool) -> None:
    """Set inference options. Call once from evaluate.py before running decomposers."""
    global _enable_thinking
    _enable_thinking = enable_thinking


@lru_cache(maxsize=1)
def _get_client() -> OpenAI:
    return OpenAI(
        base_url=os.environ.get("VLLM_BASE_URL", "http://localhost:8000/v1"),
        api_key="none",
    )


@lru_cache(maxsize=1)
def get_served_model() -> str:
    """Return the model name from the running vLLM server."""
    models = _get_client().models.list()
    if not models.data:
        raise RuntimeError("vLLM server returned no models from /v1/models")
    return models.data[0].id


def chat_generate(messages_batch: list[list[dict]]) -> list[str]:
    """Batch chat completions via the vLLM OpenAI-compatible API."""
    model = get_served_model()

    def _call(messages: list[dict]) -> str:
        last_exc: Exception | None = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                resp = _get_client().chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=0.0,
                    max_tokens=2048,
                    extra_body={"chat_template_kwargs": {"enable_thinking": _enable_thinking}},
                )
                return (resp.choices[0].message.content or "").strip()
            except Exception as exc:
                last_exc = exc
                if attempt < _RETRY_ATTEMPTS - 1:
                    time.sleep(_RETRY_DELAY)
        raise RuntimeError(f"chat_generate failed after {_RETRY_ATTEMPTS} attempts") from last_exc

    with ThreadPoolExecutor(max_workers=_MAX_CONCURRENT) as pool:
        return list(pool.map(_call, messages_batch))
