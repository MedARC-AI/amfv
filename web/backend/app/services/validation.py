from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.models import (
    Chunk,
    Document,
    EvalFact,
    EvalItem,
    EvalType,
    FactPolarity,
    RetrievalCategory,
)


@dataclass
class ValidationResult:
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocking

    def as_flags(self) -> list[dict[str, str]]:
        return [{"level": "error", "message": message} for message in self.blocking] + [
            {"level": "warning", "message": message} for message in self.warnings
        ]


def split_paragraphs(content: str) -> list[dict[str, str | int]]:
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", content.strip()) if chunk.strip()]
    if not chunks and content.strip():
        chunks = [content.strip()]
    return [{"idx": idx, "text": text} for idx, text in enumerate(chunks)]


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").casefold()).strip()


def validate_item(
    session: Session,
    item: EvalItem,
    *,
    document: Document | None = None,
    chunks: list[Chunk] | None = None,
    facts: list[EvalFact] | None = None,
) -> ValidationResult:
    result = ValidationResult()
    prompt_norm = normalize_text(item.prompt_text)
    if not prompt_norm:
        result.blocking.append("Prompt is required.")
    duplicate = session.exec(
        select(EvalItem).where(
            EvalItem.dataset_id == item.dataset_id,
            EvalItem.id != item.id,
            EvalItem.prompt_text == item.prompt_text,
            EvalItem.is_active == True,  # noqa: E712
        )
    ).first()
    if duplicate:
        result.blocking.append("An item with the same prompt already exists in this dataset.")

    if item.eval_type == EvalType.RETRIEVAL:
        _validate_retrieval(result, item, document, chunks)
    elif item.eval_type == EvalType.FACT_DECOMP:
        _validate_fact_decomp(result, facts or [])
    return result


def _validate_retrieval(
    result: ValidationResult,
    item: EvalItem,
    document: Document | None,
    chunks: list[Chunk] | None,
) -> None:
    if item.category is None:
        result.blocking.append("Retrieval category is required.")
    evidence_chunks = chunks or []
    evidence_text = " ".join(chunk.text for chunk in evidence_chunks)
    if document is None and not evidence_chunks:
        result.blocking.append("Retrieval items require source evidence.")
        return
    answer_norm = normalize_text(item.expected_answer)
    source_text = evidence_text
    if not source_text and document is not None:
        source_text = document.content
    source_norm = normalize_text(source_text)
    spans = item.evidence_spans or []
    gold_spans = [
        span
        for span in spans
        if not isinstance(span, dict) or span.get("kind", "gold") == "gold"
    ]
    trap_spans = [
        span for span in spans if isinstance(span, dict) and span.get("kind") == "trap"
    ]
    paragraph_indices = [span.get("paragraph_idx") for span in spans if isinstance(span, dict)]
    chunk_ids = item.gold_chunk_ids or [span.get("chunk_id") for span in spans if isinstance(span, dict) and span.get("chunk_id")]
    evidence_document_ids = {
        chunk.document_id for chunk in evidence_chunks if getattr(chunk, "document_id", None)
    }

    if item.category == RetrievalCategory.VERBATIM and not gold_spans:
        result.blocking.append("VERBATIM items require highlighted answer text.")
    if item.category == RetrievalCategory.VERBATIM and len(gold_spans) > 1:
        result.blocking.append("VERBATIM items require exactly one highlighted answer span.")
    if item.category not in {RetrievalCategory.VERBATIM, RetrievalCategory.ADVERSARIAL, None} and not gold_spans:
        result.blocking.append("Retrieval items require selected evidence paragraph(s).")
    if item.category == RetrievalCategory.VERBATIM and answer_norm not in source_norm:
        result.blocking.append("VERBATIM answers must appear word-for-word in the selected evidence.")
    if item.category == RetrievalCategory.ADVERSARIAL:
        if not item.why_not_answerable:
            result.blocking.append("ADVERSARIAL items require why_not_answerable.")
        if not trap_spans:
            result.blocking.append("ADVERSARIAL items require selected trap evidence paragraph(s).")
        if len(trap_spans) > 1:
            result.blocking.append("ADVERSARIAL items require exactly one trap evidence paragraph.")
        if answer_norm and answer_norm in source_norm:
            result.warnings.append("ADVERSARIAL answer-like text appears in the selected evidence.")
    if item.category == RetrievalCategory.PARAPHRASE and len(gold_spans) > 1:
        result.blocking.append("PARAPHRASE items require exactly one evidence paragraph.")
    if item.category == RetrievalCategory.PARAPHRASE and _evidence_count(paragraph_indices, chunk_ids) != 1:
        result.blocking.append("PARAPHRASE items must cite exactly one evidence paragraph or chunk.")
    if item.category in {RetrievalCategory.MULTI_CHUNK, RetrievalCategory.MULTI_DOCUMENT}:
        unique_indices = sorted({idx for idx in paragraph_indices if isinstance(idx, int)})
        if _evidence_count(unique_indices, chunk_ids) < 2:
            result.blocking.append("Multi-chunk retrieval items must cite at least two evidence paragraphs or chunks.")
        if any((b - a) == 1 for a, b in zip(unique_indices, unique_indices[1:], strict=False)):
            result.warnings.append("Multi-chunk evidence should prefer discontinuous paragraphs.")
    if item.category == RetrievalCategory.MULTI_DOCUMENT and len(evidence_document_ids) < 2:
        result.blocking.append("MULTI_DOCUMENT items must cite chunks from at least two documents.")


def _evidence_count(paragraph_indices: list, chunk_ids: list) -> int:
    paragraph_count = len({idx for idx in paragraph_indices if isinstance(idx, int)})
    chunk_count = len({chunk_id for chunk_id in chunk_ids if chunk_id})
    return max(paragraph_count, chunk_count)


def _validate_fact_decomp(result: ValidationResult, facts: list[EvalFact]) -> None:
    if any(not normalize_text(fact.fact_text) for fact in facts):
        result.blocking.append("Facts cannot be empty.")
    fact_uuids = [fact.fact_uuid for fact in facts]
    duplicate_uuids = {fact_uuid for fact_uuid in fact_uuids if fact_uuids.count(fact_uuid) > 1}
    if duplicate_uuids:
        result.blocking.append("FACT_DECOMP fact UUIDs must be unique.")
    positions = [fact.position for fact in facts]
    duplicate_positions = {position for position in positions if positions.count(position) > 1}
    if duplicate_positions:
        result.blocking.append("FACT_DECOMP fact positions must be unique.")
    polarities = {fact.polarity for fact in facts}
    if FactPolarity.SHOULD_LIST not in polarities:
        result.blocking.append("FACT_DECOMP items require at least one SHOULD_LIST fact.")
    if FactPolarity.SHOULD_NOT_LIST not in polarities:
        result.blocking.append("FACT_DECOMP items require at least one SHOULD_NOT_LIST fact.")
    normalized_facts = [normalize_text(fact.fact_text) for fact in facts]
    duplicate_facts = {
        text for text in normalized_facts if text and normalized_facts.count(text) > 1
    }
    if duplicate_facts:
        result.warnings.append("FACT_DECOMP items contain duplicate or redundant facts.")
    context_dependent = [
        fact.fact_text
        for fact in facts
        if re.search(
            r"\b(this|that|these|those|it|they|he|she|above|former|latter)\b",
            fact.fact_text,
            flags=re.IGNORECASE,
        )
    ]
    if context_dependent:
        result.warnings.append(
            "FACT_DECOMP facts should be independently verifiable without context."
        )
