""""""

from __future__ import annotations

import argparse

from ..chunking import chunk_documents
from ..corpus import read_chunks, read_documents, write_jsonl


def main() -> None:
    ap = argparse.ArgumentParser(description="Chunk documents and build a retrieval index")
    ap.add_argument("--documents", help="documents.jsonl (skip if --chunks given)")
    ap.add_argument("--chunks", help="prebuilt chunks.jsonl (skip chunking)")
    ap.add_argument("--chunks-out", default=None, help="where to write chunks.jsonl")
    ap.add_argument("--backend", choices=["bm25", "colbert"], required=True)
    ap.add_argument("--index-dir", required=True)
    ap.add_argument("--model", default=None, help="override ColBERT model (default LateOn)")
    ap.add_argument("--target-words", type=int, default=350)
    ap.add_argument("--overlap-words", type=int, default=40)
    args = ap.parse_args()

    if args.chunks:
        chunks = read_chunks(args.chunks)
    elif args.documents:
        docs = list(read_documents(args.documents))
        chunks = chunk_documents(docs, target_words=args.target_words, overlap_words=args.overlap_words)
        out = args.chunks_out or f"{args.index_dir.rstrip('/')}.chunks.jsonl"
        write_jsonl(out, chunks)
        print(f"Chunked {len(docs)} docs -> {len(chunks)} chunks ({out})")
    else:
        ap.error("provide --documents or --chunks")

    if args.backend == "bm25":
        from ..retrieval.bm25 import BM25Retriever

        r = BM25Retriever()
        r.index(chunks)
        r.save(args.index_dir)
    else:
        from ..retrieval.colbert import DEFAULT_MODEL, ColBERTRetriever

        r = ColBERTRetriever(model_name=args.model or DEFAULT_MODEL, index_dir=args.index_dir)
        r.index(chunks)
        r.save(args.index_dir)

    print(f"Built {args.backend} index over {len(chunks)} chunks -> {args.index_dir}")


if __name__ == "__main__":
    main()
