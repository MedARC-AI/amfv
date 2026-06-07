import numpy as np

from amfv_search.hybrid_retrieval import (
    dense_search_from_embedding,
    hybrid_rank,
    save_dense_index,
    load_dense_index,
)


def test_dense_index_round_trip(tmp_path) -> None:
    embeddings = np.asarray(
        [
            [1.0, 0.0],
            [0.0, 1.0],
        ],
        dtype=np.float32,
    )

    path = tmp_path / "dense_index.npz"

    save_dense_index(
        embeddings=embeddings,
        chunk_ids=["chunk_1", "chunk_2"],
        model_name="test-model",
        output_path=path,
    )

    loaded = load_dense_index(path)

    assert loaded["model_name"] == "test-model"
    assert loaded["chunk_ids"] == ["chunk_1", "chunk_2"]
    assert loaded["embeddings"].shape == (2, 2)


def test_dense_search_from_embedding_returns_nearest_chunk() -> None:
    dense_index = {
        "embeddings": np.asarray(
            [
                [1.0, 0.0],
                [0.0, 1.0],
            ],
            dtype=np.float32,
        ),
        "chunk_ids": ["asthma_chunk", "diabetes_chunk"],
        "model_name": "test-model",
    }

    chunks_by_id = {
        "asthma_chunk": {
            "chunk_id": "asthma_chunk",
            "title": "Asthma",
            "url": "https://example.com/asthma",
            "text": "Asthma treatment includes inhaled corticosteroids.",
        },
        "diabetes_chunk": {
            "chunk_id": "diabetes_chunk",
            "title": "Diabetes",
            "url": "https://example.com/diabetes",
            "text": "Diabetes treatment may include insulin.",
        },
    }

    results = dense_search_from_embedding(
        query_embedding=np.asarray([1.0, 0.0], dtype=np.float32),
        dense_index=dense_index,
        chunks_by_id=chunks_by_id,
        top_k=1,
    )

    assert results[0]["chunk_id"] == "asthma_chunk"


def test_hybrid_rank_combines_bm25_and_dense_results() -> None:
    bm25_results = [
        {
            "chunk_id": "chunk_a",
            "score": 3.0,
            "title": "BM25 result",
            "url": "https://example.com/a",
            "text": "BM25 matched text.",
        },
        {
            "chunk_id": "chunk_b",
            "score": 2.0,
            "title": "Shared result",
            "url": "https://example.com/b",
            "text": "Shared matched text.",
        },
    ]

    dense_results = [
        {
            "chunk_id": "chunk_b",
            "score": 0.9,
            "title": "Shared result",
            "url": "https://example.com/b",
            "text": "Shared matched text.",
        },
        {
            "chunk_id": "chunk_c",
            "score": 0.8,
            "title": "Dense result",
            "url": "https://example.com/c",
            "text": "Dense matched text.",
        },
    ]

    results = hybrid_rank(
        bm25_results=bm25_results,
        dense_results=dense_results,
        top_k=3,
    )

    assert results[0]["chunk_id"] == "chunk_b"
    assert results[0]["bm25_rank"] == 2
    assert results[0]["dense_rank"] == 1
    assert results[0]["retrieval_method"] == "hybrid_bm25_dense"