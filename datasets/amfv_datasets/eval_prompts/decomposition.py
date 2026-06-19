from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

ClaimType = Literal["factual", "hedged", "negation", "numeric", "procedural"]
SourceKind = Literal["model_output", "reasoning_trace", "document_passage", "multiple_choice_rationale"]


@dataclass
class DecompositionItem:
    claim: str
    claim_type: ClaimType
    source_span: str
    requires_coreference: bool = False
    original_pronoun: str | None = None
    resolved_referent: str | None = None
    is_distractor: bool = False
    distractor_reason: str | None = None
    notes: str = ""


SYSTEM_PROMPT = """\
You are an expert medical claim decomposer building a reference evaluation \
dataset for a medical fact verification system.

Your task is to decompose a piece of medical text into a list of atomic, \
independently-verifiable claims, following the three Baichuan-M3 rules and the \
hard-case handling rules below.

CORE DECOMPOSITION RULES (Baichuan-M3)
=======================================
Rule 1 — Atomic claims with full coreference resolution
  Each claim must be self-contained: anyone reading the claim alone, without \
the source text, must be able to look it up and verify it. Resolve all \
pronouns and anaphors before writing the claim.
  Example: "She was started on lisinopril 10 mg daily." →
    "The patient (45-year-old female with hypertension) was started on \
lisinopril 10 mg daily."

Rule 2 — Distractor filtering
  When the source is a multiple-choice rationale, the author frequently recites \
incorrect options before refuting them. Do NOT decompose these distractor spans \
into claims. Mark them with is_distractor: true so human reviewers can verify \
the filtering. Example: "Option B, which states that amoxicillin is the first-\
line treatment for MRSA infections, is incorrect." → mark is_distractor: true \
with distractor_reason: "recitation of incorrect MC option".

Rule 3 — Deduplication with order preservation
  If two spans assert the same fact (even with different phrasing), produce ONE \
claim and cite both source spans. Preserve the logical order of the original text.

HARD-CASE HANDLING RULES
=========================
Numeric precision
  Never round, truncate, or paraphrase numbers, units, or lab values. Preserve \
them exactly. "eGFR 45 mL/min/1.73m²" must appear verbatim in the claim, not \
as "low eGFR" or "reduced kidney function".

Hedged claims
  Preserve uncertainty markers. "may suggest", "is consistent with", "no \
definitive evidence" must appear in the claim text.  Label these hedged.

Negated findings
  "No evidence of pneumonia" is a verifiable claim.  Do not drop negations or \
rephrase them as positive statements. Label these negation.

Long pronoun chains
  When a pronoun references a subject defined more than two sentences earlier, \
set requires_coreference: true, record the pronoun in original_pronoun, and \
write the full referent in resolved_referent.

CLAIM TYPES
===========
factual      — direct assertion of a medical fact.
hedged       — source qualifies with uncertainty language.
negation     — asserts absence or non-occurrence.
numeric      — truth depends on a specific number, dose, lab value, or unit.
procedural   — a step or ordered clinical action.

OUTPUT FORMAT
=============
Respond with a JSON array and nothing else—no preamble, no markdown fences, \
no trailing commentary. Each element must be an object with:

  claim                (string)  Self-contained, coreference-resolved claim.
  claim_type           (string)  One of: factual, hedged, negation, numeric, procedural
  source_span          (string)  Exact substring(s) from the source; for multi-span,
                                 join with " [...] ".
  requires_coreference (boolean) true if pronoun resolution was needed.
  original_pronoun     (string|null)
  resolved_referent    (string|null)
  is_distractor        (boolean) true if this span should be filtered out.
  distractor_reason    (string|null)
  notes                (string)  "" if none.

QUALITY RULES
=============
- Every non-distractor claim must be independently verifiable without the source.
- Do not merge distinct facts into one claim; split them if needed.
- Do not split a single atomic fact across multiple claims.
- Keep claims in the order they appear in the source text.
- A claim about a drug-dose-indication triple must include all three elements \
  (e.g. "metformin 500 mg twice daily for type 2 diabetes mellitus").
"""


def user_prompt(
    text: str,
    *,
    source_kind: SourceKind = "model_output",
    document_title: str = "",
    document_source: str = "",
    extra_context: str = "",
) -> str:
    kind_label = {
        "model_output": "model output (final answer)",
        "reasoning_trace": "model reasoning trace (chain-of-thought)",
        "document_passage": "medical document passage",
        "multiple_choice_rationale": "multiple-choice answer rationale",
    }[source_kind]

    header_parts = [f"Source kind: {kind_label}"]
    if document_title:
        header_parts.append(f"Title / question stem: {document_title}")
    if document_source:
        header_parts.append(f"Source: {document_source}")
    if extra_context:
        header_parts.append(f"Additional context: {extra_context}")
    header = "\n".join(header_parts)

    distractor_reminder = (
        "\nNOTE: This text contains multiple-choice distractors. Apply Rule 2 "
        "carefully—mark recitations of wrong options as is_distractor: true.\n"
        if source_kind == "multiple_choice_rationale"
        else ""
    )

    return f"""\
Decompose the following medical text into atomic, independently-verifiable claims.

{header}
{distractor_reminder}
TEXT
====
{text}
"""


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_response(raw: str) -> list[DecompositionItem]:
    text = raw.strip()
    match = _JSON_BLOCK_RE.search(text)
    if match:
        text = match.group(1)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Response is not valid JSON: {exc}\n\nRaw:\n{raw[:500]}") from exc

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array at the top level, got {type(data).__name__}")

    items: list[DecompositionItem] = []
    for i, obj in enumerate(data):
        try:
            items.append(
                DecompositionItem(
                    claim=obj["claim"],
                    claim_type=obj["claim_type"],
                    source_span=obj["source_span"],
                    requires_coreference=obj.get("requires_coreference", False),
                    original_pronoun=obj.get("original_pronoun"),
                    resolved_referent=obj.get("resolved_referent"),
                    is_distractor=obj.get("is_distractor", False),
                    distractor_reason=obj.get("distractor_reason"),
                    notes=obj.get("notes", ""),
                )
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Item {i} is missing required field: {exc}\n\nItem: {obj}") from exc

    return items


def active_claims(items: list[DecompositionItem]) -> list[DecompositionItem]:
    return [c for c in items if not c.is_distractor]


def distractor_claims(items: list[DecompositionItem]) -> list[DecompositionItem]:
    return [c for c in items if c.is_distractor]


def claims_by_type(items: list[DecompositionItem], claim_type: ClaimType) -> list[DecompositionItem]:
    return [c for c in items if c.claim_type == claim_type]


decomposition_user_prompt = user_prompt
parse_decomposition_response = parse_response