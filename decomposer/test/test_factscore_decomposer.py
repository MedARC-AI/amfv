from amfv_decomposer.factscore_decomposer import (
    classify_claim_type,
    decompose_text,
    is_likely_claim,
    split_sentences,
)


def test_split_sentences() -> None:
    text = "Hypertension is common. Diabetes can cause kidney disease."

    sentences = split_sentences(text)

    assert sentences == [
        "Hypertension is common.",
        "Diabetes can cause kidney disease.",
    ]


def test_is_likely_claim_filters_short_text() -> None:
    assert not is_likely_claim("Thank you.")
    assert is_likely_claim("Hypertension can increase the risk of stroke.")


def test_classify_claim_type_treatment() -> None:
    claim_type = classify_claim_type(
        "Hypertension is treated with antihypertensive medication."
    )

    assert claim_type == "treatment"


def test_decompose_text_returns_claims() -> None:
    text = (
        "Hypertension is treated with lifestyle modification. "
        "Please consult a doctor."
    )

    claims = decompose_text(text)

    assert len(claims) == 1
    assert claims[0]["claim"] == "Hypertension is treated with lifestyle modification."