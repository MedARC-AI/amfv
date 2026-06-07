"""Base decomposer interface and shared text utilities."""

from __future__ import annotations

import re
from abc import ABC

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

    if len(sentences) > 5:
        lead = sentences[0]
        snippet = f"{lead} {context_before} {marked} {context_after}".strip()
    else:
        snippet = f"{context_before} {marked} {context_after}".strip()

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
        line = re.sub(r"^[-•*]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        line = line.strip()
        if line and line.lower() not in _NO_CLAIM_PHRASES:
            claims.append(line)
    return claims


class BaseDecomposer(ABC):
    def decompose(self, text: str, context: str = "") -> list[str]:
        raise NotImplementedError

    def decompose_batch(
        self, records: list[dict], text_key: str = "response"
    ) -> list[list[str]]:
        return [self.decompose(r[text_key]) for r in records]
