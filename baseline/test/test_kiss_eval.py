from __future__ import annotations

from amfv_baseline.kiss_eval import (
    classify_claim_type,
    generate_kiss_eval_set,
    infer_certainty,
    looks_like_claim_sentence,
    split_sentences,
    summarize_cases,
)


def test_split_sentences_returns_clean_sentences() -> None:
    text = (
        "1.1.1 Offer inhaled corticosteroids for asthma. "
        "Do not offer antibiotics for viral infections."
    )

    sentences = split_sentences(text)

    assert sentences[0] == "Offer inhaled corticosteroids for asthma."
    assert sentences[1] == "Do not offer antibiotics for viral infections."


def test_looks_like_claim_sentence_accepts_medical_claim() -> None:
    sentence = "Offer inhaled corticosteroids for adults with asthma symptoms."

    assert looks_like_claim_sentence(sentence) is True


def test_looks_like_claim_sentence_rejects_low_value_text() -> None:
    sentence = "This guideline covers information about asthma management."

    assert looks_like_claim_sentence(sentence) is False


def test_classify_claim_type() -> None:
    assert classify_claim_type("Offer inhaled corticosteroids for asthma.") == (
        "treatment"
    )
    assert classify_claim_type("Screening is recommended for this population.") == (
        "screening"
    )
    assert classify_claim_type("This test can diagnose the condition.") == (
        "diagnosis"
    )
    assert classify_claim_type("The drug is contraindicated in pregnancy.") == (
        "safety"
    )


def test_infer_certainty() -> None:
    assert infer_certainty("Offer inhaled corticosteroids for asthma.") == (
        "asserted"
    )
    assert infer_certainty("Consider oral corticosteroids for severe asthma.") == (
        "hedged"
    )
    assert infer_certainty("Do not offer antibiotics for viral infection.") == (
        "negated"
    )


def test_generate_kiss_eval_set_from_chunks() -> None:
    chunks = [
        {
            "chunk_id": "doc1::chunk_0000",
            "doc_id": "doc1",
            "source": "nice",
            "title": "Asthma",
            "url": "https://example.com/asthma",
            "chunk_index": 0,
            "text": (
                "Offer inhaled corticosteroids for adults with asthma symptoms. "
                "This guideline covers information about asthma management."
            ),
        }
    ]

    cases = generate_kiss_eval_set(
        chunks=chunks,
        max_cases=1,
        max_per_title=1,
    )

    assert len(cases) == 1
    assert cases[0]["dataset_id"] == "epfl-llm/guidelines"
    assert cases[0]["source_name"] == "NICE"
    assert cases[0]["expected_relevant_chunk_ids"] == ["doc1::chunk_0000"]
    assert cases[0]["expected_verdict"] == "strongly supported"
    assert cases[0]["expected_claims"][0]["claim"] == (
        "Offer inhaled corticosteroids for adults with asthma symptoms."
    )


def test_generate_kiss_eval_set_respects_text_filter() -> None:
    chunks = [
        {
            "chunk_id": "doc1::chunk_0000",
            "doc_id": "doc1",
            "source": "nice",
            "title": "Asthma",
            "url": "https://example.com/asthma",
            "chunk_index": 0,
            "text": "Offer inhaled corticosteroids for adults with asthma symptoms.",
        },
        {
            "chunk_id": "doc2::chunk_0000",
            "doc_id": "doc2",
            "source": "nice",
            "title": "Diabetes",
            "url": "https://example.com/diabetes",
            "chunk_index": 0,
            "text": "Offer metformin for adults with type 2 diabetes.",
        },
    ]

    cases = generate_kiss_eval_set(
        chunks=chunks,
        max_cases=2,
        text_contains="diabetes",
    )

    assert len(cases) == 1
    assert cases[0]["source"]["title"] == "Diabetes"


def test_summarize_cases() -> None:
    chunks = [
        {
            "chunk_id": "doc1::chunk_0000",
            "doc_id": "doc1",
            "source": "nice",
            "title": "Asthma",
            "url": "https://example.com/asthma",
            "chunk_index": 0,
            "text": "Offer inhaled corticosteroids for adults with asthma symptoms.",
        }
    ]

    cases = generate_kiss_eval_set(chunks=chunks, max_cases=1)
    summary = summarize_cases(cases)

    assert summary["num_cases"] == 1
    assert summary["claim_types"]["treatment"] == 1
    assert summary["num_titles"] == 1