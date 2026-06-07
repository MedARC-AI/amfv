from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from amfv_baseline.run_pipeline import run_pipeline


DEFAULT_EVAL_PATH = Path("data/eval/kiss_nice_eval.jsonl")
DEFAULT_RESULTS_PATH = Path("data/eval/runs/kiss_nice_eval_results.jsonl")
DEFAULT_SUMMARY_PATH = Path("data/eval/runs/kiss_nice_eval_summary.json")
DEFAULT_CACHE_PATH = Path("data/cache/kiss_eval_claims.sqlite")

PipelineFn = Callable[..., dict[str, Any]]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []

    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if line:
                records.append(json.loads(line))

    return records


def write_jsonl(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_json(record: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def collect_retrieved_chunk_ids(report: dict[str, Any]) -> list[str]:
    chunk_ids = []

    for claim_record in report.get("claims", []):
        for evidence in claim_record.get("evidence", []):
            chunk_id = evidence.get("chunk_id")

            if isinstance(chunk_id, str) and chunk_id:
                chunk_ids.append(chunk_id)

    return chunk_ids


def collect_verdicts(report: dict[str, Any]) -> list[str]:
    verdicts = []

    for claim_record in report.get("claims", []):
        verification = claim_record.get("verification", {})
        verdict = verification.get("verdict")

        if isinstance(verdict, str):
            verdicts.append(verdict)

    return verdicts


def collect_scores(report: dict[str, Any]) -> list[float]:
    scores = []

    for claim_record in report.get("claims", []):
        verification = claim_record.get("verification", {})
        score = verification.get("score")

        if isinstance(score, int | float):
            scores.append(float(score))

    return scores


def evaluate_pipeline_report(
    case: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    expected_chunk_ids = set(case.get("expected_relevant_chunk_ids", []))
    retrieved_chunk_ids = collect_retrieved_chunk_ids(report)
    retrieved_chunk_id_set = set(retrieved_chunk_ids)

    verdicts = collect_verdicts(report)
    scores = collect_scores(report)

    expected_verdict = case.get("expected_verdict", "strongly supported")
    expected_score_min = float(case.get("expected_score_min", 0.75))

    best_score = max(scores) if scores else 0.0

    retrieval_hit = bool(expected_chunk_ids & retrieved_chunk_id_set)
    verdict_match = expected_verdict in verdicts
    score_pass = best_score >= expected_score_min

    return {
        "eval_id": case["eval_id"],
        "expected_chunk_ids": sorted(expected_chunk_ids),
        "retrieved_chunk_ids": retrieved_chunk_ids,
        "retrieval_hit": retrieval_hit,
        "expected_verdict": expected_verdict,
        "actual_verdicts": verdicts,
        "verdict_match": verdict_match,
        "expected_score_min": expected_score_min,
        "best_score": round(best_score, 4),
        "score_pass": score_pass,
        "num_expected_claims": len(case.get("expected_claims", [])),
        "num_pipeline_claims": report.get("num_claims", 0),
        "cache_hits": report.get("cache_hits", 0),
        "cache_misses": report.get("cache_misses", 0),
        "case_pass": retrieval_hit and score_pass,
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)

    if total == 0:
        return {
            "num_cases": 0,
            "num_completed": 0,
            "num_errors": 0,
            "retrieval_hit_rate": 0.0,
            "score_pass_rate": 0.0,
            "verdict_match_rate": 0.0,
            "case_pass_rate": 0.0,
            "mean_best_score": 0.0,
        }

    completed = [result for result in results if "metrics" in result]
    errored = [result for result in results if "error" in result]

    if not completed:
        return {
            "num_cases": total,
            "num_completed": 0,
            "num_errors": len(errored),
            "retrieval_hit_rate": 0.0,
            "score_pass_rate": 0.0,
            "verdict_match_rate": 0.0,
            "case_pass_rate": 0.0,
            "mean_best_score": 0.0,
        }

    metrics = [result["metrics"] for result in completed]

    retrieval_hits = sum(metric["retrieval_hit"] for metric in metrics)
    score_passes = sum(metric["score_pass"] for metric in metrics)
    verdict_matches = sum(metric["verdict_match"] for metric in metrics)
    case_passes = sum(metric["case_pass"] for metric in metrics)
    best_scores = [metric["best_score"] for metric in metrics]

    return {
        "num_cases": total,
        "num_completed": len(completed),
        "num_errors": len(errored),
        "retrieval_hits": retrieval_hits,
        "score_passes": score_passes,
        "verdict_matches": verdict_matches,
        "case_passes": case_passes,
        "retrieval_hit_rate": round(retrieval_hits / len(completed), 4),
        "score_pass_rate": round(score_passes / len(completed), 4),
        "verdict_match_rate": round(verdict_matches / len(completed), 4),
        "case_pass_rate": round(case_passes / len(completed), 4),
        "mean_best_score": round(sum(best_scores) / len(best_scores), 4),
    }


def run_kiss_eval(
    cases: list[dict[str, Any]],
    pipeline_kwargs: dict[str, Any],
    pipeline_fn: PipelineFn = run_pipeline,
    fail_fast: bool = False,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    results = []

    for index, case in enumerate(cases, start=1):
        eval_id = case["eval_id"]
        input_text = case["input_text"]

        print(f"[{index}/{len(cases)}] Running {eval_id}")

        try:
            report = pipeline_fn(
                text=input_text,
                **pipeline_kwargs,
            )

            metrics = evaluate_pipeline_report(
                case=case,
                report=report,
            )

            result = {
                "eval_id": eval_id,
                "input_text": input_text,
                "metrics": metrics,
                "pipeline_report": report,
            }

            print(
                "  "
                f"retrieval_hit={metrics['retrieval_hit']} "
                f"best_score={metrics['best_score']} "
                f"case_pass={metrics['case_pass']}"
            )

        except Exception as error:
            if fail_fast:
                raise

            result = {
                "eval_id": eval_id,
                "input_text": input_text,
                "error": type(error).__name__,
                "error_message": str(error),
            }

            print(f"  ERROR: {type(error).__name__}: {error}")

        results.append(result)

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full AMFV pipeline against a KISS eval set."
    )

    parser.add_argument("--eval", type=Path, default=DEFAULT_EVAL_PATH)
    parser.add_argument("--output-jsonl", type=Path, default=DEFAULT_RESULTS_PATH)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_PATH)

    parser.add_argument(
        "--chunks",
        type=Path,
        default=Path("data/index/nice_chunks.jsonl"),
    )
    parser.add_argument(
        "--bm25-index",
        type=Path,
        default=Path("data/index/nice_bm25_index.json"),
    )
    parser.add_argument(
        "--dense-index",
        type=Path,
        default=Path("data/index/nice_dense_index.npz"),
    )
    parser.add_argument(
        "--embedding-model",
        type=str,
        default="Qwen/Qwen3-Embedding-0.6B",
    )
    parser.add_argument("--trust-remote-code", action="store_true")

    parser.add_argument("--decomposer-model", type=str, required=True)
    parser.add_argument("--decomposer-api-key", type=str, default=None)
    parser.add_argument("--decomposer-api-base", type=str, default=None)

    parser.add_argument("--verifier-model", type=str, required=True)
    parser.add_argument("--verifier-api-key", type=str, default=None)
    parser.add_argument("--verifier-api-base", type=str, default=None)

    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--reset-cache", action="store_true")
    parser.add_argument("--scope", type=str, default="kiss-nice-eval")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--fail-fast", action="store_true")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    cases = read_jsonl(args.eval)

    if args.max_cases is not None:
        cases = cases[: args.max_cases]

    if args.reset_cache and args.cache.exists():
        args.cache.unlink()
        print(f"Deleted existing cache: {args.cache}")

    pipeline_kwargs = {
        "chunks_path": args.chunks,
        "bm25_index_path": args.bm25_index,
        "dense_index_path": args.dense_index,
        "top_k": args.top_k,
        "cache_path": args.cache,
        "scope": args.scope,
        "embedding_model": args.embedding_model,
        "trust_remote_code": args.trust_remote_code,
        "decomposer_model": args.decomposer_model,
        "decomposer_api_key": args.decomposer_api_key,
        "decomposer_api_base": args.decomposer_api_base,
        "verifier_model": args.verifier_model,
        "verifier_api_key": args.verifier_api_key,
        "verifier_api_base": args.verifier_api_base,
    }

    results = run_kiss_eval(
        cases=cases,
        pipeline_kwargs=pipeline_kwargs,
        fail_fast=args.fail_fast,
        sleep_seconds=args.sleep_seconds,
    )

    summary = summarize_results(results)

    write_jsonl(results, args.output_jsonl)
    write_json(summary, args.summary_json)

    print("\nKISS eval summary")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nWrote results to {args.output_jsonl}")
    print(f"Wrote summary to {args.summary_json}")


if __name__ == "__main__":
    main()