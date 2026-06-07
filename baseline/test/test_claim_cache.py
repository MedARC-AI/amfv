from pathlib import Path

from amfv_baseline.claim_cache import ClaimCache, claim_hash, normalize_claim


def test_normalize_claim() -> None:
    assert normalize_claim(" Asthma is treated. ") == "asthma is treated"


def test_claim_hash_is_stable() -> None:
    assert claim_hash("Asthma is treated.") == claim_hash(" asthma is treated ")


def test_claim_cache_round_trips_verified_claim(tmp_path: Path) -> None:
    cache = ClaimCache(tmp_path / "claims.sqlite")

    evidence = [
        {
            "chunk_id": "doc1::chunk_0000",
            "title": "Asthma",
            "url": "https://example.com/asthma",
            "text": "Asthma treatment commonly includes inhaled corticosteroids.",
        }
    ]

    verification = {
        "claim": "Asthma is treated with inhaled corticosteroids.",
        "verdict": "strongly supported",
        "score": 1.0,
        "lexical_overlap": 0.8,
        "explanation": "Test verification.",
    }

    cache.upsert_verified_claim(
        claim="Asthma is treated with inhaled corticosteroids.",
        claim_type="treatment",
        scope="nice-guidelines",
        evidence=evidence,
        verification=verification,
        verifier_name="test-verifier",
    )

    cached = cache.get_verified_claim(
        claim="Asthma is treated with inhaled corticosteroids.",
        scope="nice-guidelines",
    )

    assert cached is not None
    assert cached["cache"]["status"] == "hit"
    assert cached["claim_type"] == "treatment"
    assert cached["verification"]["verdict"] == "strongly supported"
    assert cached["evidence"][0]["chunk_id"] == "doc1::chunk_0000"