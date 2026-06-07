from amfv_search.bm25_index import chunk_text, search_bm25, build_bm25_index


def test_chunk_text_splits_long_text() -> None:
    text = "This is sentence one. " * 200

    chunks = chunk_text(text, max_chars=300, overlap_chars=50)

    assert len(chunks) > 1
    assert all(len(chunk) <= 350 for chunk in chunks)


def test_bm25_search_returns_relevant_chunk() -> None:
    chunks = [
        {
            "chunk_id": "doc1::chunk_0000",
            "text": "Hypertension is treated with lifestyle changes and medication.",
            "title": "Hypertension",
            "url": "https://example.com/hypertension",
        },
        {
            "chunk_id": "doc2::chunk_0000",
            "text": "Asthma is commonly treated with inhaled corticosteroids.",
            "title": "Asthma",
            "url": "https://example.com/asthma",
        },
    ]

    index = build_bm25_index(chunks)

    results = search_bm25(
        query="hypertension medication",
        chunks=chunks,
        index=index,
        top_k=1,
    )

    assert results[0]["chunk_id"] == "doc1::chunk_0000"