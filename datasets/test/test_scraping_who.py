"""Tests for WHO scraping helpers."""

import json
from pathlib import Path

import httpx
import pytest

from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.pdf import PdfConversionError
from amfv_datasets.scraping.who import (
    BASE_URL,
    WHO_LICENSE,
    WhoFetchError,
    WhoListingPage,
    WhoPublicationRef,
    build_publication_text,
    list_publications,
    publication_ref_from_url,
    scrape_publication,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_CVC_GUIDELINE_TITLE = (
    "Guidelines for the prevention of bloodstream infections and other infections "
    "associated with the use of intravascular catheters: part 2: central venous catheters"
)
_CERVICAL_GUIDELINE_TITLE = (
    "WHO guideline for screening and treatment of cervical pre-cancer lesions for cervical cancer prevention"
)
_PAGE_URL = "https://www.who.int/publications/i/item/9789240121805"
_DOWNLOAD_URL = "https://iris.who.int/server/api/core/bitstreams/f750f24d-c0c2-425c-85fd-ec310d2ce994/content"
_PDF_BYTES = b"%PDF-1.7 fake guideline payload"
_PDF_MARKDOWN = "## Recommendations\n\nUse aseptic technique.\n\n## Evidence\n\n### Certainty\n\nModerate."


def _publication_ref(*, download_url: str | None = _DOWNLOAD_URL) -> WhoPublicationRef:
    """Build a CVC guideline ref pointing at the fixture page."""
    return WhoPublicationRef(
        publication_id="9789240121805",
        title=_CVC_GUIDELINE_TITLE,
        page_url=_PAGE_URL,
        publication_date="28 May 2026",
        tag="Guideline",
        download_url=download_url,
    )


def _page_and_pdf_client(*, pdf_status: int = 200) -> httpx.Client:
    """Serve the fixture landing page and a stub PDF payload."""
    html = (_FIXTURES / "who_publication_overview.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == _PAGE_URL:
            return httpx.Response(200, text=html)
        assert str(request.url) == _DOWNLOAD_URL
        return httpx.Response(pdf_status, content=_PDF_BYTES)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _fake_converter(data: bytes, title: str, publication_id: str) -> str:
    """Stand in for Docling so tests stay offline and fast."""
    assert data == _PDF_BYTES
    assert title == _CVC_GUIDELINE_TITLE
    assert publication_id == "9789240121805"
    return _PDF_MARKDOWN


def test_list_publications_parses_api_listing() -> None:
    """WHO listing payloads are parsed from the publications OData API."""
    payload = json.loads((_FIXTURES / "who_listing_api.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/hubs/publications"
        assert request.url.params["$filter"] == "publishingoffices/any(s:s eq c09761c0-ab8e-4cfa-9744-99509c4d306b)"
        assert request.url.params["$skip"] == "0"
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)

    listing_page = list_publications(client)

    assert listing_page == WhoListingPage(
        total=356,
        refs=[
            WhoPublicationRef(
                publication_id="9789240121805",
                title=_CVC_GUIDELINE_TITLE,
                page_url="https://www.who.int/publications/i/item/9789240121805",
                publication_date="28 May 2026",
                tag="Guideline",
                download_url=_DOWNLOAD_URL,
            ),
            WhoPublicationRef(
                publication_id="9789240121744",
                title=_CERVICAL_GUIDELINE_TITLE,
                page_url="https://www.who.int/publications/i/item/9789240121744",
                publication_date="8 May 2026",
                tag="Guideline",
                download_url="https://iris.who.int/server/api/core/bitstreams/32214b73-0e95-4243-9e83-617948510dcd/content",
            ),
        ],
    )


def test_publication_ref_from_url_normalizes_publication_url() -> None:
    """WHO publication URLs are normalized to canonical refs."""
    assert publication_ref_from_url(_PAGE_URL) == WhoPublicationRef(
        publication_id="9789240121805",
        title="9789240121805",
        page_url=_PAGE_URL,
    )


def test_build_publication_text_combines_overview_and_pdf() -> None:
    """The guideline body from the PDF is appended to the Overview."""
    content, section_count, title, metadata = build_publication_text(
        _page_and_pdf_client(),
        _publication_ref(),
        pdf_converter=_fake_converter,
    )

    assert title.startswith("Guidelines for the prevention of bloodstream infections")
    assert "### Overview" in content
    assert "central venous catheters (CVCs)" in content
    assert "Use aseptic technique." in content
    assert "WHO Team" not in content
    assert section_count == 3
    assert metadata["content_scope"] == "full"
    assert metadata["pdf_backend"] == "docling"
    assert metadata["pdf_bytes"] == len(_PDF_BYTES)
    assert metadata["download_url"] == _DOWNLOAD_URL
    assert metadata["publication_date"] == "28 May 2026"
    assert metadata["tag"] == "Guideline"
    assert metadata["isbn"] == "978-92-4-012180-5"
    assert metadata["license"] == WHO_LICENSE
    assert metadata["listing_category"] == "who-guidelines"


def test_build_publication_text_reads_download_url_from_page() -> None:
    """A ref without a download URL falls back to the landing-page link."""
    content, _, _, metadata = build_publication_text(
        _page_and_pdf_client(),
        _publication_ref(download_url=None),
        pdf_converter=_fake_converter,
    )

    assert metadata["download_url"] == _DOWNLOAD_URL
    assert metadata["content_scope"] == "full"
    assert "Use aseptic technique." in content


def test_build_publication_text_skips_pdf_when_full_text_disabled() -> None:
    """Overview-only scraping leaves the PDF untouched."""
    content, section_count, _, metadata = build_publication_text(
        _page_and_pdf_client(),
        _publication_ref(),
        include_full_text=False,
    )

    assert "### Overview" in content
    assert "Use aseptic technique." not in content
    assert section_count == 1
    assert metadata["content_scope"] == "overview"
    assert metadata["pdf_backend"] is None
    assert metadata["pdf_bytes"] is None


@pytest.mark.parametrize(
    ("pdf_status", "converter"),
    [
        pytest.param(200, None, id="conversion-fails"),
        pytest.param(404, _fake_converter, id="download-fails"),
    ],
)
def test_build_publication_text_falls_back_to_overview(pdf_status: int, converter: object) -> None:
    """A broken PDF degrades to the Overview instead of failing the document."""

    def failing_converter(data: bytes, title: str, publication_id: str) -> str:
        raise PdfConversionError("backend exploded")

    content, section_count, _, metadata = build_publication_text(
        _page_and_pdf_client(pdf_status=pdf_status),
        _publication_ref(),
        pdf_converter=converter or failing_converter,
    )

    assert "### Overview" in content
    assert "Use aseptic technique." not in content
    assert section_count == 1
    assert metadata["content_scope"] == "overview"
    assert metadata["pdf_backend"] is None


def test_scrape_publication_builds_scraped_document() -> None:
    """A WHO publication ref is normalized into a ScrapedDocument."""
    document = scrape_publication(
        _page_and_pdf_client(),
        _publication_ref(),
        link_mode=LinkMode.STRIP,
        pdf_converter=_fake_converter,
    )

    assert document.source == "who"
    assert document.external_id == "who-9789240121805"
    assert document.url == _PAGE_URL
    assert document.section_count == 3
    assert document.content.strip()
    assert document.metadata["publication_id"] == "9789240121805"
    assert document.metadata["content_scope"] == "full"


def test_build_publication_text_raises_when_overview_missing() -> None:
    """Missing publication markup raises WhoFetchError."""
    html = "<html><body><p>No publication section here.</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ref = WhoPublicationRef(
        publication_id="9789240121805",
        title="Example guideline",
        page_url=_PAGE_URL,
    )

    with pytest.raises(WhoFetchError, match="No publication content section"):
        build_publication_text(client, ref)
