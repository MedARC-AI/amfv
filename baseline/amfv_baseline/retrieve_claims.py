from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from amfv_decomposer.factscore_decomposer import decompose_text
from amfv_search.bm25_index import load_index, read_jsonl, search_bm25


def build_claim_retrieval_report(
    text: str,
    chunks_path: Path,
    index_path: Path,
    top_k: int,
) -> dict[str, Any]:
    """Decompose medical text and retrieve evidence for each claim."""
    claims = decompose_text(text)
    chunks = read_jsonl(chunks_path)
    index = load_index(index_path)

    claim_reports = []

    for claim_record in claims:
        claim = claim_record["claim"]

        evidence = search_bm25(
            query=claim,
            chunks=chunks,
            index=index,
            top_k=top_k,
        )

        claim_reports.append(
            {
                "claim": claim,
                "claim_type": claim_record["claim_type"],
                "evidence": evidence,
            }
        )

    return {
        "input_text": text,
        "num_claims": len(claims),
        "claims": claim_reports,
    }


def read_input_text(args: argparse.Namespace) -> str:
    """Read input text from --text or --input-file."""
    if args.text:
        return args.text

    if args.input_file:
        return args.input_file.read_text(encoding="utf-8")

    raise ValueError("Provide either --text or --input-file.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run baseline AMFV claim decomposition and BM25 evidence retrieval."
    )

    parser.add_argument("--text", type=str, default=None)
    parser.add_argument("--input-file", type=Path, default=None)

    parser.add_argument(
        "--chunks",
        type=Path,
        default=Path("data/index/nice_chunks.jsonl"),
    )

    parser.add_argument(
        "--index",
        type=Path,
        default=Path("data/index/nice_bm25_index.json"),
    )

    parser.add_argument("--top-k", type=int, default=3)

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to save the JSON report.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    text = read_input_text(args)

    report = build_claim_retrieval_report(
        text=text,
        chunks_path=args.chunks,
        index_path=args.index,
        top_k=args.top_k,
    )

    output = json.dumps(report, ensure_ascii=False, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote claim retrieval report to {args.output}")
        return

    print(output)


if __name__ == "__main__":
    main()