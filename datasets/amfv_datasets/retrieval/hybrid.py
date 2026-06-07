""""""

from __future__ import annotations

from collections.abc import Sequence

from .base import Retriever, SearchHit, reciprocal_rank_fusion


class HybridRetriever:
    def __init__(
        self,
        retrievers: Sequence[Retriever],
        weights: Sequence[float] | None = None,
        rrf_k: int = 60,
        fetch_k: int = 50,
    ):
        if not retrievers:
            raise ValueError("need at least one backend")
        self.retrievers = list(retrievers)
        self.weights = list(weights) if weights else None
        self.rrf_k = rrf_k
        self.fetch_k = fetch_k

    def retrieve(self, query: str, k: int = 10) -> list[SearchHit]:
        ranked = [r.retrieve(query, k=self.fetch_k) for r in self.retrievers]
        return reciprocal_rank_fusion(ranked, k=self.rrf_k, top_k=k, weights=self.weights)
