"""Tests for PDF-to-markdown conversion helpers."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from amfv_datasets.scraping import pdf as pdf_module
from amfv_datasets.scraping.pdf import (
    PdfBackend,
    PdfConversionError,
    convert_pdf,
    count_markdown_sections,
    pdf_to_markdown,
)
from amfv_datasets.scraping.pdf import _strip_running_header as strip_running_header

_GUIDELINE_TITLE = "Guidelines for the prevention of bloodstream infections"
_PDF_BYTES = b"%PDF-1.7\nmock guideline"


def _inspector_result(
    *,
    markdown: str | None = "# Recommendations\n\nUse aseptic technique.",
    pages_needing_ocr: list[int] | None = None,
    has_encoding_issues: bool = False,
) -> SimpleNamespace:
    """Build a result shaped like pdf-inspector's Python PdfResult."""
    return SimpleNamespace(
        markdown=markdown,
        pdf_type="mixed" if pages_needing_ocr else "text_based",
        title="Mock guideline",
        page_count=3,
        confidence=0.98,
        pages_needing_ocr=pages_needing_ocr or [],
        ocr_reasons_by_page=[SimpleNamespace(page=2, reasons=["image_only_page"])] if pages_needing_ocr else [],
        pages_with_tables=[1],
        pages_with_columns=[3],
        is_complex_layout=True,
        has_encoding_issues=has_encoding_issues,
        processing_time_ms=17,
    )


def _install_fake_inspector(monkeypatch: pytest.MonkeyPatch, result: SimpleNamespace) -> SimpleNamespace:
    """Install a one-call in-memory pdf-inspector test double."""
    calls: list[bytes] = []

    def process_pdf_bytes(data: bytes) -> SimpleNamespace:
        calls.append(data)
        return result

    fake = SimpleNamespace(process_pdf_bytes=process_pdf_bytes, calls=calls)
    monkeypatch.setattr(pdf_module, "_pdf_inspector", fake)
    monkeypatch.setattr(pdf_module, "HAS_PDF_INSPECTOR", True)
    monkeypatch.setattr(
        pdf_module,
        "_package_version",
        lambda distribution: {"pdf-inspector": "1.15.0", "docling": "2.119.0"}[distribution],
    )
    return fake


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        pytest.param("## A\ntext\n## B\n### sub\n## C", 3, id="counts-shallowest-level"),
        pytest.param("# Title\n## A\n## B", 1, id="ignores-deeper-levels"),
        pytest.param("no headings at all", 1, id="unstructured-counts-as-one"),
        pytest.param("", 1, id="empty-counts-as-one"),
    ],
)
def test_count_markdown_sections(markdown: str, expected: int) -> None:
    """Sections are counted from the shallowest heading level present."""
    assert count_markdown_sections(markdown) == expected


def test_strip_running_header_removes_repeated_title() -> None:
    """Repeated page headers matching the title are dropped."""
    markdown = f"{_GUIDELINE_TITLE}\n\n## Recommendations\n\nUse aseptic technique.\n\n{_GUIDELINE_TITLE}\n\nMore text."

    cleaned = strip_running_header(markdown, _GUIDELINE_TITLE)

    assert _GUIDELINE_TITLE not in cleaned
    assert "## Recommendations" in cleaned
    assert "Use aseptic technique." in cleaned
    assert "More text." in cleaned


def test_strip_running_header_removes_shortened_title() -> None:
    """Publishers shorten the title in page headers, so prefixes are dropped."""
    short = "Guidelines for the prevention of bloodstream infections"
    full = f"{short} associated with the use of intravascular catheters: part 2"
    markdown = f"{short}\n\n## Recommendations\n\nUse aseptic technique."

    cleaned = strip_running_header(markdown, full)

    assert short not in cleaned
    assert "Use aseptic technique." in cleaned


def test_strip_running_header_removes_doubled_header_line() -> None:
    """Left and right page headers merged onto one line are still dropped."""
    markdown = f"{_GUIDELINE_TITLE} {_GUIDELINE_TITLE}\n\nUse aseptic technique."

    cleaned = strip_running_header(markdown, _GUIDELINE_TITLE)

    assert "Guidelines for the prevention" not in cleaned
    assert "Use aseptic technique." in cleaned


def test_strip_running_header_keeps_short_title_fragments() -> None:
    """A short word that merely starts the title is content, not a header."""
    markdown = "Guidelines\n\nUse aseptic technique."

    cleaned = strip_running_header(markdown, _GUIDELINE_TITLE)

    assert "Guidelines" in cleaned


def test_strip_running_header_keeps_repeated_guideline_content() -> None:
    """GRADE qualifiers repeat legitimately and must survive header stripping."""
    qualifier = "(Strong recommendation, moderate-certainty evidence)"
    markdown = f"## One\n\n{qualifier}\n\n## Two\n\n{qualifier}\n\n## Three\n\n{qualifier}"

    cleaned = strip_running_header(markdown, _GUIDELINE_TITLE)

    assert cleaned.count(qualifier) == 3


def test_strip_running_header_without_header_only_trims() -> None:
    """Passing no header leaves the markdown untouched apart from trimming."""
    assert strip_running_header("\n\n## Kept\n\n", None) == "## Kept"


def test_pdf_to_markdown_rejects_empty_payload() -> None:
    """An empty payload is a conversion error rather than an empty document."""
    with pytest.raises(PdfConversionError, match="empty PDF payload"):
        pdf_to_markdown(b"")


def test_pdf_to_markdown_rejects_unknown_backend() -> None:
    """Only registered backends are accepted."""
    with pytest.raises(PdfConversionError, match="Unsupported PDF backend"):
        pdf_to_markdown(b"%PDF-1.7", backend="marker")  # type: ignore[arg-type]


def test_pdf_backend_values() -> None:
    """The backend enum serializes to a stable metadata string."""
    assert PdfBackend.AUTO.value == "auto"
    assert PdfBackend.PDF_INSPECTOR.value == "pdf-inspector"
    assert PdfBackend.DOCLING.value == "docling"


def test_docling_timeout_can_be_configured_for_remote_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remote workers can raise the hard OCR deadline without changing source code."""
    monkeypatch.setenv(pdf_module.DOCLING_TIMEOUT_ENV, "3600")

    assert pdf_module._configured_docling_timeout_seconds() == 3600


def test_docling_timeout_environment_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken deadline setting fails explicitly instead of disabling cancellation."""
    monkeypatch.setenv(pdf_module.DOCLING_TIMEOUT_ENV, "never")

    with pytest.raises(RuntimeError, match="must be a positive number"):
        pdf_module._configured_docling_timeout_seconds()


def test_convert_pdf_accepts_clean_pdf_inspector_markdown_with_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clean native text is accepted and accompanied by serializable diagnostics."""
    fake = _install_fake_inspector(monkeypatch, _inspector_result())

    result = convert_pdf(_PDF_BYTES, backend=PdfBackend.PDF_INSPECTOR)

    assert result.markdown == "# Recommendations\n\nUse aseptic technique."
    assert fake.calls == [_PDF_BYTES]
    assert result.provenance == {
        "backend": "pdf-inspector",
        "backend_version": "1.15.0",
        "requested_backend": "pdf-inspector",
        "conversion_timestamp": result.provenance["conversion_timestamp"],
        "processing_time_ms": result.provenance["processing_time_ms"],
        "conversion_timeout_seconds": None,
        "ocr": False,
        "ocr_mode": None,
        "ocr_postprocessing": None,
        "page_text_coverage_verified": False,
        "input_bytes": len(_PDF_BYTES),
        "input_sha256": hashlib.sha256(_PDF_BYTES).hexdigest(),
        "markdown_bytes": len(result.markdown.encode()),
        "markdown_sha256": hashlib.sha256(result.markdown.encode()).hexdigest(),
        "pdf_type": "text_based",
        "title": "Mock guideline",
        "page_count": 3,
        "confidence": 0.98,
        "pages_needing_ocr": [],
        "ocr_reasons_by_page": [],
        "pages_with_tables": [1],
        "pages_with_columns": [3],
        "is_complex_layout": True,
        "has_encoding_issues": False,
        "fallback_reason": None,
        "pdf_inspector": {
            "version": "1.15.0",
            "pdf_type": "text_based",
            "title": "Mock guideline",
            "page_count": 3,
            "confidence": 0.98,
            "pages_needing_ocr": [],
            "ocr_reasons_by_page": [],
            "pages_with_tables": [1],
            "pages_with_columns": [3],
            "is_complex_layout": True,
            "has_encoding_issues": False,
            "processing_time_ms": 17,
            "post_extraction_quality": {
                "heuristic_version": "amfv-native-text-v2",
                "likely_corrupt": False,
                "triggered_checks": [],
                "long_unbroken_alpha_run_count": 0,
                "corrupted_year_token_count": 0,
                "replacement_character_count": 0,
                "nul_character_count": 0,
                "disallowed_c0_control_count": 0,
                "delete_control_count": 0,
                "c1_control_count": 0,
                "invalid_control_character_count": 0,
            },
        },
        "docling_post_extraction_quality": None,
        "docling_attempts": [],
        "docling_retry_reason": None,
    }
    assert result.provenance["conversion_timestamp"].endswith("Z")
    assert result.provenance["processing_time_ms"] >= 0
    json.dumps(result.provenance)


@pytest.mark.parametrize(
    ("result", "message"),
    [
        pytest.param(_inspector_result(markdown=None), "no readable Markdown", id="empty-markdown"),
        pytest.param(_inspector_result(pages_needing_ocr=[2]), "pages require OCR: 2", id="ocr-required"),
        pytest.param(
            _inspector_result(has_encoding_issues=True),
            "unreliable font encoding",
            id="encoding-issues",
        ),
    ],
)
def test_explicit_pdf_inspector_rejects_incomplete_output(
    monkeypatch: pytest.MonkeyPatch,
    result: SimpleNamespace,
    message: str,
) -> None:
    """Explicit native conversion never labels partial or unreliable text complete."""
    _install_fake_inspector(monkeypatch, result)

    with pytest.raises(PdfConversionError, match=message):
        convert_pdf(_PDF_BYTES, backend=PdfBackend.PDF_INSPECTOR, name="guideline.pdf")


def test_auto_falls_back_to_docling_ocr_and_retains_inspector_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Automatic mode records why it replaced incomplete native extraction."""
    _install_fake_inspector(monkeypatch, _inspector_result(pages_needing_ocr=[2]))
    monkeypatch.setattr(pdf_module, "HAS_DOCLING", True)
    calls: list[tuple[bytes, bool, str | None, str]] = []

    def convert_with_docling(
        data: bytes,
        *,
        ocr: bool,
        full_page_ocr: bool,
        running_header: str | None,
        name: str,
        timeout_seconds: float | None,
    ) -> str:
        assert timeout_seconds == pdf_module.DEFAULT_DOCLING_TIMEOUT_SECONDS
        assert full_page_ocr is False
        calls.append((data, ocr, running_header, name))
        return "# Complete OCR text"

    monkeypatch.setattr(pdf_module, "_convert_with_docling_bounded", convert_with_docling)

    result = convert_pdf(_PDF_BYTES, running_header=_GUIDELINE_TITLE, name="guideline.pdf")

    assert result.markdown == "# Complete OCR text"
    assert calls == [(_PDF_BYTES, True, _GUIDELINE_TITLE, "guideline.pdf")]
    assert result.provenance["backend"] == "docling"
    assert result.provenance["backend_version"] == "2.119.0"
    assert result.provenance["requested_backend"] == "auto"
    assert result.provenance["ocr"] is True
    assert result.provenance["ocr_mode"] == "default"
    assert result.provenance["conversion_timeout_seconds"] == pdf_module.DEFAULT_DOCLING_TIMEOUT_SECONDS
    assert result.provenance["fallback_reason"] == "pages require OCR: 2"
    assert result.provenance["pages_needing_ocr"] == [2]
    assert result.provenance["ocr_reasons_by_page"] == [{"page": 2, "reasons": ["image_only_page"]}]
    assert result.provenance["pdf_inspector"]["processing_time_ms"] == 17
    assert result.provenance["docling_post_extraction_quality"]["likely_corrupt"] is False
    assert result.provenance["docling_retry_reason"] is None
    assert len(result.provenance["docling_attempts"]) == 1
    attempt = result.provenance["docling_attempts"][0]
    assert attempt["ocr_mode"] == "default"
    assert attempt["accepted"] is True
    assert attempt["rejection_reason"] is None
    assert attempt["post_extraction_quality"] == result.provenance["docling_post_extraction_quality"]
    json.dumps(result.provenance)


def test_auto_uses_full_page_ocr_when_native_text_has_dense_corruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A high-confidence native result with lost spaces and substitutions is not accepted."""
    corrupted = (
        "TheCCSMHisproudtohavebeenabletofacilitatethedevelopmentof guidelines. "
        "Theseclinicalguidelinesweredevelopedforinterdisciplinaryteams. "
        "Therecommendationsarebasedonthebestavailableevidence. "
        "Published in $%%&, revised in $%01, and reviewed in $%0>."
    )
    _install_fake_inspector(monkeypatch, _inspector_result(markdown=corrupted))
    monkeypatch.setattr(pdf_module, "HAS_DOCLING", True)
    calls: list[dict[str, object]] = []

    def convert_with_docling(data: bytes, **kwargs: object) -> str:
        calls.append({"data": data, **kwargs})
        return "# Recovered OCR text"

    monkeypatch.setattr(pdf_module, "_convert_with_docling_bounded", convert_with_docling)

    result = convert_pdf(_PDF_BYTES, name="substituted-font.pdf")

    assert result.markdown == "# Recovered OCR text"
    assert calls == [
        {
            "data": _PDF_BYTES,
            "ocr": True,
            "full_page_ocr": True,
            "running_header": None,
            "name": "substituted-font.pdf",
            "timeout_seconds": pdf_module.DEFAULT_DOCLING_TIMEOUT_SECONDS,
        }
    ]
    assert result.provenance["backend"] == "docling"
    assert result.provenance["ocr"] is True
    assert result.provenance["ocr_mode"] == "full_page"
    assert result.provenance["ocr_postprocessing"] == "normalize-known-heading-labels-v1"
    assert result.provenance["docling_retry_reason"] is None
    assert [attempt["ocr_mode"] for attempt in result.provenance["docling_attempts"]] == ["full_page"]
    assert result.provenance["docling_attempts"][0]["accepted"] is True
    assert "likely corrupted native text" in result.provenance["fallback_reason"]
    quality = result.provenance["pdf_inspector"]["post_extraction_quality"]
    assert quality == {
        "heuristic_version": "amfv-native-text-v2",
        "likely_corrupt": True,
        "triggered_checks": ["repeated_unbroken_alpha_runs", "repeated_corrupted_year_tokens"],
        "long_unbroken_alpha_run_count": 3,
        "corrupted_year_token_count": 3,
        "replacement_character_count": 0,
        "nul_character_count": 0,
        "disallowed_c0_control_count": 0,
        "delete_control_count": 0,
        "c1_control_count": 0,
        "invalid_control_character_count": 0,
    }


def test_native_text_corruption_heuristic_allows_isolated_long_identifiers_and_financial_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard remains conservative for identifiers and ordinary dollar percentages."""
    clean = (
        "# Appendix\n\n"
        "HowToTreatConstipationCausedByYourMedications is a document identifier. "
        "kilogrambasisinchildrenanddosingrangesmightdiffer is a retained source label. "
        "Prior values were $%%&, $%01, and $100%; the current improvement is 15%."
    )
    _install_fake_inspector(monkeypatch, _inspector_result(markdown=clean))

    result = convert_pdf(_PDF_BYTES, backend=PdfBackend.PDF_INSPECTOR)

    assert result.markdown == clean
    assert result.provenance["backend"] == "pdf-inspector"
    assert result.provenance["pdf_inspector"]["post_extraction_quality"] == {
        "heuristic_version": "amfv-native-text-v2",
        "likely_corrupt": False,
        "triggered_checks": [],
        "long_unbroken_alpha_run_count": 2,
        "corrupted_year_token_count": 2,
        "replacement_character_count": 0,
        "nul_character_count": 0,
        "disallowed_c0_control_count": 0,
        "delete_control_count": 0,
        "c1_control_count": 0,
        "invalid_control_character_count": 0,
    }


def test_auto_accepts_clean_default_docling_for_inspector_encoding_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    """An encoding flag alone first receives lower-cost bounded default OCR."""
    _install_fake_inspector(monkeypatch, _inspector_result(has_encoding_issues=True))
    monkeypatch.setattr(pdf_module, "HAS_DOCLING", True)
    calls: list[dict[str, object]] = []

    def convert_with_docling(data: bytes, **kwargs: object) -> str:
        calls.append({"data": data, **kwargs})
        return "# Clean default OCR text"

    monkeypatch.setattr(pdf_module, "_convert_with_docling_bounded", convert_with_docling)

    result = convert_pdf(_PDF_BYTES)

    assert [call["full_page_ocr"] for call in calls] == [False]
    assert result.markdown == "# Clean default OCR text"
    assert result.provenance["ocr_mode"] == "default"
    assert result.provenance["ocr_postprocessing"] is None
    assert result.provenance["fallback_reason"] == "pdf-inspector detected unreliable font encoding"
    assert result.provenance["docling_post_extraction_quality"]["likely_corrupt"] is False
    assert result.provenance["docling_retry_reason"] is None
    assert [attempt["accepted"] for attempt in result.provenance["docling_attempts"]] == [True]


def test_encoding_issue_retries_full_page_when_default_docling_remains_corrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a flagged default result pays the cost of a second full-page OCR attempt."""
    _install_fake_inspector(monkeypatch, _inspector_result(has_encoding_issues=True))
    monkeypatch.setattr(pdf_module, "HAS_DOCLING", True)
    corrupted = (
        "Firstverylongunbrokencorruptedalphabeticsequencefromthedefaultpass "
        "Secondverylongunbrokencorruptedalphabeticsequencefromthedefaultpass "
        "Thirdverylongunbrokencorruptedalphabeticsequencefromthedefaultpass"
    )
    calls: list[dict[str, object]] = []

    def convert_with_docling(data: bytes, **kwargs: object) -> str:
        calls.append({"data": data, **kwargs})
        return "# Clean full-page OCR text" if kwargs["full_page_ocr"] else corrupted

    monkeypatch.setattr(pdf_module, "_convert_with_docling_bounded", convert_with_docling)

    result = convert_pdf(_PDF_BYTES)

    assert [call["full_page_ocr"] for call in calls] == [False, True]
    assert result.markdown == "# Clean full-page OCR text"
    assert result.provenance["ocr_mode"] == "full_page"
    assert result.provenance["ocr_postprocessing"] == "normalize-known-heading-labels-v1"
    assert "default Docling OCR" in result.provenance["docling_retry_reason"]
    assert [attempt["accepted"] for attempt in result.provenance["docling_attempts"]] == [False, True]
    assert [attempt["ocr_mode"] for attempt in result.provenance["docling_attempts"]] == [
        "default",
        "full_page",
    ]
    assert result.provenance["docling_attempts"][0]["post_extraction_quality"]["likely_corrupt"] is True
    assert result.provenance["docling_post_extraction_quality"]["likely_corrupt"] is False


def test_full_page_ocr_heading_normalization_is_exact_and_preserves_other_text() -> None:
    """Only pinned uppercase labels at line start receive corpus-specific O/0 fixes."""
    markdown = (
        "AIMS 0F THE GUIDELINE: Text\n"
        "## AIMS 0F THE GUIDELINE UPDATE: Text\n"
        "ACKN0WLEDGEMENT: Text\n"
        "The aims 0F the guideline remain prose.\n"
        "AIMS 0F ANOTHER STUDY: keep\n"
        "ACKN0WLEDGEMENT protein remains body text.\n"
    )

    assert pdf_module._normalize_known_full_page_ocr_labels(markdown) == (
        "AIMS OF THE GUIDELINE: Text\n"
        "## AIMS OF THE GUIDELINE UPDATE: Text\n"
        "ACKNOWLEDGEMENT: Text\n"
        "The aims 0F the guideline remain prose.\n"
        "AIMS 0F ANOTHER STUDY: keep\n"
        "ACKN0WLEDGEMENT protein remains body text.\n"
    )


@pytest.mark.parametrize(
    ("invalid_character", "expected_field"),
    [
        pytest.param("\x00", "disallowed_c0_control_count", id="nul"),
        pytest.param("\x03", "disallowed_c0_control_count", id="c0"),
        pytest.param("\x7f", "delete_control_count", id="del"),
        pytest.param("\x85", "c1_control_count", id="c1"),
        pytest.param("\ufffd", "replacement_character_count", id="replacement"),
    ],
)
def test_native_invalid_text_character_uses_immediate_full_page_ocr(
    monkeypatch: pytest.MonkeyPatch,
    invalid_character: str,
    expected_field: str,
) -> None:
    """Unsafe native controls and replacement characters never enter the default OCR stage."""
    _install_fake_inspector(monkeypatch, _inspector_result(markdown=f"Native{invalid_character}text"))
    monkeypatch.setattr(pdf_module, "HAS_DOCLING", True)
    calls: list[dict[str, object]] = []

    def convert_with_docling(data: bytes, **kwargs: object) -> str:
        calls.append({"data": data, **kwargs})
        return "# Clean full-page OCR text"

    monkeypatch.setattr(pdf_module, "_convert_with_docling_bounded", convert_with_docling)

    result = convert_pdf(_PDF_BYTES)

    assert [call["full_page_ocr"] for call in calls] == [True]
    native_quality = result.provenance["pdf_inspector"]["post_extraction_quality"]
    assert native_quality["likely_corrupt"] is True
    assert native_quality[expected_field] == 1
    assert "invalid_unicode_characters" in native_quality["triggered_checks"]


def test_non_encoding_auto_fallback_retries_full_page_when_default_docling_contains_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry every automatic default-OCR path when controls remain."""
    _install_fake_inspector(monkeypatch, _inspector_result(pages_needing_ocr=[2]))
    monkeypatch.setattr(pdf_module, "HAS_DOCLING", True)
    calls: list[dict[str, object]] = []

    def convert_with_docling(data: bytes, **kwargs: object) -> str:
        calls.append({"data": data, **kwargs})
        return "# Clean full-page OCR text" if kwargs["full_page_ocr"] else "Default\x07OCR"

    monkeypatch.setattr(pdf_module, "_convert_with_docling_bounded", convert_with_docling)

    result = convert_pdf(_PDF_BYTES)

    assert [call["full_page_ocr"] for call in calls] == [False, True]
    assert result.provenance["has_encoding_issues"] is False
    assert result.provenance["fallback_reason"] == "pages require OCR: 2"
    rejected_quality = result.provenance["docling_attempts"][0]["post_extraction_quality"]
    assert rejected_quality["disallowed_c0_control_count"] == 1
    assert rejected_quality["invalid_control_character_count"] == 1
    assert result.provenance["docling_post_extraction_quality"]["invalid_control_character_count"] == 0


def test_quality_diagnostics_allow_markdown_whitespace_controls() -> None:
    """TAB, LF, and CR remain valid Markdown whitespace."""
    quality = pdf_module._native_text_quality_diagnostics("column\tvalue\nnext\rline")

    assert quality["likely_corrupt"] is False
    assert quality["invalid_control_character_count"] == 0


@pytest.mark.parametrize(
    "invalid_character",
    ["\ufffd", "\x00", "\x03", "\x7f", "\x85"],
    ids=["replacement", "nul", "c0", "del", "c1"],
)
def test_docling_invalid_unicode_output_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    invalid_character: str,
) -> None:
    """Explicit Docling output with unsafe text characters is rejected."""
    monkeypatch.setattr(pdf_module, "HAS_DOCLING", True)
    monkeypatch.setattr(
        pdf_module,
        "_convert_with_docling_bounded",
        lambda *args, **kwargs: f"Recovered{invalid_character}text",
    )

    with pytest.raises(PdfConversionError, match="produced invalid Unicode text with Docling"):
        convert_pdf(_PDF_BYTES, backend=PdfBackend.DOCLING)


def test_auto_falls_back_when_pdf_inspector_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backend exceptions become explicit fallback provenance rather than disappearing."""
    fake = SimpleNamespace(process_pdf_bytes=lambda _: (_ for _ in ()).throw(ValueError("malformed xref")))
    monkeypatch.setattr(pdf_module, "_pdf_inspector", fake)
    monkeypatch.setattr(pdf_module, "HAS_PDF_INSPECTOR", True)
    monkeypatch.setattr(pdf_module, "HAS_DOCLING", True)
    monkeypatch.setattr(pdf_module, "_convert_with_docling_bounded", lambda *args, **kwargs: "Recovered")
    monkeypatch.setattr(pdf_module, "_package_version", lambda _: "test-version")

    result = convert_pdf(_PDF_BYTES)

    assert result.markdown == "Recovered"
    assert result.provenance["fallback_reason"] == "pdf-inspector failed: malformed xref"
    assert result.provenance["pdf_inspector"] is None


def test_auto_rejects_incomplete_native_text_without_docling(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing fallback never turns partial native Markdown into a success."""
    _install_fake_inspector(monkeypatch, _inspector_result(pages_needing_ocr=[2]))
    monkeypatch.setattr(pdf_module, "HAS_DOCLING", False)

    with pytest.raises(PdfConversionError, match="Docling is unavailable for OCR fallback"):
        convert_pdf(_PDF_BYTES)


def test_explicit_pdf_inspector_reports_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    """Selecting an unavailable optional backend produces an actionable error."""
    monkeypatch.setattr(pdf_module, "HAS_PDF_INSPECTOR", False)
    monkeypatch.setattr(pdf_module, "_pdf_inspector", None)

    with pytest.raises(PdfConversionError, match="pdf-inspector is not installed"):
        convert_pdf(_PDF_BYTES, backend=PdfBackend.PDF_INSPECTOR)


def test_explicit_docling_remains_available_and_honors_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers can still select the established Docling path explicitly."""
    monkeypatch.setattr(pdf_module, "HAS_DOCLING", True)
    monkeypatch.setattr(pdf_module, "_convert_with_docling_bounded", lambda *args, **kwargs: "Docling text")
    monkeypatch.setattr(pdf_module, "_package_version", lambda _: "2.119.0")

    result = convert_pdf(_PDF_BYTES, backend=PdfBackend.DOCLING, ocr=True)

    assert result.markdown == "Docling text"
    assert result.provenance["backend"] == "docling"
    assert result.provenance["ocr"] is True
    assert result.provenance["ocr_mode"] == "default"
    assert result.provenance["pdf_inspector"] is None


def test_docling_empty_output_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An installed backend returning only whitespace does not create an empty document."""
    monkeypatch.setattr(pdf_module, "HAS_DOCLING", True)
    monkeypatch.setattr(pdf_module, "_docling_to_markdown", lambda *args, **kwargs: "  \n")
    monkeypatch.setattr(
        pdf_module,
        "_convert_with_docling_bounded",
        lambda data, *, ocr, full_page_ocr, running_header, name, timeout_seconds: pdf_module._convert_with_docling(
            data,
            ocr=ocr,
            full_page_ocr=full_page_ocr,
            running_header=running_header,
            name=name,
        ),
    )

    with pytest.raises(PdfConversionError, match="no readable Markdown with Docling"):
        convert_pdf(_PDF_BYTES, backend=PdfBackend.DOCLING)


def test_docling_backend_error_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Library-specific exceptions are normalized into PdfConversionError."""
    monkeypatch.setattr(pdf_module, "HAS_DOCLING", True)
    monkeypatch.setattr(
        pdf_module,
        "_docling_to_markdown",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("model failed")),
    )
    monkeypatch.setattr(
        pdf_module,
        "_convert_with_docling_bounded",
        lambda data, *, ocr, full_page_ocr, running_header, name, timeout_seconds: pdf_module._convert_with_docling(
            data,
            ocr=ocr,
            full_page_ocr=full_page_ocr,
            running_header=running_header,
            name=name,
        ),
    )

    with pytest.raises(PdfConversionError, match="Could not convert PDF 'broken.pdf' with Docling: model failed"):
        convert_pdf(_PDF_BYTES, backend=PdfBackend.DOCLING, name="broken.pdf")


def test_bounded_docling_worker_enforces_wall_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stalled OCR worker is terminated through subprocess timeout handling."""

    def time_out(*args: object, **kwargs: object) -> None:
        raise pdf_module.subprocess.TimeoutExpired(cmd="worker", timeout=3)

    monkeypatch.setattr(pdf_module.subprocess, "run", time_out)

    with pytest.raises(PdfConversionError, match="exceeded the 3-second wall-time limit"):
        pdf_module._convert_with_docling_bounded(
            _PDF_BYTES,
            ocr=True,
            running_header=None,
            name="scan.pdf",
            timeout_seconds=3,
        )


def test_bounded_docling_worker_reads_framed_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker diagnostics cannot be mistaken for the Markdown payload."""
    completed = SimpleNamespace(
        returncode=0,
        stdout=b"untrusted worker chatter" + pdf_module._WORKER_RESULT_MARKER + b"# Recovered",
        stderr=b"diagnostic",
    )
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return completed

    monkeypatch.setattr(pdf_module.subprocess, "run", run)

    markdown = pdf_module._convert_with_docling_bounded(
        _PDF_BYTES,
        ocr=True,
        full_page_ocr=True,
        running_header=None,
        name="scan.pdf",
        timeout_seconds=3,
    )

    assert markdown == "# Recovered"
    assert "--ocr" in commands[0]
    assert "--full-page-ocr" in commands[0]


def test_pdf_to_markdown_keeps_string_return_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing scraper call sites continue receiving a Markdown string."""
    _install_fake_inspector(monkeypatch, _inspector_result(markdown="  Native text  "))

    assert pdf_to_markdown(_PDF_BYTES) == "Native text"
