"""Tests for StatPearls scraping helpers."""

import json
import logging

import httpx
import pytest
from typer.testing import CliRunner

from amfv_datasets.scraping.base import ScrapedDocument, ScrapeRun
from amfv_datasets.scraping.cli import SCRAPERS, app
from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.statpearls import (
    LICENSE,
    LICENSE_URL,
    ChapterRef,
    ChapterSearch,
    StatpearlsFetchError,
    list_chapters,
    scrape_chapter,
    scrape_chapter_by_url,
    scrape_statpearls,
    search_section_uids,
    summarize_chapters,
)

_CHAPTER_HTML = (
    "<html><body><div id='maincontent'><div class='body-content' itemprop='text'>"
    "<div id='article-1.s1'><p>ok</p></div></div></div></body></html>"
)


def _bookinfo(chapter_title: str) -> str:
    return (
        "<Info><Path>"
        '<Parent id="statpearls" role="source" type="book" uid="4403668"><Title>StatPearls</Title></Parent>'
        f'<Parent id="article-1" role="document" type="chapter" uid="1"><Title>{chapter_title}</Title></Parent>'
        '<Self id="article-1.s1" role="object" type="sec" uid="111"><Title>Introduction</Title></Self>'
        "</Path></Info>"
    )


def _esummary_payload(records: dict[str, dict]) -> dict:
    return {"result": {"uids": list(records), **records}}


def test_search_section_uids_returns_the_result_idlist() -> None:
    """The section search page returns the raw E-utilities id list."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/entrez/eutils/esearch.fcgi"
        assert request.url.params["db"] == "books"
        assert request.url.params["term"] == "statpearls[book]"
        assert request.url.params["retstart"] == "0"
        # E-utilities policy asks callers to identify themselves.
        assert request.url.params["tool"] == "amfv-datasets"
        payload = {"esearchresult": {"idlist": ["111", "112"]}}
        return httpx.Response(200, text=json.dumps(payload))

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert search_section_uids(client, retstart=0) == ["111", "112"]


def test_search_section_uids_raises_statpearls_fetch_error_on_failure() -> None:
    """A search failure raises this module's error type, not a raw httpx error."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))

    with pytest.raises(StatpearlsFetchError, match="Could not search"):
        search_section_uids(client, retstart=0)


def test_summarize_chapters_extracts_accession_and_chapter_title() -> None:
    """Section summaries resolve to their parent chapter's accession and title."""
    payload = _esummary_payload(
        {
            "111": {"chapteraccessionid": "NBK111", "bookinfo": _bookinfo("Bilateral Vocal Cord Paralysis")},
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/entrez/eutils/esummary.fcgi"
        assert request.url.params["id"] == "111"
        assert request.url.params["tool"] == "amfv-datasets"
        return httpx.Response(200, text=json.dumps(payload))

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert summarize_chapters(client, ["111"]) == [
        ChapterRef(accession="NBK111", title="Bilateral Vocal Cord Paralysis")
    ]


def test_summarize_chapters_skips_records_without_a_chapter_accession() -> None:
    """Section records missing a chapter accession are skipped rather than erroring."""
    payload = _esummary_payload({"111": {"bookinfo": _bookinfo("Orphan Section")}})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps(payload))

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert summarize_chapters(client, ["111"]) == []


def test_summarize_chapters_returns_empty_for_no_uids() -> None:
    """No section UIDs means no E-utilities request and no chapters."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not fetch summaries for an empty UID list")

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert summarize_chapters(client, []) == []


def test_summarize_chapters_raises_statpearls_fetch_error_on_failure() -> None:
    """A summary failure raises this module's error type, not a raw httpx error."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))

    with pytest.raises(StatpearlsFetchError, match="Could not summarize"):
        summarize_chapters(client, ["111"])


def test_list_chapters_deduplicates_chapters_seen_on_earlier_pages() -> None:
    """Chapters already seen are skipped even if their sections reappear."""
    payload = _esummary_payload(
        {
            "111": {"chapteraccessionid": "NBK111", "bookinfo": _bookinfo("Chapter One")},
            "112": {"chapteraccessionid": "NBK111", "bookinfo": _bookinfo("Chapter One")},
            "113": {"chapteraccessionid": "NBK222", "bookinfo": _bookinfo("Chapter Two")},
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/entrez/eutils/esearch.fcgi":
            return httpx.Response(200, text=json.dumps({"esearchresult": {"idlist": ["111", "112", "113"]}}))
        return httpx.Response(200, text=json.dumps(payload))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    search = ChapterSearch()

    first_page = list_chapters(client, search=search)

    assert first_page == [
        ChapterRef(accession="NBK111", title="Chapter One"),
        ChapterRef(accession="NBK222", title="Chapter Two"),
    ]
    assert search.seen == {"NBK111", "NBK222"}


def test_list_chapters_continues_past_a_batch_holding_only_seen_chapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch that adds no chapter must not be reported as the end of the source.

    Sections of one chapter can fill a whole batch, and `scrape_listing_documents`
    stops at the first page that comes back empty, so returning the empty
    remainder would leave every later chapter unscraped.
    """
    monkeypatch.setattr("amfv_datasets.scraping.statpearls.time.sleep", lambda _seconds: None)
    batches = {"0": ["111"], "200": ["112"], "400": ["113"]}
    summaries = {
        "111": {"chapteraccessionid": "NBK111", "bookinfo": _bookinfo("Chapter One")},
        # A second section of the chapter the first batch already yielded.
        "112": {"chapteraccessionid": "NBK111", "bookinfo": _bookinfo("Chapter One")},
        "113": {"chapteraccessionid": "NBK222", "bookinfo": _bookinfo("Chapter Two")},
    }
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/entrez/eutils/esearch.fcgi":
            retstart = request.url.params["retstart"]
            requested.append(retstart)
            return httpx.Response(200, text=json.dumps({"esearchresult": {"idlist": batches.get(retstart, [])}}))
        uids = request.url.params["id"].split(",")
        return httpx.Response(200, text=json.dumps(_esummary_payload({uid: summaries[uid] for uid in uids})))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    search = ChapterSearch()

    assert list_chapters(client, search=search) == [ChapterRef(accession="NBK111", title="Chapter One")]
    assert list_chapters(client, search=search) == [ChapterRef(accession="NBK222", title="Chapter Two")]
    # The 200 batch contributed nothing, so the call went on to 400 instead of
    # reporting the source finished.
    assert requested == ["0", "200", "400"]


def test_list_chapters_returns_empty_only_once_the_search_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty id list is the single condition that ends the source."""
    monkeypatch.setattr("amfv_datasets.scraping.statpearls.time.sleep", lambda _seconds: None)

    searches = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal searches
        if request.url.path == "/entrez/eutils/esearch.fcgi":
            searches += 1
            return httpx.Response(200, text=json.dumps({"esearchresult": {"idlist": []}}))
        raise AssertionError("an empty batch should not be summarized")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    search = ChapterSearch()

    assert list_chapters(client, search=search) == []
    assert search.exhausted
    assert searches == 1

    # Once exhausted the cursor stops rather than searching on past the end.
    assert list_chapters(client, search=search) == []
    assert searches == 1


def test_scrape_chapter_converts_body_content_to_markdown() -> None:
    """A chapter's `itemprop="text"` body is converted to markdown."""
    html = """
        <html><body>
          <div id="maincontent">
            <div class="body-content" itemprop="text">
              <div id="article-1.s1">
                <h2>Introduction</h2>
                <p>Bilateral vocal cord paralysis is a rare condition.</p>
                <div id="article-1.s1.1"><p>A nested subsection.</p></div>
              </div>
              <div id="article-1.s2"><h2>Etiology</h2><p>Causes vary.</p></div>
            </div>
          </div>
        </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/books/NBK111/"
        return httpx.Response(200, text=html)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    document = scrape_chapter(client, ChapterRef(accession="NBK111", title="Bilateral Vocal Cord Paralysis"))

    assert document is not None
    assert document.source == "statpearls"
    assert document.external_id == "statpearls-NBK111"
    assert document.title == "Bilateral Vocal Cord Paralysis"
    assert document.url == "https://www.ncbi.nlm.nih.gov/books/NBK111/"
    assert document.content == (
        "## Introduction\n\nBilateral vocal cord paralysis is a rare condition.\n\n"
        "A nested subsection.\n\n"
        "## Etiology\n\nCauses vary."
    )
    # Nested subsections roll up into their parent section.
    assert document.section_count == 2
    assert document.metadata == {"accession": "NBK111", "license": LICENSE, "license_url": LICENSE_URL}


def test_scrape_chapter_resolves_relative_links_against_the_chapter_page() -> None:
    """A relative link resolves against the chapter URL, not the Bookshelf root.

    Resolving `figure/A1/` against the root would point at
    `https://www.ncbi.nlm.nih.gov/figure/A1/`, which is a different page.
    """
    html = (
        "<html><body><div id='maincontent'><div class='body-content' itemprop='text'>"
        "<div id='article-1.s1'>"
        "<p><a href='figure/A1/'>Figure 1</a></p>"
        "<p><img src='bin/plot.jpg'/></p>"
        "</div></div></div></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    document = scrape_chapter(client, ChapterRef(accession="NBK111", title="Chapter One"))

    assert document is not None
    assert "https://www.ncbi.nlm.nih.gov/books/NBK111/figure/A1/" in document.content
    assert "https://www.ncbi.nlm.nih.gov/books/NBK111/bin/plot.jpg" in document.content


def test_scrape_chapter_by_url_resolves_relative_links_against_the_chapter_page() -> None:
    """The `--url` path resolves relative links against the same chapter URL."""
    html = (
        "<html><head><title>Chapter One - StatPearls - NCBI Bookshelf</title></head>"
        "<body><div id='maincontent'><div class='body-content' itemprop='text'>"
        "<div id='article-1.s1'><p><a href='figure/A1/'>Figure 1</a></p></div>"
        "</div></div></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    document = scrape_chapter_by_url(client, "https://www.ncbi.nlm.nih.gov/books/NBK111/")

    assert "https://www.ncbi.nlm.nih.gov/books/NBK111/figure/A1/" in document.content


def test_scrape_chapter_skips_and_logs_a_page_without_readable_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A chapter with no `itemprop="text"` body is skipped rather than raised."""
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html><body><p>Not a chapter page.</p></body></html>")
        )
    )

    with caplog.at_level(logging.WARNING):
        document = scrape_chapter(client, ChapterRef(accession="NBK999", title="Missing"))

    assert document is None
    assert "NBK999" in caplog.text


def test_scrape_chapter_skips_and_logs_a_page_that_fails_to_fetch(caplog: pytest.LogCaptureFixture) -> None:
    """One unreachable chapter is skipped rather than aborting a multi-hour crawl."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))

    with caplog.at_level(logging.WARNING):
        document = scrape_chapter(client, ChapterRef(accession="NBK111", title="Chapter One"))

    assert document is None
    assert "NBK111" in caplog.text


def test_scrape_chapter_by_url_extracts_title_and_content() -> None:
    """A chapter URL is resolved to its accession, title, and markdown content."""
    html = """
        <html><head><title>Bilateral Vocal Cord Paralysis - StatPearls - NCBI Bookshelf</title></head>
        <body>
          <div id="maincontent">
            <div class="body-content" itemprop="text"><p>Summary text.</p></div>
          </div>
        </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/books/NBK111/"
        return httpx.Response(200, text=html)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    document = scrape_chapter_by_url(client, "https://www.ncbi.nlm.nih.gov/books/NBK111/", link_mode=LinkMode.KEEP)

    assert document.title == "Bilateral Vocal Cord Paralysis"
    assert document.external_id == "statpearls-NBK111"
    assert document.content == "Summary text."
    assert document.metadata == {"accession": "NBK111", "license": LICENSE, "license_url": LICENSE_URL}


def test_scrape_chapter_by_url_rejects_a_non_bookshelf_url() -> None:
    """URLs without an NBK accession are rejected with an actionable message."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))

    with pytest.raises(StatpearlsFetchError, match="NBK430685"):
        scrape_chapter_by_url(client, "https://www.ncbi.nlm.nih.gov/pubmed/12345")


def test_scrape_chapter_by_url_raises_statpearls_fetch_error_on_fetch_failure() -> None:
    """A --url fetch failure raises this module's error type, not a raw httpx error."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))

    with pytest.raises(StatpearlsFetchError, match="Could not fetch"):
        scrape_chapter_by_url(client, "https://www.ncbi.nlm.nih.gov/books/NBK111/")


def _mock_default_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Patch default_client to a fresh mock-transport client per call.

    scrape_statpearls opens a client for discovery and a separate one inside
    scrape_listing_documents, which closes its own client after use, so this
    must be a factory rather than one shared client instance.
    """
    monkeypatch.setattr(
        "amfv_datasets.scraping.statpearls.default_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://www.ncbi.nlm.nih.gov"),
    )
    monkeypatch.setattr("amfv_datasets.scraping.base.time.sleep", lambda seconds: None)
    monkeypatch.setattr("amfv_datasets.scraping.statpearls.time.sleep", lambda seconds: None)


def test_scrape_statpearls_scrapes_every_discovered_chapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SCRAPERS entry point discovers chapters via E-utilities and scrapes each once."""
    section_payload = _esummary_payload(
        {
            "111": {"chapteraccessionid": "NBK111", "bookinfo": _bookinfo("Chapter One")},
            "112": {"chapteraccessionid": "NBK222", "bookinfo": _bookinfo("Chapter Two")},
        }
    )
    esearch_retstarts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/entrez/eutils/esearch.fcgi":
            retstart = request.url.params["retstart"]
            esearch_retstarts.append(retstart)
            idlist = ["111", "112"] if retstart == "0" else []
            return httpx.Response(200, text=json.dumps({"esearchresult": {"idlist": idlist}}))
        if request.url.path == "/entrez/eutils/esummary.fcgi":
            return httpx.Response(200, text=json.dumps(section_payload))
        if request.url.path in {"/books/NBK111/", "/books/NBK222/"}:
            return httpx.Response(200, text=_CHAPTER_HTML)
        raise AssertionError(f"unexpected request to {request.url}")

    _mock_default_client(monkeypatch, handler)

    documents = list(scrape_statpearls(documents=None).documents)

    assert {document.title for document in documents} == {"Chapter One", "Chapter Two"}
    # Page 1 (retstart 0) finds chapters; page 2 (retstart 200) comes back empty
    # and ends discovery, so exactly two search pages should have been fetched.
    assert esearch_retstarts == ["0", "200"]


def test_scrape_statpearls_skips_a_chapter_that_fails_to_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad chapter mid-crawl is skipped rather than aborting the whole run."""
    section_payload = _esummary_payload(
        {
            "111": {"chapteraccessionid": "NBK111", "bookinfo": _bookinfo("Chapter One")},
            "112": {"chapteraccessionid": "NBK222", "bookinfo": _bookinfo("Chapter Two")},
            "113": {"chapteraccessionid": "NBK333", "bookinfo": _bookinfo("Chapter Three")},
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/entrez/eutils/esearch.fcgi":
            idlist = ["111", "112", "113"] if request.url.params["retstart"] == "0" else []
            return httpx.Response(200, text=json.dumps({"esearchresult": {"idlist": idlist}}))
        if request.url.path == "/entrez/eutils/esummary.fcgi":
            return httpx.Response(200, text=json.dumps(section_payload))
        if request.url.path == "/books/NBK222/":
            return httpx.Response(503)
        return httpx.Response(200, text=_CHAPTER_HTML)

    _mock_default_client(monkeypatch, handler)

    documents = list(scrape_statpearls(documents=None).documents)

    assert {document.title for document in documents} == {"Chapter One", "Chapter Three"}


def test_scrape_statpearls_url_mode_returns_a_single_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """--url delegates to scrape_chapter_by_url and reports a total of one."""
    html = (
        "<html><head><title>Chapter One - StatPearls - NCBI Bookshelf</title></head>"
        "<body><div itemprop='text'><p>ok</p></div></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/books/NBK111/"
        return httpx.Response(200, text=html)

    _mock_default_client(monkeypatch, handler)

    scrape_run = scrape_statpearls(documents=None, url="https://www.ncbi.nlm.nih.gov/books/NBK111/")
    documents = list(scrape_run.documents)

    assert scrape_run.total == 1
    assert len(documents) == 1
    assert documents[0].title == "Chapter One"


def test_cli_dispatches_the_statpearls_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """--source statpearls resolves through the CLI's SCRAPERS registry to this module."""

    def fake_scrape_statpearls(*, documents: int | None, link_mode: LinkMode, url: str | None = None) -> ScrapeRun:
        assert documents == 2
        assert url is None
        document = ScrapedDocument(
            source="statpearls",
            external_id="statpearls-NBK111",
            title="Chapter One",
            url="https://www.ncbi.nlm.nih.gov/books/NBK111/",
            content="content",
        )
        return ScrapeRun([document], total=1)

    monkeypatch.setitem(SCRAPERS, "statpearls", fake_scrape_statpearls)

    result = CliRunner().invoke(app, ["--source", "statpearls", "--documents", "2", "--no-progress"])

    assert result.exit_code == 0
    assert '"external_id": "statpearls-NBK111"' in result.stdout
