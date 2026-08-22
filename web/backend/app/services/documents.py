from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlmodel import Session, col, select

from app.models import Chunk, Document, EvalItem, EvalType, ItemStatus
from app.schemas import EvidenceSpan


class EvidenceSpanValidationError(ValueError):
    def __init__(self, messages: Sequence[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = list(messages)


def normalize_external_id(value: str | None) -> str:
    normalized = (value or "").strip()
    return normalized or f"doc-{uuid.uuid4().hex[:12]}"


def create_document_with_chunks(
    session: Session,
    *,
    dataset_id: int,
    title: str,
    content: str,
    external_id: str | None = None,
    is_active: bool = True,
    chunk_texts: Sequence[str] | None = None,
) -> Document:
    document_external_id = normalize_external_id(external_id)
    if chunk_texts is None:
        paragraphs = [{"idx": 0, "text": content}]
    else:
        paragraphs = [{"idx": idx, "text": text} for idx, text in enumerate(chunk_texts)]
    document = Document(
        dataset_id=dataset_id,
        external_id=document_external_id,
        title=title,
        content=content,
        paragraphs=paragraphs,
        is_active=is_active,
    )
    session.add(document)
    session.flush()
    assert document.id is not None
    for paragraph in paragraphs:
        position = int(paragraph["idx"])
        session.add(
            Chunk(
                dataset_id=dataset_id,
                document_id=document.id,
                external_id=f"{document_external_id}-chunk-{position}",
                text=str(paragraph["text"]),
                position=position,
            )
        )
    session.flush()
    return document


def validate_evidence_spans(
    session: Session,
    *,
    dataset_id: int,
    spans: Sequence[EvidenceSpan],
    allowed_document_ids: set[int] | None = None,
) -> list[Chunk]:
    messages: list[str] = []
    chunks: list[Chunk] = []
    for index, span in enumerate(spans, start=1):
        chunk = session.get(Chunk, span.chunk_id)
        if chunk is None:
            messages.append(f"Evidence span {index} references an unknown chunk.")
            continue
        chunks.append(chunk)
        if chunk.dataset_id != dataset_id:
            messages.append(f"Evidence span {index} references a chunk from another dataset.")
            continue
        if allowed_document_ids is not None and chunk.document_id not in allowed_document_ids:
            messages.append(f"Evidence span {index} references a chunk outside the selected documents.")
        if span.end > len(chunk.text):
            messages.append(f"Evidence span {index} ends past the chunk text.")
            continue
        if chunk.text[span.start : span.end] != span.text:
            messages.append(f"Evidence span {index} text does not match the current chunk text.")

    if messages:
        raise EvidenceSpanValidationError(messages)
    return chunks


def span_dicts(spans: Iterable[EvidenceSpan], *, kind: str | None = None) -> list[dict]:
    rows: list[dict] = []
    for span in spans:
        row = span.model_dump()
        if kind is not None:
            row["kind"] = kind
        rows.append(row)
    return rows


def resolve_item_chunks(
    session: Session, item: EvalItem, *, include_traps: bool = True
) -> list[Chunk]:
    chunk_ids: set[int] = set()
    for chunk_id in item.gold_chunk_ids or []:
        if isinstance(chunk_id, int):
            chunk_ids.add(chunk_id)
        elif isinstance(chunk_id, str) and chunk_id.isdigit():
            chunk_ids.add(int(chunk_id))
    if include_traps:
        for chunk_id in item.trap_chunk_ids or []:
            if isinstance(chunk_id, int):
                chunk_ids.add(chunk_id)
            elif isinstance(chunk_id, str) and chunk_id.isdigit():
                chunk_ids.add(int(chunk_id))
    for span in item.evidence_spans or []:
        if isinstance(span, dict):
            chunk_id = span.get("chunk_id")
            if isinstance(chunk_id, int):
                chunk_ids.add(chunk_id)
            elif isinstance(chunk_id, str) and chunk_id.isdigit():
                chunk_ids.add(int(chunk_id))

    if not chunk_ids:
        return []
    return list(session.exec(select(Chunk).where(col(Chunk.id).in_(chunk_ids))).all())


def documents_for_chunks(session: Session, chunks: Iterable[Chunk]) -> list[Document]:
    document_ids = {chunk.document_id for chunk in chunks}
    if not document_ids:
        return []
    return list(session.exec(select(Document).where(col(Document.id).in_(document_ids))).all())


def used_retrieval_document_ids(
    session: Session, *, dataset_id: int, user_id: uuid.UUID
) -> set[int]:
    items = session.exec(
        select(EvalItem).where(
            col(EvalItem.dataset_id) == dataset_id,
            col(EvalItem.eval_type) == EvalType.RETRIEVAL,
            col(EvalItem.author_user_id) == user_id,
            col(EvalItem.status).in_(
                [ItemStatus.DRAFT, ItemStatus.SUBMITTED, ItemStatus.ACTIVE]
            ),
            col(EvalItem.is_active) == True,  # noqa: E712
        )
    ).all()

    document_ids = {
        item.document_id for item in items if item.document_id is not None
    }
    chunks = [
        chunk
        for item in items
        for chunk in resolve_item_chunks(session, item, include_traps=True)
    ]
    document_ids.update(chunk.document_id for chunk in chunks)
    return document_ids
