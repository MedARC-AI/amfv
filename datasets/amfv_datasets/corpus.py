""""""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Document:
    """"""

    doc_id: str
    source: str
    title: str
    text: str
    url: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class Chunk:
    """"""

    chunk_id: str
    doc_id: str
    source: str
    title: str
    text: str
    ordinal: int
    url: str = ""
    metadata: dict = field(default_factory=dict)


def write_jsonl(path: str | Path, records: Iterable) -> int:
    """"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            obj = rec if isinstance(rec, dict) else asdict(rec)
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
            n += 1
    return n


def _read_jsonl(path: str | Path) -> Iterator[dict]:
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_documents(path: str | Path) -> Iterator[Document]:
    for obj in _read_jsonl(path):
        yield Document(**obj)


def read_chunks(path: str | Path) -> list[Chunk]:
    return [Chunk(**obj) for obj in _read_jsonl(path)]
