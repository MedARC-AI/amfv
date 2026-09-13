"""Disk cache for LLM generation outputs keyed by input hash."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

__all__ = ["GenerationCache"]

_DEFAULT_DIR = Path.home() / ".cache" / "amfv_eval"


class GenerationCache:
    """Persistent key-value cache backed by individual JSON files.

    Each entry is stored as ``<cache_dir>/<sha256>.json`` so the cache
    survives across processes and can be safely shared concurrently (writes
    are atomic via rename).

    Args:
        cache_dir: Directory for cache files (default: ``~/.cache/amfv_eval``).
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._dir = Path(cache_dir) if cache_dir else _DEFAULT_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def key(self, *parts: str) -> str:
        """Compute a deterministic cache key from arbitrary string parts."""
        combined = "\x00".join(parts)
        return hashlib.sha256(combined.encode()).hexdigest()

    def get(self, cache_key: str) -> dict | None:
        """Return cached value or ``None`` if absent."""
        path = self._dir / f"{cache_key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, cache_key: str, value: dict) -> None:
        """Persist ``value`` under ``cache_key`` atomically."""
        path = self._dir / f"{cache_key}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False))
        tmp.replace(path)

    def __contains__(self, cache_key: str) -> bool:
        return (self._dir / f"{cache_key}.json").exists()
