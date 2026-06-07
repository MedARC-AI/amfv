"""OpenAI-compatible client for the vLLM server (started separately via Singularity container)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI

MODEL_NAME = "Qwen/Qwen3-8B"
SERVER_URL = "http://localhost:8000/v1"

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url=SERVER_URL, api_key="EMPTY")
    return _client


def _call(messages: list[dict]) -> str:
    response = get_client().chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        temperature=0.0,
        max_tokens=1024,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    return response.choices[0].message.content.strip()


def generate(messages_batch: list[list[dict]], max_workers: int = 32) -> list[str]:
    """Send a batch of conversations to the vLLM server in parallel."""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(_call, messages_batch))
