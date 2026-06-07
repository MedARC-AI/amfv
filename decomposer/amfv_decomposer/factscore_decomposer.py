from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


DECOMPOSITION_PROMPT = """You are a medical claim decomposer.

Break the input text into atomic, independently verifiable medical claims.

Rules:
1. Each claim must be a complete sentence.
2. Preserve medical specificity, including drug names, doses, populations, time periods, and disease severity.
3. Resolve vague references where possible.
4. Do not include advice, greetings, disclaimers, or non-factual filler.
5. Do not convert uncertain statements into certain ones.
6. Keep negations intact.
7. Return only JSON with this schema:

{
  "claims": [
    {
      "claim": "string",
      "claim_type": "diagnosis | treatment | epidemiology | prognosis | safety | other"
    }
  ]
}

Input text:
{text}
"""


def normalize_text(text: str) -> str:
    """Clean input text for decomposition."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def split_sentences(text: str) -> list[str]:
    """Simple sentence splitter for the baseline decomposer."""
    text = normalize_text(text)

    if not text:
        return []

    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def is_likely_claim(sentence: str) -> bool:
    """Filter out obvious non-claim text."""
    lower = sentence.lower()

    blocked_starts = (
        "hello",
    	"hi ",
    	"thanks",
    	"thank you",
    	"i hope",
    	"please note",
    	"please consult",
    	"consult a doctor",
    	"consult your doctor",
    	"as an ai",
    	"seek medical advice",
    )

    if lower.startswith(blocked_starts):
        return False

    if len(sentence.split()) < 4:
        return False

    return True


def classify_claim_type(claim: str) -> str:
    """Assign a rough claim type for the baseline."""
    lower = claim.lower()

    treatment_terms = [
        "treat",
        "treatment",
        "therapy",
        "dose",
        "mg",
        "administer",
        "prescribe",
        "recommended",
        "contraindicated",
    ]
    diagnosis_terms = [
        "diagnosis",
        "diagnosed",
        "symptom",
        "sign",
        "test",
        "screening",
        "investigation",
    ]
    safety_terms = [
        "risk",
        "adverse",
        "side effect",
        "contraindication",
        "toxicity",
        "harm",
        "bleeding",
    ]
    prognosis_terms = [
        "mortality",
        "survival",
        "prognosis",
        "outcome",
        "recovery",
        "complication",
    ]
    epidemiology_terms = [
        "prevalence",
        "incidence",
        "common",
        "rare",
        "population",
        "epidemiology",
    ]

    if any(term in lower for term in treatment_terms):
        return "treatment"

    if any(term in lower for term in diagnosis_terms):
        return "diagnosis"

    if any(term in lower for term in safety_terms):
        return "safety"

    if any(term in lower for term in prognosis_terms):
        return "prognosis"

    if any(term in lower for term in epidemiology_terms):
        return "epidemiology"

    return "other"


def decompose_text(text: str) -> list[dict[str, str]]:
    """Baseline FActScore-style decomposition."""
    sentences = split_sentences(text)
    claims = []

    for sentence in sentences:
        if not is_likely_claim(sentence):
            continue

        claims.append(
            {
                "claim": sentence,
                "claim_type": classify_claim_type(sentence),
            }
        )

    return claims


def read_input_text(args: argparse.Namespace) -> str:
    """Read text from --text or --input-file."""
    if args.text:
        return args.text

    if args.input_file:
        return args.input_file.read_text(encoding="utf-8")

    raise ValueError("Provide either --text or --input-file.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Decompose medical text into FActScore-style claims."
    )

    parser.add_argument("--text", type=str, default=None)
    parser.add_argument("--input-file", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--print-prompt",
        action="store_true",
        help="Print the LLM decomposition prompt instead of baseline claims.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    text = read_input_text(args)

    if args.print_prompt:
        output = DECOMPOSITION_PROMPT.replace("{text}", text)
    else:
        output = json.dumps(
            {"claims": decompose_text(text)},
            ensure_ascii=False,
            indent=2,
        )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote decomposer output to {args.output}")
        return

    print(output)


if __name__ == "__main__":
    main()