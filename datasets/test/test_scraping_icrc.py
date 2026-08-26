"""Offline tests for permission-gated ICRC publication ingestion."""

from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest

import amfv_datasets.scraping.icrc as icrc_module
from amfv_datasets.scraping.base import artifact_capture_context
from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.icrc import (
    BASE_URL,
    IcrcFetchError,
    IcrcPermissionError,
    IcrcPublicationRef,
    ShopPdfResolution,
    icrc_ref_from_url,
    refs_from_manifest,
    scrape_icrc,
    scrape_publication,
)
from amfv_datasets.scraping.pdf import PdfConversionResult

_PUBLICATION_URL = f"{BASE_URL}/en/publication/4311-guidelines-mental-health-and-psychosocial-support"
_PDF_URL = f"{BASE_URL}/sites/default/files/publications/icrc-002-4311.pdf"
_PDF_BYTES = b"%PDF-1.7 fake ICRC publication"
_PDF_MARKDOWN = "# Clinical framework\n\n## Assessment\n\nAssess needs safely."
_PERMISSION_ID = "icrc-permission-2026-17"
_SHOP_URL = "https://shop.icrc.org/guidelines-mental-health-print-en.html"
_SHOP_DOWNLOAD_URL = "https://shop.icrc.org/download/ebook?sku=4311/002-ebook"
_LANDING_HTML = f"""
<html lang="en">
  <head>
    <title>Fallback title | ICRC</title>
    <meta property="og:title" content="Guidelines on Mental Health and Psychosocial Support">
    <meta name="description" content="An operational mental-health framework.">
    <meta property="article:published_time" content="2020-06-12">
  </head>
  <body>
    <nav><a href="/en">Home</a></nav>
    <main>
      <article>
        <h1>Guidelines on Mental Health and Psychosocial Support</h1>
        <p>These guidelines describe a harmonized approach to mental-health programmes.</p>
        <ul><li>Assessment</li><li>Referral</li></ul>
        <a href="{_PDF_URL}">Download PDF</a>
        <a href="https://icrc.org.evil.example/publication.pdf">Untrusted PDF</a>
      </article>
    </main>
  </body>
</html>
"""


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("# Real publication title\n\nBody", "Real publication title"),
        (
            "HOUSEHOLD\n\n## Guidelines for assessment in emergencies\n\n## 2008\n\n# Injured GPS coordinates:\n",
            "Guidelines for assessment in emergencies",
        ),
        ("## Contents\n\n# Body heading:\n", None),
    ],
)
def test_markdown_title_uses_meaningful_cover_heading(markdown: str, expected: str | None) -> None:
    """Direct-PDF titles come from front matter rather than later OCR headings."""
    assert icrc_module._markdown_title(markdown) == expected


class _CaptureSink:
    def __init__(self) -> None:
        self.artifacts: list[dict[str, object]] = []

    def capture_artifact(self, data: bytes | str, **metadata: object) -> None:
        self.artifacts.append({"data": data, **metadata})


def _fake_converter(data: bytes, title: str, publication_id: str) -> PdfConversionResult:
    assert data == _PDF_BYTES
    assert title
    assert publication_id
    return PdfConversionResult(
        markdown=_PDF_MARKDOWN,
        provenance={
            "backend": "pdf-inspector-test-double",
            "input_bytes": len(data),
            "page_count": 2,
        },
    )


def _landing_client(*, pdf_payload: bytes = _PDF_BYTES, pdf_content_type: str = "application/pdf") -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == _PUBLICATION_URL:
            return httpx.Response(200, text=_LANDING_HTML, headers={"ETag": '"landing-v1"'})
        assert str(request.url) == _PDF_URL
        return httpx.Response(
            200,
            content=pdf_payload,
            headers={"Content-Type": pdf_content_type, "Last-Modified": "Wed, 12 Jun 2020 00:00:00 GMT"},
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_icrc_ref_from_url_normalizes_official_publication_and_pdf() -> None:
    """Official landing pages and direct PDFs are normalized without query noise."""
    assert icrc_ref_from_url(f"{_PUBLICATION_URL}?download=1#summary") == IcrcPublicationRef(
        publication_id="4311-guidelines-mental-health-and-psychosocial-support",
        title="4311 Guidelines Mental Health And Psychosocial Support",
        page_url=_PUBLICATION_URL,
        direct_pdf=False,
    )
    pdf_ref = icrc_ref_from_url(f"{_PDF_URL}?token=discarded")
    assert pdf_ref.page_url == _PDF_URL
    assert pdf_ref.publication_id == "icrc-002-4311"
    assert pdf_ref.direct_pdf is True


def test_icrc_ref_from_url_upgrades_http_before_network_use() -> None:
    """Legacy HTTP manifest spellings are canonicalized to the HTTPS boundary."""
    ref = icrc_ref_from_url(_PUBLICATION_URL.replace("https://", "http://"))

    assert ref.page_url == _PUBLICATION_URL


@pytest.mark.parametrize(
    "url",
    [
        "https://icrc.org.evil.example/en/publication/4311-guidelines",
        "https://www.icrc.org/en/search?query=guidelines",
        "https://user@www.icrc.org/en/publication/4311-guidelines",
        "https://www.icrc.org:444/en/publication/4311-guidelines",
    ],
    ids=["lookalike-host", "catalogue", "credentials", "port"],
)
def test_icrc_ref_from_url_rejects_out_of_scope_urls(url: str) -> None:
    """The manifest cannot redirect the client beyond official publication scope."""
    with pytest.raises(IcrcFetchError):
        icrc_ref_from_url(url)


def test_refs_from_manifest_deduplicates_canonical_urls() -> None:
    """Query aliases in an explicit manifest result in one request."""
    refs = refs_from_manifest([_PUBLICATION_URL, f"{_PUBLICATION_URL}?language=en"])

    assert refs == [icrc_ref_from_url(_PUBLICATION_URL)]


def test_unauthorized_icrc_call_is_zero_network() -> None:
    """Authorization is checked before a client can be constructed."""
    with pytest.raises(IcrcPermissionError, match="disabled by default"):
        scrape_icrc(
            documents=1,
            link_mode=LinkMode.KEEP,
            url=_PUBLICATION_URL,
            client_factory=lambda: pytest.fail("network client must not be created"),
        )


def test_authorized_listing_still_requires_manifest_before_network() -> None:
    """Authorization never enables catalogue discovery implicitly."""
    with pytest.raises(IcrcFetchError, match="requires an explicit official publication URL manifest"):
        scrape_icrc(
            documents=1,
            link_mode=LinkMode.KEEP,
            authorized=True,
            permission_id=_PERMISSION_ID,
            client_factory=lambda: pytest.fail("network client must not be created"),
        )


def test_authorized_icrc_call_requires_permission_reference_before_network() -> None:
    """A Boolean assertion alone is not sufficient audit evidence."""
    with pytest.raises(IcrcPermissionError, match="permission_id"):
        scrape_icrc(
            documents=1,
            authorized=True,
            url=_PUBLICATION_URL,
            client_factory=lambda: pytest.fail("network client must not be created"),
        )


def test_scrape_publication_converts_official_pdf_with_provenance() -> None:
    """Landing metadata, full PDF Markdown, and both receipts survive normalization."""
    document = scrape_publication(
        _landing_client(),
        icrc_ref_from_url(_PUBLICATION_URL),
        permission_id=_PERMISSION_ID,
        pdf_converter=_fake_converter,
    )

    assert document.source == "icrc"
    assert document.external_id.startswith("icrc-4311-")
    assert document.title == "Guidelines on Mental Health and Psychosocial Support"
    assert document.metadata["publication_date"] == "2020-06-12"
    assert document.metadata["content_scope"] == "landing_page_and_full_pdf"
    assert document.metadata["source_format_types"] == ["html", "pdf"]
    assert document.metadata["source_media_types"] == ["text/html", "application/pdf"]
    assert document.metadata["pdf_resolution_status"] == "converted"
    assert document.metadata["pdf_url"] == _PDF_URL
    assert document.metadata["pdf_retrieval"]["byte_count"] == len(_PDF_BYTES)
    assert document.metadata["pdf_retrieval"]["last_modified"] == "Wed, 12 Jun 2020 00:00:00 GMT"
    assert document.metadata["pdf_conversion"]["backend"] == "pdf-inspector-test-double"
    assert document.metadata["permission_required"] is True
    assert document.metadata["authorization_asserted"] is True
    assert document.metadata["permission_id"] == _PERMISSION_ID
    assert document.provenance["permission_id"] == _PERMISSION_ID
    assert len(document.provenance["retrievals"]) == 2
    assert document.provenance["conversions"] == [document.metadata["pdf_conversion"]]
    assert "harmonized approach" in document.content
    assert "## Full publication" in document.content
    assert "Assess needs safely." in document.content
    assert "Untrusted PDF" in document.content
    assert all("evil.example" not in candidate for candidate in document.metadata["pdf_candidates"])


def test_landing_markdown_removes_recommendations_and_recurring_site_chrome() -> None:
    """Known recommendation, navigation, and promotion wrappers stay out of publication text."""
    html_text = """
    <html><body><main>
      <h1>Clinical publication</h1>
      <p>Related symptoms require more careful assessment.</p>
      <div role="navigation"><a href="/topics">Topic navigation</a></div>
      <div class="breadcrumb"><a href="/">Home</a></div>
      <div class="promo"><p>Donate today</p></div>
      <div class="newsletter-box"><p>Subscribe for updates</p></div>
      <div class="related-articles">
        <h2>Related</h2>
        <article><a href="/unrelated-story">Unrelated recommendation</a></article>
        <a href="/resource-centre">More</a>
      </div>
    </main></body></html>
    """

    markdown = icrc_module._landing_markdown(
        html_text,
        page_url=_PUBLICATION_URL,
        link_mode=LinkMode.KEEP,
    )

    assert "# Clinical publication" in markdown
    assert "Related symptoms require more careful assessment." in markdown
    assert "## Related" not in markdown
    assert "Unrelated recommendation" not in markdown
    assert "Topic navigation" not in markdown
    assert "Home" not in markdown
    assert "Donate today" not in markdown
    assert "Subscribe for updates" not in markdown


def test_shop_html_falls_back_to_explicit_landing_scope() -> None:
    """A shop/product redirect is a recorded fallback, never a fake full-PDF success."""
    shop_url = "https://shop.icrc.org/guidelines-mental-health.html"
    html_text = _LANDING_HTML.replace(_PDF_URL, shop_url).replace("Download PDF", "Get the publication")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == _PUBLICATION_URL:
            return httpx.Response(200, text=html_text)
        assert str(request.url) == shop_url
        return httpx.Response(200, text="<html><body>Product page</body></html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    document = scrape_publication(
        client,
        icrc_ref_from_url(_PUBLICATION_URL),
        permission_id=_PERMISSION_ID,
        pdf_converter=_fake_converter,
    )

    assert document.metadata["content_scope"] == "landing_page_only"
    assert document.metadata["source_format_types"] == ["html"]
    assert document.metadata["source_media_types"] == ["text/html"]
    assert document.metadata["pdf_resolution_status"] == "unresolved"
    assert "did not resolve to a PDF" in document.metadata["pdf_error"]
    assert "Provide an official direct PDF URL" in document.metadata["resolution_action"]
    assert document.metadata["pdf_conversion"] is None
    assert "## Full publication" not in document.content


def test_authorized_shop_resolver_converts_official_ebook_with_three_receipts() -> None:
    """Rendered product selection can hand a validated official SKU URL to PDF conversion."""
    html_text = _LANDING_HTML.replace(_PDF_URL, _SHOP_URL).replace("Download PDF", "Get the publication")
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if str(request.url) == _PUBLICATION_URL:
            return httpx.Response(200, text=html_text)
        assert str(request.url) == _SHOP_DOWNLOAD_URL
        return httpx.Response(200, content=_PDF_BYTES, headers={"Content-Type": "application/pdf"})

    resolution_receipt = {
        "requested_url": _SHOP_URL,
        "final_url": _SHOP_URL,
        "transport": "playwright-ephemeral-browser",
        "byte_count": 123,
        "sha256": "a" * 64,
    }
    document = scrape_publication(
        httpx.Client(transport=httpx.MockTransport(handler)),
        icrc_ref_from_url(_PUBLICATION_URL),
        permission_id=_PERMISSION_ID,
        pdf_converter=_fake_converter,
        shop_pdf_resolver=lambda url: (
            ShopPdfResolution(
                pdf_url=_SHOP_DOWNLOAD_URL,
                retrieval=resolution_receipt,
            )
            if url == _SHOP_URL
            else None
        ),
    )

    assert requests == [_PUBLICATION_URL, _SHOP_DOWNLOAD_URL]
    assert document.metadata["content_scope"] == "landing_page_and_full_pdf"
    assert document.metadata["pdf_resolution_status"] == "converted"
    assert document.metadata["pdf_url"] == "https://shop.icrc.org/download/ebook?sku=REDACTED"
    assert document.metadata["shop_resolution_retrieval"] == resolution_receipt
    assert len(document.provenance["retrievals"]) == 3
    assert document.provenance["retrievals"][1] == resolution_receipt
    assert document.provenance["retrievals"][2]["requested_url"] == (
        "https://shop.icrc.org/download/ebook?sku=REDACTED"
    )


def test_icrc_rejects_redirect_outside_official_hosts_before_following_it() -> None:
    """An official response cannot redirect the authorized client into a private network."""
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private.pdf"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(IcrcFetchError, match="permitted scheme/host boundary"):
        scrape_publication(
            client,
            icrc_ref_from_url(_PUBLICATION_URL),
            permission_id=_PERMISSION_ID,
            pdf_converter=_fake_converter,
        )

    assert requests == [_PUBLICATION_URL]


def test_authorized_manifest_is_bounded_and_uses_injected_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A multi-entry manifest honors the requested bound without persistent files."""
    urls = [
        f"{BASE_URL}/sites/default/files/publications/first.pdf",
        f"{BASE_URL}/sites/default/files/publications/middle.pdf",
        f"{BASE_URL}/sites/default/files/publications/end.pdf",
    ]
    requests: list[str] = []

    @contextmanager
    def client_factory() -> Iterator[httpx.Client]:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, content=_PDF_BYTES, headers={"Content-Type": "application/pdf"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    monkeypatch.setattr(icrc_module, "DOCUMENT_DELAY_SECONDS", 0.0)
    run = scrape_icrc(
        documents=2,
        authorized=True,
        permission_id=_PERMISSION_ID,
        manifest=urls,
        client_factory=client_factory,
        pdf_converter=_fake_converter,
    )
    documents = list(run)

    assert run.total == 2
    assert [document.url for document in documents] == urls[:2]
    assert requests == urls[:2]


def test_manifest_reuses_one_shop_browser_without_changing_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple shop-backed publications share Chromium but retain distinct PDFs."""
    landing_urls = [f"{BASE_URL}/en/publication/shop-backed-{index}" for index in range(2)]
    shop_urls = [f"https://shop.icrc.org/shop-backed-{index}-print-en.html" for index in range(2)]
    pdf_urls = [f"https://shop.icrc.org/download/ebook?sku={index}/002-ebook" for index in range(2)]
    page = object()
    browser_opens = 0
    resolution_calls: list[tuple[object, str]] = []

    @contextmanager
    def shared_page() -> Iterator[object]:
        nonlocal browser_opens
        browser_opens += 1
        yield page

    def resolve_on_page(received_page: object, shop_url: str) -> ShopPdfResolution:
        resolution_calls.append((received_page, shop_url))
        index = shop_urls.index(shop_url)
        return ShopPdfResolution(
            pdf_url=pdf_urls[index],
            retrieval={
                "requested_url": shop_url,
                "transport": "playwright-ephemeral-browser",
                "retrieval_duration_ms": 8,
            },
        )

    @contextmanager
    def client_factory() -> Iterator[httpx.Client]:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url in landing_urls:
                index = landing_urls.index(url)
                return httpx.Response(
                    200,
                    text=f"<html><main><h1>Publication {index}</h1>"
                    f"<a href='{shop_urls[index]}'>Get the publication</a></main></html>",
                )
            index = pdf_urls.index(url)
            return httpx.Response(200, content=f"%PDF-1.7 publication {index}".encode())

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    def converter(data: bytes, _title: str, _publication_id: str) -> PdfConversionResult:
        return PdfConversionResult(
            markdown=f"# {data.decode()}",
            provenance={"backend": "test", "processing_time_ms": 7},
        )

    monkeypatch.setattr(icrc_module, "DOCUMENT_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(icrc_module, "_shop_playwright_page", shared_page)
    monkeypatch.setattr(icrc_module, "_resolve_icrc_shop_pdf_on_page", resolve_on_page)
    documents = list(
        scrape_icrc(
            documents=2,
            authorized=True,
            permission_id=_PERMISSION_ID,
            manifest=landing_urls,
            client_factory=client_factory,
            pdf_converter=converter,
            shop_pdf_resolver=icrc_module.resolve_icrc_shop_pdf,
        )
    )

    assert browser_opens == 1
    assert resolution_calls == [(page, shop_urls[0]), (page, shop_urls[1])]
    assert [document.url for document in documents] == landing_urls
    assert len({document.provenance["content_sha256"] for document in documents}) == 2
    assert documents[0].provenance["phase_timings_ms"]["shop_resolution"] == 8
    assert documents[0].provenance["phase_timings_ms"]["pdf_conversion"] == 7


def test_manifest_closes_failed_shop_browser_before_fresh_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed reused page is closed before Playwright is started again."""
    landing_urls = [f"{BASE_URL}/en/publication/shop-retry-{index}" for index in range(2)]
    shop_urls = [f"https://shop.icrc.org/shop-retry-{index}-print-en.html" for index in range(2)]
    pdf_urls = [f"https://shop.icrc.org/download/ebook?sku=retry-{index}/002-ebook" for index in range(2)]
    events: list[str] = []
    browser_opens = 0
    second_product_attempts = 0

    @contextmanager
    def shared_page() -> Iterator[str]:
        nonlocal browser_opens
        page = f"page-{browser_opens}"
        browser_opens += 1
        events.append(f"open:{page}")
        try:
            yield page
        finally:
            events.append(f"close:{page}")

    def resolve_on_page(page: str, shop_url: str) -> ShopPdfResolution:
        nonlocal second_product_attempts
        index = shop_urls.index(shop_url)
        events.append(f"resolve:{page}:{index}")
        if index == 1:
            second_product_attempts += 1
            if second_product_attempts == 1:
                raise icrc_module.PlaywrightError("detached reused page")
        return ShopPdfResolution(
            pdf_url=pdf_urls[index],
            retrieval={"requested_url": shop_url, "transport": "playwright-ephemeral-browser"},
        )

    @contextmanager
    def client_factory() -> Iterator[httpx.Client]:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url in landing_urls:
                index = landing_urls.index(url)
                return httpx.Response(
                    200,
                    text=f"<html><main><h1>Publication {index}</h1>"
                    f"<a href='{shop_urls[index]}'>Get the publication</a></main></html>",
                )
            index = pdf_urls.index(url)
            return httpx.Response(200, content=f"%PDF-1.7 retry {index}".encode())

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    def converter(data: bytes, _title: str, _publication_id: str) -> PdfConversionResult:
        return PdfConversionResult(markdown=f"# {data.decode()}", provenance={"backend": "test"})

    monkeypatch.setattr(icrc_module, "DOCUMENT_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(icrc_module, "_shop_playwright_page", shared_page)
    monkeypatch.setattr(icrc_module, "_resolve_icrc_shop_pdf_on_page", resolve_on_page)
    documents = list(
        scrape_icrc(
            documents=2,
            authorized=True,
            permission_id=_PERMISSION_ID,
            manifest=landing_urls,
            client_factory=client_factory,
            pdf_converter=converter,
            shop_pdf_resolver=icrc_module.resolve_icrc_shop_pdf,
        )
    )

    assert len(documents) == 2
    assert events == [
        "open:page-0",
        "resolve:page-0:0",
        "resolve:page-0:1",
        "close:page-0",
        "open:page-1",
        "resolve:page-1:1",
        "close:page-1",
    ]


def test_transient_download_uses_retry_after_and_records_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient publisher response is retried with bounded, auditable backoff."""
    requests: list[str] = []
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, content=_PDF_BYTES, headers={"Content-Type": "application/pdf"})

    monkeypatch.setattr(icrc_module.time, "sleep", delays.append)
    document = scrape_publication(
        httpx.Client(transport=httpx.MockTransport(handler)),
        icrc_ref_from_url(_PDF_URL),
        permission_id=_PERMISSION_ID,
        pdf_converter=_fake_converter,
    )

    assert requests == [_PDF_URL, _PDF_URL]
    assert delays == [3.0]
    assert document.metadata["pdf_retrieval"]["attempt_count"] == 2
    assert document.metadata["pdf_retrieval"]["retry_delays_seconds"] == [3.0]


def test_manifest_skips_permanent_failure_and_reaches_completed_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """One stale manifest URL does not turn a 2-document request into a 1-document run."""
    urls = [
        f"{BASE_URL}/sites/default/files/publications/dead.pdf",
        f"{BASE_URL}/sites/default/files/publications/live-1.pdf",
        f"{BASE_URL}/sites/default/files/publications/live-2.pdf",
    ]
    requests: list[str] = []

    @contextmanager
    def client_factory() -> Iterator[httpx.Client]:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if str(request.url) == urls[0]:
                return httpx.Response(404)
            return httpx.Response(200, content=_PDF_BYTES, headers={"Content-Type": "application/pdf"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    monkeypatch.setattr(icrc_module, "DOCUMENT_DELAY_SECONDS", 0.0)
    documents = list(
        scrape_icrc(
            documents=2,
            authorized=True,
            permission_id=_PERMISSION_ID,
            manifest=urls,
            client_factory=client_factory,
            pdf_converter=_fake_converter,
        )
    )

    assert [document.url for document in documents] == urls[1:]
    assert requests == urls
    assert documents[0].metadata["skipped_manifest_candidates"] == [
        {
            "publication_id": "dead",
            "url": urls[0],
            "error_type": "IcrcFetchError",
            "message": f"HTTP 404 while fetching '{urls[0]}'",
        }
    ]


def test_manifest_skips_landing_alias_with_duplicate_pdf_payload_and_preserves_capture_alignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publication aliases cannot consume slots or leak artifacts into later unique results."""
    landing_urls = [
        f"{BASE_URL}/en/publication/4928-climate-plan",
        f"{BASE_URL}/en/publication/climate-plan",
        f"{BASE_URL}/en/publication/next-guideline",
    ]
    pdf_urls = [
        f"{BASE_URL}/sites/default/files/publications/climate-plan.pdf",
        f"{BASE_URL}/sites/default/files/publications/climate-plan.pdf",
        f"{BASE_URL}/sites/default/files/publications/next-guideline.pdf",
    ]
    payloads = {
        pdf_urls[0]: b"%PDF-1.7 same climate plan",
        pdf_urls[2]: b"%PDF-1.7 next unique guideline",
    }
    requests: list[str] = []

    @contextmanager
    def client_factory() -> Iterator[httpx.Client]:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            requests.append(url)
            if url in landing_urls:
                position = landing_urls.index(url)
                html = (
                    "<html><head><meta property='og:title' content='Guideline'></head>"
                    f"<body><main><h1>Guideline</h1><a href='{pdf_urls[position]}'>PDF</a></main></body></html>"
                )
                return httpx.Response(200, text=html)
            return httpx.Response(200, content=payloads[url], headers={"Content-Type": "application/pdf"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    def converter(data: bytes, _title: str, _publication_id: str) -> PdfConversionResult:
        return PdfConversionResult(markdown="# Converted", provenance={"backend": "test", "input_bytes": len(data)})

    sink = _CaptureSink()
    monkeypatch.setattr(icrc_module, "DOCUMENT_DELAY_SECONDS", 0.0)
    with artifact_capture_context(sink):
        documents = list(
            scrape_icrc(
                documents=2,
                authorized=True,
                permission_id=_PERMISSION_ID,
                manifest=landing_urls,
                client_factory=client_factory,
                pdf_converter=converter,
                shop_pdf_resolver=None,
            )
        )

    assert [document.url for document in documents] == [landing_urls[0], landing_urls[2]]
    assert requests == [landing_urls[0], pdf_urls[0], landing_urls[1], pdf_urls[1], landing_urls[2], pdf_urls[2]]
    assert [artifact["url"] for artifact in sink.artifacts] == [
        pdf_urls[0],
        landing_urls[0],
        pdf_urls[2],
        landing_urls[2],
    ]
    assert documents[1].metadata["skipped_manifest_candidates"] == [
        {
            "publication_id": "climate-plan",
            "url": landing_urls[1],
            "error_type": "IcrcDuplicatePublicationError",
            "message": (
                "ICRC publication alias resolved to a PDF payload already emitted by this manifest run "
                "(sha256: c651dd56f4a6d47c8f29434b30637aee7922a7bdb0cb48659e499d93f256e0f9)"
            ),
        }
    ]


def test_manifest_exhaustion_without_transport_failure_does_not_claim_requested_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short healthy manifest cannot silently satisfy a larger requested count."""
    urls = [
        f"{BASE_URL}/sites/default/files/publications/first.pdf",
        f"{BASE_URL}/sites/default/files/publications/second.pdf",
    ]

    @contextmanager
    def client_factory() -> Iterator[httpx.Client]:
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    content=_PDF_BYTES,
                    headers={"Content-Type": "application/pdf"},
                )
            )
        ) as client:
            yield client

    monkeypatch.setattr(icrc_module, "DOCUMENT_DELAY_SECONDS", 0.0)
    run = scrape_icrc(
        documents=3,
        authorized=True,
        permission_id=_PERMISSION_ID,
        manifest=urls,
        client_factory=client_factory,
        pdf_converter=_fake_converter,
    )

    with pytest.raises(IcrcFetchError, match="requested 3 documents.*emitted 2"):
        list(run)


def test_failed_landing_candidate_capture_does_not_leak_into_next_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skipped landing page never contributes artifacts to a later success."""
    failed_url = f"{BASE_URL}/en/publication/failed-landing"
    live_url = f"{BASE_URL}/sites/default/files/publications/live.pdf"

    @contextmanager
    def client_factory() -> Iterator[httpx.Client]:
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == failed_url:
                return httpx.Response(200, content=b"%PDF-1.7 wrong representation")
            assert str(request.url) == live_url
            return httpx.Response(200, content=_PDF_BYTES, headers={"Content-Type": "application/pdf"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    sink = _CaptureSink()
    monkeypatch.setattr(icrc_module, "DOCUMENT_DELAY_SECONDS", 0.0)
    with artifact_capture_context(sink):
        documents = list(
            scrape_icrc(
                documents=1,
                authorized=True,
                permission_id=_PERMISSION_ID,
                manifest=[failed_url, live_url],
                client_factory=client_factory,
                pdf_converter=_fake_converter,
            )
        )

    assert [document.url for document in documents] == [live_url]
    assert [artifact["filename"] for artifact in sink.artifacts] == ["live.pdf"]
    assert all(artifact["url"] != failed_url for artifact in sink.artifacts)


def test_direct_pdf_capture_precedes_conversion() -> None:
    """Review mode retains original PDF bytes for side-by-side inspection."""
    sink = _CaptureSink()
    with _landing_client() as client, artifact_capture_context(sink):
        document = scrape_publication(
            client,
            icrc_ref_from_url(_PDF_URL),
            permission_id=_PERMISSION_ID,
            pdf_converter=_fake_converter,
        )

    assert document.metadata["pdf_conversion"]["page_count"] == 2
    assert document.title == "Clinical framework"
    assert document.external_id == "icrc-002-4311"
    assert document.metadata["source_format_types"] == ["pdf"]
    assert document.metadata["source_media_types"] == ["application/pdf"]
    assert len(sink.artifacts) == 1
    artifact = sink.artifacts[0]
    assert artifact["data"] == _PDF_BYTES
    assert artifact["media_type"] == "application/pdf"
    assert artifact["filename"] == "icrc-002-4311.pdf"
    assert artifact["metadata"] == {"representation": "downloaded_pdf"}
