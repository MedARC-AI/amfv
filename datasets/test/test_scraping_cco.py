"""Tests for CCO scraping helpers."""

from types import SimpleNamespace

import pytest
from amfv_datasets.scraping.cco import (
    BASE_URL,
    CcoFetchError,
    GuidelineRef,
    _discover_guidelines_with_retries,
    _scrape_guideline_or_placeholder,
    guideline_ref_from_url,
    list_cco_guidelines,
    playwright_fetch,
    scrape_cco_guideline,
)
from amfv_datasets.scraping.html import LinkMode
from playwright.sync_api import Error as PlaywrightError

_GUIDELINE_HTML = """
<html>
  <h1>Pandemic Planning Clinical Guideline for Patients with Cancer</h1>
  <p>Version: 2 Dec 2021</p>
  <p>Type of Content: Guidelines &amp; Advice</p>
  <p>Document Status: Current</p>
  <p>Authors: CCO Pandemic Plan Review Group</p>
  <h2>Guideline Objective</h2>
  <p>To provide recommendations for a systematic approach to cancer care during a pandemic.</p>
  <h2>Patient Population</h2>
  <p>Patients with cancer who require treatment in regional cancer centres.</p>
  <h2>Intended Guideline Users</h2>
  <p>Healthcare providers and health system planners in Ontario's cancer system.</p>
  <a href="/en/file/64736/download?token=abc">Full Report (PDF) (376.82 KB)</a>
</html>
"""


def _fake_fetch(pages: dict[str, str]):
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        return pages[url]

    fetch.calls = calls
    return fetch


def test_guideline_ref_from_url_accepts_detail_url() -> None:
    """CCO guideline URLs are normalized to a canonical reference."""
    ref = guideline_ref_from_url("https://www.cancercareontario.ca/en/guidelines-advice/types-of-cancer/64736/")
    assert ref == GuidelineRef(id="64736", page_url=f"{BASE_URL}/en/guidelines-advice/types-of-cancer/64736")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/en/guidelines-advice/types-of-cancer/64736",
        "https://www.cancercareontario.ca/en/guidelines-advice/types-of-cancer/breast",
    ],
    ids=["wrong-domain", "category-not-detail-page"],
)
def test_guideline_ref_from_url_rejects_invalid_url(url: str) -> None:
    """Non-CCO domains and category (non-numeric) URLs are rejected."""
    with pytest.raises(CcoFetchError):
        guideline_ref_from_url(url)


def test_scrape_cco_guideline_parses_fields_and_sections() -> None:
    """Guideline metadata fields and narrative sections are extracted from the page."""
    ref = GuidelineRef(id="64736", page_url=f"{BASE_URL}/en/guidelines-advice/types-of-cancer/64736")
    fetch = _fake_fetch({ref.page_url: _GUIDELINE_HTML})

    document = scrape_cco_guideline(fetch, ref)

    assert document.source == "cco"
    assert document.external_id == "cco-64736"
    assert document.title == "Pandemic Planning Clinical Guideline for Patients with Cancer"
    assert document.metadata["version"] == "2 Dec 2021"
    assert document.metadata["type_of_content"] == "Guidelines & Advice"
    assert document.metadata["document_status"] == "Current"
    assert document.metadata["authors"] == "CCO Pandemic Plan Review Group"
    assert document.metadata["pdf_url"] == f"{BASE_URL}/en/file/64736/download?token=abc"
    assert document.content == (
        "## Guideline Objective\n\n"
        "To provide recommendations for a systematic approach to cancer care during a pandemic.\n\n"
        "## Patient Population\n\n"
        "Patients with cancer who require treatment in regional cancer centres.\n\n"
        "## Intended Guideline Users\n\n"
        "Healthcare providers and health system planners in Ontario's cancer system.\n\n"
        f"Full report: [Full Report (PDF) (376.82 KB)]({BASE_URL}/en/file/64736/download?token=abc)"
    )


def test_scrape_cco_guideline_strips_pdf_link_text_in_strip_mode() -> None:
    """Strip link mode keeps the PDF label without a markdown link."""
    ref = GuidelineRef(id="64736", page_url=f"{BASE_URL}/en/guidelines-advice/types-of-cancer/64736")
    fetch = _fake_fetch({ref.page_url: _GUIDELINE_HTML})

    document = scrape_cco_guideline(fetch, ref, link_mode=LinkMode.STRIP)

    assert "[Full Report" not in document.content
    assert document.content.endswith("Full Report (PDF) (376.82 KB)")


def test_scrape_cco_guideline_falls_back_to_metadata_for_thin_pages() -> None:
    """Pages with only bibliographic metadata (no narrative sections, no PDF link) still scrape."""
    thin_html = """
    <html>
      <h1>Fecal Immunochemical Tests Compared With Guaiac Fecal Occult Blood Tests</h1>
      <p>ID:</p>
      <p>15-8</p>
      <p>Nov 2011</p>
      <p>Type of Content: Guidelines &amp; Advice, Clinical</p>
      <p>Document Status: Archived</p>
      <p>Authors: FIT Guidelines Expert Panel</p>
    </html>
    """
    ref = GuidelineRef(id="2116", page_url=f"{BASE_URL}/en/guidelines-advice/types-of-cancer/2116")
    fetch = _fake_fetch({ref.page_url: thin_html})

    document = scrape_cco_guideline(fetch, ref)

    assert document.metadata["id"] == "15-8 Nov 2011"
    assert document.metadata["document_status"] == "Archived"
    assert "pdf_url" not in document.metadata
    assert "ID: 15-8 Nov 2011" in document.content
    assert "Document Status: Archived" in document.content


def test_scrape_cco_guideline_handles_archived_pages_with_bibliography_outside_main() -> None:
    """Archived pages: broken <title>, metadata outside role=main, commented-out body."""
    html = """
    <html>
      <head><title>| Cancer Care Ontario</title></head>
      <body>
        <div class="ts-breaker">
          <h1>Diagnostic Imaging in Breast Cancer</h1>
          <div class="ts-p"><strong class="ts-label">ID:</strong> <span>DIBrCa</span> <span>Apr 2012</span></div>
          <div class="ts-p"><strong>Type of Content: </strong><span>Guidelines &amp; Advice, Clinical</span></div>
          <div class="ts-p"><strong>Document Status: </strong><span>Archived</span></div>
          <div class="ts-p"><strong>Authors: </strong><span>Diagnostic Imaging Guidelines Panel</span></div>
        </div>
        <div id="content" role="main">
          <!--
          <h2>Abstract</h2>
          <div class="guideline-file"><a href="/en/file/3376/download?token=x">Full Report (PDF)</a></div>
          -->
        </div>
        <footer>Feedback CCO tips Understanding CCO Disclaimer: informational purposes only.</footer>
      </body>
    </html>
    """
    ref = GuidelineRef(id="2561", page_url=f"{BASE_URL}/en/guidelines-advice/types-of-cancer/2561")
    fetch = _fake_fetch({ref.page_url: html})

    document = scrape_cco_guideline(fetch, ref)

    assert document.title == "Diagnostic Imaging in Breast Cancer"
    assert document.metadata["id"] == "DIBrCa Apr 2012"
    assert document.metadata["document_status"] == "Archived"
    assert document.metadata["authors"] == "Diagnostic Imaging Guidelines Panel"
    assert "pdf_url" not in document.metadata
    assert "Feedback" not in document.content
    assert "Document Status: Archived" in document.content


def test_scrape_cco_guideline_falls_back_to_full_page_when_scoping_misses_content() -> None:
    """If role=main matches an empty/irrelevant element, parsing falls back to the whole page."""
    html_with_misleading_main = """
    <html>
      <div role="main"><p>Unrelated widget with no guideline content.</p></div>
      <div class="guideline-detail">
        <h1>A Real Guideline</h1>
        <p>Version: 1</p>
        <h2>Guideline Objective</h2>
        <p>Do the thing.</p>
      </div>
    </html>
    """
    ref = GuidelineRef(id="301", page_url=f"{BASE_URL}/en/guidelines-advice/types-of-cancer/301")
    fetch = _fake_fetch({ref.page_url: html_with_misleading_main})

    document = scrape_cco_guideline(fetch, ref)

    assert document.metadata["version"] == "1"
    assert "## Guideline Objective\n\nDo the thing." in document.content


def test_scrape_cco_guideline_excludes_footer_boilerplate_from_last_section() -> None:
    """Nav/footer text outside the main content region doesn't bleed into the last section."""
    html_with_chrome = """
    <html>
      <nav>Home Types of Cancer Cancer Treatments</nav>
      <div role="main">
        <h1>Exercise for People with Cancer</h1>
        <p>Version: 3</p>
        <h2>Intended Guideline Users</h2>
        <p>Oncologists and exercise consultants.</p>
        <h2>Research Question(s)</h2>
        <p>Does exercise improve quality of life?</p>
      </div>
      <footer>
        Feedback CCO tips Understanding CCO Disclaimer: informational purposes only.
      </footer>
    </html>
    """
    ref = GuidelineRef(id="201", page_url=f"{BASE_URL}/en/guidelines-advice/types-of-cancer/201")
    fetch = _fake_fetch({ref.page_url: html_with_chrome})

    document = scrape_cco_guideline(fetch, ref)

    assert "Feedback" not in document.content
    assert "Disclaimer" not in document.content
    assert "## Intended Guideline Users\n\nOncologists and exercise consultants." in document.content
    assert "## Research Question(s)\n\nDoes exercise improve quality of life?" in document.content


class _FakePage:
    def __init__(self, html: str, *, fail_times: int = 0) -> None:
        self._html = html
        self._fail_times = fail_times
        self._content_calls = 0

    def goto(self, url: str, wait_until: str | None = None, timeout: int | None = None) -> SimpleNamespace:
        return SimpleNamespace(status=200)

    def wait_for_timeout(self, milliseconds: int) -> None:
        pass

    def wait_for_load_state(self, state: str | None = None, timeout: int | None = None) -> None:
        pass

    def title(self) -> str:
        return "A Guideline | Cancer Care Ontario"

    def content(self) -> str:
        self._content_calls += 1
        if self._content_calls <= self._fail_times:
            raise PlaywrightError("Page.content: Unable to retrieve content because the page is navigating.")
        return self._html


def test_playwright_fetch_retries_through_a_transient_navigation_race() -> None:
    """A transient 'page is navigating' error from page.content() is retried, not raised."""
    page = _FakePage("<html>ok</html>", fail_times=1)

    result = playwright_fetch(page, f"{BASE_URL}/en/guidelines-advice/types-of-cancer/1")

    assert result == "<html>ok</html>"


def test_playwright_fetch_raises_after_exhausting_retries() -> None:
    """A persistently failing page eventually raises rather than retrying forever."""
    page = _FakePage("<html>ok</html>", fail_times=99)

    with pytest.raises(CcoFetchError):
        playwright_fetch(page, f"{BASE_URL}/en/guidelines-advice/types-of-cancer/1")


def test_discover_guidelines_with_retries_retries_a_suspiciously_thin_result() -> None:
    """A thin first discovery (likely a load failure) is retried until a full result comes back."""
    thin_html = f'<html><a href="{BASE_URL}/en/guidelines-advice/types-of-cancer/1">One</a></html>'
    full_html = (
        "<html>"
        + "".join(f'<a href="{BASE_URL}/en/guidelines-advice/types-of-cancer/{i}">G{i}</a>' for i in range(100, 160))
        + "</html>"
    )
    calls = {"count": 0}

    def fetch(url: str) -> str:
        calls["count"] += 1
        return thin_html if calls["count"] == 1 else full_html

    refs = _discover_guidelines_with_retries(fetch)

    assert len(refs) == 60
    assert calls["count"] == 2


def test_discover_guidelines_with_retries_raises_rather_than_report_a_thin_result() -> None:
    """A persistently thin discovery raises instead of silently reporting a near-empty success."""
    thin_html = f'<html><a href="{BASE_URL}/en/guidelines-advice/types-of-cancer/1">One</a></html>'

    with pytest.raises(CcoFetchError):
        _discover_guidelines_with_retries(lambda url: thin_html)


def test_scrape_guideline_or_placeholder_survives_a_bad_page() -> None:
    """An unparseable guideline yields a flagged placeholder instead of aborting the run."""
    ref = GuidelineRef(id="999", page_url=f"{BASE_URL}/en/guidelines-advice/types-of-cancer/999")
    fetch = _fake_fetch({ref.page_url: "<html><body>nothing recognizable here</body></html>"})

    document = _scrape_guideline_or_placeholder(fetch, ref, link_mode=LinkMode.KEEP)

    assert document.external_id == "cco-999"
    assert document.content == ""
    assert "scrape_error" in document.metadata


def test_list_cco_guidelines_follows_the_load_more_pager() -> None:
    """The master listing page is paged through its Drupal 'Load more' pager, not a 'next' link."""
    index_url = f"{BASE_URL}/en/guidelines-advice/types-of-cancer"
    page_2_url = f"{BASE_URL}/en/guidelines-advice/types-of-cancer?page=1"

    pages = {
        index_url: (
            "<html>"
            f'<a href="{BASE_URL}/en/guidelines-advice/types-of-cancer/101">Guideline A</a>'
            f'<a href="{BASE_URL}/en/guidelines-advice/types-of-cancer/breast">Breast Cancer</a>'
            f'<ul class="pager pager-load-more"><li class="pager-next first last">'
            f'<a href="{page_2_url}">Load more</a></li></ul>'
            "</html>"
        ),
        page_2_url: f'<html><a href="{BASE_URL}/en/guidelines-advice/types-of-cancer/102">Guideline B</a></html>',
    }
    fetch = _fake_fetch(pages)

    refs = list_cco_guidelines(fetch)

    assert refs == [
        GuidelineRef(id="101", page_url=f"{BASE_URL}/en/guidelines-advice/types-of-cancer/101"),
        GuidelineRef(id="102", page_url=f"{BASE_URL}/en/guidelines-advice/types-of-cancer/102"),
    ]
    assert f"{BASE_URL}/en/guidelines-advice/types-of-cancer/breast" not in fetch.calls