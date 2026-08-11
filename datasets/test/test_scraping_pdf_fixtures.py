"""Conversion tests against real WHO guideline PDF excerpts.

These run the actual Docling pipeline rather than a stub, so they are the check
that guideline content survives conversion. They need the `pdf` extra
(`uv sync --group pdf`) and are skipped when it is absent, which is why CI stays
green without installing a PDF stack.

Assertions here are deliberately semantic: they check that a recommendation, its
certainty rating and its table rows are present, not that the markdown is
byte-for-byte identical. A Docling upgrade may reflow whitespace without
changing meaning, and that must not fail the suite.

The byte-exact comparison against the committed `*.expected.md` files is opt-in
via `AMFV_CHECK_GOLDEN=1`; see `datasets/benchmarks/README.md`.

The fixture PDFs are page excerpts of WHO guidelines redistributed under
CC BY-NC-SA 3.0 IGO; see `amfv_datasets/scraping/LICENSE_NOTES.md`.
"""

import os
from functools import cache
from pathlib import Path

import httpx
import pytest

from amfv_datasets.scraping.pdf import HAS_DOCLING, count_markdown_sections, pdf_to_markdown
from amfv_datasets.scraping.who import WhoPublicationRef, scrape_publication

pytestmark = pytest.mark.skipif(not HAS_DOCLING, reason="requires the `pdf` extra (uv sync --group pdf)")

_FIXTURES = Path(__file__).parent / "fixtures"
_PDF_FIXTURES = _FIXTURES / "pdf"

_CVC_PDF = "who_9789240121805_excerpt.pdf"
_CVC_TITLE = (
    "Guidelines for the prevention of bloodstream infections and other infections "
    "associated with the use of intravascular catheters: part 2: central venous catheters"
)
_HIV_PDF = "who_9789240124233_excerpt.pdf"
_HIV_TITLE = "Consolidated HIV guidelines: service delivery"

_PAGE_URL = "https://www.who.int/publications/i/item/9789240121805"
_DOWNLOAD_URL = "https://iris.who.int/server/api/core/bitstreams/f750f24d-c0c2-425c-85fd-ec310d2ce994/content"

_CHECK_GOLDEN_ENV = "AMFV_CHECK_GOLDEN"
_UPDATE_GOLDEN_ENV = "AMFV_UPDATE_GOLDEN"
_FIXTURE_DOCUMENTS = [
    pytest.param(_CVC_PDF, _CVC_TITLE, id="bloodstream-infections"),
    pytest.param(_HIV_PDF, _HIV_TITLE, id="hiv-service-delivery"),
]


@cache
def _converted(pdf_name: str, title: str) -> str:
    """Convert a fixture PDF once and reuse it across assertions."""
    return pdf_to_markdown((_PDF_FIXTURES / pdf_name).read_bytes(), running_header=title, name=pdf_name)


def _table_rows(markdown: str) -> int:
    """Count markdown table rows."""
    return sum(1 for line in markdown.splitlines() if line.strip().startswith("|"))


def test_recommendation_and_grade_certainty_survive_conversion() -> None:
    """The recommendation text and its certainty rating both reach the markdown."""
    markdown = _converted(_CVC_PDF, _CVC_TITLE)

    assert "Recommendation 1." in markdown
    assert "chlorhexidine-containing body wash" in markdown
    assert "certainty of evidence" in markdown
    assert "## Remarks" in markdown
    assert "## Summary of the evidence" in markdown


def test_heading_structure_is_recovered() -> None:
    """Section structure is preserved so documents can be chunked later."""
    markdown = _converted(_CVC_PDF, _CVC_TITLE)

    assert count_markdown_sections(markdown) >= 15


def test_running_header_is_removed_from_real_pdf() -> None:
    """The repeated page header does not leak into the document body."""
    markdown = _converted(_CVC_PDF, _CVC_TITLE)

    header_lines = [
        line
        for line in markdown.splitlines()
        if line.strip().casefold().startswith("guidelines for the prevention of bloodstream")
    ]
    assert header_lines == []


def test_recommendation_table_structure_is_preserved() -> None:
    """Recommendation tables convert to markdown tables with their rows intact.

    Row loss here is the failure mode that makes a table unusable for fact
    verification, so the row count is asserted rather than mere table presence.
    """
    markdown = _converted(_HIV_PDF, _HIV_TITLE)

    assert "Table 1: HIV service delivery recommendations" in markdown
    assert "| Date" in markdown
    assert _table_rows(markdown) >= 30
    assert "Rapid ART initiation should be offered" in markdown


@pytest.mark.skipif(
    not (os.environ.get(_CHECK_GOLDEN_ENV) or os.environ.get(_UPDATE_GOLDEN_ENV)),
    reason=f"set {_CHECK_GOLDEN_ENV}=1 to diff against the committed .expected.md files",
)
@pytest.mark.parametrize(("pdf_name", "title"), _FIXTURE_DOCUMENTS)
def test_conversion_matches_golden_output(pdf_name: str, title: str) -> None:
    """Compare conversion byte for byte against the committed markdown.

    Opt-in because it is sensitive to the Docling version: an upgrade that
    reflows whitespace should prompt a human to review the new output, not break
    CI for everyone.
    """
    markdown = _converted(pdf_name, title)
    golden = _PDF_FIXTURES / f"{Path(pdf_name).stem}.expected.md"

    if os.environ.get(_UPDATE_GOLDEN_ENV):
        golden.write_text(markdown, encoding="utf-8")
        pytest.skip(f"rewrote {golden.name}")

    regenerate = f"{_UPDATE_GOLDEN_ENV}=1 uv run --group pdf pytest {Path(__file__).name}"
    assert markdown == golden.read_text(encoding="utf-8"), (
        f"Conversion of {pdf_name} no longer matches {golden.name}. "
        f"Review the difference, then regenerate with `{regenerate}`."
    )


def test_scrape_publication_emits_full_text_document() -> None:
    """End to end: landing page plus real PDF become one full-text document."""
    html = (_FIXTURES / "who_publication_overview.html").read_text(encoding="utf-8")
    pdf_bytes = (_PDF_FIXTURES / _CVC_PDF).read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == _PAGE_URL:
            return httpx.Response(200, text=html)
        return httpx.Response(200, content=pdf_bytes)

    ref = WhoPublicationRef(
        publication_id="9789240121805",
        title=_CVC_TITLE,
        page_url=_PAGE_URL,
        download_url=_DOWNLOAD_URL,
    )

    document = scrape_publication(httpx.Client(transport=httpx.MockTransport(handler)), ref)

    assert document.metadata["content_scope"] == "full"
    assert document.metadata["pdf_backend"] == "docling"
    assert document.metadata["pdf_bytes"] == len(pdf_bytes)
    assert document.section_count >= 15
    assert "### Overview" in document.content
    assert "central venous catheters (CVCs)" in document.content
    assert "chlorhexidine-containing body wash" in document.content
