"""Tests for WikiDoc scraping helpers."""

import json

import httpx
import pytest

from amfv_datasets.scraping import base
from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.wikidoc import (
    BASE_URL,
    WikiDocFetchError,
    WikiDocPageRef,
    build_wikidoc_article_text,
    list_wikidoc_articles,
    scrape_wikidoc,
    scrape_wikidoc_article,
    wikidoc_ref_from_url,
)


def _client(payload: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api.php"
        assert request.url.params["format"] == "json"
        return httpx.Response(200, text=json.dumps(payload))

    return httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)


def test_list_wikidoc_articles_filters_sandbox_and_template_pseudo_titles() -> None:
    """Mainspace drafts created with literal quotes are not articles."""
    payload = {
        "continue": {"gapcontinue": "Hypertension"},
        "query": {
            "pages": [
                {"pageid": 1, "title": "Hypertelorism", "revisions": [{"timestamp": "2012-03-04T05:06:07Z"}]},
                {"pageid": 2, "title": '"sandbox:A.R"', "revisions": [{"timestamp": "2020-07-17T19:55:56Z"}]},
                {"pageid": 3, "title": '"template:AM"', "revisions": [{"timestamp": "2019-01-01T00:00:00Z"}]},
                {"pageid": 5, "title": "SANDBOX:HT", "revisions": [{"timestamp": "2019-01-01T00:00:00Z"}]},
                {"pageid": 6, "title": "''Asparagaceae''", "revisions": [{"timestamp": "2019-01-01T00:00:00Z"}]},
                {"pageid": 4, "title": "Hypertension", "revisions": [{"timestamp": "2022-08-09T10:11:12Z"}]},
            ]
        },
    }

    refs, token = list_wikidoc_articles(_client(payload))

    assert refs == [
        WikiDocPageRef(title="Hypertelorism", pageid=1, last_revised="2012-03-04T05:06:07Z"),
        WikiDocPageRef(title="Hypertension", pageid=4, last_revised="2022-08-09T10:11:12Z"),
    ]
    assert token == "Hypertension"


def test_list_wikidoc_articles_returns_no_token_when_listing_is_exhausted() -> None:
    """A batch without a continue block ends pagination."""
    page = {"pageid": 9, "title": "Zoonosis", "revisions": [{"timestamp": "2024-01-02T03:04:05Z"}]}
    payload = {"query": {"pages": [page]}}

    refs, token = list_wikidoc_articles(_client(payload))

    assert [ref.title for ref in refs] == ["Zoonosis"]
    assert token is None


def test_list_wikidoc_articles_sorts_by_title() -> None:
    """A generator returns pages unordered; the batch is resorted to match `list=allpages`."""
    payload = {
        "query": {
            "pages": [
                {"pageid": 3, "title": "Sepsis", "revisions": [{"timestamp": "2020-01-01T00:00:00Z"}]},
                {"pageid": 1, "title": "Asthma", "revisions": [{"timestamp": "2020-01-01T00:00:00Z"}]},
                {"pageid": 2, "title": "Hypertension", "revisions": [{"timestamp": "2020-01-01T00:00:00Z"}]},
            ]
        },
    }

    refs, _ = list_wikidoc_articles(_client(payload))

    assert [ref.title for ref in refs] == ["Asthma", "Hypertension", "Sepsis"]


def test_list_wikidoc_articles_tolerates_pages_without_a_readable_revision() -> None:
    """Corrupt pages come back with no `revisions`; they list with an empty timestamp."""
    payload = {
        "query": {
            "pages": [
                {"pageid": 1, "title": "(+)-borneol dehydrogenase"},
                {"pageid": 2, "title": "Hypertension", "revisions": [{"timestamp": "2022-08-09T10:11:12Z"}]},
            ]
        },
    }

    refs, _ = list_wikidoc_articles(_client(payload))

    assert [(ref.title, ref.last_revised) for ref in refs] == [
        ("(+)-borneol dehydrogenase", ""),
        ("Hypertension", "2022-08-09T10:11:12Z"),
    ]


def test_build_wikidoc_article_text_strips_nav_tables_but_keeps_content_tables() -> None:
    """Transcluded `table.infobox` navigation is removed; `wikitable` data is kept."""
    article_html = (
        '<div class="mw-parser-output">'
        '<table class="infobox bordered"><tbody><tr><td>'
        "<b>WikiDoc Resources for Hypertension</b>"
        '<a href="http://www.ncbi.nlm.nih.gov/entrez/query.fcgi">Most recent articles</a>'
        "</td></tr></tbody></table>"
        '<table class="infobox"><tbody><tr><td>Hypertension Microchapters</td></tr></tbody></table>'
        "<p><b>Editor-In-Chief:</b> C. Michael Gibson, M.S., M.D.; "
        "<b>Associate Editor(s)-in-Chief:</b> Priyamvada Singh, M.B.B.S.</p>"
        "<p>Hypertension is persistently elevated arterial blood pressure.</p>"
        '<table class="wikitable"><tbody><tr><td>Stage 2</td><td>140/90 mmHg</td></tr></tbody></table>'
        "</div>"
    )
    payload = {
        "parse": {
            "title": "Hypertension",
            "pageid": 249048,
            "revid": 1744458,
            "text": {"*": article_html},
            "categories": [{"*": "Cardiology"}, {"*": "Up-To-Date"}],
            "sections": [{"line": "Overview"}, {"line": "Causes"}],
        }
    }

    content, section_count, title, metadata = build_wikidoc_article_text(
        _client(payload),
        WikiDocPageRef(title="Hypertension"),
        link_mode=LinkMode.STRIP,
    )

    assert "WikiDoc Resources" not in content
    assert "Microchapters" not in content
    assert "ncbi.nlm.nih.gov" not in content
    assert "persistently elevated arterial blood pressure" in content
    assert "140/90 mmHg" in content
    # The byline moves to metadata rather than being discarded: CC BY-SA needs
    # attribution, but the names should not head the retrievable content.
    assert "Michael Gibson" not in content
    assert metadata["editors"].startswith("Editor-In-Chief:")
    assert "Priyamvada Singh" in metadata["editors"]
    assert section_count == 2
    assert title == "Hypertension"
    assert metadata["revid"] == 1744458
    assert metadata["categories"] == ["Cardiology", "Up-To-Date"]
    assert metadata["license"] == "CC BY-SA 3.0"
    assert metadata["content_length_chars"] == len(content)


def test_scrape_wikidoc_article_normalizes_into_a_scraped_document() -> None:
    """Scraped articles use the shared source/external_id conventions."""
    payload = {
        "parse": {
            "title": "Sepsis",
            "pageid": 100,
            "revid": 200,
            "text": {"*": "<div><p>Sepsis is life-threatening organ dysfunction.</p></div>"},
            "categories": [],
            "sections": [],
        }
    }

    document = scrape_wikidoc_article(_client(payload), WikiDocPageRef(title="Sepsis"))

    assert document.source == "wikidoc"
    assert document.external_id == "wikidoc-Sepsis"
    assert document.url == "https://www.wikidoc.org/index.php/Sepsis"
    assert document.section_count == 1
    assert "life-threatening organ dysfunction" in document.content


def test_build_wikidoc_article_text_records_the_listing_revision_timestamp() -> None:
    """A ref carrying a timestamp needs no extra lookup to record it."""
    payload = {
        "parse": {
            "title": "Sepsis",
            "pageid": 100,
            "revid": 200,
            "text": {"*": "<div><p>Sepsis is life-threatening organ dysfunction.</p></div>"},
            "categories": [],
            "sections": [],
        }
    }
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text=json.dumps(payload))

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)
    ref = WikiDocPageRef(title="Sepsis", pageid=100, last_revised="2017-05-06T07:08:09Z")

    _content, _sections, _title, metadata = build_wikidoc_article_text(client, ref)

    assert metadata["last_revised"] == "2017-05-06T07:08:09Z"
    assert len(calls) == 1


def test_build_wikidoc_article_text_looks_up_a_timestamp_the_ref_lacks() -> None:
    """The `--url` path builds a ref with no timestamp, so it is fetched."""
    parse_payload = {
        "parse": {
            "title": "Sepsis",
            "pageid": 100,
            "revid": 200,
            "text": {"*": "<div><p>Sepsis is life-threatening organ dysfunction.</p></div>"},
            "categories": [],
            "sections": [],
        }
    }
    revisions_payload = {
        "query": {"pages": [{"pageid": 100, "title": "Sepsis", "revisions": [{"timestamp": "2019-02-03T04:05:06Z"}]}]}
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("action") == "parse":
            return httpx.Response(200, text=json.dumps(parse_payload))
        return httpx.Response(200, text=json.dumps(revisions_payload))

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)

    _content, _sections, _title, metadata = build_wikidoc_article_text(client, WikiDocPageRef(title="Sepsis"))

    assert metadata["last_revised"] == "2019-02-03T04:05:06Z"


def test_build_wikidoc_article_text_rejects_an_empty_parse_result() -> None:
    """A missing parse block is an error, not an empty document."""
    with pytest.raises(WikiDocFetchError):
        build_wikidoc_article_text(_client({}), WikiDocPageRef(title="Nonexistent"))


def test_scrape_wikidoc_threads_the_continue_token_across_listing_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pagination advances by token, crosses a batch boundary, and terminates.

    Without the `exhausted` flag the final batch's `None` token would restart the
    listing from the beginning of the alphabet and never stop, so the fake API
    refuses to serve more listing batches than the test expects.
    """
    batches = {
        None: {
            "continue": {"gapcontinue": "Sepsis"},
            "query": {
                "pages": [{"pageid": 1, "title": "Hypertension", "revisions": [{"timestamp": "2022-01-01T00:00:00Z"}]}]
            },
        },
        "Sepsis": {
            "query": {"pages": [{"pageid": 2, "title": "Sepsis", "revisions": [{"timestamp": "2017-05-06T07:08:09Z"}]}]}
        },
    }
    seen_tokens: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if params.get("meta") == "siteinfo":
            return httpx.Response(200, text=json.dumps({"query": {"statistics": {"articles": 2}}}))
        if params.get("generator") == "allpages":
            token = params.get("gapcontinue")
            assert len(seen_tokens) < len(batches), f"listing did not terminate; tokens={seen_tokens}"
            seen_tokens.append(token)
            return httpx.Response(200, text=json.dumps(batches[token]))
        title = params["page"]
        return httpx.Response(
            200,
            text=json.dumps(
                {
                    "parse": {
                        "title": title,
                        "pageid": 0,
                        "revid": 7,
                        "text": {"*": f"<div><p>{title} body text.</p></div>"},
                        "categories": [],
                        "sections": [],
                    }
                }
            ),
        )

    monkeypatch.setattr(
        "amfv_datasets.scraping.wikidoc.default_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL),
    )
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    run = scrape_wikidoc(documents=None)
    documents = list(run.documents)

    assert seen_tokens == [None, "Sepsis"]
    assert [document.external_id for document in documents] == ["wikidoc-Hypertension", "wikidoc-Sepsis"]
    assert run.total == 2


def test_scrape_wikidoc_skips_articles_wikidoc_cannot_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    """One corrupt page does not abort a run over the rest of the listing."""
    listing = {
        "query": {
            "pages": [
                {"pageid": 1, "title": "Hypertelorism"},
                {"pageid": 2, "title": "Sepsis", "revisions": [{"timestamp": "2017-05-06T07:08:09Z"}]},
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if params.get("meta") == "siteinfo":
            return httpx.Response(200, text=json.dumps({"query": {"statistics": {"articles": 2}}}))
        if params.get("generator") == "allpages":
            return httpx.Response(200, text=json.dumps(listing))
        title = params["page"]
        if title == "Hypertelorism":
            # WikiDoc returns HTTP 200 with an error body for corrupt pages.
            return httpx.Response(200, text=json.dumps({"error": {"info": "There is no revision with ID 388595."}}))
        return httpx.Response(
            200,
            text=json.dumps(
                {
                    "parse": {
                        "title": title,
                        "pageid": 2,
                        "revid": 9,
                        "text": {"*": f"<div><p>{title} body text.</p></div>"},
                        "categories": [],
                        "sections": [],
                    }
                }
            ),
        )

    monkeypatch.setattr(
        "amfv_datasets.scraping.wikidoc.default_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL),
    )
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    documents = list(scrape_wikidoc(documents=None).documents)

    assert [document.external_id for document in documents] == ["wikidoc-Sepsis"]


def test_wikidoc_ref_from_url_round_trips_titles_with_spaces() -> None:
    """Article URLs use underscores; titles use spaces."""
    ref = wikidoc_ref_from_url("https://www.wikidoc.org/index.php/Hypertension_in_the_elderly")

    assert ref.title == "Hypertension in the elderly"
    assert ref.slug == "Hypertension_in_the_elderly"
    assert ref.page_url == "https://www.wikidoc.org/index.php/Hypertension_in_the_elderly"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/index.php/Hypertension",
        "https://www.wikidoc.org/wiki/Hypertension",
        "ftp://www.wikidoc.org/index.php/Hypertension",
    ],
)
def test_wikidoc_ref_from_url_rejects_non_article_urls(url: str) -> None:
    """Only wikidoc.org article paths are accepted."""
    with pytest.raises(WikiDocFetchError):
        wikidoc_ref_from_url(url)
