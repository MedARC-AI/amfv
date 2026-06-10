"""vLLM inference client via the OpenAI-compatible API server.

run_eval.sh starts `vllm serve` before invoking evaluate.py.
Set VLLM_BASE_URL to override the default server address.
"""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from openai import APIConnectionError, APIStatusError, OpenAI

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 2.0
# vLLM v1 has a race condition when hundreds of concurrent requests arrive at once.
# Cap in-flight requests to avoid triggering it. This is the only fan-out point:
# BaseDecomposer.decompose_batch sends all records through one chat_generate call,
# so this cap bounds total in-flight requests.
_MAX_CONCURRENT = int(os.environ.get("VLLM_MAX_CONCURRENT", "32"))


def _is_retryable(exc: Exception) -> bool:
    # Connection errors (including timeouts) and server-side failures are
    # transient; 4xx like context-length-exceeded will never succeed on retry.
    if isinstance(exc, APIConnectionError):
        return True
    return isinstance(exc, APIStatusError) and (exc.status_code >= 500 or exc.status_code == 429)

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
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                resp = _get_client().chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=0.0,
                    max_tokens=2048,
                    extra_body={"chat_template_kwargs": {"enable_thinking": _enable_thinking}},
                )
                content = (resp.choices[0].message.content or "").strip()
                return _THINK_RE.sub("", content).strip()
            except Exception as exc:
                if not _is_retryable(exc) or attempt == _RETRY_ATTEMPTS - 1:
                    raise
                time.sleep(_RETRY_DELAY)
        raise AssertionError("unreachable")

    with ThreadPoolExecutor(max_workers=_MAX_CONCURRENT) as pool:
        return list(pool.map(_call, messages_batch))
