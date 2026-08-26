"""Convert source PDFs into markdown for scrapers.

Some guideline publishers release the document body only as a PDF, so the HTML
page carries an abstract at best. These helpers convert such PDFs into the same
clean markdown the HTML scrapers produce.

pdf-inspector is the primary backend because it quickly extracts born-digital
PDFs and returns page-level quality signals. In automatic mode, a document that
needs OCR or has suspect font encoding falls back to Docling with OCR rather
than silently treating partial text as the full guideline. See
`datasets/benchmarks/README.md` for the original Docling evaluation.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib import metadata as importlib_metadata
from io import BytesIO
from typing import Any

from amfv_datasets.scraping.base import ScrapeError

try:
    import pdf_inspector as _pdf_inspector

    HAS_PDF_INSPECTOR = True
except ImportError:
    _pdf_inspector = None
    HAS_PDF_INSPECTOR = False

try:
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import OcrMode, PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    HAS_DOCLING = True
except ImportError:
    HAS_DOCLING = False

logger = logging.getLogger(__name__)

MIN_RUNNING_HEADER_CHARS = 20
"""Shortest title fragment treated as a running header rather than content."""

DOCLING_TIMEOUT_ENV = "AMFV_DOCLING_TIMEOUT_SECONDS"
"""Optional process-wide Docling wall-time setting for remote corpus jobs."""


def _configured_docling_timeout_seconds() -> float:
    raw = os.environ.get(DOCLING_TIMEOUT_ENV, "").strip()
    if not raw:
        return 900.0
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{DOCLING_TIMEOUT_ENV} must be a positive number of seconds; got {raw!r}") from exc
    if timeout <= 0:
        raise RuntimeError(f"{DOCLING_TIMEOUT_ENV} must be positive; got {raw!r}")
    return timeout


DEFAULT_DOCLING_TIMEOUT_SECONDS = _configured_docling_timeout_seconds()
"""Maximum wall time for one isolated Docling conversion by default."""

_WORKER_RESULT_MARKER = b"\x00AMFV_DOCLING_MARKDOWN\x00"

_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_LONG_UNBROKEN_ALPHA_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]{45,}(?![A-Za-z])")
_CORRUPTED_YEAR_TOKEN_RE = re.compile(r"(?<!\w)\$%[0-9%&>]{2,}(?!\w)")
_DISALLOWED_C0_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_DELETE_CONTROL_RE = re.compile(r"\x7f")
_C1_CONTROL_RE = re.compile(r"[\x80-\x9f]")
_KNOWN_OCR_HEADING_LABEL_RE = re.compile(
    r"^(?P<prefix>[ \t]*(?:#{1,6}[ \t]+)?)"
    r"(?P<label>AIMS 0F THE GUIDELINE(?: UPDATE)?|ACKN0WLEDGEMENTS?)"
    r"(?P<suffix>:[ \t]*)"
)

NATIVE_TEXT_QUALITY_HEURISTIC_VERSION = "amfv-native-text-v2"
"""Version persisted with deterministic post-extraction quality diagnostics."""

MAX_CLEAN_LONG_UNBROKEN_ALPHA_RUNS = 2
"""A few long identifiers are valid; repeated prose-sized runs indicate lost spaces."""

MAX_CLEAN_CORRUPTED_YEAR_TOKENS = 2
"""Repeated ``$%...`` tokens indicate a common substituted-font encoding failure."""

FULL_PAGE_OCR_POSTPROCESSING_VERSION = "normalize-known-heading-labels-v1"
"""Narrow correction for pinned all-caps label tokens seen in full-page OCR."""


class PdfBackend(StrEnum):
    """Supported PDF-to-markdown conversion backends."""

    AUTO = "auto"
    PDF_INSPECTOR = "pdf-inspector"
    DOCLING = "docling"


class PdfConversionError(ScrapeError):
    """Raised when a PDF cannot be converted to markdown."""


@dataclass(frozen=True)
class PdfConversionResult:
    """Markdown plus JSON-serializable conversion provenance."""

    markdown: str
    provenance: dict[str, Any]


_DOCLING_CONVERTERS: dict[tuple[bool, bool], Any] = {}


def _docling_converter(*, ocr: bool, full_page_ocr: bool = False) -> Any:
    """Return a cached Docling converter, since model load is expensive."""
    if not HAS_DOCLING:
        raise PdfConversionError("Docling is required to convert PDFs; install it with `uv sync --group pdf`")
    converter_key = (ocr, full_page_ocr)
    converter = _DOCLING_CONVERTERS.get(converter_key)
    if converter is None:
        options = PdfPipelineOptions()
        options.do_ocr = ocr
        if full_page_ocr:
            options.ocr_options.mode = OcrMode.FULL_PAGE
        options.do_table_structure = True
        options.table_structure_options.do_cell_matching = True
        converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        _DOCLING_CONVERTERS[converter_key] = converter
    return converter


def _package_version(distribution: str) -> str:
    """Return an installed distribution version without making it a hard dependency."""
    try:
        return importlib_metadata.version(distribution)
    except importlib_metadata.PackageNotFoundError:
        return "unknown"


def _conversion_timestamp() -> str:
    """Return a UTC ISO-8601 timestamp suitable for persisted provenance."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_ocr_reasons(entries: Any) -> list[dict[str, Any]]:
    """Normalize pdf-inspector OCR-reason objects into JSON-compatible dictionaries."""
    normalized: list[dict[str, Any]] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            page = entry.get("page")
            reasons = entry.get("reasons", [])
        else:
            page = getattr(entry, "page", None)
            reasons = getattr(entry, "reasons", [])
        normalized.append({"page": page, "reasons": list(reasons or [])})
    return normalized


def _pdf_inspector_diagnostics(result: Any) -> dict[str, Any]:
    """Extract the stable, serializable portion of a pdf-inspector result."""
    return {
        "version": _package_version("pdf-inspector"),
        "pdf_type": getattr(result, "pdf_type", None),
        "title": getattr(result, "title", None),
        "page_count": getattr(result, "page_count", None),
        "confidence": getattr(result, "confidence", None),
        "pages_needing_ocr": list(getattr(result, "pages_needing_ocr", []) or []),
        "ocr_reasons_by_page": _normalize_ocr_reasons(getattr(result, "ocr_reasons_by_page", [])),
        "pages_with_tables": list(getattr(result, "pages_with_tables", []) or []),
        "pages_with_columns": list(getattr(result, "pages_with_columns", []) or []),
        "is_complex_layout": bool(getattr(result, "is_complex_layout", False)),
        "has_encoding_issues": bool(getattr(result, "has_encoding_issues", False)),
        "processing_time_ms": getattr(result, "processing_time_ms", None),
    }


def _native_text_quality_diagnostics(markdown: str) -> dict[str, Any]:
    """Detect dense native-text corruption missed by backend encoding flags.

    This intentionally uses two high-specificity corpus symptoms instead of a
    general spelling model: repeated prose-sized alphabetic runs caused by lost
    spaces, repeated ``$%...`` year-like tokens caused by a substituted font
    map, and characters that cannot safely appear in Markdown text. TAB, LF,
    and CR remain valid; other C0 controls, DEL, C1 controls, NUL, and U+FFFD do
    not. One or two text-pattern occurrences remain acceptable because long
    identifiers and literal financial notation can be legitimate.
    """
    long_run_count = len(_LONG_UNBROKEN_ALPHA_RE.findall(markdown))
    corrupted_year_token_count = len(_CORRUPTED_YEAR_TOKEN_RE.findall(markdown))
    replacement_character_count = markdown.count("\ufffd")
    nul_character_count = markdown.count("\x00")
    disallowed_c0_control_count = len(_DISALLOWED_C0_CONTROL_RE.findall(markdown))
    delete_control_count = len(_DELETE_CONTROL_RE.findall(markdown))
    c1_control_count = len(_C1_CONTROL_RE.findall(markdown))
    invalid_control_character_count = disallowed_c0_control_count + delete_control_count + c1_control_count
    triggered_checks: list[str] = []
    if replacement_character_count or invalid_control_character_count:
        triggered_checks.append("invalid_unicode_characters")
    if long_run_count > MAX_CLEAN_LONG_UNBROKEN_ALPHA_RUNS:
        triggered_checks.append("repeated_unbroken_alpha_runs")
    if corrupted_year_token_count > MAX_CLEAN_CORRUPTED_YEAR_TOKENS:
        triggered_checks.append("repeated_corrupted_year_tokens")
    return {
        "heuristic_version": NATIVE_TEXT_QUALITY_HEURISTIC_VERSION,
        "likely_corrupt": bool(triggered_checks),
        "triggered_checks": triggered_checks,
        "long_unbroken_alpha_run_count": long_run_count,
        "corrupted_year_token_count": corrupted_year_token_count,
        "replacement_character_count": replacement_character_count,
        "nul_character_count": nul_character_count,
        "disallowed_c0_control_count": disallowed_c0_control_count,
        "delete_control_count": delete_control_count,
        "c1_control_count": c1_control_count,
        "invalid_control_character_count": invalid_control_character_count,
    }


def _has_invalid_text_characters(quality: dict[str, Any]) -> bool:
    """Return whether quality diagnostics contain unsafe Markdown characters."""
    return bool(quality["replacement_character_count"] or quality["invalid_control_character_count"])


def _quality_failure_summary(quality: dict[str, Any]) -> str:
    """Describe deterministic text-quality failures for errors and provenance."""
    return (
        f"{', '.join(quality['triggered_checks']) or 'no triggered checks'}; "
        f"{quality['long_unbroken_alpha_run_count']} repeated unbroken alphabetic runs; "
        f"{quality['corrupted_year_token_count']} corrupted year tokens; "
        f"{quality['replacement_character_count']} replacement characters; "
        f"{quality['invalid_control_character_count']} invalid controls "
        f"({quality['disallowed_c0_control_count']} disallowed C0, "
        f"{quality['delete_control_count']} DEL, {quality['c1_control_count']} C1; "
        f"{quality['nul_character_count']} NUL)"
    )


def _normalize_known_full_page_ocr_labels(markdown: str) -> str:
    """Correct two pinned O/0 confusions only in exact all-caps heading labels.

    Ordinary prose, arbitrary headings, medical terms, and other numeric tokens
    remain untouched. The rule is deliberately corpus-specific rather than a
    general OCR spell-checker.
    """
    normalized: list[str] = []
    for line in markdown.splitlines(keepends=True):
        match = _KNOWN_OCR_HEADING_LABEL_RE.match(line)
        if match is None:
            normalized.append(line)
            continue
        label = match.group("label").replace(" 0F ", " OF ").replace("ACKN0WLEDGEMENT", "ACKNOWLEDGEMENT")
        normalized.append(f"{match.group('prefix')}{label}{match.group('suffix')}{line[match.end() :]}")
    return "".join(normalized)


def _incomplete_pdf_reason(markdown: str, diagnostics: dict[str, Any]) -> str | None:
    """Explain why native extraction must not be labeled complete."""
    reasons: list[str] = []
    if not markdown.strip():
        reasons.append("pdf-inspector produced no readable Markdown")
    if diagnostics["pages_needing_ocr"]:
        pages = ", ".join(str(page) for page in diagnostics["pages_needing_ocr"])
        reasons.append(f"pages require OCR: {pages}")
    if diagnostics["has_encoding_issues"]:
        reasons.append("pdf-inspector detected unreliable font encoding")
    quality = diagnostics.get("post_extraction_quality", {})
    if quality.get("likely_corrupt"):
        reasons.append(
            "post-extraction quality heuristic detected likely corrupted native text "
            f"({_quality_failure_summary(quality)})"
        )
    return "; ".join(reasons) or None


def _docling_to_markdown(data: bytes, *, ocr: bool, full_page_ocr: bool = False, name: str) -> str:
    """Run Docling entirely in memory and return its raw Markdown."""
    result = _docling_converter(ocr=ocr, full_page_ocr=full_page_ocr).convert(
        DocumentStream(name=name, stream=BytesIO(data))
    )
    return result.document.export_to_markdown()


def _convert_with_docling(
    data: bytes,
    *,
    ocr: bool,
    full_page_ocr: bool = False,
    running_header: str | None,
    name: str,
) -> str:
    """Convert with Docling and normalize backend failures."""
    if not HAS_DOCLING:
        raise PdfConversionError("Docling is required to convert PDFs; install it with `uv sync --group pdf`")
    try:
        markdown = _docling_to_markdown(data, ocr=ocr, full_page_ocr=full_page_ocr, name=name)
    except PdfConversionError:
        raise
    except Exception as exc:  # noqa: BLE001 - backend raises library-specific errors
        raise PdfConversionError(f"Could not convert PDF '{name}' with Docling: {exc}") from exc
    markdown = _strip_running_header(markdown, running_header)
    if full_page_ocr:
        markdown = _normalize_known_full_page_ocr_labels(markdown)
    if not markdown:
        raise PdfConversionError(f"PDF '{name}' produced no readable Markdown with Docling")
    return markdown


def _convert_with_docling_bounded(
    data: bytes,
    *,
    ocr: bool,
    full_page_ocr: bool = False,
    running_header: str | None,
    name: str,
    timeout_seconds: float | None,
) -> str:
    """Run Docling in a cancellable child process.

    A thread timeout cannot stop native OCR work. The worker therefore receives
    the PDF over stdin and returns Markdown over stdout; ``subprocess.run``
    terminates and reaps it when the wall-time limit expires. No PDF is written
    to disk.
    """
    if timeout_seconds is None:
        return _convert_with_docling(
            data,
            ocr=ocr,
            full_page_ocr=full_page_ocr,
            running_header=running_header,
            name=name,
        )

    command = [sys.executable, "-m", "amfv_datasets.scraping._pdf_worker", "--name", name]
    if ocr:
        command.append("--ocr")
    if full_page_ocr:
        command.append("--full-page-ocr")
    if running_header is not None:
        command.extend(("--running-header", running_header))
    try:
        completed = subprocess.run(
            command,
            input=data,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfConversionError(
            f"Docling conversion for '{name}' exceeded the {timeout_seconds:g}-second wall-time limit"
        ) from exc

    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    if completed.returncode != 0:
        detail = stderr[-2_000:] or f"worker exited with status {completed.returncode}"
        raise PdfConversionError(f"Could not convert PDF '{name}' with Docling worker: {detail}")
    marker_at = completed.stdout.rfind(_WORKER_RESULT_MARKER)
    if marker_at < 0:
        detail = stderr[-2_000:] or "worker returned no result marker"
        raise PdfConversionError(f"Could not convert PDF '{name}' with Docling worker: {detail}")
    markdown_bytes = completed.stdout[marker_at + len(_WORKER_RESULT_MARKER) :]
    try:
        markdown = markdown_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PdfConversionError(f"Docling worker returned invalid UTF-8 for '{name}'") from exc
    if not markdown.strip():
        raise PdfConversionError(f"PDF '{name}' produced no readable Markdown with Docling")
    return markdown


def _conversion_provenance(
    *,
    data: bytes,
    markdown: str,
    requested_backend: PdfBackend,
    backend: PdfBackend,
    processing_time_ms: int,
    ocr: bool,
    full_page_ocr: bool = False,
    conversion_timeout_seconds: float | None = None,
    inspector_diagnostics: dict[str, Any] | None = None,
    fallback_reason: str | None = None,
    docling_quality: dict[str, Any] | None = None,
    docling_attempts: list[dict[str, Any]] | None = None,
    docling_retry_reason: str | None = None,
) -> dict[str, Any]:
    """Build a stable JSON-compatible conversion provenance envelope."""
    markdown_data = markdown.encode("utf-8")
    diagnostics = inspector_diagnostics or {}
    return {
        "backend": backend.value,
        "backend_version": _package_version("pdf-inspector" if backend is PdfBackend.PDF_INSPECTOR else "docling"),
        "requested_backend": requested_backend.value,
        "conversion_timestamp": _conversion_timestamp(),
        "processing_time_ms": processing_time_ms,
        "conversion_timeout_seconds": conversion_timeout_seconds,
        "ocr": ocr,
        "ocr_mode": "full_page" if full_page_ocr else ("default" if ocr else None),
        "ocr_postprocessing": FULL_PAGE_OCR_POSTPROCESSING_VERSION if full_page_ocr else None,
        "page_text_coverage_verified": False,
        "input_bytes": len(data),
        "input_sha256": hashlib.sha256(data).hexdigest(),
        "markdown_bytes": len(markdown_data),
        "markdown_sha256": hashlib.sha256(markdown_data).hexdigest(),
        "pdf_type": diagnostics.get("pdf_type"),
        "title": diagnostics.get("title"),
        "page_count": diagnostics.get("page_count"),
        "confidence": diagnostics.get("confidence"),
        "pages_needing_ocr": list(diagnostics.get("pages_needing_ocr", [])),
        "ocr_reasons_by_page": list(diagnostics.get("ocr_reasons_by_page", [])),
        "pages_with_tables": list(diagnostics.get("pages_with_tables", [])),
        "pages_with_columns": list(diagnostics.get("pages_with_columns", [])),
        "is_complex_layout": bool(diagnostics.get("is_complex_layout", False)),
        "has_encoding_issues": bool(diagnostics.get("has_encoding_issues", False)),
        "fallback_reason": fallback_reason,
        "pdf_inspector": inspector_diagnostics,
        "docling_post_extraction_quality": docling_quality,
        "docling_attempts": list(docling_attempts or []),
        "docling_retry_reason": docling_retry_reason,
    }


def convert_pdf(
    data: bytes,
    *,
    backend: PdfBackend = PdfBackend.AUTO,
    ocr: bool = False,
    running_header: str | None = None,
    name: str = "document.pdf",
    docling_timeout_seconds: float | None = DEFAULT_DOCLING_TIMEOUT_SECONDS,
) -> PdfConversionResult:
    """Convert PDF bytes into Markdown with conversion provenance.

    Args:
        data: Raw PDF bytes.
        backend: Conversion backend to use. Automatic mode prefers
            pdf-inspector and falls back to Docling with OCR when native text is
            incomplete (default: PdfBackend.AUTO).
        ocr: Whether explicit Docling conversion should run OCR. Automatic
            fallback always enables OCR (default: False).
        running_header: Repeated page header to drop from the output, usually the
            document title (default: None).
        name: File name reported to the backend, used for logging and format
            detection (default: "document.pdf").
        docling_timeout_seconds: Maximum wall time for the isolated Docling
            fallback. Pass ``None`` only when an external job deadline already
            enforces cancellation (default: 900 seconds).
    """
    try:
        requested_backend = PdfBackend(backend)
    except ValueError as exc:
        raise PdfConversionError(f"Unsupported PDF backend: {backend}") from exc
    if not data:
        raise PdfConversionError("Cannot convert an empty PDF payload")
    if docling_timeout_seconds is not None and docling_timeout_seconds <= 0:
        raise ValueError("docling_timeout_seconds must be positive or None")

    started = time.perf_counter()
    inspector_diagnostics: dict[str, Any] | None = None
    inspector_error: str | None = None
    if requested_backend in {PdfBackend.AUTO, PdfBackend.PDF_INSPECTOR}:
        if not HAS_PDF_INSPECTOR or _pdf_inspector is None:
            inspector_error = "pdf-inspector is not installed"
        else:
            try:
                inspector_result = _pdf_inspector.process_pdf_bytes(data)
                inspector_diagnostics = _pdf_inspector_diagnostics(inspector_result)
                inspector_markdown = _strip_running_header(inspector_result.markdown or "", running_header)
                inspector_diagnostics["post_extraction_quality"] = _native_text_quality_diagnostics(inspector_markdown)
                inspector_error = _incomplete_pdf_reason(inspector_markdown, inspector_diagnostics)
            except Exception as exc:  # noqa: BLE001 - native extension raises ValueError for malformed PDFs
                inspector_error = f"pdf-inspector failed: {exc}"

            if inspector_error is None:
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                return PdfConversionResult(
                    markdown=inspector_markdown,
                    provenance=_conversion_provenance(
                        data=data,
                        markdown=inspector_markdown,
                        requested_backend=requested_backend,
                        backend=PdfBackend.PDF_INSPECTOR,
                        processing_time_ms=elapsed_ms,
                        ocr=False,
                        full_page_ocr=False,
                        conversion_timeout_seconds=None,
                        inspector_diagnostics=inspector_diagnostics,
                    ),
                )

        if requested_backend is PdfBackend.PDF_INSPECTOR:
            raise PdfConversionError(f"Could not fully convert PDF '{name}' with pdf-inspector: {inspector_error}")
        if not HAS_DOCLING:
            raise PdfConversionError(
                f"Could not fully convert PDF '{name}' with pdf-inspector ({inspector_error}); "
                "Docling is unavailable for OCR fallback"
            )

    docling_ocr = True if requested_backend is PdfBackend.AUTO else ocr
    native_quality_corrupt = bool(
        requested_backend is PdfBackend.AUTO
        and inspector_diagnostics
        and inspector_diagnostics.get("post_extraction_quality", {}).get("likely_corrupt")
    )
    full_page_ocr = native_quality_corrupt
    docling_attempts: list[dict[str, Any]] = []
    docling_retry_reason: str | None = None

    attempt_started = time.perf_counter()
    markdown = _convert_with_docling_bounded(
        data,
        ocr=docling_ocr,
        full_page_ocr=full_page_ocr,
        running_header=running_header,
        name=name,
        timeout_seconds=docling_timeout_seconds,
    )
    attempt_processing_time_ms = round((time.perf_counter() - attempt_started) * 1000)
    docling_quality = _native_text_quality_diagnostics(markdown)

    if requested_backend is PdfBackend.AUTO and not full_page_ocr and docling_quality["likely_corrupt"]:
        docling_retry_reason = (
            "default Docling OCR post-extraction quality remained likely corrupt "
            f"({_quality_failure_summary(docling_quality)})"
        )
        docling_attempts.append(
            {
                "attempt": 1,
                "ocr": docling_ocr,
                "ocr_mode": "default" if docling_ocr else None,
                "conversion_timeout_seconds": docling_timeout_seconds,
                "processing_time_ms": attempt_processing_time_ms,
                "post_extraction_quality": docling_quality,
                "accepted": False,
                "rejection_reason": docling_retry_reason,
            }
        )
        full_page_ocr = True
        attempt_started = time.perf_counter()
        markdown = _convert_with_docling_bounded(
            data,
            ocr=docling_ocr,
            full_page_ocr=True,
            running_header=running_header,
            name=name,
            timeout_seconds=docling_timeout_seconds,
        )
        attempt_processing_time_ms = round((time.perf_counter() - attempt_started) * 1000)
        docling_quality = _native_text_quality_diagnostics(markdown)

    if _has_invalid_text_characters(docling_quality):
        raise PdfConversionError(
            f"PDF '{name}' produced invalid Unicode text with Docling ({_quality_failure_summary(docling_quality)})"
        )
    docling_attempts.append(
        {
            "attempt": len(docling_attempts) + 1,
            "ocr": docling_ocr,
            "ocr_mode": "full_page" if full_page_ocr else ("default" if docling_ocr else None),
            "conversion_timeout_seconds": docling_timeout_seconds,
            "processing_time_ms": attempt_processing_time_ms,
            "post_extraction_quality": docling_quality,
            "accepted": True,
            "rejection_reason": None,
        }
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return PdfConversionResult(
        markdown=markdown,
        provenance=_conversion_provenance(
            data=data,
            markdown=markdown,
            requested_backend=requested_backend,
            backend=PdfBackend.DOCLING,
            processing_time_ms=elapsed_ms,
            ocr=docling_ocr,
            full_page_ocr=full_page_ocr,
            conversion_timeout_seconds=docling_timeout_seconds,
            inspector_diagnostics=inspector_diagnostics,
            fallback_reason=inspector_error if requested_backend is PdfBackend.AUTO else None,
            docling_quality=docling_quality,
            docling_attempts=docling_attempts,
            docling_retry_reason=docling_retry_reason,
        ),
    )


def pdf_to_markdown(
    data: bytes,
    *,
    backend: PdfBackend = PdfBackend.AUTO,
    ocr: bool = False,
    running_header: str | None = None,
    name: str = "document.pdf",
    docling_timeout_seconds: float | None = DEFAULT_DOCLING_TIMEOUT_SECONDS,
) -> str:
    """Convert PDF bytes into Markdown, preserving the historical string API."""
    return convert_pdf(
        data,
        backend=backend,
        ocr=ocr,
        running_header=running_header,
        name=name,
        docling_timeout_seconds=docling_timeout_seconds,
    ).markdown


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

    A line is dropped only when it is a long prefix of the title or the title is
    a prefix of it, because publishers shorten the title in page headers. Lines
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
        candidate = _collapse_repeated_text(" ".join(line.split()).casefold().strip("# "))
        is_header = len(candidate) >= MIN_RUNNING_HEADER_CHARS and (
            needle.startswith(candidate) or candidate.startswith(needle)
        )
        if not is_header:
            kept.append(line)
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(kept)).strip()


__all__ = [
    "DEFAULT_DOCLING_TIMEOUT_SECONDS",
    "DOCLING_TIMEOUT_ENV",
    "HAS_DOCLING",
    "HAS_PDF_INSPECTOR",
    "PdfBackend",
    "PdfConversionError",
    "PdfConversionResult",
    "convert_pdf",
    "count_markdown_sections",
    "pdf_to_markdown",
]
