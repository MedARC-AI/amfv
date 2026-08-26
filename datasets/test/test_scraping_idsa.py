"""Tests for IDSA scraping helpers."""

import inspect

import httpx
import pytest

from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.idsa import (
    BASE_URL,
    LISTING_URL,
    IDSAFetchError,
    IDSAGuidelineRef,
    idsa_ref_from_url,
    list_practice_guidelines,
    scrape_guideline,
    scrape_idsa,
)

_LISTING_CASES = (
    ("current-guideline", "Current Guideline", 2024, ("Current",)),
    ("current-endorsed-guideline", "Current Endorsed Guideline", 2023, ("Current", "Endorsed")),
    ("endorsed-guideline", "Endorsed Guideline", 2022, ("Endorsed",)),
    ("archived-guideline", "Archived Guideline", 2021, ("Archived",)),
    ("development-guideline", "Development Guideline", 2020, ("In Development",)),
    ("archived-development-guideline", "Archived Development Guideline", 2019, ("Archived", "In Development")),
)


def test_list_practice_guidelines_parses_listing_fields() -> None:
    """The IDSA listing parser normalizes title, URL, year, and statuses."""
    listing = list_practice_guidelines(_listing_client())

    assert listing.total == 3
    assert listing.refs == [
        IDSAGuidelineRef(
            title="Current Guideline",
            slug="current-guideline",
            page_url="https://www.idsociety.org/practice-guideline/current-guideline/",
            year=2024,
            statuses=("Current",),
        ),
        IDSAGuidelineRef(
            title="Current Endorsed Guideline",
            slug="current-endorsed-guideline",
            page_url="https://www.idsociety.org/practice-guideline/current-endorsed-guideline/",
            year=2023,
            statuses=("Current", "Endorsed"),
        ),
        IDSAGuidelineRef(
            title="Archived Guideline",
            slug="archived-guideline",
            page_url="https://www.idsociety.org/practice-guideline/archived-guideline/",
            year=2021,
            statuses=("Archived",),
        ),
    ]


def test_list_practice_guidelines_filters_unpublished_statuses() -> None:
    """Current and archived records are kept while unpublished records are excluded."""
    listing = list_practice_guidelines(_listing_client())

    assert [ref.slug for ref in listing.refs] == [
        "current-guideline",
        "current-endorsed-guideline",
        "archived-guideline",
    ]


def test_scrape_idsa_uses_the_shared_scraper_signature() -> None:
    """The registered IDSA entry point accepts only the shared scraper options."""
    assert tuple(inspect.signature(scrape_idsa).parameters) == ("documents", "link_mode", "url")


def test_idsa_ref_from_url_normalizes_practice_guideline_url() -> None:
    """IDSA practice guideline URLs are normalized to canonical refs."""
    assert idsa_ref_from_url("https://www.idsociety.org/practice-guideline/Current-Guideline/?utm=1") == (
        IDSAGuidelineRef(
            title="Current Guideline",
            slug="current-guideline",
            page_url="https://www.idsociety.org/practice-guideline/current-guideline/",
            year=None,
            statuses=(),
        )
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/practice-guideline/current-guideline/",
        "https://www.idsociety.org/news/current-guideline/",
    ],
    ids=["wrong-domain", "wrong-path"],
)
def test_idsa_ref_from_url_rejects_non_guideline_urls(url: str) -> None:
    """Only IDSA practice guideline URLs are accepted."""
    with pytest.raises(IDSAFetchError):
        idsa_ref_from_url(url)


def test_scrape_guideline_extracts_content_and_link_metadata() -> None:
    """Guideline page content is converted to markdown and noisy UI is stripped."""
    document = scrape_guideline(_guideline_client(_guideline_html()), _ref())

    assert document.title == "Current Guideline"
    assert document.section_count == 1
    assert "# Current Guideline" in document.content
    assert "## Abstract" in document.content
    assert "## Recommendations" in document.content
    assert "[Download PDF](https://academic.oup.com/example.pdf)" in document.content
    assert "Recommendation text with [evidence](https://doi.org/10.1093/cid/example)." in document.content
    assert "Back to top" not in document.content
    assert "Table of Contents" not in document.content
    assert "https://www.idsociety.org#abstract" not in document.content
    assert document.metadata["external_links"] == [
        "https://academic.oup.com/example.pdf",
        "https://doi.org/10.1093/cid/example",
    ]
    assert document.metadata["pdf_links"] == ["https://academic.oup.com/example.pdf"]
    assert document.metadata["statuses"] == ["Current"]
    assert document.metadata["content_length_chars"] == len(document.content)
    assert document.metadata["quality_flags"] == ["short_content"]
    assert "###" not in document.content


def test_scrape_guideline_strips_links_when_requested() -> None:
    """The IDSA scraper honors the shared link-mode option."""
    document = scrape_guideline(_guideline_client(_guideline_html()), _ref(), link_mode=LinkMode.STRIP)

    assert "Recommendation text with evidence." in document.content
    assert "[evidence]" not in document.content


def test_scrape_guideline_merges_listing_and_page_statuses() -> None:
    """Listing and page statuses are merged once in their source order."""
    document = scrape_guideline(
        _guideline_client(_guideline_html(statuses=("Current", "In Development"))),
        _ref(statuses=("Current", "Endorsed")),
    )

    assert document.metadata["statuses"] == ["Current", "Endorsed", "In Development"]
    assert document.metadata["quality_flags"] == ["short_content"]


def test_scrape_idsa_returns_normalized_document() -> None:
    """The IDSA scraper returns normalized scraped documents."""
    transport = httpx.MockTransport(_scrape_idsa_handler)

    class ClientFactory:
        def __call__(self) -> httpx.Client:
            return httpx.Client(transport=transport)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("amfv_datasets.scraping.idsa.default_client", ClientFactory())
        scrape_run = scrape_idsa(documents=1)
        documents = list(scrape_run.documents)

    assert scrape_run.total == 1
    assert len(documents) == 1
    assert documents[0].source == "idsa"
    assert documents[0].external_id == "idsa-current-guideline"
    assert documents[0].metadata["statuses"] == ["Current"]
    assert documents[0].metadata["year"] == 2024
    assert documents[0].metadata["slug"] == "current-guideline"
    assert documents[0].metadata["listing_url"] == LISTING_URL


@pytest.mark.parametrize(
    ("paragraph", "statuses", "expected_flags"),
    [
        ("Useful abstract.", ("Current",), ["short_content"]),
        ("Recommendation text. " * 700, ("Current",), []),
        ("Useful abstract.", ("Archived",), ["short_content", "outdated"]),
        ("Recommendation text. " * 700, ("Archived",), ["outdated"]),
        ("Useful abstract.", ("In Development",), ["short_content"]),
    ],
    ids=["current-short", "current-long", "archived-short", "archived-long", "in-development"],
)
def test_scrape_guideline_sets_content_quality_flags(
    paragraph: str,
    statuses: tuple[str, ...],
    expected_flags: list[str],
) -> None:
    """Content length and archival state produce independent quality flags."""
    document = scrape_guideline(
        _guideline_client(_guideline_html(paragraph=paragraph, statuses=statuses)),
        _ref(statuses=statuses),
    )

    assert document.metadata["content_length_chars"] == len(document.content)
    assert document.metadata["statuses"] == list(statuses)
    assert document.metadata["quality_flags"] == expected_flags


def test_scrape_idsa_url_uses_page_status_for_outdated_flag() -> None:
    """A directly supplied archived URL derives its status from the page."""
    url = "https://www.idsociety.org/practice-guideline/archived-guideline/"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            text=_guideline_html(title="Archived Guideline", statuses=("Archived",)),
            request=request,
        )
    )

    class ClientFactory:
        def __call__(self) -> httpx.Client:
            return httpx.Client(transport=transport)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("amfv_datasets.scraping.idsa.default_client", ClientFactory())
        documents = list(scrape_idsa(documents=1, url=url).documents)

    assert len(documents) == 1
    assert documents[0].metadata["statuses"] == ["Archived"]
    assert documents[0].metadata["quality_flags"] == ["short_content", "outdated"]


def _listing_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == LISTING_URL
        return httpx.Response(200, text=_listing_html())

    return httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)


def _guideline_client(content_html: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://www.idsociety.org/practice-guideline/current-guideline/"
        return httpx.Response(200, text=content_html)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)


def _scrape_idsa_handler(request: httpx.Request) -> httpx.Response:
    if str(request.url) == LISTING_URL:
        return httpx.Response(200, text=_listing_html())
    if str(request.url) == "https://www.idsociety.org/practice-guideline/current-guideline/":
        return httpx.Response(200, text=_guideline_html())
    raise AssertionError(f"Unexpected URL: {request.url}")


def _listing_html() -> str:
    items = "\n".join(
        _listing_item(slug=slug, title=title, year=year, statuses=statuses)
        for slug, title, year, statuses in _LISTING_CASES
    )
    return f'<html><div class="alpha-listing"><ul class="list-pages">{items}</ul></div></html>'


def _listing_item(*, slug: str, title: str, year: int, statuses: tuple[str, ...]) -> str:
    categories = "".join(f'<li class="category-dot">{status}</li>' for status in statuses)
    return f"""
        <li>
          <ul class="list-pages__categories">{categories}</ul>
          <a class="list-pages__link" href="/practice-guideline/{slug}/">{title}</a>
          <span class="list-pages__year">{year}</span>
        </li>
    """


def _guideline_html(
    *,
    title: str = "Current Guideline",
    paragraph: str = "Useful abstract.",
    statuses: tuple[str, ...] = ("Current",),
) -> str:
    status_html = "".join(f'<span class="status">{status.upper()}</span>' for status in statuses)
    return f"""
        <html>
          <title>{title} | IDSA</title>
          <div class="standardpage-col-left">
            <nav>Navigation noise</nav>
            <p>Intro column noise.</p>
          </div>
          <div class="standardpage-col-left">
            <section class="status-section">{status_html}</section>
            <p><a href="https://academic.oup.com/example.pdf">Download PDF</a></p>
            <h1>{title}</h1>
            <h3>Table of Contents</h3>
            <ul>
              <li><a href="#abstract">Abstract</a></li>
              <li><a href="#recommendations">Recommendations</a></li>
            </ul>
            <p class="social">Share this page</p>
            <p>Published January 1, 2024</p>
            <h2>Abstract</h2>
            <p>{paragraph}</p>
            <h2>Recommendations</h2>
            <p>Recommendation text with <a href="https://doi.org/10.1093/cid/example">evidence</a>.</p>
            <p><a href="mailto:author@example.org">Email the author</a></p>
            <p><a href="tel:+12025550123">Call the author</a></p>
            <p><a href="javascript:void(0)">Open a script</a></p>
            <p>Back to top</p>
            <h3><a id="empty-heading"></a></h3>
          </div>
        </html>
    """


def _ref(*, statuses: tuple[str, ...] = ("Current",)) -> IDSAGuidelineRef:
    return IDSAGuidelineRef(
        title="Current Guideline",
        slug="current-guideline",
        page_url="https://www.idsociety.org/practice-guideline/current-guideline/",
        year=2024,
        statuses=statuses,
    )
