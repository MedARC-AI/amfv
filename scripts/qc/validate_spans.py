from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "datasets"))
from amfv_datasets.eval_prompts.retrieval import RetrievalItem


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def describe_mismatch(span: str, document: str, norm_document: str) -> str:
    norm_span = normalize(span)
    if norm_span in norm_document:
        return f"whitespace-only difference: {span[:80]!r}"

    anchor = span[:40]
    idx = document.find(anchor)
    if idx == -1:
        norm_anchor = normalize(anchor)
        if normalize(anchor) in norm_document:
            return f"anchor only matches after normalization: {span[:80]!r}"
        return f"not found at all: {span[:80]!r}"

    doc_excerpt = document[idx: idx + len(span) + 40]
    matcher = difflib.SequenceMatcher(None, span, doc_excerpt)
    match = matcher.find_longest_match(0, len(span), 0, len(doc_excerpt))
    divergence = match.a + match.size
    before = span[max(0, divergence - 15):divergence]
    after = span[divergence:divergence + 20]
    return f"diverges at char {divergence}: ...{before!r} vs document {after!r}"


def validate_file(document: str, items_path: Path, repair: bool) -> int:
    norm_document = normalize(document)
    records = [json.loads(line) for line in items_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    total_repaired = 0
    total_unresolved = 0

    for record in records:
        item = RetrievalItem(
            question=record["question"],
            question_type=record["question_type"],
            answer=record["answer"],
            supporting_spans=record["supporting_spans"],
            is_answerable=record["is_answerable"],
            notes=record.get("notes", ""),
        )

        if repair:
            total_repaired += item.repair_truncated_spans(document)
            record["supporting_spans"] = item.supporting_spans

        unresolved = item.validate_spans(document) + item.find_truncated_spans(document)
        status = "OK" if not unresolved else f"MISMATCH ({len(unresolved)}/{len(item.supporting_spans)})"
        print(f"[{item.question_type:<15}] {status}  {item.question[:60]}")

        for span in item.find_truncated_spans(document):
            print(f"    truncated before bullet list: {span[:90]!r}")

        for span in item.validate_spans(document):
            print(f"    {describe_mismatch(span, document, norm_document)}")

        total_unresolved += len(unresolved)

    if repair:
        out_path = items_path.with_suffix(".repaired.jsonl")
        out_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
            encoding="utf-8",
        )
        print(f"repaired {total_repaired} span(s), wrote {out_path}")

    return total_unresolved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_doc", type=Path)
    parser.add_argument("items_jsonl", type=Path)
    parser.add_argument("--repair", action="store_true", help="Extend truncated spans in place and write a .repaired.jsonl copy.")
    args = parser.parse_args()

    document = args.source_doc.read_text(encoding="utf-8")
    unresolved = validate_file(document, args.items_jsonl, args.repair)
    sys.exit(1 if unresolved else 0)


if __name__ == "__main__":
    main()