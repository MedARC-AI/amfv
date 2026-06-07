""""""

from __future__ import annotations

import argparse
import textwrap


def main() -> None:
    ap = argparse.ArgumentParser(description="Query an AMFV retrieval index")
    ap.add_argument("-q", "--query", required=True)
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--bm25-dir")
    ap.add_argument("--colbert-dir")
    ap.add_argument(
        "--weights",
        type=float,
        nargs="+",
        default=None,
        help="RRF weights, order: bm25 then colbert (only if both given)",
    )
    ap.add_argument("--fetch-k", type=int, default=50)
    args = ap.parse_args()

    retrievers = []
    if args.bm25_dir:
        from ..retrieval.bm25 import BM25Retriever

        retrievers.append(BM25Retriever.load(args.bm25_dir))
    if args.colbert_dir:
        from ..retrieval.colbert import ColBERTRetriever

        retrievers.append(ColBERTRetriever.load(args.colbert_dir))
    if not retrievers:
        ap.error("provide --bm25-dir and/or --colbert-dir")

    if len(retrievers) == 1:
        hits = retrievers[0].retrieve(args.query, k=args.k)
    else:
        from ..retrieval.hybrid import HybridRetriever

        hits = HybridRetriever(retrievers, weights=args.weights, fetch_k=args.fetch_k).retrieve(args.query, k=args.k)

    print(f"\nQuery: {args.query}\n" + "=" * 72)
    for h in hits:
        head = f"[{h.rank}] {h.score:.4f}  {h.source} | {h.title or h.doc_id}  ({h.chunk_id})"
        print(head)
        print(textwrap.indent(textwrap.shorten(h.text, width=320, placeholder=" …"), "    "))
        print("-" * 72)


if __name__ == "__main__":
    main()
