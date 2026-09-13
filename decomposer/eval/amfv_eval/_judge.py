"""LLM judge for atom coverage and operator-awareness evaluation."""

from __future__ import annotations

import json
import sys

import openai
from pydantic import BaseModel, ValidationError

from amfv_eval._cache import GenerationCache
from amfv_eval.operators import get_operator
from amfv_eval._utils import extract_json, split_thinking

__all__ = [
    "JudgeError",
    "judge_atoms",
    "judge_claim_consistency",
    "judge_operators",
    "judge_source_claims",
    "judge_operator_awareness",
    "judge_context_extraction",
]


class JudgeError(Exception):
    """Raised by a judge function when the API call or response parsing fails."""

    def __init__(self, exception_type: str, finish_reason: str | None = None) -> None:
        self.exception_type = exception_type
        self.finish_reason = finish_reason
        super().__init__(f"{exception_type} (finish_reason={finish_reason!r})")

_ATOM_SYSTEM = """\
You are evaluating whether a set of extracted atomic claims covers a set of gold \
atomic claims.

For each gold atom, output true if it is semantically covered by any of the extracted \
atoms — even if worded differently — and false otherwise.

Respond with valid JSON:
  {"atom_covered": [<bool>, <bool>, ...]}
One boolean per gold atom, in the same order.
"""

_OPERATOR_SYSTEM = """\
You are evaluating whether a scientific passage correctly applies specified linguistic \
operators. The relevant linguistic structure must be clearly present in the passage.

For each operator listed, output true if the passage uses it correctly, false otherwise.

Operator reference with examples:

Non-Relation Adding (no new relational fact introduced):
  coreference_fusion    — "Aspirin reduces inflammation, and it also thins the blood."
  apposition            — "BRCA1, a tumor suppressor gene, is located on chromosome 17."
  relative_clause       — "The drug, which targets EGFR, was approved in 2015."
  conjunction_reduction — "The treatment lowered blood pressure and cholesterol."
  ellipsis              — "Group A received the vaccine, and Group B the placebo."

Relation Adding (each introduces one new relational fact):
  causal      — "The tumor shrank because the drug inhibited EGFR."
  contrastive — "Group A improved, whereas Group B declined."
  temporal    — "The patient received the vaccine and developed immunity later."

Respond with valid JSON:
  {"operators_applied": {"<operator_name>": <bool>, ...}}
"""


_SOURCE_CLAIM_SYSTEM = """\
You are verifying the quality of a synthetic scientific training example.

Given a list of seed claims, a generated passage, and a list of gold atoms:
1. For each seed claim, output true in "in_passage" if the claim's fact is semantically \
represented in the passage (possibly paraphrased).
2. For each seed claim, output true in "in_gold" if it is semantically covered by at \
least one gold atom.

Respond with valid JSON:
  {"in_passage": [<bool>, ...], "in_gold": [<bool>, ...]}
One boolean per seed claim, in the same order as the input list.
"""

_OPERATOR_AWARENESS_SYSTEM = """\
You are evaluating whether a model's reasoning trace demonstrates awareness of specific \
linguistic relationships present in a scientific passage.

For each operator listed, output true if the reasoning trace shows the model noticed \
that kind of relationship — it does not need to name the operator explicitly.

Operator reference with examples:

Non-Relation Adding (no new relational fact introduced):
  coreference_fusion    — "Aspirin reduces inflammation, and it also thins the blood."
  apposition            — "BRCA1, a tumor suppressor gene, is located on chromosome 17."
  relative_clause       — "The drug, which targets EGFR, was approved in 2015."
  conjunction_reduction — "The treatment lowered blood pressure and cholesterol."
  ellipsis              — "Group A received the vaccine, and Group B the placebo."

Relation Adding (each introduces one new relational fact):
  causal      — "The tumor shrank because the drug inhibited EGFR."
  contrastive — "Group A improved, whereas Group B declined."
  temporal    — "The patient received the vaccine and developed immunity later."

Respond with valid JSON:
  {"operators_noticed": {"<operator_name>": <bool>, ...}}
"""

_CONTEXT_SYSTEM = """\
You are checking whether a model incorrectly extracted non-verifiable context \
sentences as atomic claims.

Context sentences are background, methodological, or framing statements that a \
correct decomposer should NOT extract — they are not independently verifiable facts.

Examples of context sentences:
  - "Regulatory T cells are central to immune homeostasis."
  - "This study used a murine model of experimental autoimmune encephalomyelitis."
  - "The role of transcription factors in T cell differentiation has been extensively studied."
  - "Participants were recruited from a tertiary care centre between 2010 and 2015."

For each context sentence, output true if it was extracted as an atomic claim \
(i.e., any extracted atom is semantically equivalent to or subsumes it), \
false otherwise.

Respond with valid JSON:
  {"context_extracted": [<bool>, <bool>, ...]}
One boolean per context sentence, in the same order.
"""

_CONSISTENCY_SYSTEM = """\
You are checking whether a set of scientific claims can all be simultaneously true.

If any two claims directly contradict each other — one asserts X while another \
asserts not-X, or they make mutually exclusive claims about the same subject — \
output false. Otherwise output true.

Respond with valid JSON:
  {"consistent": <bool>}
"""

MAX_TOKENS = 16384

class _AtomOutput(BaseModel):
    atom_covered: list[bool]


class _OperatorOutput(BaseModel):
    operators_applied: dict[str, bool]


class _SourceClaimOutput(BaseModel):
    in_passage: list[bool]
    in_gold: list[bool]


class _OperatorAwarenessOutput(BaseModel):
    operators_noticed: dict[str, bool]


class _ContextOutput(BaseModel):
    context_extracted: list[bool]


class _ConsistencyOutput(BaseModel):
    consistent: bool


def judge_atoms(
    required_atoms: list[str],
    extracted_atoms: list[str],
    *,
    client: openai.OpenAI,
    model: str,
    cache: GenerationCache,
) -> list[bool]:
    """Judge which gold atoms are semantically covered by extracted atoms.

    Args:
        required_atoms: Reference atoms from the eval example.
        extracted_atoms: Atoms produced by the decomposer model.
        client: OpenAI-compatible client.
        model: Judge model name.
        cache: Disk cache.

    Returns:
        One bool per gold atom; ``True`` means covered.

    Raises:
        JudgeError: On API failure or unparseable response.
    """
    gold_text = "\n".join(f"{i + 1}. {a}" for i, a in enumerate(required_atoms))
    extr_text = "\n".join(f"- {a}" for a in extracted_atoms) if extracted_atoms else "(none)"
    user_msg = f"Gold atoms:\n{gold_text}\n\nExtracted atoms:\n{extr_text}"
    messages = [
        {"role": "system", "content": _ATOM_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    cache_key = cache.key(model, json.dumps(messages))
    cached = cache.get(cache_key)
    if cached is not None:
        return cached["atom_covered"]

    _finish_reason: str | None = None
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=MAX_TOKENS,
        )
        _finish_reason = response.choices[0].finish_reason
        if _finish_reason == "length" and not response.choices[0].message.content:
            print("[WARNING] judge_atoms: response truncated — reasoning did not finish", file=sys.stderr, flush=True)
        content = response.choices[0].message.content or ""
        covered = _AtomOutput.model_validate_json(extract_json(content)).atom_covered
        # Align to gold length in case the model under/over-counts
        covered = (covered + [False] * len(required_atoms))[: len(required_atoms)]
    except (openai.APIError, json.JSONDecodeError, ValidationError) as e:
        raise JudgeError(type(e).__qualname__, _finish_reason) from e

    cache.set(cache_key, {"atom_covered": covered})
    return covered


def judge_operators(
    operators: list[str],
    passage: str,
    *,
    client: openai.OpenAI,
    model: str,
    cache: GenerationCache,
    stats: dict | None = None,
) -> dict[str, bool]:
    """Judge whether a passage correctly applies each specified operator.

    Args:
        operators: Operator names that were supposed to be applied.
        passage: The generated passage to evaluate.
        client: OpenAI-compatible client.
        model: Judge model name.
        cache: Disk cache.

    Returns:
        Mapping from operator name to applied boolean.

    Raises:
        JudgeError: On API failure or unparseable response.
    """
    op_desc = "\n".join(
        f"- {name}: {get_operator(name).description}" for name in operators
    )
    user_msg = f"Operators:\n{op_desc}\n\nPassage:\n{passage}"
    messages = [
        {"role": "system", "content": _OPERATOR_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    cache_key = cache.key(model, json.dumps(messages))
    cached = cache.get(cache_key)
    if cached is not None:
        return cached["operators_applied"]

    _finish_reason: str | None = None
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=MAX_TOKENS,
        )
        _finish_reason = response.choices[0].finish_reason
        if _finish_reason == "length":
            print("[WARNING] judge_operators: response truncated — reasoning did not finish", file=sys.stderr, flush=True)
        content = response.choices[0].message.content or ""
        if stats is not None:
            thinking, rest = split_thinking(content)
            stats["thinking_chars"] = stats.get("thinking_chars", 0) + len(thinking)
            stats.setdefault("responses", []).append(rest)
        applied = _OperatorOutput.model_validate_json(extract_json(content)).operators_applied
        for op in operators:
            applied.setdefault(op, False)
    except (openai.APIError, json.JSONDecodeError, ValidationError) as e:
        raise JudgeError(type(e).__qualname__, _finish_reason) from e

    cache.set(cache_key, {"operators_applied": applied})
    return applied


def judge_source_claims(
    source_claims: list[str],
    passage: str,
    required_atoms: list[str],
    *,
    client: openai.OpenAI,
    model: str,
    cache: GenerationCache,
    stats: dict | None = None,
) -> dict[str, list[bool]]:
    """Check that each source claim is represented in the passage and in the gold atoms.

    Used during dataset creation to verify example quality.

    Returns:
        Dict with ``"in_passage"`` and ``"in_gold"`` lists, one bool per source claim.

    Raises:
        JudgeError: On API failure or unparseable response.
    """
    n = len(source_claims)
    claims_text = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(source_claims))
    gold_text = "\n".join(f"- {a}" for a in required_atoms) if required_atoms else "(none)"
    user_msg = (
        f"Seed claims:\n{claims_text}\n\n"
        f"Passage:\n{passage}\n\n"
        f"Gold atoms:\n{gold_text}"
    )
    messages = [
        {"role": "system", "content": _SOURCE_CLAIM_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    cache_key = cache.key(model, json.dumps(messages))
    cached = cache.get(cache_key)
    if cached is not None:
        return {"in_passage": cached["in_passage"], "in_gold": cached["in_gold"]}

    _finish_reason: str | None = None
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=MAX_TOKENS,
        )
        _finish_reason = response.choices[0].finish_reason
        if _finish_reason == "length":
            print("[WARNING] judge_source_claims: response truncated — reasoning did not finish", file=sys.stderr, flush=True)
        content = response.choices[0].message.content or ""
        if stats is not None:
            thinking, rest = split_thinking(content)
            stats["thinking_chars"] = stats.get("thinking_chars", 0) + len(thinking)
            stats.setdefault("responses", []).append(rest)
        out = _SourceClaimOutput.model_validate_json(extract_json(content))
        in_passage = (out.in_passage + [False] * n)[:n]
        in_gold = (out.in_gold + [False] * n)[:n]
    except (openai.APIError, json.JSONDecodeError, ValidationError) as e:
        raise JudgeError(type(e).__qualname__, _finish_reason) from e

    result = {"in_passage": in_passage, "in_gold": in_gold}
    cache.set(cache_key, result)
    return result


def judge_operator_awareness(
    operators: list[str],
    thinking_trace: str,
    *,
    client: openai.OpenAI,
    model: str,
    cache: GenerationCache,
) -> dict[str, bool]:
    """Score whether a thinking trace mentions each operator relationship.

    Used during downstream model evaluation — not a hard pass/fail criterion.

    Returns:
        Mapping from operator name to noticed boolean.

    Raises:
        JudgeError: On API failure or unparseable response.
    """
    op_desc = "\n".join(
        f"- {name}: {get_operator(name).description}" for name in operators
    )
    user_msg = (
        f"Operators:\n{op_desc}\n\n"
        f"Reasoning trace:\n{thinking_trace or '(no trace available)'}"
    )
    messages = [
        {"role": "system", "content": _OPERATOR_AWARENESS_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    cache_key = cache.key(model, json.dumps(messages))
    cached = cache.get(cache_key)
    if cached is not None:
        return cached["operators_noticed"]

    _finish_reason: str | None = None
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=MAX_TOKENS,
        )
        _finish_reason = response.choices[0].finish_reason
        if _finish_reason == "length":
            print("[WARNING] judge_operator_awareness: response truncated — reasoning did not finish", file=sys.stderr, flush=True)
        content = response.choices[0].message.content or ""
        noticed = _OperatorAwarenessOutput.model_validate_json(extract_json(content)).operators_noticed
        for op in operators:
            noticed.setdefault(op, False)
    except (openai.APIError, json.JSONDecodeError, ValidationError) as e:
        raise JudgeError(type(e).__qualname__, _finish_reason) from e

    cache.set(cache_key, {"operators_noticed": noticed})
    return noticed


def judge_context_extraction(
    context_sentences: list[str],
    extracted_atoms: list[str],
    *,
    client: openai.OpenAI,
    model: str,
    cache: GenerationCache,
) -> list[bool]:
    """Judge whether context sentences were incorrectly extracted as atomic claims.

    Args:
        context_sentences: Non-verifiable sentences from the passage.
        extracted_atoms: Atoms produced by the decomposer model.
        client: OpenAI-compatible client.
        model: Judge model name.
        cache: Disk cache.

    Returns:
        One bool per context sentence; ``True`` means it was wrongly extracted.

    Raises:
        JudgeError: On API failure or unparseable response.
    """
    if not context_sentences:
        return []

    ctx_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(context_sentences))
    extr_text = "\n".join(f"- {a}" for a in extracted_atoms) if extracted_atoms else "(none)"
    user_msg = f"Context sentences:\n{ctx_text}\n\nExtracted atoms:\n{extr_text}"
    messages = [
        {"role": "system", "content": _CONTEXT_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    cache_key = cache.key(model, json.dumps(messages))
    cached = cache.get(cache_key)
    if cached is not None:
        return cached["context_extracted"]

    _finish_reason: str | None = None
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=MAX_TOKENS,
        )
        _finish_reason = response.choices[0].finish_reason
        content = response.choices[0].message.content or ""
        extracted = _ContextOutput.model_validate_json(extract_json(content)).context_extracted
        extracted = (extracted + [False] * len(context_sentences))[: len(context_sentences)]
    except (openai.APIError, json.JSONDecodeError, ValidationError) as e:
        raise JudgeError(type(e).__qualname__, _finish_reason) from e

    cache.set(cache_key, {"context_extracted": extracted})
    return extracted


def judge_claim_consistency(
    claims: list[str],
    *,
    client: openai.OpenAI,
    model: str,
    cache: GenerationCache,
) -> bool:
    """Judge whether a set of claims can all be simultaneously true.

    Args:
        claims: Claim texts to check for mutual consistency.
        client: OpenAI-compatible client.
        model: Judge model name.
        cache: Disk cache.

    Returns:
        ``True`` if the claims are mutually consistent, ``False`` if any pair
        directly contradicts.

    Raises:
        JudgeError: On API failure or unparseable response.
    """
    claims_text = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(claims))
    user_msg = f"Claims:\n{claims_text}"
    messages = [
        {"role": "system", "content": _CONSISTENCY_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    cache_key = cache.key(model, json.dumps(messages))
    cached = cache.get(cache_key)
    if cached is not None:
        return cached["consistent"]

    _finish_reason: str | None = None
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=MAX_TOKENS,
        )
        _finish_reason = response.choices[0].finish_reason
        content = response.choices[0].message.content or ""
        consistent = _ConsistencyOutput.model_validate_json(extract_json(content)).consistent
    except (openai.APIError, json.JSONDecodeError, ValidationError) as e:
        raise JudgeError(type(e).__qualname__, _finish_reason) from e

    cache.set(cache_key, {"consistent": consistent})
    return consistent
