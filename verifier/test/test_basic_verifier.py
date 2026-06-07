from amfv_verifier.basic_verifier import (
    assign_verdict,
    lexical_overlap_score,
    verify_claim,
    verify_report,
)


def test_lexical_overlap_score() -> None:
    claim = "Asthma is treated with inhaled corticosteroids."
    evidence = "Asthma treatment commonly includes inhaled corticosteroids."

    score = lexical_overlap_score(claim, evidence)

    assert score > 0.5


def test_assign_verdict() -> None:
    assert assign_verdict(0.8) == "strongly supported"
    assert assign_verdict(0.5) == "weakly supported"
    assert assign_verdict(0.3) == "unclear"
    assert assign_verdict(0.1) == "weakly unsubstantiated"
    assert assign_verdict(0.0) == "strongly unsubstantiated"


def test_verify_claim_returns_verdict() -> None:
    evidence = [
        {
            "text": "Asthma treatment commonly includes inhaled corticosteroids.",
        }
    ]

    result = verify_claim(
        claim="Asthma is treated with inhaled corticosteroids.",
        evidence=evidence,
    )

    assert result["verdict"] in {
        "strongly supported",
        "weakly supported",
        "unclear",
        "weakly unsubstantiated",
        "strongly unsubstantiated",
    }
    assert 0.0 <= result["score"] <= 1.0


def test_verify_report_adds_hallucination_score() -> None:
    report = {
        "input_text": "Asthma is treated with inhaled corticosteroids.",
        "num_claims": 1,
        "claims": [
            {
                "claim": "Asthma is treated with inhaled corticosteroids.",
                "claim_type": "treatment",
                "evidence": [
                    {
                        "text": (
                            "Asthma treatment commonly includes inhaled "
                            "corticosteroids."
                        ),
                    }
                ],
            }
        ],
    }

    verified_report = verify_report(report)

    assert "hallucination_score" in verified_report
    assert "mean_verification_score" in verified_report
    assert "verification" in verified_report["claims"][0]