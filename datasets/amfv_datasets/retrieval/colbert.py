""""""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from ..corpus import Chunk
from .base import SearchHit

DEFAULT_MODEL = "lightonai/LateOn"
_INDEX_NAME = "amfv"
_META = "chunks.jsonl"


class ColBERTRetriever:
    def __init__(self, model_name: str = DEFAULT_MODEL, index_dir: str | None = None, batch_size: int = 32):
        self.model_name = model_name
        self.index_dir = index_dir
        self.batch_size = batch_size
        self._model = None
        self._index = None
        self._retriever = None
        self._by_id: dict[str, Chunk] = {}

    def _load_model(self):
        if self._model is None:
            from pylate import models

            self._model = models.ColBERT(model_name_or_path=self.model_name)
        return self._model

    def index(self, chunks: Sequence[Chunk]) -> None:
        from pylate import indexes, retrieve

        if self.index_dir is None:
            raise ValueError("set index_dir before indexing")
        chunks = list(chunks)
        self._by_id = {c.chunk_id: c for c in chunks}
        model = self._load_model()

        embeddings = model.encode(
            [f"{c.title}\n{c.text}" if c.title else c.text for c in chunks],
            batch_size=self.batch_size,
            is_query=False,
            show_progress_bar=True,
        )
        self._index = indexes.PLAID(index_folder=self.index_dir, index_name=_INDEX_NAME, override=True)
        self._index.add_documents(
            documents_ids=[c.chunk_id for c in chunks],
            documents_embeddings=embeddings,
        )
        self._retriever = retrieve.ColBERT(index=self._index)

    def retrieve(self, query: str, k: int = 10) -> list[SearchHit]:
        if self._retriever is None:
            raise RuntimeError("index() or load() first")
        model = self._load_model()
        q_emb = model.encode([query], is_query=True, show_progress_bar=False)
        results = self._retriever.retrieve(queries_embeddings=q_emb, k=k)[0]
        hits: list[SearchHit] = []
        for rank, r in enumerate(results, start=1):
            cid = str(r["id"])
            c = self._by_id.get(cid)
            hits.append(
                SearchHit(
                    chunk_id=cid,
                    score=float(r["score"]),
                    rank=rank,
                    text=c.text if c else "",
                    doc_id=c.doc_id if c else "",
                    source=c.source if c else "",
                    title=c.title if c else "",
                    url=c.url if c else "",
                    metadata=c.metadata if c else {},
                )
            )
        return hits

    def save(self, index_dir: str) -> None:

        p = Path(index_dir)
        p.mkdir(parents=True, exist_ok=True)
        (p / "config.json").write_text(json.dumps({"model_name": self.model_name}))
        with (p / _META).open("w", encoding="utf-8") as fh:
            for c in self._by_id.values():
                fh.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, index_dir: str) -> "ColBERTRetriever":
        from pylate import indexes, retrieve

        p = Path(index_dir)
        cfg = json.loads((p / "config.json").read_text())
        obj = cls(model_name=cfg["model_name"], index_dir=index_dir)
        obj._index = indexes.PLAID(index_folder=index_dir, index_name=_INDEX_NAME, override=False)
        obj._retriever = retrieve.ColBERT(index=obj._index)
        with (p / _META).open(encoding="utf-8") as fh:
            obj._by_id = {(c := Chunk(**json.loads(line))).chunk_id: c for line in fh if line.strip()}
        return obj
