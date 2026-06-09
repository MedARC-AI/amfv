#!/usr/bin/env python3
"""Evaluate baseline decomposers on AskDocsAI and PUMA datasets.

Usage:
    python evaluate.py --data /path/to/AskDocs.jsonl --decomposers factscore medscore veriscore
    python evaluate.py --data /path/to/AskDocs.demo.jsonl --max-records 5   # quick test
    python evaluate.py --data /path/to/AskDocs.jsonl --enable-thinking       # Qwen3 think mode
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path

from amfv_decomposer import vllm_client
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

# Decomposers that require a running vLLM server.
_VLLM_DECOMPOSERS = frozenset({"factscore", "medscore", "veriscore"})


def build_output_dir(
    base: Path,
    model_id: str,
    enable_thinking: bool,
    dataset_stem: str,
    timestamp: str,
) -> Path:
    shortname = model_id.rsplit("/", 1)[-1]
    suffix = "-think" if enable_thinking else ""
    return base / f"{shortname}{suffix}" / dataset_stem / timestamp


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate claim decomposers")
    parser.add_argument("--data", required=True, type=Path, help="Path to .jsonl dataset")
    parser.add_argument(
        "--decomposers",
        nargs="+",
        default=list(DECOMPOSER_REGISTRY.keys()),
        choices=list(DECOMPOSER_REGISTRY.keys()),
        metavar="METHOD",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results"),
        dest="output_base",
        help="Base results directory. Versioned subdirs are created automatically.",
    )
    parser.add_argument("--max-records", type=int, default=None, dest="max_records")
    parser.add_argument("--text-key", default="response", dest="text_key")
    parser.add_argument(
        "--context-key",
        default=None,
        dest="context_key",
        help="Record field to use as context (e.g. 'question').",
    )
    parser.add_argument(
        "--enable-thinking",
        action="store_true",
        default=False,
        dest="enable_thinking",
        help="Enable Qwen3 chain-of-thought for vLLM-backed decomposers.",
    )
    parser.add_argument(
        "--model-name",
        default=None,
        dest="model_name",
        help="Model name for output path. Auto-queried from the vLLM server when omitted. "
             "Required for non-vLLM runs (e.g. veriscore_original).",
    )
    return parser


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
    args = make_parser().parse_args()

    vllm_client.configure(enable_thinking=args.enable_thinking)

    uses_vllm = any(d in _VLLM_DECOMPOSERS for d in args.decomposers)
    if args.model_name:
        model_id = args.model_name
    elif uses_vllm:
        model_id = vllm_client.get_served_model()
    else:
        raise SystemExit(
            "ERROR: --model-name is required when no vLLM-backed decomposer is selected."
        )

    dataset_stem = args.data.stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = build_output_dir(
        args.output_base, model_id, args.enable_thinking, dataset_stem, timestamp
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    records = load_jsonl(args.data)
    if args.max_records:
        records = records[: args.max_records]

    print(f"Loaded {len(records)} records from {args.data.name}")
    print(f"Output → {output_dir}")

    table_results: dict[str, dict] = {}
    all_predictions: dict[str, list[dict]] = {}

    for name in args.decomposers:
        print(f"\n[{name}] decomposing {len(records)} records...")
        decomposer = DECOMPOSER_REGISTRY[name]()
        claims_per_record = decomposer.decompose_batch(
            records, text_key=args.text_key, context_key=args.context_key
        )

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

        out_path = output_dir / f"{name}_{dataset_stem}.json"
        with open(out_path, "w") as f:
            json.dump({"method": name, "metrics": metrics, "predictions": predictions}, f, indent=2)
        print(f"  saved → {out_path}")

    sample_records = records[:min(20, len(records))]
    claims_index = {
        name: {p["id"]: p["claims"] for p in preds}
        for name, preds in all_predictions.items()
    }
    sample_path = output_dir / f"sample_for_annotation_{dataset_stem}.jsonl"
    with open(sample_path, "w") as f:
        for i, r in enumerate(sample_records):
            rid = r.get("id", str(i))
            entry: dict = {"id": rid, args.text_key: r[args.text_key]}
            for name in args.decomposers:
                entry[name] = claims_index[name].get(rid, [])
            f.write(json.dumps(entry) + "\n")
    print(f"\nAnnotation sample ({len(sample_records)} records) → {sample_path}")

    summary_path = output_dir / f"summary_{dataset_stem}.json"
    with open(summary_path, "w") as f:
        json.dump(table_results, f, indent=2)

    print(f"\n## Results — {dataset_stem}\n")
    print(format_table(table_results))


if __name__ == "__main__":
    main()
