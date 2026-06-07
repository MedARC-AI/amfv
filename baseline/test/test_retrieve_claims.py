import json
from pathlib import Path

from amfv_baseline.retrieve_claims import build_claim_retrieval_report
from amfv_search.bm25_index import build_bm25_index, write_jsonl


def test_build_claim_retrieval_report(tmp_path: Path) -> None:
    chunks = [
        {
            "chunk_id": "doc1::chunk_0000",
            "doc_id": "doc1",
            "source": "nice",
            "title": "Asthma",
            "url": "https://example.com/asthma",
            "chunk_index": 0,
            "text": "Asthma is commonly treated with inhaled corticosteroids.",
        },
        {
            "chunk_id": "doc2::chunk_0000",
            "doc_id": "doc2",
            "source": "nice",
            "title": "Hypertension",
            "url": "https://example.com/hypertension",
            "chunk_index": 0,
            "text": "Hypertension is treated with antihypertensive medication.",
        },
    ]

    chunks_path = tmp_path / "chunks.jsonl"
    index_path = tmp_path / "index.json"

    write_jsonl(chunks, chunks_path)

    index = build_bm25_index(chunks)
    index_path.write_text(json.dumps(index), encoding="utf-8")

    report = build_claim_retrieval_report(
        text="Asthma is commonly treated with inhaled corticosteroids.",
        chunks_path=chunks_path,
        index_path=index_path,
        top_k=1,
    )

    assert report["num_claims"] == 1
    assert report["claims"][0]["claim"] == (
        "Asthma is commonly treated with inhaled corticosteroids."
    )
    assert report["claims"][0]["evidence"][0]["chunk_id"] == "doc1::chunk_0000"