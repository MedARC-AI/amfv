"""Convert source PDFs into markdown for scrapers.

Some guideline publishers release the document body only as a PDF, so the HTML
page carries an abstract at best. These helpers convert such PDFs into the same
clean markdown the HTML scrapers produce.

Docling is the default backend. On a five-document sample of WHO guidelines it
preserved GRADE evidence-table rows that Marker dropped, emitted no stray page
numbers where PyMuPDF4LLM emitted roughly twenty per excerpt, and recovered the
most heading structure. It also runs on CPU, which keeps the scrapers usable
without a GPU. See `datasets/benchmarks/README.md` for the measurements and
`datasets/benchmarks/pdf_backends.py` to reproduce them.
"""

# Adapted from Ashu's MedARC-AI/amfv PR #6 (Apache-2.0).

from __future__ import annotations

import logging
import re
from enum import StrEnum
from io import BytesIO

from amfv_datasets.scraping.base import ScrapeError

try:
    from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import DocItemLabel, DoclingDocument

    HAS_DOCLING = True
except ImportError:
    HAS_DOCLING = False

logger = logging.getLogger(__name__)

MIN_RUNNING_HEADER_CHARS = 20
"""Shortest title fragment treated as a running header rather than content."""

_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


class PdfBackend(StrEnum):
    """Supported PDF-to-markdown conversion backends."""

    DOCLING = "docling"


class PdfConversionError(ScrapeError):
    """Raised when a PDF cannot be converted to markdown."""


_DOCLING_CONVERTERS: dict[bool, DocumentConverter] = {}


def _docling_converter(*, ocr: bool) -> DocumentConverter:
    """Return a cached Docling converter, since model load is expensive."""
    converter = _DOCLING_CONVERTERS.get(ocr)
    if converter is None:
        options = PdfPipelineOptions()
        options.do_ocr = ocr
        options.do_table_structure = True
        options.table_structure_options.do_cell_matching = True
        converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        _DOCLING_CONVERTERS[ocr] = converter
    return converter


def pdf_to_markdown(
    data: bytes,
    *,
    backend: PdfBackend = PdfBackend.DOCLING,
    ocr: bool = False,
    running_header: str | None = None,
    name: str = "document.pdf",
) -> str:
    """Convert PDF bytes into markdown.

    Args:
        data: Raw PDF bytes.
        backend: Conversion backend to use (default: PdfBackend.DOCLING).
        ocr: Whether to run OCR. Leave disabled for born-digital PDFs, where it
            only adds runtime (default: False).
        running_header: Repeated page header to drop from the output, usually the
            document title (default: None).
        name: File name reported to the backend, used for logging and format
            detection (default: "document.pdf").
    """
    if not data:
        raise PdfConversionError("Cannot convert an empty PDF payload")
    if backend is not PdfBackend.DOCLING:
        raise PdfConversionError(f"Unsupported PDF backend: {backend}")
    if not HAS_DOCLING:
        raise PdfConversionError("Docling is required to convert PDFs; install it with `uv sync --group pdf`")

    try:
        result = _docling_converter(ocr=ocr).convert(DocumentStream(name=name, stream=BytesIO(data)))
        if result.status != ConversionStatus.SUCCESS:
            raise PdfConversionError(f"PDF '{name}' conversion was incomplete: {result.status}")
        markdown = result.document.export_to_markdown()
        markdown = _append_missing_footnotes(markdown, result.document)
    except PdfConversionError:
        raise
    except Exception as exc:  # noqa: BLE001 - backend raises library-specific errors
        raise PdfConversionError(f"Could not convert PDF '{name}': {exc}") from exc

    markdown = _strip_running_header(markdown, running_header)
    if not markdown.strip():
        raise PdfConversionError(f"PDF '{name}' produced no readable markdown")
    return markdown


def _append_missing_footnotes(markdown: str, document: DoclingDocument) -> str:
    # Docling's markdown serializer skips some table footnotes even when their label is enabled.
    normalized = " ".join(markdown.split())
    notes: list[str] = []
    for item in document.texts:
        if item.label is not DocItemLabel.FOOTNOTE or not item.text.strip():
            continue
        text = item.text.strip()
        if " ".join(text.split()) in normalized:
            continue
        page = f"Page {item.prov[0].page_no}: " if item.prov else ""
        notes.append(f"- {page}{text}")
    if notes:
        return f"{markdown}\n\n## PDF footnotes\n\n" + "\n".join(notes)
    return markdown


def count_markdown_sections(markdown: str) -> int:
    """Count sections in markdown by its shallowest heading level.

    Args:
        markdown: Markdown text to inspect.
    """
    levels = [len(match.group(1)) for line in markdown.splitlines() if (match := _HEADING_RE.match(line.strip()))]
    if not levels:
        return 1
    top_level = min(levels)
    return sum(1 for level in levels if level == top_level)


def _collapse_repeated_text(text: str) -> str:
    """Reduce text built from a repeated phrase to a single copy.

    Left and right page headers often land on one line, so a running header
    arrives doubled.
    """
    words = text.split()
    for parts in (2, 3, 4):
        if len(words) < parts or len(words) % parts:
            continue
        size = len(words) // parts
        chunk = words[:size]
        if all(words[index * size : (index + 1) * size] == chunk for index in range(parts)):
            return " ".join(chunk)
    return text


def _strip_running_header(markdown: str, running_header: str | None) -> str:
    """Drop standalone lines repeating a PDF running header.

    A non-heading line is dropped only when it is a long prefix of the title,
    because publishers shorten the title in page headers. Longer statements and headings
    such as "(Strong recommendation, moderate-certainty evidence)" repeat
    legitimately and must survive.
    """
    if not running_header:
        return markdown.strip()

    needle = " ".join(running_header.split()).casefold()
    if not needle:
        return markdown.strip()

    kept: list[str] = []
    for line in markdown.splitlines():
        candidate = _collapse_repeated_text(" ".join(line.split()).casefold())
        is_header = (
            not _HEADING_RE.match(line.strip())
            and len(candidate) >= MIN_RUNNING_HEADER_CHARS
            and (needle.startswith(candidate))
        )
        if not is_header:
            kept.append(line)
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(kept)).strip()


__all__ = [
    "HAS_DOCLING",
    "PdfBackend",
    "PdfConversionError",
    "count_markdown_sections",
    "pdf_to_markdown",
]
