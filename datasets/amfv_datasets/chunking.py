""""""

from __future__ import annotations

import re

from .corpus import Chunk, Document

_HEADING = re.compile(r"^\s{0,3}(#{1,6}\s|\d+(\.\d+)*\s+\S|[A-Z][A-Z \-]{6,}$)")


def _segments(text: str) -> list[str]:
    """"""
    raw = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    return [s.strip() for s in raw if s.strip()]


def chunk_document(
    doc: Document,
    target_words: int = 350,
    overlap_words: int = 40,
    min_words: int = 20,
) -> list[Chunk]:
    """"""
    segments = _segments(doc.text)
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_words = 0
    ordinal = 0

    def flush() -> None:
        nonlocal buf, buf_words, ordinal
        if not buf:
            return
        body = "\n\n".join(buf).strip()
        if len(body.split()) >= min_words or not chunks:
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}::{ordinal}",
                    doc_id=doc.doc_id,
                    source=doc.source,
                    title=doc.title,
                    text=body,
                    ordinal=ordinal,
                    url=doc.url,
                    metadata=dict(doc.metadata),
                )
            )
            ordinal += 1
        buf, buf_words = [], 0

    for seg in segments:
        seg_words = len(seg.split())

        if seg_words > target_words and not _HEADING.match(seg):
            flush()
            words = seg.split()
            step = max(1, target_words - overlap_words)
            for start in range(0, len(words), step):
                window = words[start : start + target_words]
                if len(window) < min_words and chunks:
                    break
                chunks.append(
                    Chunk(
                        chunk_id=f"{doc.doc_id}::{ordinal}",
                        doc_id=doc.doc_id,
                        source=doc.source,
                        title=doc.title,
                        text=" ".join(window),
                        ordinal=ordinal,
                        url=doc.url,
                        metadata=dict(doc.metadata),
                    )
                )
                ordinal += 1
            continue

        if buf_words + seg_words > target_words and buf:
            flush()

            if overlap_words and chunks:
                tail = chunks[-1].text.split()[-overlap_words:]
                buf, buf_words = [" ".join(tail)], len(tail)
        buf.append(seg)
        buf_words += seg_words

    flush()
    return chunks


def chunk_documents(docs, **kwargs) -> list[Chunk]:
    out: list[Chunk] = []
    for doc in docs:
        out.extend(chunk_document(doc, **kwargs))
    return out
