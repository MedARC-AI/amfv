"""Base decomposer interface and shared text utilities."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor

_nlp = None
_SKIP_PREFIXES = ("facts:", "here are", "the following", "note:", "based on")
_NO_CLAIM_PHRASES = frozenset({
    "no verifiable claim.",
    "no verifiable claim",
    "none",
    "n/a",
    "no claims",
    "no facts",
})


def _get_nlp():
    global _nlp
    if _nlp is None:
        import spacy
        try:
            _nlp = spacy.load("en_core_web_sm", disable=["ner", "tagger", "lemmatizer"])
        except OSError:
            raise RuntimeError(
                "spaCy model not found. Run: python -m spacy download en_core_web_sm"
            )
    return _nlp


def split_sentences(text: str) -> list[str]:
    nlp = _get_nlp()
    doc = nlp(text)
    return [s.text.strip() for s in doc.sents if s.text.strip()]


def sliding_window(
    sentences: list[str], i: int, before: int = 3, after: int = 1
) -> tuple[str, str]:
    """
    Returns (snippet, sentence).

    snippet wraps the target sentence in <SOS>/<EOS> tags surrounded by context,
    mirroring VeriScore's approach. For long paragraphs (>5 sentences) the lead
    sentence is prepended for additional grounding.
    """
    context_before = " ".join(sentences[max(0, i - before):i])
    sentence = sentences[i].strip()
    context_after = " ".join(sentences[i + 1:i + 1 + after])

    marked = f"<SOS>{sentence}<EOS>"
    snippet = f"{context_before} {marked} {context_after}".strip()

    # Prepend lead sentence for grounding in long texts, but only when it is
    # not already inside the context window (i.e. when i > before).
    if len(sentences) > 5 and i > before:
        snippet = f"{sentences[0]} {snippet}"

    return snippet, sentence


def parse_claims(text: str) -> list[str]:
    """Parse model output into clean claim strings, stripping list markers and noise."""
    claims = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        lower = line.lower()
        if any(lower.startswith(p) for p in _SKIP_PREFIXES):
            continue
        line = re.sub(r"^(?:[-•*]|\d+[.)])\s+", "", line).strip()
        if line and line.lower() not in _NO_CLAIM_PHRASES:
            claims.append(line)
    return claims


class BaseDecomposer(ABC):
    # Subclasses set this to the record field that carries context (e.g. "question").
    # None means the method is context-free by design (FActScore).
    default_context_key: str | None = None

    @abstractmethod
    def decompose(self, text: str, context: str = "") -> list[str]: ...

    def decompose_batch(
        self,
        records: list[dict],
        text_key: str = "response",
        context_key: str | None = None,
    ) -> list[list[str]]:
        # Explicit argument wins; fall back to the class-level default.
        effective_key = context_key if context_key is not None else self.default_context_key

        def _run(r: dict) -> list[str]:
            context = r.get(effective_key, "") if effective_key else ""
            return self.decompose(r[text_key], context=context)

        with ThreadPoolExecutor() as pool:
            return list(pool.map(_run, records))
