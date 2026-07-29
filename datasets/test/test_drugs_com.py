"""Tests for Drugs.com scraping helpers."""

import httpx
import pytest

from amfv_datasets.scraping import base
from amfv_datasets.scraping.drugs_com import (
    BASE_URL,
    DrugsComFetchError,
    DrugsComPageRef,
    build_drugs_com_article_text,
    drugscom_ref_from_url,
    list_drug_refs,
    list_two_letter_pages,
    scrape_drugs_com,
    scrape_drugs_com_page,
)
from amfv_datasets.scraping.html import LinkMode

_NAV_CHROME = """
<header><nav>
 <a href="/alpha/a.html">a</a><a href="/alpha/b.html">b</a><a href="/alpha/z.html">z</a>
 <a href="/alpha/0-9.html">0-9</a>
</nav></header>
"""


def _html_client(routes: dict[str, str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path

        if path not in routes:
            return httpx.Response(404, text="not found")

        return httpx.Response(200, text=routes[path])

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=BASE_URL,
    )


def test_list_two_letter_pages_returns_only_linked_pairs() -> None:
    """Letter pairs with no drugs render as unlinked text and are skipped."""
    page = (
        _NAV_CHROME + '<div id="content"><h1>Drugs: Z</h1>'
        '<ul><li><a href="/alpha/za.html">Za</a></li>'
        "<li>Zb</li>"
        '<li><a href="/alpha/zc.html">Zc</a></li></ul></div>'
    )

    paths = list_two_letter_pages(
        _html_client({"/alpha/z.html": page}),
        "z",
    )

    assert paths == [
        "/alpha/za.html",
        "/alpha/zc.html",
    ]


def test_list_drug_refs_covers_every_namespace_dedupes_and_skips_pro_toggle() -> None:
    """Every drug namespace is picked up; chrome and pro variants are not."""
    page = (
        _NAV_CHROME + '<div id="content"><h1>Drugs: Za</h1>'
        "<ul>"
        '<li><a href="/alpha/za.html?pro=1">Professional Monographs</a></li>'
        '<li><a href="/zaltrap.html">Zaltrap</a></li>'
        '<li><a href="/mtm/zaditor.html">Zaditor</a></li>'
        '<li><a href="/cons/zagam.html">Zagam</a></li>'
        '<li><a href="/pro/zafemy.html">Zafemy</a></li>'
        '<li><a href="/monograph/zanidatamab-hrii.html">Zanidatamab-hrii</a></li>'
        '<li><a href="/zaltrap.html">Zaltrap</a></li>'
        '<li><a href="/drug_information.html">Browse all medications</a></li>'
        "</ul></div>"
    )

    refs = list_drug_refs(
        _html_client({"/alpha/za.html": page}),
        "/alpha/za.html",
    )

    assert refs == [
        DrugsComPageRef(slug="zaltrap", name="Zaltrap"),
        DrugsComPageRef(slug="mtm/zaditor", name="Zaditor"),
        DrugsComPageRef(slug="cons/zagam", name="Zagam"),
        DrugsComPageRef(slug="pro/zafemy", name="Zafemy"),
        DrugsComPageRef(
            slug="monograph/zanidatamab-hrii",
            name="Zanidatamab-hrii",
        ),
    ]


def test_build_drugs_com_article_text_strips_chrome_and_boilerplate_sections() -> None:
    """Nav/footer chrome, trailing resource blocks, and widgets are removed."""
    page = (
        _NAV_CHROME + '<div id="content">'
        "<h1>Zoloft</h1>"
        "<p>Medically reviewed by Melisa Puckey, BPharm. Last updated on Aug 23, 2023.</p>"
        "<h2>What is Zoloft?</h2>"
        "<p>Zoloft is an antidepressant that belongs to a group of drugs called SSRIs.</p>"
        "<h2>Related/similar drugs</h2>"
        "<p>Vraylar is a once a day antipsychotic medication.</p>"
        "<h2>What other drugs will affect Zoloft?</h2>"
        "<p>Zoloft can cause a serious heart problem.</p>"
        "<h2>More about Zoloft (sertraline)</h2>"
        "<ul><li>Check interactions</li></ul>"
        "<p>Copyright 1996-2026 Cerner Multum, Inc. Version: 29.01.</p>"
        "</div>"
        "<footer>Drugs.com Mobile App download links</footer>"
    )

    content, title, metadata = build_drugs_com_article_text(
        _html_client({"/zoloft.html": page}),
        DrugsComPageRef(slug="zoloft", name="Zoloft"),
        link_mode=LinkMode.STRIP,
    )

    assert title == "Zoloft"
    assert "Mobile App" not in content
    assert "Vraylar" not in content
    assert "Related/similar drugs" not in content
    assert "Check interactions" not in content
    assert "More about Zoloft" not in content
    assert "Copyright" not in content
    assert "belongs to a group of drugs called SSRIs" in content
    assert "serious heart problem" in content
    assert metadata["reviewed_by"] == "Melisa Puckey, BPharm"
    assert metadata["last_updated"] == "Aug 23, 2023"
    assert metadata["data_source"] == "Cerner Multum, Inc"
    assert metadata["namespace"] == "consumer"
    assert metadata["license"] == "proprietary"


def test_build_drugs_com_article_text_handles_a_linked_reviewer_byline() -> None:
    """Linked reviewer names are extracted correctly into metadata."""
    page = (
        _NAV_CHROME + '<div id="content">'
        "<h1>Zoloft</h1>"
        '<p>Medically reviewed by <a href="https://www.drugs.com/support/editor/21/melisa-puckey-bpharm.html">'
        "Melisa Puckey, BPharm</a>. Last updated on Aug 23, 2023.</p>"
        "<h2>What is Zoloft?</h2>"
        "<p>Zoloft is an antidepressant.</p>"
        "</div>"
    )

    content, _, metadata = build_drugs_com_article_text(
        _html_client({"/zoloft.html": page}),
        DrugsComPageRef(slug="zoloft", name="Zoloft"),
    )

    assert metadata["reviewed_by"] == "Melisa Puckey, BPharm"
    assert metadata["last_updated"] == "Aug 23, 2023"
    assert "Medically reviewed by" not in content
    assert "bpharm.html" not in content


def test_scrape_drugs_com_page_normalizes_into_a_scraped_document() -> None:
    """Scraped pages follow shared source and external ID conventions."""
    page = _NAV_CHROME + '<div id="content"><h1>Zafirlukast</h1><p>Zafirlukast treats asthma.</p></div>'

    document = scrape_drugs_com_page(
        _html_client({"/mtm/zafirlukast.html": page}),
        DrugsComPageRef(
            slug="mtm/zafirlukast",
            name="Zafirlukast",
        ),
    )

    assert document.source == "drugscom"
    assert document.external_id == "drugscom-mtm-zafirlukast"
    assert document.url == "https://www.drugs.com/mtm/zafirlukast.html"
    assert "Zafirlukast treats asthma" in document.content
    assert document.metadata["namespace"] == "mtm"


def test_build_drugs_com_article_text_rejects_a_content_free_page() -> None:
    """Pages with no remaining content raise an error."""
    page = _NAV_CHROME + "<footer>only chrome here</footer>"

    with pytest.raises(DrugsComFetchError):
        build_drugs_com_article_text(
            _html_client({"/nonexistent.html": page}),
            DrugsComPageRef(
                slug="nonexistent",
                name="Nonexistent",
            ),
        )


def test_drugscom_ref_from_url_parses_bare_and_namespaced_urls() -> None:
    """Namespace prefixes fold into slug values."""
    assert drugscom_ref_from_url("https://www.drugs.com/zoloft.html") == DrugsComPageRef(
        slug="zoloft",
        name="zoloft",
    )

    assert drugscom_ref_from_url("https://www.drugs.com/pro/zafemy.html") == DrugsComPageRef(
        slug="pro/zafemy",
        name="zafemy",
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/zoloft.html",
        "https://www.drugs.com/support/about.html",
        "https://www.drugs.com/drug_information.html",
        "ftp://www.drugs.com/zoloft.html",
    ],
)
def test_drugscom_ref_from_url_rejects_non_drug_urls(url: str) -> None:
    """Non-Drugs.com hosts and unrelated pages are rejected."""
    with pytest.raises(DrugsComFetchError):
        drugscom_ref_from_url(url)


def test_scrape_drugs_com_threads_two_level_pagination_with_a_no_subpage_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pagination walks letter pages, subpages, and direct drug listings."""
    a_index = _NAV_CHROME + '<div id="content"><h1>Drugs: A</h1><ul><li><a href="/alpha/aa.html">Aa</a></li></ul></div>'

    aa_listing = _NAV_CHROME + '<div id="content"><h1>Drugs: Aa</h1><ul><li><a href="/aaa.html">Aaa</a></li></ul></div>'

    aaa_page = _NAV_CHROME + '<div id="content"><h1>Aaa</h1><p>Aaa is a fictional drug.</p></div>'

    digits_index = (
        _NAV_CHROME + '<div id="content"><h1>Drugs: 0-9</h1><ul><li><a href="/5-htp.html">5-HTP</a></li></ul></div>'
    )

    five_htp_page = _NAV_CHROME + '<div id="content"><h1>5-HTP</h1><p>5-HTP is a supplement.</p></div>'

    routes = {
        "/alpha/a.html": a_index,
        "/alpha/aa.html": aa_listing,
        "/aaa.html": aaa_page,
        "/alpha/0-9.html": digits_index,
        "/5-htp.html": five_htp_page,
    }

    empty_index_template = _NAV_CHROME + '<div id="content"><h1>Drugs: {letter}</h1></div>'

    for letter in "bcdefghijklmnopqrstuvwxy":
        routes[f"/alpha/{letter}.html"] = empty_index_template.format(letter=letter)

    routes["/alpha/z.html"] = empty_index_template.format(letter="z")

    monkeypatch.setattr(
        "amfv_datasets.scraping.drugs_com.default_client",
        lambda: _html_client(routes),
    )

    monkeypatch.setattr(
        base.time,
        "sleep",
        lambda _seconds: None,
    )

    documents = list(scrape_drugs_com(documents=None).documents)

    assert [document.external_id for document in documents] == [
        "drugscom-aaa",
        "drugscom-5-htp",
    ]
    assert "Aaa is a fictional drug" in documents[0].content
    assert "5-HTP is a supplement" in documents[1].content


def test_scrape_drugs_com_skips_pages_it_cannot_serve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed drug page does not stop the crawler."""
    a_index = _NAV_CHROME + '<div id="content"><h1>Drugs: A</h1><ul><li><a href="/alpha/aa.html">Aa</a></li></ul></div>'

    aa_listing = (
        _NAV_CHROME + '<div id="content"><h1>Drugs: Aa</h1>'
        '<ul><li><a href="/abraxane.html">Abraxane</a></li>'
        '<li><a href="/aaaaunobtainium.html">Aaaaunobtainium</a></li></ul></div>'
    )

    abraxane_page = _NAV_CHROME + '<div id="content"><h1>Abraxane</h1><p>Abraxane treats cancer.</p></div>'

    routes = {
        "/alpha/a.html": a_index,
        "/alpha/aa.html": aa_listing,
        "/abraxane.html": abraxane_page,
    }

    empty_index_template = _NAV_CHROME + '<div id="content"><h1>Drugs: {letter}</h1></div>'

    for letter in list("bcdefghijklmnopqrstuvwxyz") + ["0-9"]:
        routes[f"/alpha/{letter}.html"] = empty_index_template.format(letter=letter)

    monkeypatch.setattr(
        "amfv_datasets.scraping.drugs_com.default_client",
        lambda: _html_client(routes),
    )

    monkeypatch.setattr(
        base.time,
        "sleep",
        lambda _seconds: None,
    )

    documents = list(scrape_drugs_com(documents=None).documents)

    assert [document.external_id for document in documents] == ["drugscom-abraxane"]
