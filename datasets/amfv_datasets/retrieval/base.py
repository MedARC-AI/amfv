""""""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..corpus import Chunk


@dataclass(slots=True)
class SearchHit:
    chunk_id: str
    score: float
    rank: int
    text: str = ""
    doc_id: str = ""
    source: str = ""
    title: str = ""
    url: str = ""
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class Retriever(Protocol):
    """"""

    def index(self, chunks: Sequence[Chunk]) -> None: ...
    def retrieve(self, query: str, k: int = 10) -> list[SearchHit]: ...
    def save(self, index_dir: str) -> None: ...
    @classmethod
    def load(cls, index_dir: str) -> "Retriever": ...


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[SearchHit]],
    k: int = 60,
    top_k: int = 10,
    weights: Sequence[float] | None = None,
) -> list[SearchHit]:
    """"""
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError("weights must match number of ranked lists")

    fused: dict[str, float] = {}
    best_hit: dict[str, SearchHit] = {}
    for hits, w in zip(ranked_lists, weights):
        for hit in hits:
            fused[hit.chunk_id] = fused.get(hit.chunk_id, 0.0) + w / (k + hit.rank)

            if hit.chunk_id not in best_hit or hit.rank < best_hit[hit.chunk_id].rank:
                best_hit[hit.chunk_id] = hit

    ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    out: list[SearchHit] = []
    for new_rank, (chunk_id, score) in enumerate(ordered, start=1):
        h = best_hit[chunk_id]
        out.append(
            SearchHit(
                chunk_id=chunk_id,
                score=score,
                rank=new_rank,
                text=h.text,
                doc_id=h.doc_id,
                source=h.source,
                title=h.title,
                url=h.url,
                metadata=h.metadata,
            )
        )
    return out
