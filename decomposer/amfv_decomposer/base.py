"""Base decomposer interface and shared text utilities."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import NamedTuple

_nlp = None
_SKIP_PREFIXES = (
    "facts:",
    "here are",
    "here is",
    "independent facts",
    "the following",
    "note:",
    "based on",
)
_NO_CLAIM_PHRASES = frozenset({
    "no verifiable claim.",
    "no verifiable claim",
    "none",
    "n/a",
    "no claims",
    "no facts",
})
_LIST_MARKER_RE = re.compile(r"^(?:[-•*]|\d+[.)])\s+")
_BOLD_LINE_RE = re.compile(r"^\*\*(.+)\*\*$")


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
    """Split text into sentences using spaCy."""
    nlp = _get_nlp()
    doc = nlp(text)
    return [s.text.strip() for s in doc.sents if s.text.strip()]


def sliding_window(
    sentences: list[str], i: int, before: int = 3, after: int = 1
) -> tuple[str, str]:
    """Return (snippet, sentence) for the i-th sentence.

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
    """Parse model output into clean claim strings, stripping list markers and noise.

    Filters formatting noise observed in real runs: preamble lines
    ("Here is the breakdown..."), markdown header lines ("**Independent Facts:**"),
    and any line ending in a colon.
    """
    claims = []
    for line in text.split("\n"):
        line = _LIST_MARKER_RE.sub("", line.strip()).strip()
        bold = _BOLD_LINE_RE.match(line)
        if bold:
            line = bold.group(1).strip()
        if not line or line.endswith(":"):
            continue
        lower = line.lower()
        if any(lower.startswith(p) for p in _SKIP_PREFIXES):
            continue
        if lower in _NO_CLAIM_PHRASES:
            continue
        claims.append(line)
    return claims


class RecordResult(NamedTuple):
    """Decomposition output for a single record."""

    claims: list[str]
    raw_outputs: list[str]  # one generation per request, kept for offline re-scoring
    n_sentences: int


class BaseDecomposer(ABC):
    """Shared decomposition pipeline; subclasses only build prompts.

    The pipeline is: split sentences -> build_requests -> one flat generate
    call across all records -> parse_output per generation. Batching at this
    level keeps the inference client's concurrency cap as the only fan-out.
    """

    # "vllm": build_requests returns chat-message lists for the vLLM server.
    # "hf": build_requests returns prompt strings for a local pipeline
    #       loaded from `model_id`.
    backend: str = "vllm"
    model_id: str | None = None
    # Record field that carries context (e.g. "question").
    # None means the method is context-free by design (FActScore).
    default_context_key: str | None = None

    @abstractmethod
    def build_requests(self, text: str, sentences: list[str], context: str) -> list:
        """Return one request payload per LLM call (typically one per sentence)."""

    def parse_output(self, raw: str) -> list[str]:
        """Parse one raw generation into claims. Override for non-default formats."""
        return parse_claims(raw)

    def decompose(self, text: str, context: str = "") -> list[str]:
        """Decompose a single text into claims (library convenience API)."""
        sentences = split_sentences(text)
        if not sentences:
            return []
        outputs = self._generate(self.build_requests(text, sentences, context))
        return [c for out in outputs for c in self.parse_output(out)]

    def decompose_batch(
        self,
        records: list[dict],
        text_key: str = "response",
        context_key: str | None = None,
    ) -> list[RecordResult]:
        """Decompose all records in one flat generate call."""
        # Explicit argument wins; fall back to the class-level default.
        effective_key = context_key if context_key is not None else self.default_context_key

        sentences_per_record: list[list[str]] = []
        requests_per_record: list[list] = []
        for r in records:
            context = r.get(effective_key, "") if effective_key else ""
            sentences = split_sentences(r[text_key])
            sentences_per_record.append(sentences)
            requests_per_record.append(self.build_requests(r[text_key], sentences, context))

        flat_outputs = self._generate([req for reqs in requests_per_record for req in reqs])

        results = []
        pos = 0
        for sentences, requests in zip(sentences_per_record, requests_per_record):
            raw = flat_outputs[pos:pos + len(requests)]
            pos += len(requests)
            claims = [c for out in raw for c in self.parse_output(out)]
            results.append(RecordResult(claims, raw, len(sentences)))
        return results

    def _generate(self, requests: list) -> list[str]:
        if not requests:
            return []
        if self.backend == "hf":
            if self.model_id is None:
                raise ValueError(f"{type(self).__name__}: hf backend requires model_id")
            from .hf_client import hf_generate
            return hf_generate(requests, model_id=self.model_id)
        from .vllm_client import chat_generate
        return chat_generate(requests)
