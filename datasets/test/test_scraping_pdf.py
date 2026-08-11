"""Tests for PDF-to-markdown conversion helpers."""

import pytest

from amfv_datasets.scraping.pdf import (
    PdfBackend,
    PdfConversionError,
    count_markdown_sections,
    pdf_to_markdown,
)
from amfv_datasets.scraping.pdf import _strip_running_header as strip_running_header

_GUIDELINE_TITLE = "Guidelines for the prevention of bloodstream infections"


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
    assert PdfBackend.DOCLING.value == "docling"
