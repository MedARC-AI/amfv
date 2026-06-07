from __future__ import annotations

import argparse
import json
import os
import re
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any


DEFAULT_DECOMPOSER_MODEL = (
    os.getenv("AMFV_DECOMPOSER_MODEL")
    or os.getenv("DECOMPOSER_MODEL")
    or "gpt-4o-mini"
)

CLAIM_TYPES = {
    "diagnosis",
    "treatment",
    "epidemiology",
    "prognosis",
    "safety",
    "pathophysiology",
    "screening",
    "other",
}

CERTAINTY_VALUES = {
    "asserted",
    "hedged",
    "negated",
}

PROVIDER_EXAMPLES = {
    "openai": {
        "model_env": "AMFV_OPENAI_MODEL",
        "default_model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
    },
    "anthropic": {
        "model_env": "AMFV_ANTHROPIC_MODEL",
        "default_model": "anthropic/claude-3-5-haiku-latest",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "groq": {
        "model_env": "AMFV_GROQ_MODEL",
        "default_model": "groq/llama-3.1-8b-instant",
        "api_key_env": "GROQ_API_KEY",
    },
    "gemini": {
        "model_env": "AMFV_GEMINI_MODEL",
        "default_model": "gemini/gemini-1.5-flash",
        "api_key_env": "GEMINI_API_KEY",
    },
    "together_ai": {
        "model_env": "AMFV_TOGETHER_MODEL",
        "default_model": (
            "together_ai/meta-llama/"
            "Meta-Llama-3.1-8B-Instruct-Turbo"
        ),
        "api_key_env": "TOGETHERAI_API_KEY",
    },
    "mistral": {
        "model_env": "AMFV_MISTRAL_MODEL",
        "default_model": "mistral/mistral-small-latest",
        "api_key_env": "MISTRAL_API_KEY",
    },
    "cohere": {
        "model_env": "AMFV_COHERE_MODEL",
        "default_model": "command-r",
        "api_key_env": "COHERE_API_KEY",
    },
    "perplexity": {
        "model_env": "AMFV_PERPLEXITY_MODEL",
        "default_model": "perplexity/sonar",
        "api_key_env": "PERPLEXITYAI_API_KEY",
    },
    "openrouter": {
        "model_env": "AMFV_OPENROUTER_MODEL",
        "default_model": "openrouter/openai/gpt-4o-mini",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "fireworks": {
        "model_env": "AMFV_FIREWORKS_MODEL",
        "default_model": (
            "fireworks_ai/accounts/fireworks/models/"
            "llama-v3p1-8b-instruct"
        ),
        "api_key_env": "FIREWORKS_API_KEY",
    },
    "ollama": {
        "model_env": "AMFV_OLLAMA_MODEL",
        "default_model": "ollama/llama3.2",
        "api_key_env": None,
    },
    "local_openai": {
        "model_env": "AMFV_LOCAL_OPENAI_MODEL",
        "default_model": "openai/local-model",
        "api_key_env": None,
        "api_base_env": "AMFV_LOCAL_OPENAI_API_BASE",
    },
}


SYSTEM_PROMPT = textwrap.dedent(
    """
    You are the AMFV Medical Claim Decomposer.

    Your job is to transform a medical passage into atomic, self-contained,
    independently verifiable claims in the style of FActScore.

    The downstream system will retrieve evidence and verify each claim. Therefore,
    your output must be precise, clinically faithful, and conservative.

    Core objective:
    Extract only the factual medical claims that are explicitly present in the
    input. Do not add medical knowledge, do not infer unstated conclusions, and
    do not simplify claims in a way that changes their clinical meaning.

    Definition of an atomic medical claim:
    A claim is atomic when it expresses one verifiable proposition about one
    main subject, relation, and object/value. If a sentence contains multiple
    facts, split it into multiple claims.

    Examples of facts that should be split:
    - "Metformin lowers HbA1c and may cause gastrointestinal side effects."
      becomes:
      1. "Metformin lowers HbA1c."
      2. "Metformin may cause gastrointestinal side effects."

    - "Aspirin reduces platelet aggregation and increases bleeding risk."
      becomes:
      1. "Aspirin reduces platelet aggregation."
      2. "Aspirin increases bleeding risk."

    Medical specificity rules:
    Preserve all clinically meaningful details exactly where present:
    - drug names
    - doses
    - routes of administration
    - dosing frequency
    - treatment duration
    - laboratory values
    - units
    - percentages
    - age groups
    - pregnancy status
    - renal function
    - disease severity
    - contraindications
    - comparators
    - guideline names or codes
    - time horizon
    - population restrictions
    - diagnostic thresholds

    Do not generalize:
    - "eGFR below 30 mL/min/1.73 m²" must not become "low kidney function".
    - "children under 5 years" must not become "children".
    - "severe asthma exacerbation" must not become "asthma".
    - "may reduce mortality" must not become "reduces mortality".

    Coreference resolution:
    Resolve pronouns and vague references when the referent is clear.
    - If the passage says "Metformin is first-line. It reduces HbA1c.",
      output "Metformin reduces HbA1c."
    - If the referent is ambiguous, keep the claim faithful and do not guess.

    Hedging and uncertainty:
    Preserve uncertainty markers.
    Use certainty = "hedged" when the claim contains uncertainty, possibility,
    weak recommendation, conditionality, or non-definitive language.

    Examples of hedged language:
    - may
    - might
    - can
    - could
    - is associated with
    - is consistent with
    - suggests
    - may indicate
    - may be considered
    - is likely
    - is possible

    Negation:
    Preserve negation exactly.
    Use certainty = "negated" when the claim states that something is absent,
    not recommended, not diagnostic, not associated, not shown, or does not
    occur.

    Examples:
    - "PSA alone does not diagnose prostate cancer."
    - "Antibiotics are not recommended for viral upper respiratory infection."
    - "The trial did not show a mortality benefit."

    Do not turn negated claims into positive claims.

    Distractor and rejected-option handling:
    Many medical answers discuss incorrect diagnoses, rejected multiple-choice
    options, or differential diagnoses. Do not convert rejected options into
    asserted claims.

    Example:
    Input: "Pneumonia is unlikely because the chest X-ray is normal."
    Correct:
    - "Pneumonia is unlikely in the context of a normal chest X-ray."
    Incorrect:
    - "The patient has pneumonia."

    Input: "Option B is wrong because beta-blockers can worsen asthma."
    Correct:
    - "Beta-blockers can worsen asthma."
    Incorrect:
    - "Option B is correct."

    Recommendations and guidelines:
    A recommendation is a factual claim if the text states that a guideline,
    clinician, or source recommends something.
    Preserve the source of the recommendation when present.

    Correct:
    - "NICE recommends inhaled corticosteroids for maintenance therapy in asthma."
    Incorrect:
    - "Inhaled corticosteroids are always required for all asthma patients."

    Comparative claims:
    Preserve comparators and directionality.
    Do not remove the comparison group.

    Correct:
    - "Ticagrelor reduces cardiovascular death, MI, or stroke relative to
      clopidogrel."
    Incorrect:
    - "Ticagrelor reduces cardiovascular events."

    Causal versus associative claims:
    Do not convert association into causation.

    Correct:
    - "Smoking is associated with increased cardiovascular risk."
    Incorrect:
    - "Smoking causes cardiovascular disease."
    unless the input explicitly says "causes".

    Scope/context dependence:
    Set requires_context = true when verification depends on any of the
    following:
    - age
    - pregnancy
    - renal function
    - liver function
    - disease severity
    - dose
    - route
    - treatment duration
    - country or guideline source
    - date or guideline version
    - inpatient versus outpatient setting
    - adult versus pediatric population
    - comorbidities
    - contraindications
    - diagnostic threshold
    - population subgroup

    Set requires_context = false only for broad claims that can be verified
    without substantial clinical context.

    Claim type rules:
    Choose exactly one claim_type.

    diagnosis:
    Claims about diagnosis, diagnostic criteria, clinical signs, symptoms,
    investigations, thresholds, or interpretation of tests.

    treatment:
    Claims about treatment, management, prevention, drugs, procedures, dosing,
    monitoring, or guideline recommendations.

    epidemiology:
    Claims about prevalence, incidence, risk factors, populations, frequency,
    burden, demographics, or distribution.

    prognosis:
    Claims about outcomes, mortality, survival, recovery, recurrence,
    complications, disease progression, or risk prediction.

    safety:
    Claims about adverse effects, contraindications, toxicity, interactions,
    warnings, bleeding risk, allergy, pregnancy safety, renal safety, or harm.

    pathophysiology:
    Claims about mechanisms, biological processes, disease pathways,
    pharmacology, physiology, or causal mechanisms.

    screening:
    Claims about screening tests, screening intervals, eligibility,
    population-level detection, or preventive case-finding.

    other:
    Use only when no other category fits.

    What to exclude:
    Do not output:
    - greetings
    - empathy
    - filler
    - apologies
    - disclaimers
    - generic safety advice
    - "consult a doctor" statements
    - headings without factual content
    - rhetorical questions
    - purely administrative text
    - unsupported implications not explicitly stated
    - duplicate claims

    Output requirements:
    Return only one valid JSON object.
    Do not include markdown.
    Do not include explanations.
    Do not include citations.
    Do not include confidence scores.
    Do not include evidence.
    Do not include any text outside the JSON object.

    The JSON object must have this exact structure:

    {
      "claims": [
        {
          "claim": "single atomic medical claim",
          "claim_type": "diagnosis | treatment | epidemiology | prognosis | safety | pathophysiology | screening | other",
          "certainty": "asserted | hedged | negated",
          "requires_context": true
        }
      ]
    }

    If the passage contains no factual medical claims, return:

    {
      "claims": []
    }
    """
).strip()


DEMO_PASSAGE_1 = (
    "Metformin is the first-line pharmacological treatment for type 2 diabetes "
    "in adults. It reduces HbA1c by approximately 1–2% and is associated with "
    "weight neutrality or modest weight loss. Metformin is contraindicated in "
    "patients with an eGFR below 30 mL/min/1.73 m²."
)

DEMO_OUTPUT_1 = {
    "claims": [
        {
            "claim": (
                "Metformin is the first-line pharmacological treatment for "
                "type 2 diabetes in adults."
            ),
            "claim_type": "treatment",
            "certainty": "asserted",
            "requires_context": True,
        },
        {
            "claim": "Metformin reduces HbA1c by approximately 1–2%.",
            "claim_type": "treatment",
            "certainty": "asserted",
            "requires_context": True,
        },
        {
            "claim": (
                "Metformin is associated with weight neutrality or modest "
                "weight loss."
            ),
            "claim_type": "safety",
            "certainty": "asserted",
            "requires_context": True,
        },
        {
            "claim": (
                "Metformin is contraindicated in patients with an eGFR below "
                "30 mL/min/1.73 m²."
            ),
            "claim_type": "safety",
            "certainty": "asserted",
            "requires_context": True,
        },
    ]
}


DEMO_PASSAGE_2 = (
    "I'm sorry you're worried. A PSA of 4.2 ng/mL can be elevated depending "
    "on age and clinical context. Prostate cancer is not diagnosed by PSA "
    "alone, and benign prostatic hyperplasia can also increase PSA."
)

DEMO_OUTPUT_2 = {
    "claims": [
        {
            "claim": (
                "A PSA of 4.2 ng/mL can be elevated depending on age and "
                "clinical context."
            ),
            "claim_type": "diagnosis",
            "certainty": "hedged",
            "requires_context": True,
        },
        {
            "claim": "Prostate cancer is not diagnosed by PSA alone.",
            "claim_type": "diagnosis",
            "certainty": "negated",
            "requires_context": True,
        },
        {
            "claim": "Benign prostatic hyperplasia can increase PSA.",
            "claim_type": "pathophysiology",
            "certainty": "hedged",
            "requires_context": True,
        },
    ]
}


DEMO_PASSAGE_3 = (
    "Pneumonia is unlikely because the chest X-ray is normal. The patient has "
    "wheeze that improves with salbutamol, which supports asthma exacerbation. "
    "Antibiotics are not routinely recommended for uncomplicated viral upper "
    "respiratory infections."
)

DEMO_OUTPUT_3 = {
    "claims": [
        {
            "claim": (
                "Pneumonia is unlikely in the context of a normal chest X-ray."
            ),
            "claim_type": "diagnosis",
            "certainty": "hedged",
            "requires_context": True,
        },
        {
            "claim": "The patient's wheeze improves with salbutamol.",
            "claim_type": "diagnosis",
            "certainty": "asserted",
            "requires_context": True,
        },
        {
            "claim": (
                "Wheeze that improves with salbutamol supports asthma "
                "exacerbation."
            ),
            "claim_type": "diagnosis",
            "certainty": "hedged",
            "requires_context": True,
        },
        {
            "claim": (
                "Antibiotics are not routinely recommended for uncomplicated "
                "viral upper respiratory infections."
            ),
            "claim_type": "treatment",
            "certainty": "negated",
            "requires_context": True,
        },
    ]
}


class DecomposerParseError(ValueError):
    """Raised when LLM output cannot be parsed into AMFV claim records."""


def build_messages(text: str, repair_instruction: str | None = None) -> list[dict[str, str]]:
    """Build the few-shot FActScore-style prompt."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Decompose the following medical text into atomic claims.\n\n"
                f"Text:\n{DEMO_PASSAGE_1}"
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(DEMO_OUTPUT_1, ensure_ascii=False),
        },
        {
            "role": "user",
            "content": (
                "Decompose the following medical text into atomic claims.\n\n"
                f"Text:\n{DEMO_PASSAGE_2}"
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(DEMO_OUTPUT_2, ensure_ascii=False),
        },
        {
            "role": "user",
            "content": (
                "Decompose the following medical text into atomic claims.\n\n"
                f"Text:\n{DEMO_PASSAGE_3}"
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(DEMO_OUTPUT_3, ensure_ascii=False),
        },
        {
            "role": "user",
            "content": (
                "Decompose the following medical text into atomic claims.\n\n"
                f"Text:\n{text.strip()}"
            ),
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
    """Extract the outermost JSON object from model output."""
    text = strip_markdown_fence(raw_text)

    if text.startswith("{") and text.endswith("}"):
        return text

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise DecomposerParseError("LLM response did not contain a JSON object.")

    return text[start : end + 1]


def validate_claim_record(item: Any) -> dict[str, Any]:
    """Validate one decomposed claim record."""
    if not isinstance(item, dict):
        raise DecomposerParseError("Each claim must be a JSON object.")

    claim = item.get("claim")
    claim_type = item.get("claim_type")
    certainty = item.get("certainty")
    requires_context = item.get("requires_context")

    if not isinstance(claim, str) or not claim.strip():
        raise DecomposerParseError("Each claim must have a non-empty claim string.")

    if claim_type not in CLAIM_TYPES:
        raise DecomposerParseError(f"Invalid claim_type: {claim_type}")

    if certainty not in CERTAINTY_VALUES:
        raise DecomposerParseError(f"Invalid certainty: {certainty}")

    if not isinstance(requires_context, bool):
        raise DecomposerParseError("requires_context must be a boolean.")

    return {
        "claim": claim.strip(),
        "claim_type": claim_type,
        "certainty": certainty,
        "requires_context": requires_context,
    }


def parse_decomposition(raw_text: str) -> list[dict[str, Any]]:
    """Parse LLM JSON output into AMFV claim records."""
    json_text = extract_json_object(raw_text)

    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as error:
        raise DecomposerParseError(
            f"LLM response was not valid JSON: {raw_text}"
        ) from error

    claims = payload.get("claims")

    if not isinstance(claims, list):
        raise DecomposerParseError("LLM output must contain a claims list.")

    return [validate_claim_record(item) for item in claims]


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

    raise DecomposerParseError("LLM response did not contain text content.")


class LiteLLMClaimDecomposer:
    """Provider-agnostic FActScore-style medical claim decomposer."""

    def __init__(
        self,
        model: str = DEFAULT_DECOMPOSER_MODEL,
        api_key: str | None = None,
        api_base: str | None = None,
        max_tokens: int = 1600,
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

    def decompose_text(self, text: str) -> list[dict[str, Any]]:
        """Call an LLM and return structured AMFV claim records."""
        text = text.strip()

        if not text:
            return []

        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            repair_instruction = None

            if attempt > 0:
                repair_instruction = (
                    "Your previous output was invalid for the AMFV schema. "
                    "Return only one valid JSON object with a top-level "
                    "'claims' list. Each item must contain claim, claim_type, "
                    "certainty, and requires_context. Do not include markdown "
                    "or explanations."
                )

            response = self.completion_fn(
                **self._build_completion_kwargs(
                    text=text,
                    repair_instruction=repair_instruction,
                )
            )

            raw_text = get_response_content(response)

            try:
                return parse_decomposition(raw_text)
            except DecomposerParseError as error:
                last_error = error

        raise DecomposerParseError(
            f"Failed to parse LLM decomposition after "
            f"{self.max_retries + 1} attempt(s)."
        ) from last_error

    def _build_completion_kwargs(
        self,
        text: str,
        repair_instruction: str | None,
    ) -> dict[str, Any]:
        """Build LiteLLM completion kwargs."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": build_messages(
                text=text,
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


def read_input_text(args: argparse.Namespace) -> str:
    """Read input from CLI arguments."""
    if args.text:
        return args.text

    if args.input_file:
        return args.input_file.read_text(encoding="utf-8")

    raise ValueError("Provide either --text or --input-file.")


def list_providers() -> None:
    """Print provider examples."""
    print("Provider examples for AMFV decomposer integration tests:")

    for provider, config in PROVIDER_EXAMPLES.items():
        print(f"{provider}:")
        print(f"  model env: {config['model_env']}")
        print(f"  default model: {config['default_model']}")
        print(f"  api key env: {config.get('api_key_env') or 'none'}")

        if config.get("api_base_env"):
            print(f"  api base env: {config['api_base_env']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LLM FActScore-style medical claim decomposition."
    )

    parser.add_argument("--text", type=str, default=None)
    parser.add_argument("--input-file", type=Path, default=None)
    parser.add_argument("--model", type=str, default=DEFAULT_DECOMPOSER_MODEL)
    parser.add_argument("--api-key", type=str, default=None)
    parser.add_argument("--api-base", type=str, default=None)
    parser.add_argument("--max-tokens", type=int, default=1600)
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

    text = read_input_text(args)

    decomposer = LiteLLMClaimDecomposer(
        model=args.model,
        api_key=args.api_key,
        api_base=args.api_base,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        max_retries=args.max_retries,
    )

    claims = decomposer.decompose_text(text)
    output = json.dumps({"claims": claims}, ensure_ascii=False, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote LLM decomposition to {args.output}")
        return

    print(output)


if __name__ == "__main__":
    main()