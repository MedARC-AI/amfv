from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from datasets import load_dataset


def normalize_text(text: str) -> str:
    """Normalize guideline text while preserving paragraph structure."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_record(row: dict[str, Any]) -> dict[str, Any] | None:
    """Convert one Hugging Face guideline row into the AMFV corpus format."""
    if row.get("source") != "nice":
        return None

    text = normalize_text(str(row.get("clean_text") or row.get("raw_text") or ""))

    if not text:
        return None

    return {
        "doc_id": row.get("id"),
        "source": row.get("source"),
        "title": row.get("title"),
        "url": row.get("url"),
        "overview": row.get("overview"),
        "text": text,
        "text_char_count": len(text),
    }


def iter_nice_records(limit: int | None, min_chars: int) -> Iterator[dict[str, Any]]:
    """Stream NICE guideline records from the epfl-llm/guidelines dataset."""
    dataset = load_dataset("epfl-llm/guidelines", split="train", streaming=True)

    count = 0

    for row in dataset:
        record = build_record(row)

        if record is None:
            continue

        if record["text_char_count"] < min_chars:
            continue

        yield record

        count += 1

        if limit is not None and count >= limit:
            break


def write_jsonl(records: Iterator[dict[str, Any]], output_path: Path) -> int:
    """Write records to JSONL and return the number of written records."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0

    with output_path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest NICE clinical guidelines from epfl-llm/guidelines into AMFV JSONL format."
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/raw/nice_sample.jsonl"),
        help="Output JSONL path.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum number of NICE documents to ingest. Use 0 for no limit.",
    )

    parser.add_argument(
        "--min-chars",
        type=int,
        default=500,
        help="Skip documents shorter than this number of characters.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    limit = None if args.limit == 0 else args.limit

    records = iter_nice_records(limit=limit, min_chars=args.min_chars)
    written = write_jsonl(records=records, output_path=args.output)

    print(f"Wrote {written} NICE guideline documents to {args.output}")


if __name__ == "__main__":
    main()