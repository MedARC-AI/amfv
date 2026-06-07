from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np

from amfv_search.bm25_index import load_index, read_jsonl, search_bm25


DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"


def load_embedding_model(model_name: str, trust_remote_code: bool = False) -> Any:
    """Load a Sentence Transformers-compatible embedding model."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        model_name,
        trust_remote_code=trust_remote_code,
    )


def encode_texts(
    model: Any,
    texts: list[str],
    batch_size: int,
    show_progress_bar: bool,
) -> np.ndarray:
    """Encode texts as normalized dense embeddings."""
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=show_progress_bar,
        convert_to_numpy=True,
    )

    return np.asarray(embeddings, dtype=np.float32)


def save_dense_index(
    embeddings: np.ndarray,
    chunk_ids: list[str],
    model_name: str,
    output_path: Path,
) -> None:
    """Save a dense vector index to disk."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(
        output_path,
        embeddings=np.asarray(embeddings, dtype=np.float32),
        chunk_ids=np.asarray(chunk_ids),
        model_name=np.asarray([model_name]),
    )


def load_dense_index(path: Path) -> dict[str, Any]:
    """Load a dense vector index from disk."""
    data = np.load(path, allow_pickle=False)

    return {
        "embeddings": np.asarray(data["embeddings"], dtype=np.float32),
        "chunk_ids": [str(chunk_id) for chunk_id in data["chunk_ids"]],
        "model_name": str(data["model_name"][0]),
    }


def build_dense_index(
    chunks_path: Path,
    output_path: Path,
    model_name: str,
    batch_size: int,
    trust_remote_code: bool,
) -> None:
    """Build a dense embedding index from NICE chunks."""
    chunks = read_jsonl(chunks_path)
    texts = [str(chunk["text"]) for chunk in chunks]
    chunk_ids = [str(chunk["chunk_id"]) for chunk in chunks]

    model = load_embedding_model(
        model_name=model_name,
        trust_remote_code=trust_remote_code,
    )

    embeddings = encode_texts(
        model=model,
        texts=texts,
        batch_size=batch_size,
        show_progress_bar=True,
    )

    save_dense_index(
        embeddings=embeddings,
        chunk_ids=chunk_ids,
        model_name=model_name,
        output_path=output_path,
    )

    print(f"Wrote dense index for {len(chunk_ids)} chunks to {output_path}")
    print(f"Embedding model: {model_name}")


def dense_search_from_embedding(
    query_embedding: np.ndarray,
    dense_index: dict[str, Any],
    chunks_by_id: dict[str, dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    """Search dense index using a precomputed query embedding."""
    embeddings = dense_index["embeddings"]
    chunk_ids = dense_index["chunk_ids"]

    query_embedding = np.asarray(query_embedding, dtype=np.float32)

    scores = embeddings @ query_embedding

    if len(scores) == 0:
        return []

    candidate_count = min(top_k, len(scores))
    candidate_indices = np.argpartition(-scores, candidate_count - 1)[
        :candidate_count
    ]
    candidate_indices = candidate_indices[
        np.argsort(-scores[candidate_indices])
    ]

    results = []

    for index in candidate_indices:
        chunk_id = chunk_ids[int(index)]
        chunk = chunks_by_id[chunk_id]

        results.append(
            {
                "score": round(float(scores[int(index)]), 4),
                "chunk_id": chunk_id,
                "title": chunk.get("title"),
                "url": chunk.get("url"),
                "text": chunk["text"],
            }
        )

    return results


def hybrid_rank(
    bm25_results: list[dict[str, Any]],
    dense_results: list[dict[str, Any]],
    top_k: int,
    rrf_k: int = 60,
    bm25_weight: float = 1.0,
    dense_weight: float = 1.0,
) -> list[dict[str, Any]]:
    """Fuse BM25 and dense results using reciprocal rank fusion."""
    combined: dict[str, dict[str, Any]] = {}

    for rank, result in enumerate(bm25_results, start=1):
        chunk_id = result["chunk_id"]

        combined.setdefault(
            chunk_id,
            {
                **result,
                "hybrid_score": 0.0,
                "bm25_rank": None,
                "dense_rank": None,
                "bm25_score": None,
                "dense_score": None,
                "retrieval_method": "hybrid_bm25_dense",
            },
        )

        combined[chunk_id]["bm25_rank"] = rank
        combined[chunk_id]["bm25_score"] = result["score"]
        combined[chunk_id]["hybrid_score"] += bm25_weight / (rrf_k + rank)

    for rank, result in enumerate(dense_results, start=1):
        chunk_id = result["chunk_id"]

        combined.setdefault(
            chunk_id,
            {
                **result,
                "hybrid_score": 0.0,
                "bm25_rank": None,
                "dense_rank": None,
                "bm25_score": None,
                "dense_score": None,
                "retrieval_method": "hybrid_bm25_dense",
            },
        )

        combined[chunk_id]["dense_rank"] = rank
        combined[chunk_id]["dense_score"] = result["score"]
        combined[chunk_id]["hybrid_score"] += dense_weight / (rrf_k + rank)

    ranked = sorted(
        combined.values(),
        key=lambda result: result["hybrid_score"],
        reverse=True,
    )

    final_results = []

    for result in ranked[:top_k]:
        result = dict(result)
        result["hybrid_score"] = round(float(result["hybrid_score"]), 6)
        final_results.append(result)

    return final_results


class HybridRetriever:
    """BM25 + dense retriever for AMFV evidence search."""

    def __init__(
        self,
        chunks_path: Path,
        bm25_index_path: Path,
        dense_index_path: Path,
        model_name: str | None = None,
        trust_remote_code: bool = False,
    ) -> None:
        self.chunks = read_jsonl(chunks_path)
        self.chunks_by_id = {
            str(chunk["chunk_id"]): chunk for chunk in self.chunks
        }

        self.bm25_index = load_index(bm25_index_path)
        self.dense_index = load_dense_index(dense_index_path)

        self.model_name = model_name or self.dense_index["model_name"]
        self.model = load_embedding_model(
            model_name=self.model_name,
            trust_remote_code=trust_remote_code,
        )

    def dense_search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        query_embedding = encode_texts(
            model=self.model,
            texts=[query],
            batch_size=1,
            show_progress_bar=False,
        )[0]

        return dense_search_from_embedding(
            query_embedding=query_embedding,
            dense_index=self.dense_index,
            chunks_by_id=self.chunks_by_id,
            top_k=top_k,
        )

    def search(
        self,
        query: str,
        top_k: int,
        bm25_pool: int = 50,
        dense_pool: int = 50,
    ) -> list[dict[str, Any]]:
        bm25_results = search_bm25(
            query=query,
            chunks=self.chunks,
            index=self.bm25_index,
            top_k=bm25_pool,
        )

        dense_results = self.dense_search(
            query=query,
            top_k=dense_pool,
        )

        return hybrid_rank(
            bm25_results=bm25_results,
            dense_results=dense_results,
            top_k=top_k,
        )


def build_dense_command(args: argparse.Namespace) -> None:
    build_dense_index(
        chunks_path=args.chunks,
        output_path=args.output,
        model_name=args.model_name,
        batch_size=args.batch_size,
        trust_remote_code=args.trust_remote_code,
    )


def query_command(args: argparse.Namespace) -> None:
    retriever = HybridRetriever(
        chunks_path=args.chunks,
        bm25_index_path=args.bm25_index,
        dense_index_path=args.dense_index,
        model_name=args.model_name,
        trust_remote_code=args.trust_remote_code,
    )

    results = retriever.search(
        query=args.query,
        top_k=args.top_k,
        bm25_pool=args.bm25_pool,
        dense_pool=args.dense_pool,
    )

    for result_number, result in enumerate(results, start=1):
        print(f"\nResult {result_number}")
        print(f"Hybrid score: {result['hybrid_score']}")
        print(f"BM25 rank: {result['bm25_rank']}")
        print(f"Dense rank: {result['dense_rank']}")
        print(f"BM25 score: {result['bm25_score']}")
        print(f"Dense score: {result['dense_score']}")
        print(f"Title: {result['title']}")
        print(f"Chunk ID: {result['chunk_id']}")
        print(f"URL: {result['url']}")
        print(result["text"][:800])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and query BM25 + dense hybrid retrieval for AMFV."
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    build_dense_parser = subparsers.add_parser("build-dense")
    build_dense_parser.add_argument(
        "--chunks",
        type=Path,
        default=Path("data/index/nice_chunks.jsonl"),
    )
    build_dense_parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/index/nice_dense_index.npz"),
    )
    build_dense_parser.add_argument(
        "--model-name",
        type=str,
        default=DEFAULT_EMBEDDING_MODEL,
    )
    build_dense_parser.add_argument("--batch-size", type=int, default=4)
    build_dense_parser.add_argument("--trust-remote-code", action="store_true")
    build_dense_parser.set_defaults(func=build_dense_command)

    query_parser = subparsers.add_parser("query")
    query_parser.add_argument("--query", type=str, required=True)
    query_parser.add_argument(
        "--chunks",
        type=Path,
        default=Path("data/index/nice_chunks.jsonl"),
    )
    query_parser.add_argument(
        "--bm25-index",
        type=Path,
        default=Path("data/index/nice_bm25_index.json"),
    )
    query_parser.add_argument(
        "--dense-index",
        type=Path,
        default=Path("data/index/nice_dense_index.npz"),
    )
    query_parser.add_argument(
        "--model-name",
        type=str,
        default=None,
    )
    query_parser.add_argument("--trust-remote-code", action="store_true")
    query_parser.add_argument("--top-k", type=int, default=5)
    query_parser.add_argument("--bm25-pool", type=int, default=50)
    query_parser.add_argument("--dense-pool", type=int, default=50)
    query_parser.set_defaults(func=query_command)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()