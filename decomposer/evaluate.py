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
from datetime import datetime
from pathlib import Path

from amfv_decomposer import vllm_client
from amfv_decomposer.base import RecordResult
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

# veriscore_original needs the [hf] extras and a GPU, so it is opt-in.
_DEFAULT_DECOMPOSERS = ["factscore", "medscore", "veriscore"]


def build_output_dir(
    base: Path,
    model_id: str,
    enable_thinking: bool,
    dataset_stem: str,
    timestamp: str,
) -> Path:
    """Build the versioned output path: <base>/<model>[-think]/<dataset>/<timestamp>."""
    shortname = model_id.rsplit("/", 1)[-1]
    suffix = "-think" if enable_thinking else ""
    return base / f"{shortname}{suffix}" / dataset_stem / timestamp


def make_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Evaluate claim decomposers")
    parser.add_argument("--data", required=True, type=Path, help="Path to .jsonl dataset")
    parser.add_argument(
        "--decomposers",
        nargs="+",
        default=_DEFAULT_DECOMPOSERS,
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
    parser.add_argument("--max-records", type=int, default=None)
    parser.add_argument("--text-key", default="response")
    parser.add_argument(
        "--context-key",
        default=None,
        help="Record field to use as context (e.g. 'question').",
    )
    parser.add_argument(
        "--enable-thinking",
        action="store_true",
        help="Enable Qwen3 chain-of-thought for vLLM-backed decomposers.",
    )
    parser.add_argument(
        "--run-label",
        default=None,
        help="Label for the output path. Auto-queried from the vLLM server when "
             "omitted; required when no vLLM-backed decomposer is selected.",
    )
    return parser


def load_jsonl(path: Path) -> list[dict]:
    """Load one JSON record per non-empty line."""
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def compute_metrics(results: list[RecordResult]) -> dict:
    """Aggregate claim-count metrics over per-record decomposition results."""
    total_sentences = sum(res.n_sentences for res in results)
    total_claims = sum(len(res.claims) for res in results)
    n_records = max(len(results), 1)
    return {
        "n_records": len(results),
        "claims_per_response": total_claims / n_records,
        "claims_per_sentence": total_claims / max(total_sentences, 1),
        "zero_claim_rate": sum(1 for res in results if not res.claims) / n_records,
        "total_claims": total_claims,
        "total_sentences": total_sentences,
    }


def format_table(results: dict[str, dict]) -> str:
    """Render per-method metrics as a markdown table."""
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
    """Run the selected decomposers over the dataset and write versioned results."""
    args = make_parser().parse_args()

    vllm_client.configure(enable_thinking=args.enable_thinking)

    uses_vllm = any(DECOMPOSER_REGISTRY[d].backend == "vllm" for d in args.decomposers)
    if args.run_label:
        model_id = args.run_label
    elif uses_vllm:
        model_id = vllm_client.get_served_model()
    else:
        raise SystemExit(
            "ERROR: --run-label is required when no vLLM-backed decomposer is selected."
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
    claims_by_method: dict[str, list[list[str]]] = {}  # aligned with records

    for name in args.decomposers:
        print(f"\n[{name}] decomposing {len(records)} records...")
        decomposer = DECOMPOSER_REGISTRY[name]()
        results = decomposer.decompose_batch(
            records, text_key=args.text_key, context_key=args.context_key
        )

        metrics = compute_metrics(results)
        table_results[name] = metrics
        claims_by_method[name] = [res.claims for res in results]

        print(f"  claims/response : {metrics['claims_per_response']:.2f}")
        print(f"  claims/sentence : {metrics['claims_per_sentence']:.2f}")
        print(f"  0-claim rate    : {metrics['zero_claim_rate']:.1%}")

        # Raw generations are kept so parsing changes can be re-scored offline.
        predictions = [
            {"id": r.get("id", str(i)), "claims": res.claims, "raw": res.raw_outputs}
            for i, (r, res) in enumerate(zip(records, results))
        ]

        out_path = output_dir / f"{name}_{dataset_stem}.json"
        with open(out_path, "w") as f:
            json.dump({"method": name, "metrics": metrics, "predictions": predictions}, f, indent=2)
        print(f"  saved → {out_path}")

    sample_records = records[:20]
    sample_path = output_dir / f"sample_for_annotation_{dataset_stem}.jsonl"
    with open(sample_path, "w") as f:
        for i, r in enumerate(sample_records):
            entry: dict = {"id": r.get("id", str(i)), args.text_key: r[args.text_key]}
            for name in args.decomposers:
                entry[name] = claims_by_method[name][i]
            f.write(json.dumps(entry) + "\n")
    print(f"\nAnnotation sample ({len(sample_records)} records) → {sample_path}")

    summary_path = output_dir / f"summary_{dataset_stem}.json"
    with open(summary_path, "w") as f:
        json.dump(table_results, f, indent=2)

    print(f"\n## Results — {dataset_stem}\n")
    print(format_table(table_results))


if __name__ == "__main__":
    main()
