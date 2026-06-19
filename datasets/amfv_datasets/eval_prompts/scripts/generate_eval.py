from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# insert the datasets package into the path when run directly.
_repo_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_repo_root / "datasets"))

from amfv_datasets.eval_prompts.decomposition import (
    SYSTEM_PROMPT as DECOMP_SYSTEM,
    DecompositionItem,
    parse_response as parse_decomp,
    user_prompt as decomp_user_prompt,
)
from amfv_datasets.eval_prompts.retrieval import (
    SYSTEM_PROMPT as RET_SYSTEM,
    RetrievalItem,
    parse_response as parse_ret,
    user_prompt as ret_user_prompt,
)

MODEL = "google/gemma-4-31b-it:free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_TOKENS = 4096


def _call_openrouter(
    api_key: str,
    system: str,
    user: str,
) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "AMFV Eval Generator",
    }
    body = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    resp = requests.post(OPENROUTER_URL, headers=headers, data=json.dumps(body), timeout=120)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        raise RuntimeError(f"OpenRouter error: {data['error']}")

    return data["choices"][0]["message"]["content"]


def _to_jsonl_record(item: RetrievalItem | DecompositionItem, **meta: str) -> str:
    d = dataclasses.asdict(item)
    d.update(meta)
    return json.dumps(d, ensure_ascii=False)


def generate_retrieval(
    document: str,
    *,
    api_key: str,
    counts: dict[str, int] | None = None,
    title: str = "",
    source: str = "",
) -> list[RetrievalItem]:
    raw = _call_openrouter(
        api_key,
        system=RET_SYSTEM,
        user=ret_user_prompt(document, counts=counts, document_title=title, document_source=source),
    )
    items = parse_ret(raw)

    total_repaired = 0
    for item in items:
        total_repaired += item.repair_truncated_spans(document)
    if total_repaired:
        print(f"  (auto-repaired {total_repaired} truncated span(s))", file=sys.stderr)

    return items


def generate_decomposition(
    text: str,
    *,
    api_key: str,
    source_kind: str = "model_output",
    title: str = "",
    source: str = "",
) -> list[DecompositionItem]:
    raw = _call_openrouter(
        api_key,
        system=DECOMP_SYSTEM,
        user=decomp_user_prompt(text, source_kind=source_kind, document_title=title, document_source=source),  # type: ignore[arg-type]
    )
    return parse_decomp(raw)


def parse_counts(raw: str) -> dict[str, int]:
    result = {}
    for part in raw.split(","):
        key, _, val = part.partition("=")
        result[key.strip()] = int(val.strip())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate AMFV eval items via OpenRouter.")
    parser.add_argument("eval_type", choices=["retrieval", "decomposition"])
    parser.add_argument("--input", required=True, help="Path to the source document text file.")
    parser.add_argument("--output", default="-", help="Output JSONL path (default: stdout).")
    parser.add_argument("--title", default="", help="Optional document title.")
    parser.add_argument("--source", default="", help="Optional source URL / identifier.")
    parser.add_argument(
        "--counts",
        default="",
        help="Retrieval only: comma-separated key=value overrides, e.g. verbatim=3,adversarial=1.",
    )
    parser.add_argument(
        "--source-kind",
        default="model_output",
        choices=["model_output", "reasoning_trace", "document_passage", "multiple_choice_rationale"],
        help="Decomposition only: what kind of text is being decomposed.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("OPENROUTER_API_KEY is not set")

    document = Path(args.input).read_text(encoding="utf-8")

    meta = {
        "_source_file": args.input,
        "_eval_type": args.eval_type,
        "_model": MODEL,
        "_generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    print(f"Using model: {MODEL}", file=sys.stderr)

    if args.eval_type == "retrieval":
        counts = parse_counts(args.counts) if args.counts else None
        items = generate_retrieval(
            document,
            api_key=api_key,
            counts=counts,
            title=args.title,
            source=args.source,
        )
    else:
        items = generate_decomposition(
            document,
            api_key=api_key,
            source_kind=args.source_kind,
            title=args.title,
            source=args.source,
        )

    lines = [_to_jsonl_record(item, **meta) for item in items]
    output = "\n".join(lines) + "\n"

    if args.output == "-":
        sys.stdout.write(output)
    else:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"Wrote {len(items)} items → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()