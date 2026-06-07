from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_CHUNKS_PATH = Path("data/index/nice_chunks.jsonl")
DEFAULT_EVAL_PATH = Path("data/eval/kiss_nice_eval.jsonl")

DATASET_ID = "epfl-llm/guidelines"
SOURCE_NAME = "NICE"

CLAIM_TRIGGER_TERMS = {
    "recommend",
    "recommended",
    "recommendation",
    "offer",
    "consider",
    "advise",
    "assess",
    "diagnose",
    "diagnosis",
    "test",
    "investigate",
    "screen",
    "screening",
    "treat",
    "treatment",
    "therapy",
    "manage",
    "management",
    "monitor",
    "refer",
    "prescribe",
    "administer",
    "dose",
    "dosing",
    "contraindicated",
    "contraindication",
    "adverse",
    "risk",
    "symptom",
    "sign",
    "complication",
    "mortality",
    "survival",
    "pregnancy",
    "pregnant",
    "renal",
    "hepatic",
    "children",
    "adults",
    "should",
    "must",
    "do not",
    "do no",
    "avoid",
    "urgent",
}

LOW_VALUE_PATTERNS = {
    "this guideline covers",
    "this guideline includes",
    "this guideline replaces",
    "this guideline updates",
    "nice has produced",
    "information about",
    "terms used in this guideline",
    "the committee",
    "see the nice",
    "see also",
    "isbn",
    "copyright",
    "last reviewed",
    "next review",
    "implementation",
    "recommendations for research",
    "rationale and impact",
    "context",
    "overview",
    "who is it for",
    "commissioners",
    "healthcare professionals",
    "social care practitioners",
    "people using services",
    "your responsibility",
    "local commissioners",
    "local providers",
}

NEGATION_TERMS = {
    "do not",
    "should not",
    "must not",
    "not recommended",
    "is not recommended",
    "are not recommended",
    "avoid",
    "contraindicated",
    "not be used",
    "do no",
}

HEDGING_TERMS = {
    "consider",
    "may",
    "might",
    "can",
    "could",
    "is associated with",
    "are associated with",
    "suggests",
    "possible",
    "likely",
    "unlikely",
    "suspected",
    "if appropriate",
}


def normalize_whitespace(text: str) -> str:
    """Normalize whitespace while preserving content."""
    return re.sub(r"\s+", " ", text).strip()


def strip_leading_numbering(text: str) -> str:
    """Remove NICE numbering and bullet markers from sentence starts."""
    text = normalize_whitespace(text)
    text = re.sub(r"^\d+(?:\.\d+)*\s+", "", text)
    text = re.sub(r"^[•\-–—]\s+", "", text)
    return text.strip()


def stable_hash(text: str, length: int = 12) -> str:
    """Create a stable short hash."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSONL records."""
    records = []

    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if line:
                records.append(json.loads(line))

    return records


def write_jsonl(records: list[dict[str, Any]], path: Path) -> None:
    """Write JSONL records."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def split_sentences(text: str) -> list[str]:
    """Split chunk text into candidate sentences."""
    text = normalize_whitespace(text)

    if not text:
        return []

    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)

    sentences = []

    for part in parts:
        sentence = strip_leading_numbering(part)

        if sentence:
            sentences.append(sentence)

    return sentences


def contains_any(text: str, terms: set[str]) -> bool:
    """Return True if lowercased text contains any term."""
    text_lower = text.lower()
    return any(term in text_lower for term in terms)


def is_low_value_sentence(sentence: str) -> bool:
    """Filter administrative or non-evaluable NICE text."""
    sentence_lower = sentence.lower()

    if contains_any(sentence_lower, LOW_VALUE_PATTERNS):
        return True

    if "http://" in sentence_lower or "https://" in sentence_lower:
        return True

    if sentence_lower.startswith(("table ", "figure ", "box ")):
        return True

    if re.fullmatch(r"[\d\s.,;:()/-]+", sentence_lower):
        return True

    return False


def looks_like_claim_sentence(sentence: str) -> bool:
    """Return True if a sentence is a usable KISS medical claim."""
    sentence = strip_leading_numbering(sentence)

    if len(sentence) < 45 or len(sentence) > 360:
        return False

    if len(sentence.split()) < 7:
        return False

    if is_low_value_sentence(sentence):
        return False

    return contains_any(sentence, CLAIM_TRIGGER_TERMS)


def infer_certainty(sentence: str) -> str:
    """Infer certainty label for the source-derived claim."""
    sentence_lower = sentence.lower()

    if contains_any(sentence_lower, NEGATION_TERMS):
        return "negated"

    if contains_any(sentence_lower, HEDGING_TERMS):
        return "hedged"

    return "asserted"


def classify_claim_type(sentence: str) -> str:
    """Classify a source-derived claim into AMFV claim types."""
    sentence_lower = sentence.lower()

    if any(term in sentence_lower for term in ("screen", "screening")):
        return "screening"

    if any(
        term in sentence_lower
        for term in (
            "contraindicated",
            "contraindication",
            "adverse",
            "side effect",
            "harm",
            "toxicity",
            "interaction",
            "avoid",
        )
    ):
        return "safety"

    if any(
        term in sentence_lower
        for term in (
            "recommend",
            "offer",
            "consider",
            "advise",
            "treat",
            "treatment",
            "therapy",
            "dose",
            "dosing",
            "monitor",
            "refer",
            "manage",
            "prescribe",
            "administer",
        )
    ):
        return "treatment"

    if any(
        term in sentence_lower
        for term in (
            "diagnose",
            "diagnosis",
            "symptom",
            "sign",
            "test",
            "investigation",
            "assess",
            "assessment",
        )
    ):
        return "diagnosis"

    if any(
        term in sentence_lower
        for term in (
            "mortality",
            "survival",
            "prognosis",
            "complication",
            "recurrence",
            "outcome",
        )
    ):
        return "prognosis"

    if any(
        term in sentence_lower
        for term in (
            "prevalence",
            "incidence",
            "risk factor",
            "burden",
            "common in",
            "more common",
        )
    ):
        return "epidemiology"

    if any(
        term in sentence_lower
        for term in (
            "mechanism",
            "pathway",
            "causes",
            "caused by",
            "mediated by",
            "associated with",
        )
    ):
        return "pathophysiology"

    return "other"


def infer_requires_context(sentence: str) -> bool:
    """Infer whether the claim requires clinical context."""
    sentence_lower = sentence.lower()

    context_terms = {
        "adult",
        "adults",
        "child",
        "children",
        "young people",
        "pregnancy",
        "pregnant",
        "renal",
        "kidney",
        "liver",
        "hepatic",
        "severe",
        "mild",
        "moderate",
        "dose",
        "dosing",
        "mg",
        "ml/min",
        "years",
        "under",
        "over",
        "risk",
        "contraindicated",
        "guideline",
        "first-line",
        "second-line",
        "if",
        "when",
        "unless",
    }

    return contains_any(sentence_lower, context_terms)


def make_eval_id(sentence: str, chunk_id: str, index: int) -> str:
    """Create stable eval ID."""
    digest = stable_hash(f"{chunk_id}::{sentence}")
    return f"nice_kiss_{index:04d}_{digest}"


def make_eval_case(
    sentence: str,
    chunk: dict[str, Any],
    eval_index: int,
) -> dict[str, Any]:
    """Create one KISS eval case from one NICE source sentence."""
    sentence = strip_leading_numbering(sentence)
    chunk_id = str(chunk["chunk_id"])
    title = str(chunk.get("title", ""))
    url = str(chunk.get("url", ""))
    doc_id = str(chunk.get("doc_id", ""))

    claim_type = classify_claim_type(sentence)
    certainty = infer_certainty(sentence)

    return {
        "eval_id": make_eval_id(sentence, chunk_id, eval_index),
        "eval_version": "kiss_nice_v1",
        "dataset_id": DATASET_ID,
        "source_name": SOURCE_NAME,
        "eval_type": "source_grounded_single_claim",
        "input_text": sentence,
        "expected_claims": [
            {
                "claim": sentence,
                "claim_type": claim_type,
                "certainty": certainty,
                "requires_context": infer_requires_context(sentence),
            }
        ],
        "expected_relevant_chunk_ids": [chunk_id],
        "expected_min_recall_at_k": 1,
        "expected_verdict": "strongly supported",
        "expected_score_min": 0.75,
        "gold_evidence": [
            {
                "evidence_id": "e1",
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "title": title,
                "url": url,
                "text": normalize_whitespace(str(chunk.get("text", ""))),
            }
        ],
        "source": {
            "chunk_id": chunk_id,
            "doc_id": doc_id,
            "title": title,
            "url": url,
            "chunk_index": int(chunk.get("chunk_index", 0)),
            "source": str(chunk.get("source", "nice")),
        },
        "tags": {
            "claim_type": claim_type,
            "certainty": certainty,
            "topic_title": title,
        },
        "generation_method": "deterministic_sentence_extraction_from_nice_chunk",
        "review_status": "machine_generated_needs_human_review",
    }


def passes_filters(
    sentence: str,
    chunk: dict[str, Any],
    title_contains: str | None,
    text_contains: str | None,
) -> bool:
    """Apply optional title/text filters."""
    title = str(chunk.get("title", "")).lower()
    sentence_lower = sentence.lower()
    chunk_text = str(chunk.get("text", "")).lower()

    if title_contains and title_contains.lower() not in title:
        return False

    if text_contains:
        text_filter = text_contains.lower()

        if text_filter not in sentence_lower and text_filter not in chunk_text:
            return False

    return True


def generate_kiss_eval_set(
    chunks: list[dict[str, Any]],
    max_cases: int,
    max_per_title: int = 2,
    max_per_claim_type: int = 8,
    title_contains: str | None = None,
    text_contains: str | None = None,
) -> list[dict[str, Any]]:
    """Generate a deterministic KISS eval set from NICE chunks."""
    cases = []
    seen_claims = set()
    title_counts: Counter[str] = Counter()
    claim_type_counts: Counter[str] = Counter()

    for chunk in chunks:
        title = str(chunk.get("title", "untitled"))

        if title_counts[title] >= max_per_title:
            continue

        for sentence in split_sentences(str(chunk.get("text", ""))):
            sentence = strip_leading_numbering(sentence)

            if not passes_filters(
                sentence=sentence,
                chunk=chunk,
                title_contains=title_contains,
                text_contains=text_contains,
            ):
                continue

            if not looks_like_claim_sentence(sentence):
                continue

            claim_type = classify_claim_type(sentence)

            if claim_type_counts[claim_type] >= max_per_claim_type:
                continue

            claim_key = sentence.lower()

            if claim_key in seen_claims:
                continue

            eval_case = make_eval_case(
                sentence=sentence,
                chunk=chunk,
                eval_index=len(cases) + 1,
            )

            cases.append(eval_case)
            seen_claims.add(claim_key)
            title_counts[title] += 1
            claim_type_counts[claim_type] += 1

            if len(cases) >= max_cases:
                return cases

            if title_counts[title] >= max_per_title:
                break

    return cases


def summarize_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Return summary stats for an eval set."""
    claim_types = Counter(
        case["expected_claims"][0]["claim_type"] for case in cases
    )
    certainties = Counter(
        case["expected_claims"][0]["certainty"] for case in cases
    )
    titles = Counter(case["source"]["title"] for case in cases)

    return {
        "num_cases": len(cases),
        "claim_types": dict(claim_types),
        "certainties": dict(certainties),
        "num_titles": len(titles),
        "top_titles": dict(titles.most_common(10)),
    }


def create_command(args: argparse.Namespace) -> None:
    """Create a KISS eval JSONL file."""
    chunks = read_jsonl(args.chunks)

    cases = generate_kiss_eval_set(
        chunks=chunks,
        max_cases=args.max_cases,
        max_per_title=args.max_per_title,
        max_per_claim_type=args.max_per_claim_type,
        title_contains=args.title_contains,
        text_contains=args.text_contains,
    )

    if not cases:
        raise RuntimeError(
            "No KISS eval cases were generated. Try removing filters or "
            "reducing the sentence restrictions."
        )

    write_jsonl(cases, args.output)

    summary = summarize_cases(cases)

    print(f"Wrote {len(cases)} KISS eval cases to {args.output}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def inspect_command(args: argparse.Namespace) -> None:
    """Print selected eval cases for manual inspection."""
    cases = read_jsonl(args.eval)

    for case in cases[: args.limit]:
        claim = case["expected_claims"][0]

        print("\n---")
        print(f"Eval ID: {case['eval_id']}")
        print(f"Dataset: {case['dataset_id']}")
        print(f"Source: {case['source_name']}")
        print(f"Title: {case['source']['title']}")
        print(f"Chunk ID: {case['source']['chunk_id']}")
        print(f"Claim: {claim['claim']}")
        print(f"Claim type: {claim['claim_type']}")
        print(f"Certainty: {claim['certainty']}")
        print(f"Requires context: {claim['requires_context']}")
        print(f"Expected verdict: {case['expected_verdict']}")
        print(f"URL: {case['source']['url']}")


def stats_command(args: argparse.Namespace) -> None:
    """Print eval set summary statistics."""
    cases = read_jsonl(args.eval)
    summary = summarize_cases(cases)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create, inspect, and summarize a small KISS eval set from "
            "EPFL-LLM/guidelines NICE chunks."
        )
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument(
        "--chunks",
        type=Path,
        default=DEFAULT_CHUNKS_PATH,
    )
    create_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_EVAL_PATH,
    )
    create_parser.add_argument("--max-cases", type=int, default=25)
    create_parser.add_argument("--max-per-title", type=int, default=2)
    create_parser.add_argument("--max-per-claim-type", type=int, default=8)
    create_parser.add_argument("--title-contains", type=str, default=None)
    create_parser.add_argument("--text-contains", type=str, default=None)
    create_parser.set_defaults(func=create_command)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument(
        "--eval",
        type=Path,
        default=DEFAULT_EVAL_PATH,
    )
    inspect_parser.add_argument("--limit", type=int, default=5)
    inspect_parser.set_defaults(func=inspect_command)

    stats_parser = subparsers.add_parser("stats")
    stats_parser.add_argument(
        "--eval",
        type=Path,
        default=DEFAULT_EVAL_PATH,
    )
    stats_parser.set_defaults(func=stats_command)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()