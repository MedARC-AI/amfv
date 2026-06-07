from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "for",
    "from",
    "in",
    "is",
    "it",
    "may",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
}


VERDICT_SCORES = {
    "strongly supported": 1.0,
    "weakly supported": 0.75,
    "unclear": 0.5,
    "weakly unsubstantiated": 0.25,
    "strongly unsubstantiated": 0.0,
}


def tokenize(text: str) -> list[str]:
    """Tokenize text for the baseline verifier."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [token for token in tokens if token not in STOPWORDS]


def collect_evidence_text(evidence: list[dict[str, Any]]) -> str:
    """Join retrieved evidence snippets into one text block."""
    return "\n\n".join(str(item.get("text", "")) for item in evidence)


def lexical_overlap_score(claim: str, evidence_text: str) -> float:
    """Compute claim-token coverage in the retrieved evidence."""
    claim_tokens = set(tokenize(claim))
    evidence_tokens = set(tokenize(evidence_text))

    if not claim_tokens or not evidence_tokens:
        return 0.0

    overlap = claim_tokens.intersection(evidence_tokens)
    return len(overlap) / len(claim_tokens)


def assign_verdict(overlap_score: float) -> str:
    """Map lexical overlap to a temporary five-level AMFV verdict."""
    if overlap_score >= 0.75:
        return "strongly supported"

    if overlap_score >= 0.45:
        return "weakly supported"

    if overlap_score >= 0.25:
        return "unclear"

    if overlap_score > 0:
        return "weakly unsubstantiated"

    return "strongly unsubstantiated"


def verify_claim(
    claim: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify one claim using a simple lexical baseline."""
    evidence_text = collect_evidence_text(evidence)
    overlap_score = lexical_overlap_score(claim, evidence_text)
    verdict = assign_verdict(overlap_score)

    return {
        "claim": claim,
        "verdict": verdict,
        "score": VERDICT_SCORES[verdict],
        "lexical_overlap": round(overlap_score, 4),
        "explanation": (
            "Baseline lexical verifier verdict based on claim-token overlap "
            "with retrieved evidence. This is not a clinical judgment."
        ),
    }


def verify_report(report: dict[str, Any]) -> dict[str, Any]:
    """Add verifier verdicts to a claim retrieval report."""
    verified_claims = []

    for claim_record in report["claims"]:
        verification = verify_claim(
            claim=claim_record["claim"],
            evidence=claim_record["evidence"],
        )

        verified_claims.append(
            {
                **claim_record,
                "verification": verification,
            }
        )

    if verified_claims:
        mean_score = sum(
            claim["verification"]["score"] for claim in verified_claims
        ) / len(verified_claims)
    else:
        mean_score = 0.0

    hallucination_score = round(1.0 - mean_score, 4)

    return {
        **report,
        "claims": verified_claims,
        "mean_verification_score": round(mean_score, 4),
        "hallucination_score": hallucination_score,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add baseline verifier verdicts to an AMFV retrieval report."
    )

    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    report = json.loads(args.input.read_text(encoding="utf-8"))
    verified_report = verify_report(report)

    output = json.dumps(verified_report, ensure_ascii=False, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote verified report to {args.output}")
        return

    print(output)


if __name__ == "__main__":
    main()