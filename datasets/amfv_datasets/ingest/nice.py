""""""

from __future__ import annotations

import argparse

from ..corpus import Document, write_jsonl

HF_DATASET = "epfl-llm/guidelines"


_TEXT_FIELDS = ("clean_text", "text", "content", "raw_text", "body")
_TITLE_FIELDS = ("title", "name", "heading")
_SOURCE_FIELDS = ("source", "dataset", "origin")
_URL_FIELDS = ("url", "link", "source_url")
_ID_FIELDS = ("id", "doc_id", "uuid")


def _pick(columns, candidates) -> str | None:
    for c in candidates:
        if c in columns:
            return c
    return None


def load_nice(limit: int | None = None, inspect: bool = False):
    from datasets import load_dataset

    ds = load_dataset(HF_DATASET, split="train")
    columns = set(ds.column_names)
    if inspect:
        print("Columns:", sorted(columns))
        print("Example:", {k: str(v)[:120] for k, v in ds[0].items()})

    text_f = _pick(columns, _TEXT_FIELDS)
    title_f = _pick(columns, _TITLE_FIELDS)
    source_f = _pick(columns, _SOURCE_FIELDS)
    url_f = _pick(columns, _URL_FIELDS)
    id_f = _pick(columns, _ID_FIELDS)
    if text_f is None:
        raise RuntimeError(f"No text column found among {_TEXT_FIELDS}; columns are {sorted(columns)}")

    if source_f is not None:
        ds = ds.filter(lambda r: str(r[source_f]).strip().upper() == "NICE")

    n = 0
    for i, row in enumerate(ds):
        text = (row.get(text_f) or "").strip()
        if not text:
            continue
        yield Document(
            doc_id=str(row.get(id_f, f"nice-{i}")),
            source="NICE",
            title=(row.get(title_f) or "").strip() if title_f else "",
            text=text,
            url=(row.get(url_f) or "").strip() if url_f else "",
            metadata={"hf_dataset": HF_DATASET, "row": i},
        )
        n += 1
        if limit is not None and n >= limit:
            break


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest NICE guidelines into documents.jsonl")
    ap.add_argument("--out", default="data/nice/documents.jsonl")
    ap.add_argument("--limit", type=int, default=None, help="cap document count (handy for smoke tests)")
    ap.add_argument("--inspect", action="store_true", help="print dataset schema and exit-ish")
    args = ap.parse_args()

    written = write_jsonl(args.out, load_nice(limit=args.limit, inspect=args.inspect))
    print(f"Wrote {written} NICE documents -> {args.out}")


if __name__ == "__main__":
    main()
