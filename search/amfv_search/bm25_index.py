from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any


def tokenize(text: str) -> list[str]:
    """Simple tokenizer for baseline BM25 retrieval."""
    return re.findall(r"[a-z0-9]+", text.lower())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSONL records from disk."""
    records: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                records.append(json.loads(line))

    return records


def write_jsonl(records: list[dict[str, Any]], path: Path) -> None:
    """Write JSONL records to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def chunk_text(
    text: str,
    max_chars: int = 1200,
    overlap_chars: int = 200,
) -> list[str]:
    """Split long guideline text into overlapping chunks."""
    chunks: list[str] = []
    start = 0
    text = text.strip()

    while start < len(text):
        end = min(start + max_chars, len(text))

        if end < len(text):
            paragraph_break = text.rfind("\n\n", start, end)
            sentence_break = text.rfind(". ", start, end)

            if paragraph_break > start + max_chars // 2:
                end = paragraph_break
            elif sentence_break > start + max_chars // 2:
                end = sentence_break + 1

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        next_start = max(0, end - overlap_chars)

        if next_start <= start:
            next_start = end

        start = next_start

    return chunks


def create_chunks(
    input_path: Path,
    output_path: Path,
    max_chars: int,
    overlap_chars: int,
) -> list[dict[str, Any]]:
    """Create retrievable chunks from ingested NICE documents."""
    documents = read_jsonl(input_path)
    chunks: list[dict[str, Any]] = []

    for document in documents:
        doc_id = str(document["doc_id"])
        text_chunks = chunk_text(
            text=document["text"],
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )

        for index, chunk in enumerate(text_chunks):
            chunks.append(
                {
                    "chunk_id": f"{doc_id}::chunk_{index:04d}",
                    "doc_id": doc_id,
                    "source": document.get("source"),
                    "title": document.get("title"),
                    "url": document.get("url"),
                    "chunk_index": index,
                    "text": chunk,
                }
            )

    write_jsonl(chunks, output_path)
    return chunks


def build_bm25_index(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a small JSON-serializable BM25 index."""
    tokenized_docs = [tokenize(chunk["text"]) for chunk in chunks]
    doc_lengths = [len(tokens) for tokens in tokenized_docs]
    avg_doc_length = sum(doc_lengths) / max(len(doc_lengths), 1)

    doc_frequencies: Counter[str] = Counter()

    for tokens in tokenized_docs:
        for token in set(tokens):
            doc_frequencies[token] += 1

    number_of_docs = len(tokenized_docs)
    idf = {
        token: math.log(
            1 + (number_of_docs - freq + 0.5) / (freq + 0.5)
        )
        for token, freq in doc_frequencies.items()
    }

    term_frequencies = [dict(Counter(tokens)) for tokens in tokenized_docs]

    return {
        "k1": 1.5,
        "b": 0.75,
        "avg_doc_length": avg_doc_length,
        "doc_lengths": doc_lengths,
        "idf": idf,
        "term_frequencies": term_frequencies,
        "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
    }


def save_index(index: dict[str, Any], path: Path) -> None:
    """Save BM25 index as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(index, file)


def load_index(path: Path) -> dict[str, Any]:
    """Load BM25 index from JSON."""
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def score_chunk(
    query_tokens: list[str],
    index: dict[str, Any],
    chunk_position: int,
) -> float:
    """Score one chunk using BM25."""
    k1 = index["k1"]
    b = index["b"]
    avg_doc_length = index["avg_doc_length"]
    doc_length = index["doc_lengths"][chunk_position]
    term_frequencies = index["term_frequencies"][chunk_position]
    idf = index["idf"]

    score = 0.0

    for token in query_tokens:
        if token not in term_frequencies:
            continue

        term_frequency = term_frequencies[token]
        denominator = term_frequency + k1 * (
            1 - b + b * doc_length / avg_doc_length
        )

        score += idf.get(token, 0.0) * (
            term_frequency * (k1 + 1) / denominator
        )

    return score


def search_bm25(
    query: str,
    chunks: list[dict[str, Any]],
    index: dict[str, Any],
    top_k: int,
) -> list[dict[str, Any]]:
    """Return top BM25 search results."""
    query_tokens = tokenize(query)

    scored_results = []

    for position, chunk in enumerate(chunks):
        score = score_chunk(
            query_tokens=query_tokens,
            index=index,
            chunk_position=position,
        )

        scored_results.append((score, chunk))

    scored_results.sort(key=lambda item: item[0], reverse=True)

    results = []

    for score, chunk in scored_results[:top_k]:
        results.append(
            {
                "score": round(score, 4),
                "chunk_id": chunk["chunk_id"],
                "title": chunk.get("title"),
                "url": chunk.get("url"),
                "text": chunk["text"],
            }
        )

    return results


def build_command(args: argparse.Namespace) -> None:
    chunks = create_chunks(
        input_path=args.input,
        output_path=args.chunks_output,
        max_chars=args.max_chars,
        overlap_chars=args.overlap_chars,
    )

    index = build_bm25_index(chunks)
    save_index(index, args.index_output)

    print(f"Wrote {len(chunks)} chunks to {args.chunks_output}")
    print(f"Wrote BM25 index to {args.index_output}")


def query_command(args: argparse.Namespace) -> None:
    chunks = read_jsonl(args.chunks)
    index = load_index(args.index)

    results = search_bm25(
        query=args.query,
        chunks=chunks,
        index=index,
        top_k=args.top_k,
    )

    for result_number, result in enumerate(results, start=1):
        print(f"\nResult {result_number}")
        print(f"Score: {result['score']}")
        print(f"Title: {result['title']}")
        print(f"Chunk ID: {result['chunk_id']}")
        print(f"URL: {result['url']}")
        print(result["text"][:800])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and query a baseline BM25 index for AMFV."
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/raw/nice_sample.jsonl"),
    )
    build_parser.add_argument(
        "--chunks-output",
        type=Path,
        default=Path("data/index/nice_chunks.jsonl"),
    )
    build_parser.add_argument(
        "--index-output",
        type=Path,
        default=Path("data/index/nice_bm25_index.json"),
    )
    build_parser.add_argument("--max-chars", type=int, default=1200)
    build_parser.add_argument("--overlap-chars", type=int, default=200)
    build_parser.set_defaults(func=build_command)

    query_parser = subparsers.add_parser("query")
    query_parser.add_argument("--query", type=str, required=True)
    query_parser.add_argument(
        "--chunks",
        type=Path,
        default=Path("data/index/nice_chunks.jsonl"),
    )
    query_parser.add_argument(
        "--index",
        type=Path,
        default=Path("data/index/nice_bm25_index.json"),
    )
    query_parser.add_argument("--top-k", type=int, default=5)
    query_parser.set_defaults(func=query_command)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()