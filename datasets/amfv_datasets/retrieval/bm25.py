""""""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from ..corpus import Chunk
from .base import SearchHit

_META = "chunks.jsonl"


class BM25Retriever:
    def __init__(self, stopwords: str = "en"):
        self.stopwords = stopwords
        self._bm25 = None
        self._chunks: list[Chunk] = []

    def index(self, chunks: Sequence[Chunk]) -> None:
        import bm25s

        self._chunks = list(chunks)
        corpus = [f"{c.title}\n{c.text}" if c.title else c.text for c in self._chunks]
        tokens = bm25s.tokenize(corpus, stopwords=self.stopwords, show_progress=False)
        self._bm25 = bm25s.BM25()
        self._bm25.index(tokens, show_progress=False)

    def retrieve(self, query: str, k: int = 10) -> list[SearchHit]:
        import bm25s

        if self._bm25 is None:
            raise RuntimeError("index() or load() first")
        q = bm25s.tokenize(query, stopwords=self.stopwords, show_progress=False)
        k = min(k, len(self._chunks))
        idxs, scores = self._bm25.retrieve(q, k=k, show_progress=False)
        hits: list[SearchHit] = []
        for rank, (i, s) in enumerate(zip(idxs[0], scores[0]), start=1):
            c = self._chunks[int(i)]
            hits.append(
                SearchHit(
                    chunk_id=c.chunk_id,
                    score=float(s),
                    rank=rank,
                    text=c.text,
                    doc_id=c.doc_id,
                    source=c.source,
                    title=c.title,
                    url=c.url,
                    metadata=c.metadata,
                )
            )
        return hits

    def save(self, index_dir: str) -> None:
        p = Path(index_dir)
        p.mkdir(parents=True, exist_ok=True)
        self._bm25.save(str(p / "bm25"))
        with (p / _META).open("w", encoding="utf-8") as fh:
            for c in self._chunks:
                fh.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, index_dir: str) -> "BM25Retriever":
        import bm25s

        p = Path(index_dir)
        obj = cls()
        obj._bm25 = bm25s.BM25.load(str(p / "bm25"))
        with (p / _META).open(encoding="utf-8") as fh:
            obj._chunks = [Chunk(**json.loads(line)) for line in fh if line.strip()]
        return obj
