"""Tests for the CDC full-text HTML scraper."""

import logging

import httpx
import pytest
from typer.testing import CliRunner

from amfv_datasets.scraping.base import ScrapedDocument, ScrapeRun
from amfv_datasets.scraping.cdc import (
    ATTRIBUTION,
    LICENSE,
    LICENSE_URL,
    STACKS_BASE_URL,
    CdcFetchError,
    CdcRecord,
    fetch_cdc_record,
    list_cdc_records,
    scrape_cdc,
    scrape_cdc_record,
    stacks_identifier_from_url,
)
from amfv_datasets.scraping.cli import SCRAPERS, app
from amfv_datasets.scraping.html import LinkMode

_SUPPORTED_RIS = """TY  - Journal Article
AU  - Patel, Rupa R.
AU  - Hoover, Karen W.
A2  - Centers for Disease Control and Prevention (U.S.)
AN  - cdc:182510
LA  - eng
PB  - https://doi.org/10.15585/mmwr.mm7435a1
PY  - 2025
TI  - Clinical Recommendations for Injectable Lenacapavir
UR  - https://stacks.cdc.gov/view/cdc/182510
ER  -
"""

_PDF_ONLY_RIS = """TY  - Guidance
AU  - Hoffman, Adela
AN  - cdc:255040
DO  - https://doi.org/10.15620/cdc/255040
LA  - eng
PY  - 2026
TI  - Public Health Rapid Response Team Programs
UR  - https://stacks.cdc.gov/view/cdc/255040
ER  -
"""

_ARTICLE_HTML = """
<html>
  <head><meta property="article:published_time" content="2025-09-18" /></head>
  <body>
    <nav>Page chrome must not be scraped.</nav>
    <main>
      <h1>Clinical Recommendation for Injectable Lenacapavir</h1>
      <div class="syndicate">
        <h2>Summary</h2>
        <p>{summary}</p>
        <div class="no-syndicate"><p>Article metrics must not be scraped.</p></div>
        <h2>Recommendation</h2>
        <p>Clinicians should read the <a href="/hivnexus/hcp/prep/">PrEP guidance</a>.</p>
      </div>
    </main>
  </body>
</html>
""".format(summary="Evidence from randomized trials supports this recommendation. " * 12)


def _listing_html(*ris_records: str, total: int | None = None) -> str:
    items = []
    for ris in ris_records:
        identifier = "182510" if "182510" in ris else "255040"
        title = "Supported guideline" if identifier == "182510" else "PDF-only guideline"
        items.append(
            f"""
            <li>
              <div class="object-title"><a href="/view/cdc/{identifier}">{title}</a></div>
              <span class="badge">Public Domain</span>
              <textarea id="RIScitation{identifier}">{ris}</textarea>
            </li>
            """
        )
    result_count = f"Showing 1 - {len(items)} of {total} results" if total is not None else ""
    return f"<html><body><p>{result_count}</p><ul id='search-results-list'>{''.join(items)}</ul></body></html>"


def _record_html(ris: str, *, public_domain: bool = True) -> str:
    badge = '<span class="badge">Public Domain</span>' if public_domain else ""
    return f"""
    <html><head><meta name="citation_title" content="Fallback title" /></head>
      <body><h1>Fallback title</h1>{badge}<textarea id="RIScitation182510">{ris}</textarea></body>
    </html>
    """


def _supported_record() -> CdcRecord:
    return CdcRecord(
        identifier="182510",
        title="Clinical Recommendations for Injectable Lenacapavir",
        stacks_url=f"{STACKS_BASE_URL}/view/cdc/182510",
        doi_url="https://doi.org/10.15585/mmwr.mm7435a1",
        language="eng",
        public_domain=True,
        publication_type="Journal Article",
        published_year="2025",
        authors=("Patel, Rupa R.", "Hoover, Karen W."),
        corporate_authors=("Centers for Disease Control and Prevention (U.S.)",),
    )


def _article_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "doi.org":
            return httpx.Response(
                302,
                headers={"location": "https://www.cdc.gov/mmwr/volumes/74/wr/mm7435a1.htm?source=doi"},
            )
        if request.url.host == "www.cdc.gov":
            return httpx.Response(200, text=_ARTICLE_HTML, headers={"content-type": "text/html; charset=utf-8"})
        raise AssertionError(f"unexpected request to {request.url}")

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_list_cdc_records_parses_stacks_ris_metadata_and_total() -> None:
    """Discovery retains reusable citation metadata and the collection total."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/cbrowse"
        assert request.url.params["pid"] == "cdc:100"
        assert request.url.params["maxResults"] == "100"
        assert request.url.params["start"] == "0"
        return httpx.Response(200, text=_listing_html(_SUPPORTED_RIS, _PDF_ONLY_RIS, total=787))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    listing = list_cdc_records(client)

    assert listing.total == 787
    assert len(listing.records) == 2
    supported = listing.records[0]
    assert supported.identifier == "182510"
    assert supported.doi_url == "https://doi.org/10.15585/mmwr.mm7435a1"
    assert supported.authors == ("Patel, Rupa R.", "Hoover, Karen W.")
    assert supported.corporate_authors == ("Centers for Disease Control and Prevention (U.S.)",)
    assert supported.public_domain is True
    assert listing.records[1].doi_url is None


def test_list_cdc_records_wraps_http_failures() -> None:
    """Collection failures use the scraper's actionable error type."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))

    with pytest.raises(CdcFetchError, match="listing page 1"):
        list_cdc_records(client)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/view/cdc/182510",
        "https://stacks.cdc.gov/cbrowse?pid=cdc%3A100",
        "https://stacks.cdc.gov/view/cdc/not-a-number",
    ],
    ids=["wrong-host", "collection-page", "non-numeric-id"],
)
def test_stacks_identifier_from_url_rejects_non_record_urls(url: str) -> None:
    """URL mode accepts a single canonical Stacks record shape."""
    with pytest.raises(CdcFetchError, match="CDC Stacks"):
        stacks_identifier_from_url(url)


def test_fetch_cdc_record_reads_ris_and_normalizes_the_url() -> None:
    """A www-prefixed input becomes a canonical Stacks record."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{STACKS_BASE_URL}/view/cdc/182510"
        return httpx.Response(200, text=_record_html(_SUPPORTED_RIS))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    record = fetch_cdc_record(client, "http://www.stacks.cdc.gov/view/cdc/182510?download=true")

    assert record.identifier == "182510"
    assert record.stacks_url == f"{STACKS_BASE_URL}/view/cdc/182510"
    assert record.title == "Clinical Recommendations for Injectable Lenacapavir"
    assert record.doi_url == "https://doi.org/10.15585/mmwr.mm7435a1"
    assert record.public_domain is True


def test_scrape_cdc_record_extracts_only_the_full_text_html_body() -> None:
    """The semantic article body becomes normalized markdown with provenance."""
    with _article_client() as client:
        document = scrape_cdc_record(client, _supported_record())

    assert document is not None
    assert document.source == "cdc"
    assert document.external_id == "cdc-182510"
    assert document.title == "Clinical Recommendation for Injectable Lenacapavir"
    assert document.url == "https://www.cdc.gov/mmwr/volumes/74/wr/mm7435a1.htm"
    assert document.section_count == 2
    assert "## Summary" in document.content
    assert "[PrEP guidance](https://www.cdc.gov/hivnexus/hcp/prep/)" in document.content
    assert "Page chrome" not in document.content
    assert "Article metrics" not in document.content
    assert document.metadata == {
        "attribution": ATTRIBUTION,
        "license": LICENSE,
        "license_url": LICENSE_URL,
        "stacks_url": "https://stacks.cdc.gov/view/cdc/182510",
        "doi_url": "https://doi.org/10.15585/mmwr.mm7435a1",
        "language": "eng",
        "publication_type": "Journal Article",
        "published_year": "2025",
        "authors": ["Patel, Rupa R.", "Hoover, Karen W."],
        "corporate_authors": ["Centers for Disease Control and Prevention (U.S.)"],
        "content_scope": "full_html",
    }


def test_scrape_cdc_record_strips_links_in_strip_mode() -> None:
    """Shared link mode applies to the extracted CDC article body."""
    with _article_client() as client:
        document = scrape_cdc_record(client, _supported_record(), link_mode=LinkMode.STRIP)

    assert document is not None
    assert "PrEP guidance" in document.content
    assert "[PrEP guidance]" not in document.content


@pytest.mark.parametrize(
    "record",
    [
        CdcRecord("1", "PDF only", "https://stacks.cdc.gov/view/cdc/1", None, "eng", True),
        CdcRecord("2", "Spanish", "https://stacks.cdc.gov/view/cdc/2", "https://doi.org/10.15585/mmwr.x", "spa", True),
        CdcRecord(
            "3", "Restricted", "https://stacks.cdc.gov/view/cdc/3", "https://doi.org/10.15585/mmwr.x", "eng", False
        ),
    ],
    ids=["pdf-only", "non-english", "not-public-domain"],
)
def test_scrape_cdc_record_skips_unsupported_records_without_requesting_them(record: CdcRecord) -> None:
    """Unsupported record types are filtered before any document request."""
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError("unexpected request")))
    )

    assert scrape_cdc_record(client, record) is None


def test_scrape_cdc_record_logs_and_skips_a_failed_document(caplog: pytest.LogCaptureFixture) -> None:
    """One stale DOI costs one record rather than the whole collection run."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)), follow_redirects=True)

    with caplog.at_level(logging.WARNING):
        document = scrape_cdc_record(client, _supported_record())

    assert document is None
    assert "182510" in caplog.text


def test_scrape_cdc_counts_successful_documents_not_pdf_only_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """A skipped record does not consume the caller's document limit."""
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.host == "stacks.cdc.gov" and request.url.path == "/cbrowse":
            return httpx.Response(200, text=_listing_html(_PDF_ONLY_RIS, _SUPPORTED_RIS, total=2))
        if request.url.host == "doi.org":
            return httpx.Response(302, headers={"location": "https://www.cdc.gov/mmwr/article.htm"})
        if request.url.host == "www.cdc.gov":
            return httpx.Response(200, text=_ARTICLE_HTML, headers={"content-type": "text/html"})
        raise AssertionError(f"unexpected request to {request.url}")

    monkeypatch.setattr(
        "amfv_datasets.scraping.cdc.default_client",
        lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
    )
    monkeypatch.setattr("amfv_datasets.scraping.cdc.time.sleep", lambda seconds: None)

    scrape_run = scrape_cdc(documents=1)
    documents = list(scrape_run.documents)

    assert scrape_run.total == 1
    assert [document.external_id for document in documents] == ["cdc-182510"]
    assert sum("doi.org" in request for request in requests) == 1


def test_scrape_cdc_url_mode_rejects_a_pdf_only_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit PDF-only records fail with the supported content boundary."""
    monkeypatch.setattr(
        "amfv_datasets.scraping.cdc.default_client",
        lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=_record_html(_PDF_ONLY_RIS)))
        ),
    )

    scrape_run = scrape_cdc(documents=None, url="https://stacks.cdc.gov/view/cdc/255040")

    with pytest.raises(CdcFetchError, match="PDF-only"):
        list(scrape_run.documents)


def test_cli_dispatches_the_cdc_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CDC entry point is registered with the shared scraping CLI."""

    def fake_scrape_cdc(*, documents: int | None, link_mode: LinkMode, url: str | None = None) -> ScrapeRun:
        assert documents == 2
        assert link_mode is LinkMode.KEEP
        assert url is None
        document = ScrapedDocument(
            source="cdc",
            external_id="cdc-182510",
            title="CDC guideline",
            url="https://www.cdc.gov/mmwr/article.htm",
            content="content",
        )
        return ScrapeRun([document], total=1)

    monkeypatch.setitem(SCRAPERS, "cdc", fake_scrape_cdc)

    result = CliRunner().invoke(app, ["--source", "cdc", "--documents", "2", "--no-progress"])

    assert result.exit_code == 0
    assert '"external_id": "cdc-182510"' in result.stdout
