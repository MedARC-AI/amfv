"""Tests for USPSTF scraping helpers."""

import logging

import httpx
import pytest
from typer.testing import CliRunner

from amfv_datasets.scraping.base import ScrapedDocument, ScrapeRun
from amfv_datasets.scraping.cli import SCRAPERS, app
from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.uspstf import (
    ATTRIBUTION,
    BASE_URL,
    CATEGORY_IDS,
    LICENSE,
    LICENSE_URL,
    RecommendationRef,
    UspstfFetchError,
    list_category_recommendations,
    list_recommendations,
    recommendation_slug_from_url,
    scrape_recommendation,
    scrape_recommendation_by_url,
    scrape_uspstf,
)


def _listing_html(rows: str) -> str:
    return f"""
        <html><body><table class="table"><thead><tr>
          <th>Status</th><th>Type</th><th>Year</th><th>Topic Name</th>
          <th>Age Group</th><th>Grade</th><th>Category</th>
        </tr></thead><tbody>{rows}</tbody></table></body></html>
    """


def _listing_row(slug: str, title: str, *, grade: str = "B", year: str = "2024") -> str:
    return f"""
        <tr class="score10">
          <td>Published</td><td>Screening</td><td>{year}</td>
          <td><p><a href='/uspstf/recommendation/{slug}'>{title}</a></p></td>
          <td>Adult, Senior</td><td>{grade}</td><td>Cancer</td>
        </tr>
    """


_RECOMMENDATION_HTML = """
<html><body>
  <article><div class="content">
    <section class="recommendation-statement-intro">
      <h4>Final Recommendation Statement</h4>
      <h1>Breast Cancer: Screening</h1>
      <h4 class="pubdate">April 30, 2024</h4>
      <div class="summary-table field">
        <table><tr><th>Population</th><th>Recommendation</th><th>Grade</th></tr>
        <tr><td>Women aged 40 to 74</td><td>Screen biennially.</td><td>B</td></tr></table>
      </div>
      <div id="bootstrap-panel--4" class="panel">
        <div class="panel-title">Preamble</div>
        <div class="panel-body"><p>Boilerplate repeated on every page.</p></div>
      </div>
      <div id="bootstrap-panel--5" class="panel">
        <div class="panel-title">Importance</div>
        <div class="panel-body"><p>Breast cancer is common.</p></div>
      </div>
      <div id="bootstrap-panel--6" class="panel">
        <div class="panel-title">Practice Considerations</div>
        <div class="panel-body"><p>See <a href="/uspstf/about-uspstf">guidance</a>.</p></div>
      </div>
      <div id="bootstrap-panel--7" class="panel">
        <div class="panel-title">Copyright and Source Information</div>
        <div class="panel-body"><p>Administrative text.</p></div>
      </div>
      <div id="bootstrap-panel--8-collapse" class="panel-collapse collapse">
        <div class="panel-body"><p>Inner collapse wrapper, not a top-level panel.</p></div>
      </div>
    </section>
  </div></article>
</body></html>
"""


def test_list_category_recommendations_parses_rows_into_refs() -> None:
    """A category listing row becomes a ref carrying the row's metadata."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/uspstf/topic_search_results"
        assert request.url.params["topic_status"] == "P"
        assert request.url.params["category[]"] == "15"
        return httpx.Response(
            200, text=_listing_html(_listing_row("breast-cancer-screening", "Breast Cancer: Screening"))
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)

    assert list_category_recommendations(client, 15) == [
        RecommendationRef(
            slug="breast-cancer-screening",
            title="Breast Cancer: Screening",
            status="Published",
            topic_type="Screening",
            year="2024",
            age_group="Adult, Senior",
            grade="B",
            category="Cancer",
        )
    ]


def test_list_category_recommendations_skips_rows_without_a_link() -> None:
    """The site's Hits counter can exceed rendered rows; unlinked rows are unreachable."""
    rows = "<tr><td>Published</td><td>Screening</td><td>2024</td><td><p>No link here</p></td></tr>"

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=_listing_html(rows))))

    assert list_category_recommendations(client, 15) == []


@pytest.mark.parametrize("status", ["Inactive", "Referred", ""], ids=["inactive", "referred", "unlabelled"])
def test_list_category_recommendations_skips_retired_guidance(status: str) -> None:
    """topic_status=P is not honoured under a category filter, so retired rows are dropped."""
    rows = f"""
        <tr><td>{status}</td><td>Counseling</td><td>1996</td>
        <td><p><a href='/uspstf/recommendation/youth-violence-counseling'>Youth Violence</a></p></td>
        <td>Adolescent</td><td></td><td>Injury Prevention</td></tr>
    """

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=_listing_html(rows))))

    assert list_category_recommendations(client, 15) == []


def test_list_category_recommendations_raises_uspstf_fetch_error_on_failure() -> None:
    """A listing failure raises this module's error type, not a raw httpx error."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))

    with pytest.raises(UspstfFetchError, match="Could not fetch the USPSTF listing"):
        list_category_recommendations(client, 15)


def test_list_recommendations_visits_every_category_and_deduplicates() -> None:
    """All twelve categories are walked, and a recommendation in several is kept once."""
    categories_requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        category = request.url.params["category[]"]
        categories_requested.append(category)
        # "a-screening" is listed under two categories; a middle category is
        # empty, and later ones still contribute.
        if category == "15":
            return httpx.Response(200, text=_listing_html(_listing_row("a-screening", "A")))
        if category == "16":
            return httpx.Response(200, text=_listing_html(_listing_row("a-screening", "A")))
        if category == "26":
            return httpx.Response(200, text=_listing_html(_listing_row("z-screening", "Z")))
        return httpx.Response(200, text=_listing_html(""))

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)

    refs = list_recommendations(client, delay_seconds=0)

    assert [ref.slug for ref in refs] == ["a-screening", "z-screening"]
    # A category contributing nothing new must not end discovery early: the
    # last category still has to be reached.
    assert categories_requested == [str(category) for category in CATEGORY_IDS]


def test_list_recommendations_raises_when_no_recommendations_are_found() -> None:
    """An unexpected listing shape fails loudly rather than scraping nothing."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=_listing_html(""))))

    with pytest.raises(UspstfFetchError, match="No published recommendations"):
        list_recommendations(client, delay_seconds=0)


def test_scrape_recommendation_builds_content_and_skips_boilerplate() -> None:
    """The summary table and content panels are kept; boilerplate panels are dropped."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/uspstf/recommendation/breast-cancer-screening"
        return httpx.Response(200, text=_RECOMMENDATION_HTML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ref = RecommendationRef(slug="breast-cancer-screening", title="Breast Cancer: Screening", grade="B", year="2024")

    document = scrape_recommendation(client, ref)

    assert document is not None
    assert document.source == "uspstf"
    assert document.external_id == "uspstf-breast-cancer-screening"
    assert document.url == "https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/breast-cancer-screening"
    # Summary table, Importance, Practice Considerations: preamble and copyright dropped.
    assert document.section_count == 3
    assert "## Recommendation Summary" in document.content
    assert "| Women aged 40 to 74 | Screen biennially. | B |" in document.content
    assert "## Importance" in document.content
    assert "Boilerplate repeated on every page." not in document.content
    assert "Administrative text." not in document.content
    assert "Inner collapse wrapper" not in document.content
    assert document.metadata["grade"] == "B"
    assert document.metadata["published"] == "April 30, 2024"
    assert document.metadata["license"] == LICENSE
    assert document.metadata["license_url"] == LICENSE_URL
    assert document.metadata["attribution"] == ATTRIBUTION


def test_scrape_recommendation_strips_links_in_strip_mode() -> None:
    """Link stripping mode drops markdown link syntax from panel bodies."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=_RECOMMENDATION_HTML)))
    ref = RecommendationRef(slug="breast-cancer-screening", title="Breast Cancer: Screening")

    document = scrape_recommendation(client, ref, link_mode=LinkMode.STRIP)

    assert document is not None
    assert "See guidance." in document.content
    assert "](" not in document.content


def test_scrape_recommendation_skips_and_logs_a_page_that_fails_to_fetch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One unreachable page is skipped rather than aborting the crawl."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))

    with caplog.at_level(logging.WARNING):
        document = scrape_recommendation(client, RecommendationRef(slug="a-screening", title="A"))

    assert document is None
    assert "a-screening" in caplog.text


def test_scrape_recommendation_skips_a_page_without_content(caplog: pytest.LogCaptureFixture) -> None:
    """A page with no summary table or panels is skipped rather than yielding an empty document."""
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html><body><p>Nothing.</p></body></html>")
        )
    )

    with caplog.at_level(logging.WARNING):
        document = scrape_recommendation(client, RecommendationRef(slug="a-screening", title="A"))

    assert document is None
    assert "no readable content" in caplog.text


def test_scrape_recommendation_by_url_reads_the_title_from_the_page() -> None:
    """--url has no listing row, so the title comes from the page heading."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=_RECOMMENDATION_HTML)))

    document = scrape_recommendation_by_url(
        client,
        f"{BASE_URL}/uspstf/recommendation/breast-cancer-screening",
    )

    assert document.title == "Breast Cancer: Screening"
    assert document.external_id == "uspstf-breast-cancer-screening"
    # Listing-only fields are absent for a --url scrape, but licensing still travels.
    assert document.metadata["grade"] == ""
    assert document.metadata["license"] == LICENSE


_RELATIVE_LINK_HTML = """
    <html><body>
      <section class="recommendation-statement-intro"><h1>Breast Cancer: Screening</h1></section>
      <div class="summary-table"><table><tr><td><a href="grade-definitions">Grade B</a></td></tr></table></div>
      <div id="bootstrap-panel-1" class="panel">
        <div class="panel-title">Rationale</div>
        <div class="panel-body"><p><a href="tools/risk-assessment">Risk tool</a></p></div>
      </div>
    </body></html>
"""


def test_scrape_recommendation_resolves_relative_links_against_the_recommendation_page() -> None:
    """Relative links resolve against the recommendation page, not the site root.

    Both conversions are covered: the summary table and a panel body. A
    recommendation URL carries no trailing slash, so a relative reference
    resolves against the directory holding it, `/uspstf/recommendation/`, which
    is what a browser on that page does. Resolving against the site root instead
    would drop that directory and yield `/grade-definitions`.
    """
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=_RELATIVE_LINK_HTML)))

    document = scrape_recommendation(client, RecommendationRef(slug="breast-cancer-screening", title="Breast"))

    assert document is not None
    assert f"{BASE_URL}/uspstf/recommendation/grade-definitions" in document.content
    assert f"{BASE_URL}/uspstf/recommendation/tools/risk-assessment" in document.content


def test_scrape_recommendation_by_url_resolves_relative_links_against_the_recommendation_page() -> None:
    """The --url reader resolves relative links against the same page URL."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=_RELATIVE_LINK_HTML)))

    document = scrape_recommendation_by_url(
        client,
        f"{BASE_URL}/uspstf/recommendation/breast-cancer-screening",
    )

    assert f"{BASE_URL}/uspstf/recommendation/grade-definitions" in document.content
    assert f"{BASE_URL}/uspstf/recommendation/tools/risk-assessment" in document.content


def test_scrape_recommendation_by_url_raises_on_fetch_failure() -> None:
    """A --url fetch failure raises rather than silently yielding nothing."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))

    with pytest.raises(UspstfFetchError, match="Could not fetch"):
        scrape_recommendation_by_url(client, f"{BASE_URL}/uspstf/recommendation/a-screening")


@pytest.mark.parametrize(
    ("url", "expected_message"),
    [
        ("https://example.com/uspstf/recommendation/a", "uspreventiveservicestaskforce.org"),
        (f"{BASE_URL}/uspstf/topic_search_results", "recommendation URL"),
        (f"{BASE_URL}/uspstf/about-uspstf", "recommendation URL"),
        (
            "https://notuspreventiveservicestaskforce.org/uspstf/recommendation/a",
            "uspreventiveservicestaskforce.org",
        ),
        (
            "https://uspreventiveservicestaskforce.org.example.com/uspstf/recommendation/a",
            "uspreventiveservicestaskforce.org",
        ),
    ],
    ids=["wrong-host", "listing-page", "about-page", "lookalike-domain", "domain-as-subdomain"],
)
def test_recommendation_slug_from_url_rejects_non_recommendation_urls(url: str, expected_message: str) -> None:
    """Non-recommendation URLs are rejected with a message naming the expected shape."""
    with pytest.raises(UspstfFetchError, match=expected_message):
        recommendation_slug_from_url(url)


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE_URL}/uspstf/recommendation/breast-cancer-screening",
        "https://uspreventiveservicestaskforce.org/uspstf/recommendation/breast-cancer-screening",
        "https://www.uspreventiveservicestaskforce.org:443/uspstf/recommendation/breast-cancer-screening",
    ],
    ids=["www", "bare-domain", "explicit-port"],
)
def test_recommendation_slug_from_url_accepts_a_recommendation_url(url: str) -> None:
    """The domain itself and its subdomains resolve to the slug, port or not."""
    assert recommendation_slug_from_url(url) == "breast-cancer-screening"


def _mock_default_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Patch default_client to a fresh mock-transport client, and drop all delays.

    scrape_uspstf opens a client for discovery and a separate one inside
    scrape_listing_documents, which closes its own client after use, so this
    must be a factory rather than one shared client instance.
    """
    monkeypatch.setattr(
        "amfv_datasets.scraping.uspstf.default_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL),
    )
    monkeypatch.setattr("amfv_datasets.scraping.uspstf.time.sleep", lambda seconds: None)
    monkeypatch.setattr("amfv_datasets.scraping.base.time.sleep", lambda seconds: None)


def test_scrape_uspstf_walks_categories_and_scrapes_each_recommendation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SCRAPERS entry point discovers across categories and scrapes each once."""
    categories_requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/uspstf/topic_search_results":
            category = request.url.params["category[]"]
            categories_requested.append(category)
            # The same recommendation is listed under the first two categories,
            # and the last category still contributes one of its own.
            rows = _listing_row("a-screening", "A") if category in {"15", "16"} else ""
            if category == "26":
                rows += _listing_row("b-screening", "B")
            return httpx.Response(200, text=_listing_html(rows))
        return httpx.Response(200, text=_RECOMMENDATION_HTML)

    _mock_default_client(monkeypatch, handler)

    scrape_run = scrape_uspstf(documents=None)
    documents = list(scrape_run.documents)

    assert [document.external_id for document in documents] == ["uspstf-a-screening", "uspstf-b-screening"]
    # Discovery reports a real total once the categories have been walked.
    assert scrape_run.total == 2
    # Every category is visited exactly once, in order.
    assert categories_requested == [str(category) for category in CATEGORY_IDS]


def test_scrape_uspstf_skips_a_recommendation_that_fails_to_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad page mid-crawl is skipped rather than aborting the run."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/uspstf/topic_search_results":
            rows = (
                _listing_row("a-screening", "A")
                + _listing_row("bad-screening", "Bad")
                + _listing_row("c-screening", "C")
                if request.url.params["category[]"] == "15"
                else ""
            )
            return httpx.Response(200, text=_listing_html(rows))
        if request.url.path == "/uspstf/recommendation/bad-screening":
            return httpx.Response(503)
        return httpx.Response(200, text=_RECOMMENDATION_HTML)

    _mock_default_client(monkeypatch, handler)

    documents = list(scrape_uspstf(documents=None).documents)

    assert [document.external_id for document in documents] == ["uspstf-a-screening", "uspstf-c-screening"]


def test_cli_dispatches_the_uspstf_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """--source uspstf resolves through the CLI's SCRAPERS registry to this module."""

    def fake_scrape_uspstf(*, documents: int | None, link_mode: LinkMode, url: str | None = None) -> ScrapeRun:
        assert documents == 2
        assert url is None
        document = ScrapedDocument(
            source="uspstf",
            external_id="uspstf-breast-cancer-screening",
            title="Breast Cancer: Screening",
            url=f"{BASE_URL}/uspstf/recommendation/breast-cancer-screening",
            content="content",
        )
        return ScrapeRun([document], total=1)

    monkeypatch.setitem(SCRAPERS, "uspstf", fake_scrape_uspstf)

    result = CliRunner().invoke(app, ["--source", "uspstf", "--documents", "2", "--no-progress"])

    assert result.exit_code == 0
    assert '"external_id": "uspstf-breast-cancer-screening"' in result.stdout
