from __future__ import annotations

from pathlib import Path
from typing import Any

from amfv_baseline.run_kiss_eval import (
    evaluate_pipeline_report,
    run_kiss_eval,
    summarize_results,
)


def test_evaluate_pipeline_report_detects_retrieval_hit() -> None:
    case = {
        "eval_id": "case_1",
        "input_text": "Offer inhaled corticosteroids for asthma.",
        "expected_relevant_chunk_ids": ["doc1::chunk_0000"],
        "expected_verdict": "strongly supported",
        "expected_score_min": 0.75,
        "expected_claims": [],
    }

    report = {
        "num_claims": 1,
        "cache_hits": 0,
        "cache_misses": 1,
        "claims": [
            {
                "claim": "Offer inhaled corticosteroids for asthma.",
                "evidence": [
                    {
                        "chunk_id": "doc1::chunk_0000",
                        "text": "Offer inhaled corticosteroids for asthma.",
                    }
                ],
                "verification": {
                    "verdict": "strongly supported",
                    "score": 1.0,
                },
            }
        ],
    }

    metrics = evaluate_pipeline_report(case=case, report=report)

    assert metrics["retrieval_hit"] is True
    assert metrics["verdict_match"] is True
    assert metrics["score_pass"] is True
    assert metrics["case_pass"] is True


def test_evaluate_pipeline_report_detects_retrieval_miss() -> None:
    case = {
        "eval_id": "case_1",
        "input_text": "Offer inhaled corticosteroids for asthma.",
        "expected_relevant_chunk_ids": ["doc1::chunk_0000"],
        "expected_verdict": "strongly supported",
        "expected_score_min": 0.75,
        "expected_claims": [],
    }

    report = {
        "num_claims": 1,
        "cache_hits": 0,
        "cache_misses": 1,
        "claims": [
            {
                "claim": "Offer inhaled corticosteroids for asthma.",
                "evidence": [
                    {
                        "chunk_id": "wrong::chunk_0000",
                        "text": "Wrong evidence.",
                    }
                ],
                "verification": {
                    "verdict": "unclear",
                    "score": 0.5,
                },
            }
        ],
    }

    metrics = evaluate_pipeline_report(case=case, report=report)

    assert metrics["retrieval_hit"] is False
    assert metrics["verdict_match"] is False
    assert metrics["score_pass"] is False
    assert metrics["case_pass"] is False


def test_run_kiss_eval_with_fake_pipeline(tmp_path: Path) -> None:
    cases = [
        {
            "eval_id": "case_1",
            "input_text": "Offer inhaled corticosteroids for asthma.",
            "expected_relevant_chunk_ids": ["doc1::chunk_0000"],
            "expected_verdict": "strongly supported",
            "expected_score_min": 0.75,
            "expected_claims": [],
        }
    ]

    def fake_pipeline(text: str, **kwargs: Any) -> dict[str, Any]:
        assert text == "Offer inhaled corticosteroids for asthma."
        assert kwargs["cache_path"] == tmp_path / "cache.sqlite"

        return {
            "num_claims": 1,
            "cache_hits": 0,
            "cache_misses": 1,
            "claims": [
                {
                    "claim": text,
                    "evidence": [
                        {
                            "chunk_id": "doc1::chunk_0000",
                            "text": text,
                        }
                    ],
                    "verification": {
                        "verdict": "strongly supported",
                        "score": 1.0,
                    },
                }
            ],
        }

    results = run_kiss_eval(
        cases=cases,
        pipeline_kwargs={
            "cache_path": tmp_path / "cache.sqlite",
        },
        pipeline_fn=fake_pipeline,
    )

    assert len(results) == 1
    assert results[0]["metrics"]["case_pass"] is True


def test_summarize_results() -> None:
    results = [
        {
            "eval_id": "case_1",
            "metrics": {
                "retrieval_hit": True,
                "score_pass": True,
                "verdict_match": True,
                "case_pass": True,
                "best_score": 1.0,
            },
        },
        {
            "eval_id": "case_2",
            "metrics": {
                "retrieval_hit": False,
                "score_pass": False,
                "verdict_match": False,
                "case_pass": False,
                "best_score": 0.5,
            },
        },
    ]

    summary = summarize_results(results)

    assert summary["num_cases"] == 2
    assert summary["retrieval_hit_rate"] == 0.5
    assert summary["score_pass_rate"] == 0.5
    assert summary["case_pass_rate"] == 0.5
    assert summary["mean_best_score"] == 0.75