from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from amfv_baseline.claim_cache import ClaimCache
from amfv_decomposer.llm_decomposer import (
    DEFAULT_DECOMPOSER_MODEL,
    LiteLLMClaimDecomposer,
)
from amfv_search.hybrid_retrieval import (
    DEFAULT_EMBEDDING_MODEL,
    HybridRetriever,
)
from amfv_verifier.llm_verifier import (
    DEFAULT_VERIFIER_MODEL,
    LiteLLMClaimVerifier,
)


DEFAULT_VERIFIER_NAME = "llm_medv1_style_verifier"

ClaimDecomposer = Callable[[str], list[dict[str, Any]]]
ClaimVerifier = Callable[[str, list[dict[str, Any]]], dict[str, Any]]


def run_pipeline(
    text: str,
    chunks_path: Path,
    bm25_index_path: Path,
    dense_index_path: Path,
    top_k: int,
    cache_path: Path,
    scope: str,
    embedding_model: str | None = None,
    trust_remote_code: bool = False,
    decomposer_model: str = DEFAULT_DECOMPOSER_MODEL,
    decomposer_api_key: str | None = None,
    decomposer_api_base: str | None = None,
    verifier_model: str = DEFAULT_VERIFIER_MODEL,
    verifier_api_key: str | None = None,
    verifier_api_base: str | None = None,
    decomposer: ClaimDecomposer | None = None,
    verifier: ClaimVerifier | None = None,
) -> dict[str, Any]:
    """Run AMFV with LLM decomposition, cache lookup, retrieval, and LLM verification."""
    if decomposer is None:
        claim_decomposer = LiteLLMClaimDecomposer(
            model=decomposer_model,
            api_key=decomposer_api_key,
            api_base=decomposer_api_base,
        )
        claim_records = claim_decomposer.decompose_text(text)
    else:
        claim_records = decomposer(text)

    if verifier is None:
        claim_verifier = LiteLLMClaimVerifier(
            model=verifier_model,
            api_key=verifier_api_key,
            api_base=verifier_api_base,
        )
        verify_claim = claim_verifier.verify_claim
    else:
        verify_claim = verifier

    cache = ClaimCache(cache_path)

    retriever: HybridRetriever | None = None

    def load_retriever() -> HybridRetriever:
        nonlocal retriever

        if retriever is None:
            retriever = HybridRetriever(
                chunks_path=chunks_path,
                bm25_index_path=bm25_index_path,
                dense_index_path=dense_index_path,
                model_name=embedding_model,
                trust_remote_code=trust_remote_code,
            )

        return retriever

    verified_claims = []
    cache_hits = 0
    cache_misses = 0

    for claim_record in claim_records:
        claim = claim_record["claim"]
        claim_type = claim_record["claim_type"]

        cached_claim = cache.get_verified_claim(claim=claim, scope=scope)

        if cached_claim is not None:
            cache_hits += 1
            verified_claims.append(cached_claim)
            continue

        cache_misses += 1

        evidence = load_retriever().search(
            query=claim,
            top_k=top_k,
        )

        verification = verify_claim(
            claim=claim,
            evidence=evidence,
        )

        cache.upsert_verified_claim(
            claim=claim,
            claim_type=claim_type,
            scope=scope,
            evidence=evidence,
            verification=verification,
            verifier_name=DEFAULT_VERIFIER_NAME,
        )

        verified_claims.append(
            {
                "claim": claim,
                "claim_type": claim_type,
                "certainty": claim_record.get("certainty"),
                "requires_context": claim_record.get("requires_context"),
                "scope": scope,
                "evidence": evidence,
                "verification": verification,
                "cache": {
                    "status": "miss",
                    "stored": True,
                    "verifier_name": DEFAULT_VERIFIER_NAME,
                },
            }
        )

    if verified_claims:
        mean_score = sum(
            claim["verification"]["score"] for claim in verified_claims
        ) / len(verified_claims)
    else:
        mean_score = 0.0

    return {
        "input_text": text,
        "num_claims": len(verified_claims),
        "scope": scope,
        "decomposer": {
            "method": "llm_factscore_style",
            "model": decomposer_model,
        },
        "verifier": {
            "method": "llm_medv1_style",
            "model": verifier_model,
        },
        "retrieval": {
            "method": "bm25+dense_rrf",
            "embedding_model": embedding_model or DEFAULT_EMBEDDING_MODEL,
            "dense_index_path": str(dense_index_path),
            "bm25_index_path": str(bm25_index_path),
        },
        "cache_path": str(cache_path),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "claims": verified_claims,
        "mean_verification_score": round(mean_score, 4),
        "hallucination_score": round(1.0 - mean_score, 4),
    }


def read_input_text(args: argparse.Namespace) -> str:
    """Read input text from --text or --input-file."""
    if args.text:
        return args.text

    if args.input_file:
        return args.input_file.read_text(encoding="utf-8")

    raise ValueError("Provide either --text or --input-file.")


def print_summary(report: dict[str, Any]) -> None:
    """Print a human-readable pipeline summary."""
    print("\nAMFV Baseline Report")
    print(f"Claims checked: {report['num_claims']}")
    print(f"Decomposer: {report['decomposer']['method']}")
    print(f"Decomposer model: {report['decomposer']['model']}")
    print(f"Verifier: {report['verifier']['method']}")
    print(f"Verifier model: {report['verifier']['model']}")
    print(f"Retrieval: {report['retrieval']['method']}")
    print(f"Embedding model: {report['retrieval']['embedding_model']}")
    print(f"Cache hits: {report['cache_hits']}")
    print(f"Cache misses: {report['cache_misses']}")
    print(f"Mean verification score: {report['mean_verification_score']}")
    print(f"Hallucination score: {report['hallucination_score']}")

    for index, claim_record in enumerate(report["claims"], start=1):
        verification = claim_record["verification"]

        print(f"\nClaim {index}")
        print(f"Claim: {claim_record['claim']}")
        print(f"Claim type: {claim_record['claim_type']}")
        print(f"Certainty: {claim_record.get('certainty')}")
        print(f"Requires context: {claim_record.get('requires_context')}")
        print(f"Cache status: {claim_record['cache']['status']}")
        print(f"Verdict: {verification['verdict']}")
        print(f"Score: {verification['score']}")
        print(f"Confidence: {verification.get('confidence')}")

        if claim_record["evidence"]:
            top_evidence = claim_record["evidence"][0]
            print(f"Top evidence title: {top_evidence.get('title')}")
            print(f"Top evidence hybrid score: {top_evidence.get('hybrid_score')}")
            print(f"Top evidence BM25 rank: {top_evidence.get('bm25_rank')}")
            print(f"Top evidence dense rank: {top_evidence.get('dense_rank')}")
            print(f"Top evidence chunk: {top_evidence.get('chunk_id')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run AMFV with LLM FActScore decomposition, "
            "cache lookup, BM25+dense retrieval, and LLM Med-V1-style verification."
        )
    )

    parser.add_argument("--text", type=str, default=None)
    parser.add_argument("--input-file", type=Path, default=None)

    parser.add_argument(
        "--chunks",
        type=Path,
        default=Path("data/index/nice_chunks.jsonl"),
    )

    parser.add_argument(
        "--bm25-index",
        type=Path,
        default=Path("data/index/nice_bm25_index.json"),
    )

    parser.add_argument(
        "--dense-index",
        type=Path,
        default=Path("data/index/nice_dense_index.npz"),
    )

    parser.add_argument(
        "--embedding-model",
        type=str,
        default=DEFAULT_EMBEDDING_MODEL,
    )

    parser.add_argument("--trust-remote-code", action="store_true")

    parser.add_argument(
        "--decomposer-model",
        type=str,
        default=DEFAULT_DECOMPOSER_MODEL,
    )
    parser.add_argument("--decomposer-api-key", type=str, default=None)
    parser.add_argument("--decomposer-api-base", type=str, default=None)

    parser.add_argument(
        "--verifier-model",
        type=str,
        default=DEFAULT_VERIFIER_MODEL,
    )
    parser.add_argument("--verifier-api-key", type=str, default=None)
    parser.add_argument("--verifier-api-base", type=str, default=None)

    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("data/cache/claims.sqlite"),
    )

    parser.add_argument(
        "--scope",
        type=str,
        default="nice-guidelines-dense",
    )

    parser.add_argument("--top-k", type=int, default=3)

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/reports/amfv_baseline_report.json"),
    )

    parser.add_argument("--summary", action="store_true")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    text = read_input_text(args)

    report = run_pipeline(
        text=text,
        chunks_path=args.chunks,
        bm25_index_path=args.bm25_index,
        dense_index_path=args.dense_index,
        top_k=args.top_k,
        cache_path=args.cache,
        scope=args.scope,
        embedding_model=args.embedding_model,
        trust_remote_code=args.trust_remote_code,
        decomposer_model=args.decomposer_model,
        decomposer_api_key=args.decomposer_api_key,
        decomposer_api_base=args.decomposer_api_base,
        verifier_model=args.verifier_model,
        verifier_api_key=args.verifier_api_key,
        verifier_api_base=args.verifier_api_base,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Wrote AMFV baseline report to {args.output}")

    if args.summary:
        print_summary(report)


if __name__ == "__main__":
    main()