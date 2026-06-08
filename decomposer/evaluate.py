#!/usr/bin/env python3
"""Evaluate baseline decomposers on AskDocsAI and PUMA datasets.

Usage:
    python evaluate.py --data /path/to/AskDocs.jsonl --decomposers factscore medscore veriscore
    python evaluate.py --data /path/to/AskDocs.demo.jsonl --max-records 5   # quick test
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from amfv_decomposer.base import split_sentences
from amfv_decomposer.baselines import (
    FActScoreDecomposer,
    MedScoreDecomposer,
    VeriScoreDecomposer,
    VeriScoreOriginalDecomposer,
)

DECOMPOSER_REGISTRY = {
    "factscore": FActScoreDecomposer,
    "medscore": MedScoreDecomposer,
    "veriscore": VeriScoreDecomposer,
    "veriscore_original": VeriScoreOriginalDecomposer,
}


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def compute_metrics(records: list[dict], claims_per_record: list[list[str]], text_key: str) -> dict:
    total_sentences = sum(len(split_sentences(r[text_key])) for r in records)
    total_claims = sum(len(c) for c in claims_per_record)
    zero_claim_count = sum(1 for c in claims_per_record if len(c) == 0)

    return {
        "n_records": len(records),
        "claims_per_response": statistics.mean(len(c) for c in claims_per_record),
        "claims_per_sentence": total_claims / max(total_sentences, 1),
        "zero_claim_rate": zero_claim_count / len(records),
        "total_claims": total_claims,
        "total_sentences": total_sentences,
    }


def format_table(results: dict[str, dict]) -> str:
    header = "| Method    | Claims/Response | Claims/Sentence | 0-claim rate |"
    sep    = "|-----------|----------------|----------------|-------------|"
    rows = [header, sep]
    for method, m in results.items():
        rows.append(
            f"| {method:<9} | {m['claims_per_response']:>14.2f} | "
            f"{m['claims_per_sentence']:>14.2f} | {m['zero_claim_rate']:>11.1%} |"
        )
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate claim decomposers")
    parser.add_argument("--data", required=True, type=Path, help="Path to .jsonl dataset")
    parser.add_argument(
        "--decomposers",
        nargs="+",
        default=list(DECOMPOSER_REGISTRY.keys()),
        choices=list(DECOMPOSER_REGISTRY.keys()),
        metavar="METHOD",
    )
    parser.add_argument("--output", type=Path, default=Path("results"))
    parser.add_argument("--max-records", type=int, default=None, dest="max_records")
    parser.add_argument("--text-key", default="response", dest="text_key")
    args = parser.parse_args()

    records = load_jsonl(args.data)
    if args.max_records:
        records = records[: args.max_records]

    print(f"Loaded {len(records)} records from {args.data.name}")
    args.output.mkdir(parents=True, exist_ok=True)

    table_results: dict[str, dict] = {}
    all_predictions: dict[str, list[dict]] = {}

    for name in args.decomposers:
        print(f"\n[{name}] decomposing {len(records)} records...")
        decomposer = DECOMPOSER_REGISTRY[name]()
        claims_per_record = decomposer.decompose_batch(records, text_key=args.text_key)

        metrics = compute_metrics(records, claims_per_record, args.text_key)
        table_results[name] = metrics

        print(f"  claims/response : {metrics['claims_per_response']:.2f}")
        print(f"  claims/sentence : {metrics['claims_per_sentence']:.2f}")
        print(f"  0-claim rate    : {metrics['zero_claim_rate']:.1%}")

        predictions = [
            {"id": r.get("id", str(i)), "claims": claims}
            for i, (r, claims) in enumerate(zip(records, claims_per_record))
        ]
        all_predictions[name] = predictions

        out_path = args.output / f"{name}_{args.data.stem}.json"
        with open(out_path, "w") as f:
            json.dump({"method": name, "metrics": metrics, "predictions": predictions}, f, indent=2)
        print(f"  saved → {out_path}")

    # Save annotation sample: 20 records with all methods' claims side by side
    sample_records = records[:20]
    claims_index = {
        name: {p["id"]: p["claims"] for p in preds}
        for name, preds in all_predictions.items()
    }
    sample_path = args.output / f"sample_for_annotation_{args.data.stem}.jsonl"
    with open(sample_path, "w") as f:
        for i, r in enumerate(sample_records):
            rid = r.get("id", str(i))
            entry: dict = {"id": rid, args.text_key: r[args.text_key]}
            for name in args.decomposers:
                entry[name] = claims_index[name].get(rid, [])
            f.write(json.dumps(entry) + "\n")
    print(f"\nAnnotation sample (20 records) → {sample_path}")

    # Summary JSON
    summary_path = args.output / f"summary_{args.data.stem}.json"
    with open(summary_path, "w") as f:
        json.dump(table_results, f, indent=2)

    print(f"\n## Results — {args.data.stem}\n")
    print(format_table(table_results))


if __name__ == "__main__":
    main()
