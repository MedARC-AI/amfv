"""Tests for PDF-to-markdown conversion helpers."""

from collections.abc import Callable
from types import SimpleNamespace

import pytest

import amfv_datasets.scraping.pdf as pdf_module
from amfv_datasets.scraping.pdf import (
    PdfBackend,
    PdfConversionError,
    count_markdown_sections,
    pdf_to_markdown,
)

_GUIDELINE_TITLE = "Guidelines for the prevention of bloodstream infections"


@pytest.fixture
def convert_pdf(monkeypatch: pytest.MonkeyPatch) -> Callable[[str, str | None], str]:
    """Serve controlled backend text through the public PDF converter."""
    monkeypatch.setattr(pdf_module, "HAS_DOCLING", True)
    monkeypatch.setattr(pdf_module, "ConversionStatus", SimpleNamespace(SUCCESS="success"), raising=False)
    monkeypatch.setattr(pdf_module, "DocumentStream", lambda **kwargs: kwargs, raising=False)

    def convert(markdown: str, header: str | None) -> str:
        result = SimpleNamespace(
            status="success", document=SimpleNamespace(texts=[], export_to_markdown=lambda: markdown)
        )
        monkeypatch.setattr(
            pdf_module, "_docling_converter", lambda **kwargs: SimpleNamespace(convert=lambda stream: result)
        )
        return pdf_to_markdown(b"%PDF-1.7", running_header=header)

    return convert


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


def test_pdf_to_markdown_removes_repeated_title(convert_pdf: Callable[[str, str | None], str]) -> None:
    """Repeated page headers matching the title are dropped."""
    markdown = f"{_GUIDELINE_TITLE}\n\n## Recommendations\n\nUse aseptic technique.\n\n{_GUIDELINE_TITLE}\n\nMore text."

    cleaned = convert_pdf(markdown, _GUIDELINE_TITLE)

    assert _GUIDELINE_TITLE not in cleaned
    assert "## Recommendations" in cleaned
    assert "Use aseptic technique." in cleaned
    assert "More text." in cleaned


def test_pdf_to_markdown_removes_shortened_title(convert_pdf: Callable[[str, str | None], str]) -> None:
    """Publishers shorten the title in page headers, so prefixes are dropped."""
    short = "Guidelines for the prevention of bloodstream infections"
    full = f"{short} associated with the use of intravascular catheters: part 2"
    markdown = f"{short}\n\n## Recommendations\n\nUse aseptic technique."

    cleaned = convert_pdf(markdown, full)

    assert short not in cleaned
    assert "Use aseptic technique." in cleaned


def test_pdf_to_markdown_removes_doubled_header_line(convert_pdf: Callable[[str, str | None], str]) -> None:
    """Left and right page headers merged onto one line are still dropped."""
    markdown = f"{_GUIDELINE_TITLE} {_GUIDELINE_TITLE}\n\nUse aseptic technique."

    cleaned = convert_pdf(markdown, _GUIDELINE_TITLE)

    assert "Guidelines for the prevention" not in cleaned
    assert "Use aseptic technique." in cleaned


def test_pdf_to_markdown_keeps_short_title_fragments(convert_pdf: Callable[[str, str | None], str]) -> None:
    """A short word that merely starts the title is content, not a header."""
    markdown = "Guidelines\n\nUse aseptic technique."

    cleaned = convert_pdf(markdown, _GUIDELINE_TITLE)

    assert "Guidelines" in cleaned


def test_pdf_to_markdown_keeps_repeated_guideline_content(convert_pdf: Callable[[str, str | None], str]) -> None:
    """GRADE qualifiers repeat legitimately and must survive header stripping."""
    qualifier = "(Strong recommendation, moderate-certainty evidence)"
    markdown = f"## One\n\n{qualifier}\n\n## Two\n\n{qualifier}\n\n## Three\n\n{qualifier}"

    cleaned = convert_pdf(markdown, _GUIDELINE_TITLE)

    assert cleaned.count(qualifier) == 3


def test_pdf_to_markdown_without_header_only_trims(convert_pdf: Callable[[str, str | None], str]) -> None:
    """Passing no header leaves the markdown untouched apart from trimming."""
    assert convert_pdf("\n\n## Kept\n\n", None) == "## Kept"


def test_pdf_to_markdown_rejects_empty_payload() -> None:
    """An empty payload is a conversion error rather than an empty document."""
    with pytest.raises(PdfConversionError, match="empty PDF payload"):
        pdf_to_markdown(b"")


def test_pdf_to_markdown_rejects_unknown_backend() -> None:
    """Only registered backends are accepted."""
    with pytest.raises(PdfConversionError, match="Unsupported PDF backend"):
        pdf_to_markdown(b"%PDF-1.7", backend="marker")  # type: ignore[arg-type]


def test_pdf_to_markdown_preserves_title_headings_and_substantive_prose(
    convert_pdf: Callable[[str, str | None], str],
) -> None:
    """Cleaning page headers must not delete headings or statements starting with the title."""
    title = "Guidelines for the prevention of bloodstream infections"
    prose = f"{title} recommend maintaining aseptic technique."
    markdown = f"{title}\n\n## {title}\n\n{prose}\n\n{title}"

    assert convert_pdf(markdown, title) == f"## {title}\n\n{prose}"


def test_pdf_to_markdown_rejects_partial_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    """A partial backend result cannot be mislabeled as full guideline text."""
    monkeypatch.setattr(pdf_module, "HAS_DOCLING", True)
    monkeypatch.setattr(pdf_module, "ConversionStatus", SimpleNamespace(SUCCESS="success"), raising=False)
    monkeypatch.setattr(pdf_module, "DocumentStream", lambda **kwargs: kwargs, raising=False)
    monkeypatch.setattr(
        pdf_module,
        "_docling_converter",
        lambda **kwargs: SimpleNamespace(convert=lambda stream: SimpleNamespace(status="partial_success")),
    )

    with pytest.raises(PdfConversionError, match="incomplete.*partial_success"):
        pdf_to_markdown(b"%PDF-1.7")


def test_pdf_backend_values() -> None:
    """The backend enum serializes to a stable metadata string."""
    assert PdfBackend.DOCLING.value == "docling"
