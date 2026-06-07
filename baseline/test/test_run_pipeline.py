from __future__ import annotations

from pathlib import Path
from typing import Any

import amfv_baseline.run_pipeline as pipeline


class FakeHybridRetriever:
    def __init__(
        self,
        chunks_path: Path,
        bm25_index_path: Path,
        dense_index_path: Path,
        model_name: str | None = None,
        trust_remote_code: bool = False,
    ) -> None:
        self.chunks_path = chunks_path
        self.bm25_index_path = bm25_index_path
        self.dense_index_path = dense_index_path
        self.model_name = model_name
        self.trust_remote_code = trust_remote_code

    def search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        return [
            {
                "chunk_id": "doc1::chunk_0000",
                "doc_id": "doc1",
                "source": "nice",
                "title": "Asthma",
                "url": "https://example.com/asthma",
                "chunk_index": 0,
                "text": "Asthma treatment commonly includes inhaled corticosteroids.",
                "hybrid_score": 0.032,
                "bm25_rank": 1,
                "dense_rank": 1,
                "bm25_score": 4.0,
                "dense_score": 0.9,
                "retrieval_method": "hybrid_bm25_dense",
            }
        ][:top_k]


def fake_decomposer(text: str) -> list[dict[str, Any]]:
    return [
        {
            "claim": "Asthma is treated with inhaled corticosteroids.",
            "claim_type": "treatment",
            "certainty": "asserted",
            "requires_context": True,
        }
    ]

def fake_verifier(
    claim: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "claim": claim,
        "verdict": "strongly supported",
        "medv1_score": 2,
        "score": 1.0,
        "confidence": "high",
        "supported_evidence_ids": ["e1"],
        "contradicted_evidence_ids": [],
        "missing_context": [],
        "explanation": "The fake evidence supports the fake claim.",
        "verifier_name": "llm_medv1_style_verifier",
        "verifier_model": "test-verifier",
    }


def test_run_pipeline_uses_llm_decomposer_contract_and_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(pipeline, "HybridRetriever", FakeHybridRetriever)

    cache_path = tmp_path / "claims.sqlite"

    first_report = pipeline.run_pipeline(
        text="Asthma is treated with inhaled corticosteroids.",
        chunks_path=tmp_path / "chunks.jsonl",
        bm25_index_path=tmp_path / "bm25.json",
        dense_index_path=tmp_path / "dense.npz",
        top_k=1,
        cache_path=cache_path,
        scope="nice-guidelines-dense",
        embedding_model="test-model",
        decomposer_model="test-decomposer",
        decomposer=fake_decomposer,
        verifier_model="test-verifier",
        verifier=fake_verifier,
    )

    assert first_report["decomposer"]["method"] == "llm_factscore_style"
    assert first_report["decomposer"]["model"] == "test-decomposer"
    assert first_report["cache_hits"] == 0
    assert first_report["cache_misses"] == 1
    assert first_report["claims"][0]["certainty"] == "asserted"
    assert first_report["claims"][0]["requires_context"] is True
    assert first_report["verifier"]["method"] == "llm_medv1_style"
    assert first_report["verifier"]["model"] == "test-verifier"
    assert first_report["claims"][0]["verification"]["medv1_score"] == 2
    assert first_report["claims"][0]["verification"]["confidence"] == "high"

    second_report = pipeline.run_pipeline(
        text="Asthma is treated with inhaled corticosteroids.",
        chunks_path=tmp_path / "missing_chunks.jsonl",
        bm25_index_path=tmp_path / "missing_bm25.json",
        dense_index_path=tmp_path / "missing_dense.npz",
        top_k=1,
        cache_path=cache_path,
        scope="nice-guidelines-dense",
        embedding_model="test-model",
        decomposer_model="test-decomposer",
        decomposer=fake_decomposer,
        verifier_model="test-verifier",
        verifier=fake_verifier,
    )

    assert second_report["cache_hits"] == 1
    assert second_report["cache_misses"] == 0
    assert second_report["claims"][0]["cache"]["status"] == "hit"
    assert second_report["claims"][0]["evidence"][0]["chunk_id"] == (
        "doc1::chunk_0000"
    )