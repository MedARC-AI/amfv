import pytest

from app.services.fact_decomp_review import (
    FactDecompCorrectionMetadata,
    ModelCorrectionRating,
)


def _metadata() -> dict:
    return {
        "schema_version": 1,
        "review_mode": "MODEL_LABEL_CORRECTION",
        "case_id": "case-1",
        "arm_id": "arm-1",
        "canonical_row_sha256": "a" * 64,
        "generator": {
            "model_id": "model",
            "prompt_id": "prompt-1",
            "prompt_hash": "b" * 64,
            "pydantic_ai_version": "2.33.0",
            "generation": {},
        },
        "ordered_claim_annotations": [],
    }


def test_correction_metadata_is_strict_and_bounded() -> None:
    parsed = FactDecompCorrectionMetadata.model_validate(_metadata())
    assert parsed.review_mode == "MODEL_LABEL_CORRECTION"

    with pytest.raises(ValueError):
        FactDecompCorrectionMetadata.model_validate({**_metadata(), "private": True})
    with pytest.raises(ValueError):
        FactDecompCorrectionMetadata.model_validate(
            {**_metadata(), "case_id": "!unsafe"}
        )


def test_correction_rating_rejects_unbounded_claim_lists() -> None:
    with pytest.raises(ValueError):
        ModelCorrectionRating.model_validate(
            {
                "review_mode": "MODEL_LABEL_CORRECTION",
                "proposed_labels": ["substantive"] * 10_001,
                "final_claims": [],
            }
        )

    with pytest.raises(ValueError):
        ModelCorrectionRating.model_validate(
            {
                "review_mode": "MODEL_LABEL_CORRECTION",
                "proposed_labels": [],
                "final_claims": [
                    {
                        "original_position": None,
                        "claim_text": "claim",
                        "response_spans": [{"start": 0, "end": 1, "text": "c"}],
                        "label": "substantive",
                    }
                ]
                * 10_001,
            }
        )
