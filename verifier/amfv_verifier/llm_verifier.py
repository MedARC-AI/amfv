from __future__ import annotations

import argparse
import json
import os
import re
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any


DEFAULT_VERIFIER_MODEL = (
    os.getenv("AMFV_VERIFIER_MODEL")
    or os.getenv("VERIFIER_MODEL")
    or "gpt-4o-mini"
)

VERDICTS = {
    "strongly supported",
    "weakly supported",
    "unclear",
    "weakly unsubstantiated",
    "strongly unsubstantiated",
}

MEDV1_SCORES = {
    "strongly supported": 2,
    "weakly supported": 1,
    "unclear": 0,
    "weakly unsubstantiated": -1,
    "strongly unsubstantiated": -2,
}

NORMALIZED_SCORES = {
    "strongly supported": 1.0,
    "weakly supported": 0.75,
    "unclear": 0.5,
    "weakly unsubstantiated": 0.25,
    "strongly unsubstantiated": 0.0,
}

PROVIDER_EXAMPLES = {
    "openai": {
        "model_env": "AMFV_OPENAI_VERIFIER_MODEL",
        "default_model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
    },
    "anthropic": {
        "model_env": "AMFV_ANTHROPIC_VERIFIER_MODEL",
        "default_model": "anthropic/claude-3-5-haiku-latest",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "groq": {
        "model_env": "AMFV_GROQ_VERIFIER_MODEL",
        "default_model": "groq/llama-3.1-8b-instant",
        "api_key_env": "GROQ_API_KEY",
    },
    "gemini": {
        "model_env": "AMFV_GEMINI_VERIFIER_MODEL",
        "default_model": "gemini/gemini-2.5-flash",
        "api_key_env": "GEMINI_API_KEY",
    },
    "together_ai": {
        "model_env": "AMFV_TOGETHER_VERIFIER_MODEL",
        "default_model": (
            "together_ai/meta-llama/"
            "Meta-Llama-3.1-8B-Instruct-Turbo"
        ),
        "api_key_env": "TOGETHERAI_API_KEY",
    },
    "mistral": {
        "model_env": "AMFV_MISTRAL_VERIFIER_MODEL",
        "default_model": "mistral/mistral-small-latest",
        "api_key_env": "MISTRAL_API_KEY",
    },
    "cohere": {
        "model_env": "AMFV_COHERE_VERIFIER_MODEL",
        "default_model": "command-r",
        "api_key_env": "COHERE_API_KEY",
    },
    "perplexity": {
        "model_env": "AMFV_PERPLEXITY_VERIFIER_MODEL",
        "default_model": "perplexity/sonar",
        "api_key_env": "PERPLEXITYAI_API_KEY",
    },
    "openrouter": {
        "model_env": "AMFV_OPENROUTER_VERIFIER_MODEL",
        "default_model": "openrouter/openai/gpt-4o-mini",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "fireworks": {
        "model_env": "AMFV_FIREWORKS_VERIFIER_MODEL",
        "default_model": (
            "fireworks_ai/accounts/fireworks/models/"
            "llama-v3p1-8b-instruct"
        ),
        "api_key_env": "FIREWORKS_API_KEY",
    },
    "ollama": {
        "model_env": "AMFV_OLLAMA_VERIFIER_MODEL",
        "default_model": "ollama/llama3.2",
        "api_key_env": None,
    },
    "local_openai": {
        "model_env": "AMFV_LOCAL_OPENAI_VERIFIER_MODEL",
        "default_model": "openai/local-model",
        "api_key_env": None,
        "api_base_env": "AMFV_LOCAL_OPENAI_API_BASE",
    },
}


SYSTEM_PROMPT = textwrap.dedent(
    """
    You are the AMFV Medical Claim Verifier.

    Your job is to judge whether a single atomic medical claim is supported,
    contradicted, unclear, or not substantiated by the provided evidence.

    You must behave like a conservative medical fact-verification model in the
    style of Med-V1. You are not answering the medical question directly. You
    are verifying one claim against retrieved evidence.

    Inputs:
    - One atomic medical claim.
    - A set of retrieved evidence snippets from medical guidelines, clinical
      references, papers, or curated sources.

    Core rule:
    Judge only whether the evidence supports the exact claim. Do not use outside
    medical knowledge unless it is explicitly present in the evidence snippets.

    Verdict scale:
    Use exactly one of these five verdicts.

    strongly supported:
    The evidence directly and clearly supports the claim. Important clinical
    qualifiers in the claim are present in the evidence or are unambiguously
    entailed by it.

    weakly supported:
    The evidence generally supports the claim, but support is indirect, partial,
    less specific, or missing a minor qualifier.

    unclear:
    The evidence is insufficient, mixed, ambiguous, off-topic, too general, or
    does not allow a reliable judgment.

    weakly unsubstantiated:
    The evidence leans against the claim, but contradiction is indirect, partial,
    or depends on missing context.

    strongly unsubstantiated:
    The evidence directly contradicts the claim, or the claim makes a strong
    medical assertion that the evidence clearly does not support.

    Important clinical verification rules:

    1. Preserve scope.
       A claim about adults is not supported by pediatric-only evidence unless
       the evidence explicitly generalizes to adults.
       A claim about severe disease is not supported by evidence about mild
       disease.
       A claim about pregnancy, renal impairment, liver disease, or age groups
       requires evidence matching that context.

    2. Preserve dose, route, duration, and timing.
       If the claim includes a dose, route, frequency, or duration, the evidence
       must support those details for strong support.

    3. Preserve directionality.
       Do not treat "increases risk" as supporting "reduces risk".
       Do not treat "preferred over clopidogrel" as supporting "equivalent to
       clopidogrel".

    4. Preserve uncertainty.
       Evidence saying "may reduce" does not strongly support a claim saying
       "reduces" unless the evidence is otherwise definitive.
       Claims with hedging may be supported by hedged evidence.

    5. Preserve negation.
       Evidence saying "not recommended" contradicts a claim saying
       "recommended".
       Evidence saying "does not diagnose" contradicts a claim saying
       "diagnoses".

    6. Distinguish association from causation.
       Evidence of association does not strongly support a causal claim.

    7. Do not reward keyword overlap alone.
       Shared terms are not enough. The relationship between subject, predicate,
       object, population, and context must match.

    8. Use "unclear" when evidence is irrelevant, missing, ambiguous, or too
       general.
       Do not overstate support.

    9. If evidence snippets conflict, choose "unclear" unless one side clearly
       dominates and explain the conflict briefly.

    10. Evidence absence is not automatic contradiction.
        If no useful evidence is present, use "unclear" or
        "weakly unsubstantiated", depending on how unsupported the claim is.

    Output requirements:
    Return only one valid JSON object.
    Do not include markdown.
    Do not include citations outside the provided evidence IDs.
    Do not include text outside the JSON object.

    The JSON object must have this exact structure:

    {
      "verdict": "strongly supported | weakly supported | unclear | weakly unsubstantiated | strongly unsubstantiated",
      "medv1_score": 2,
      "score": 1.0,
      "confidence": "high | medium | low",
      "supported_evidence_ids": ["string"],
      "contradicted_evidence_ids": ["string"],
      "missing_context": ["string"],
      "explanation": "brief explanation grounded only in the provided evidence"
    }

    Score mapping:
    strongly supported -> medv1_score 2, score 1.0
    weakly supported -> medv1_score 1, score 0.75
    unclear -> medv1_score 0, score 0.5
    weakly unsubstantiated -> medv1_score -1, score 0.25
    strongly unsubstantiated -> medv1_score -2, score 0.0
    """
).strip()


DEMO_CLAIM_1 = "Metformin is contraindicated in patients with an eGFR below 30 mL/min/1.73 m²."

DEMO_EVIDENCE_1 = [
    {
        "evidence_id": "e1",
        "title": "Type 2 diabetes guideline",
        "text": (
            "Do not use metformin in people with an eGFR below "
            "30 mL/min/1.73 m²."
        ),
    }
]

DEMO_OUTPUT_1 = {
    "verdict": "strongly supported",
    "medv1_score": 2,
    "score": 1.0,
    "confidence": "high",
    "supported_evidence_ids": ["e1"],
    "contradicted_evidence_ids": [],
    "missing_context": [],
    "explanation": (
        "The evidence directly states that metformin should not be used when "
        "eGFR is below 30 mL/min/1.73 m², matching the claim."
    ),
}


DEMO_CLAIM_2 = "Antibiotics are routinely recommended for uncomplicated viral upper respiratory infections."

DEMO_EVIDENCE_2 = [
    {
        "evidence_id": "e1",
        "title": "Respiratory infection guidance",
        "text": (
            "Antibiotics are not routinely recommended for uncomplicated viral "
            "upper respiratory tract infections."
        ),
    }
]

DEMO_OUTPUT_2 = {
    "verdict": "strongly unsubstantiated",
    "medv1_score": -2,
    "score": 0.0,
    "confidence": "high",
    "supported_evidence_ids": [],
    "contradicted_evidence_ids": ["e1"],
    "missing_context": [],
    "explanation": (
        "The evidence directly contradicts the claim by stating that antibiotics "
        "are not routinely recommended for uncomplicated viral upper respiratory "
        "tract infections."
    ),
}


DEMO_CLAIM_3 = "A PSA of 4.2 ng/mL always indicates prostate cancer."

DEMO_EVIDENCE_3 = [
    {
        "evidence_id": "e1",
        "title": "PSA interpretation",
        "text": (
            "PSA may be elevated in prostate cancer, benign prostatic "
            "hyperplasia, prostatitis, or after recent instrumentation. PSA "
            "alone does not diagnose prostate cancer."
        ),
    }
]

DEMO_OUTPUT_3 = {
    "verdict": "strongly unsubstantiated",
    "medv1_score": -2,
    "score": 0.0,
    "confidence": "high",
    "supported_evidence_ids": [],
    "contradicted_evidence_ids": ["e1"],
    "missing_context": [],
    "explanation": (
        "The evidence states that PSA alone does not diagnose prostate cancer "
        "and may be elevated for other reasons, contradicting the claim that "
        "a PSA of 4.2 ng/mL always indicates prostate cancer."
    ),
}


class VerifierParseError(ValueError):
    """Raised when LLM output cannot be parsed into a verifier record."""


def evidence_id_for_index(index: int) -> str:
    """Return stable evidence IDs for prompt and output."""
    return f"e{index + 1}"


def format_evidence(
    evidence: list[dict[str, Any]],
    max_total_chars: int = 12000,
    max_snippet_chars: int = 1800,
) -> list[dict[str, str]]:
    """Format retrieved evidence for verifier prompting."""
    formatted = []
    used_chars = 0

    for index, item in enumerate(evidence):
        text = str(item.get("text", "")).strip()

        if not text:
            continue

        text = re.sub(r"\s+", " ", text)
        text = text[:max_snippet_chars]

        if used_chars + len(text) > max_total_chars:
            break

        evidence_id = evidence_id_for_index(index)

        formatted.append(
            {
                "evidence_id": evidence_id,
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "chunk_id": str(item.get("chunk_id", "")),
                "text": text,
            }
        )

        used_chars += len(text)

    return formatted


def build_user_content(claim: str, evidence: list[dict[str, str]]) -> str:
    """Build the user content for one verification request."""
    payload = {
        "claim": claim,
        "evidence": evidence,
    }

    return (
        "Verify this medical claim against the provided evidence.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def build_messages(
    claim: str,
    evidence: list[dict[str, Any]],
    repair_instruction: str | None = None,
) -> list[dict[str, str]]:
    """Build few-shot Med-V1-style verifier messages."""
    formatted_evidence = format_evidence(evidence)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": build_user_content(DEMO_CLAIM_1, DEMO_EVIDENCE_1),
        },
        {
            "role": "assistant",
            "content": json.dumps(DEMO_OUTPUT_1, ensure_ascii=False),
        },
        {
            "role": "user",
            "content": build_user_content(DEMO_CLAIM_2, DEMO_EVIDENCE_2),
        },
        {
            "role": "assistant",
            "content": json.dumps(DEMO_OUTPUT_2, ensure_ascii=False),
        },
        {
            "role": "user",
            "content": build_user_content(DEMO_CLAIM_3, DEMO_EVIDENCE_3),
        },
        {
            "role": "assistant",
            "content": json.dumps(DEMO_OUTPUT_3, ensure_ascii=False),
        },
        {
            "role": "user",
            "content": build_user_content(claim, formatted_evidence),
        },
    ]

    if repair_instruction:
        messages.append(
            {
                "role": "user",
                "content": repair_instruction,
            }
        )

    return messages


def strip_markdown_fence(raw_text: str) -> str:
    """Remove common JSON markdown fences."""
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_json_object(raw_text: str) -> str:
    """Extract outermost JSON object from model output."""
    text = strip_markdown_fence(raw_text)

    if text.startswith("{") and text.endswith("}"):
        return text

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise VerifierParseError("LLM response did not contain a JSON object.")

    return text[start : end + 1]


def validate_string_list(value: Any, field_name: str) -> list[str]:
    """Validate list[str] fields from verifier output."""
    if not isinstance(value, list):
        raise VerifierParseError(f"{field_name} must be a list.")

    if not all(isinstance(item, str) for item in value):
        raise VerifierParseError(f"{field_name} must contain only strings.")

    return value


def validate_verification(payload: Any) -> dict[str, Any]:
    """Validate and normalize one verifier output."""
    if not isinstance(payload, dict):
        raise VerifierParseError("Verifier output must be a JSON object.")

    verdict = payload.get("verdict")

    if verdict not in VERDICTS:
        raise VerifierParseError(f"Invalid verdict: {verdict}")

    expected_medv1_score = MEDV1_SCORES[verdict]
    expected_score = NORMALIZED_SCORES[verdict]

    confidence = payload.get("confidence")

    if confidence not in {"high", "medium", "low"}:
        raise VerifierParseError(f"Invalid confidence: {confidence}")

    explanation = payload.get("explanation")

    if not isinstance(explanation, str) or not explanation.strip():
        raise VerifierParseError("Verifier output needs a non-empty explanation.")

    return {
        "verdict": verdict,
        "medv1_score": expected_medv1_score,
        "score": expected_score,
        "confidence": confidence,
        "supported_evidence_ids": validate_string_list(
            payload.get("supported_evidence_ids", []),
            "supported_evidence_ids",
        ),
        "contradicted_evidence_ids": validate_string_list(
            payload.get("contradicted_evidence_ids", []),
            "contradicted_evidence_ids",
        ),
        "missing_context": validate_string_list(
            payload.get("missing_context", []),
            "missing_context",
        ),
        "explanation": explanation.strip(),
    }


def parse_verification(raw_text: str) -> dict[str, Any]:
    """Parse LLM JSON output into a verifier record."""
    json_text = extract_json_object(raw_text)

    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as error:
        raise VerifierParseError(
            f"LLM response was not valid JSON: {raw_text}"
        ) from error

    return validate_verification(payload)


def get_response_content(response: Any) -> str:
    """Extract text content from a LiteLLM response."""
    choice = response.choices[0]
    message = choice.message

    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)

    if isinstance(content, str):
        return content.strip()

    raise VerifierParseError("LLM response did not contain text content.")


class LiteLLMClaimVerifier:
    """Provider-agnostic Med-V1-style medical claim verifier."""

    def __init__(
        self,
        model: str = DEFAULT_VERIFIER_MODEL,
        api_key: str | None = None,
        api_base: str | None = None,
        max_tokens: int = 1200,
        temperature: float | None = 0.0,
        max_retries: int = 1,
        completion_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries

        if completion_fn is None:
            from litellm import completion

            completion_fn = completion

        self.completion_fn = completion_fn

    def verify_claim(
        self,
        claim: str,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Call an LLM and verify one medical claim against evidence."""
        claim = claim.strip()

        if not claim:
            raise ValueError("claim must be non-empty")

        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            repair_instruction = None

            if attempt > 0:
                repair_instruction = (
                    "Your previous output was invalid for the AMFV verifier "
                    "schema. Return only one valid JSON object with verdict, "
                    "medv1_score, score, confidence, supported_evidence_ids, "
                    "contradicted_evidence_ids, missing_context, and explanation."
                )

            response = self.completion_fn(
                **self._build_completion_kwargs(
                    claim=claim,
                    evidence=evidence,
                    repair_instruction=repair_instruction,
                )
            )

            raw_text = get_response_content(response)

            try:
                verification = parse_verification(raw_text)
                verification["verifier_name"] = "llm_medv1_style_verifier"
                verification["verifier_model"] = self.model
                return verification
            except VerifierParseError as error:
                last_error = error

        raise VerifierParseError(
            f"Failed to parse LLM verification after "
            f"{self.max_retries + 1} attempt(s)."
        ) from last_error

    def _build_completion_kwargs(
        self,
        claim: str,
        evidence: list[dict[str, Any]],
        repair_instruction: str | None,
    ) -> dict[str, Any]:
        """Build LiteLLM completion kwargs."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": build_messages(
                claim=claim,
                evidence=evidence,
                repair_instruction=repair_instruction,
            ),
            "max_tokens": self.max_tokens,
        }

        if self.temperature is not None:
            kwargs["temperature"] = self.temperature

        if self.api_key:
            kwargs["api_key"] = self.api_key

        if self.api_base:
            kwargs["api_base"] = self.api_base

        return kwargs


def read_input_claim(args: argparse.Namespace) -> str:
    """Read claim from CLI arguments."""
    if args.claim:
        return args.claim

    if args.claim_file:
        return args.claim_file.read_text(encoding="utf-8").strip()

    raise ValueError("Provide either --claim or --claim-file.")


def read_evidence(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Read evidence JSON from CLI arguments."""
    if args.evidence_json:
        data = json.loads(args.evidence_json)

        if not isinstance(data, list):
            raise ValueError("--evidence-json must be a JSON list.")

        return data

    if args.evidence_file:
        data = json.loads(args.evidence_file.read_text(encoding="utf-8"))

        if not isinstance(data, list):
            raise ValueError("--evidence-file must contain a JSON list.")

        return data

    raise ValueError("Provide either --evidence-json or --evidence-file.")


def list_providers() -> None:
    """Print provider examples."""
    print("Provider examples for AMFV verifier integration tests:")

    for provider, config in PROVIDER_EXAMPLES.items():
        print(f"{provider}:")
        print(f"  model env: {config['model_env']}")
        print(f"  default model: {config['default_model']}")
        print(f"  api key env: {config.get('api_key_env') or 'none'}")

        if config.get("api_base_env"):
            print(f"  api base env: {config['api_base_env']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LLM Med-V1-style medical claim verification."
    )

    parser.add_argument("--claim", type=str, default=None)
    parser.add_argument("--claim-file", type=Path, default=None)
    parser.add_argument("--evidence-json", type=str, default=None)
    parser.add_argument("--evidence-file", type=Path, default=None)
    parser.add_argument("--model", type=str, default=DEFAULT_VERIFIER_MODEL)
    parser.add_argument("--api-key", type=str, default=None)
    parser.add_argument("--api-base", type=str, default=None)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--list-providers", action="store_true")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.list_providers:
        list_providers()
        return

    claim = read_input_claim(args)
    evidence = read_evidence(args)

    verifier = LiteLLMClaimVerifier(
        model=args.model,
        api_key=args.api_key,
        api_base=args.api_base,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        max_retries=args.max_retries,
    )

    verification = verifier.verify_claim(claim=claim, evidence=evidence)
    output = json.dumps(verification, ensure_ascii=False, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote LLM verification to {args.output}")
        return

    print(output)


if __name__ == "__main__":
    main()