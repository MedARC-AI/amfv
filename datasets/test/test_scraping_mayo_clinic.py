"""Offline tests for permission-gated Mayo Clinic ingestion."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

import amfv_datasets.scraping.mayo_clinic as mayo_module
from amfv_datasets.scraping.base import artifact_capture_context
from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.mayo_clinic import (
    BASE_URL,
    INDEX_URL,
    SITEMAP_URL,
    MayoClinicArticleRef,
    MayoClinicFetchError,
    MayoClinicPageUnavailableError,
    MayoClinicPermissionError,
    list_mayo_clinic_index,
    mayo_clinic_ref_from_url,
    refs_from_manifest,
    refs_from_sitemap_xml,
    scrape_mayo_clinic,
)

_ARTICLE_URL = f"{BASE_URL}/diseases-conditions/acne/symptoms-causes/syc-20368047"
_DIAGNOSIS_URL = f"{BASE_URL}/diseases-conditions/acne/diagnosis-treatment/drc-20368050"
_UNAVAILABLE_URL = f"{BASE_URL}/diseases-conditions/bartholin-cyst/symptoms-causes/syc-20369976"
_AEM_ARTICLE_URL = f"{BASE_URL}/diseases-conditions/acanthosis-nigricans/symptoms-causes/syc-20368983"
_ARTICLE_HTML = """
<html>
  <head>
    <meta name="Description" content="Causes, symptoms and care for acne.">
    <meta name="PublishDate" content="2024-07-20">
  </head>
  <body>
    <div class="main">
      <header><div class="row"><h1><a href="/diseases-conditions/acne">Acne</a></h1></div></header>
      <article id="main-content">
        <div class="row">
          <div class="content">
            <div class="thin-content-bar"><div class="print">Print</div></div>
            <div>
              <h2>Overview</h2>
              <p>Acne occurs when follicles become blocked.</p>
              <div id="maincta" class="acces-list-container rc-list">
                <h3>Products &amp; Services</h3><p>Buy a book.</p>
              </div>
              <h2>Symptoms</h2>
              <p>Common signs include:</p>
              <ul><li>Whiteheads</li><li>Blackheads</li></ul>
              <h3>When to see a doctor</h3>
              <p>
                Seek care for persistent symptoms. Read the
                <a href="/about-this-site/health-information-policy">policy</a>.
              </p>
              <table>
                <thead><tr><th>Sign</th><th>Action</th></tr></thead>
                <tbody><tr><td>Severe pain</td><td>Seek care</td></tr></tbody>
              </table>
              <div class="requestappt">Request an appointment</div>
              <div class="contentbox no-border">
                <form><h2>From Mayo Clinic to your inbox</h2><input type="email"></form>
              </div>
              <div class="thin-content-by">By Mayo Clinic Staff</div>
            </div>
            <div class="sectionnav">Diagnosis &amp; treatment</div>
            <div class="pubdate">July 20, 2024</div>
            <div class="references">
              <button>Show references</button>
              <div><ol><li>Clinical reference one.</li></ol></div>
            </div>
            <div class="acces-list-container"><h2>Related</h2><p>Unrelated recommendation.</p></div>
            <div class="tableofcontents">Symptoms and causes</div>
          </div>
          <div class="sidebar">Advertisement</div>
        </div>
      </article>
    </div>
  </body>
</html>
"""
_INDEX_HTML = f"""
<html><body><main>
  <h1>Diseases &amp; Conditions</h1>
  <div id="cmp-skip-to-main__content" class="cmp-azresults cmp-azresults-from-model">
    <a class="cmp-result-name__link" href="{_ARTICLE_URL}">Acne</a>
    <a class="cmp-result-name__link" href="{_ARTICLE_URL}?duplicate=1">Acne alias</a>
    <a class="cmp-results-with-primary-name__see-link" href="{_UNAVAILABLE_URL}">Abscess, Bartholin</a>
  </div>
  <a href="/diseases-conditions/index?letter=B">B</a>
</main></body></html>
"""
_AEM_ARTICLE_HTML = """
<html>
  <head>
    <meta name="Description" content="A current-template condition article.">
    <meta name="PublishDate" content="2025-04-30T05:00:00.000-05:00">
  </head>
  <body class="consumerconditionpage spapage page basicpage light-mode">
    <header><h1 id="page-title">Acanthosis nigricans</h1></header>
    <article class="cmp-article">
      <div class="aem-container cmp-aside-container">
        <div class="container-child"><nav>On this page Overview Symptoms</nav></div>
        <div class="container-child">
          <div class="cmp-text__rich-content cmp-dita-content">
            <h2>Overview</h2>
            <p>Acanthosis nigricans causes areas of dark, thick skin.</p>
          </div>
          <div class="cmp-related-content cmp-related-content--inline">
            <h3>Products &amp; Services</h3><a href="https://store.example/book">Buy a book</a>
          </div>
          <form><h2>Newsletter</h2><input type="email"></form>
          <div class="cmp-text__rich-content cmp-dita-content">
            <h2>Symptoms</h2>
            <p>Changes often appear slowly.</p>
          </div>
        </div>
        <div class="container-child">
          Request an appointment
          <div class="cmp-meet-mc-staff-button">By Mayo Clinic Staff</div>
        </div>
      </div>
    </article>
  </body>
</html>
"""
_ARTICLE_RECEIPT = {
    "transport": "playwright-ephemeral-browser",
    "sha256": "a" * 64,
    "retrieval_duration_ms": 12,
}
_INDEX_RECEIPT = {"transport": "playwright-ephemeral-browser", "sha256": "b" * 64}
_SITEMAP_XML = f"""
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{_ARTICLE_URL}</loc><lastmod>2026-08-20</lastmod></url>
  <url><loc>{_DIAGNOSIS_URL}</loc><lastmod>2026-08-21</lastmod></url>
  <url><loc>{_ARTICLE_URL}?duplicate=1</loc><lastmod>2026-08-22</lastmod></url>
  <url><loc>{BASE_URL}/diseases-conditions/acne/doctors-departments/ddc-20368049</loc></url>
</urlset>
"""


class _CaptureSink:
    def __init__(self) -> None:
        self.artifacts: list[dict[str, object]] = []

    def capture_artifact(self, data: bytes | str, **metadata: object) -> None:
        self.artifacts.append({"data": data, **metadata})


def test_mayo_clinic_ref_from_url_normalizes_supported_article() -> None:
    """Exact Mayo hosts and condition section routes normalize predictably."""
    assert mayo_clinic_ref_from_url(
        "http://mayoclinic.org/diseases-conditions/acne/symptoms-causes/syc-20368047?p=1#overview"
    ) == MayoClinicArticleRef(
        slug="acne",
        section="symptoms-causes",
        document_id="syc-20368047",
        title="Acne",
        page_url=_ARTICLE_URL,
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://mayoclinic.org.evil.example/diseases-conditions/acne/symptoms-causes/syc-20368047",
        "https://www.mayoclinic.org/diseases-conditions/index?letter=A",
        "https://www.mayoclinic.org/diseases-conditions/acne/doctors-departments/ddc-20368049",
    ],
    ids=["lookalike-host", "catalogue", "nonclinical-section"],
)
def test_mayo_clinic_ref_from_url_rejects_unsupported_urls(url: str) -> None:
    """The adapter cannot be redirected into catalogue or unrelated routes."""
    with pytest.raises(MayoClinicFetchError):
        mayo_clinic_ref_from_url(url)


def test_refs_from_manifest_deduplicates_canonical_urls() -> None:
    """Manifest aliases differing only by host/query produce one request."""
    refs = refs_from_manifest(
        [
            _ARTICLE_URL,
            "https://mayoclinic.org/diseases-conditions/acne/symptoms-causes/syc-20368047?p=1",
        ]
    )

    assert refs == [mayo_clinic_ref_from_url(_ARTICLE_URL)]


def test_condition_sitemap_keeps_both_clinical_section_families() -> None:
    """The official inventory retains symptoms and diagnosis pages without duplicates."""
    refs = refs_from_sitemap_xml(_SITEMAP_XML)

    assert [ref.page_url for ref in refs] == [_ARTICLE_URL, _DIAGNOSIS_URL]
    assert [ref.section for ref in refs] == ["symptoms-causes", "diagnosis-treatment"]
    assert [ref.sitemap_last_modified for ref in refs] == ["2026-08-20", "2026-08-21"]
    assert all(ref.discovery_url == SITEMAP_URL for ref in refs)


class _FakePage:
    def __init__(self, final_url: str) -> None:
        self.url = final_url
        self.waited_for: list[str] = []

    def goto(self, url: str, *, wait_until: str, timeout: int):
        return type("Response", (), {"status": 200})()

    def wait_for_selector(self, selector: str, *, timeout: int) -> None:
        self.waited_for.append(selector)

    def content(self) -> str:
        return _ARTICLE_HTML


class _FakeResponse:
    def __init__(self, status: int, *, retry_after: str | None = None) -> None:
        self.status = status
        self._retry_after = retry_after

    def header_value(self, name: str) -> str | None:
        return self._retry_after if name.casefold() == "retry-after" else None


class _RetryPage(_FakePage):
    def __init__(self, final_url: str, responses: list[_FakeResponse | BaseException]) -> None:
        super().__init__(final_url)
        self.responses = responses
        self.goto_calls = 0

    def goto(self, url: str, *, wait_until: str, timeout: int):
        response = self.responses[self.goto_calls]
        self.goto_calls += 1
        if isinstance(response, BaseException):
            raise response
        return response


def test_browser_fetch_accepts_only_the_manifest_article_route() -> None:
    """The in-memory browser fetch verifies its final URL before returning HTML."""
    page = _FakePage(_ARTICLE_URL)

    html_text, receipt = mayo_module._playwright_fetch(page, _ARTICLE_URL)

    assert html_text == _ARTICLE_HTML
    assert receipt["status_code"] == 200
    assert receipt["attempts"] == 1
    assert receipt["retry_delays_seconds"] == []
    assert isinstance(receipt["retrieval_duration_ms"], int)
    assert len(receipt["sha256"]) == 64
    assert page.waited_for == [mayo_module.ARTICLE_SELECTOR]


def test_browser_fetch_honors_bounded_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient throttle is retried and its attempt history is auditable."""
    page = _RetryPage(_ARTICLE_URL, [_FakeResponse(429, retry_after="0"), _FakeResponse(200)])
    sleeps: list[float] = []
    monkeypatch.setattr(mayo_module.time, "sleep", sleeps.append)

    html_text, receipt = mayo_module._playwright_fetch(page, _ARTICLE_URL)

    assert html_text == _ARTICLE_HTML
    assert page.goto_calls == 2
    assert sleeps == [0.0]
    assert receipt["attempts"] == 2
    assert receipt["retry_delays_seconds"] == [0.0]


def test_browser_receipt_redacts_index_query_values() -> None:
    """A-Z receipt URLs retain useful parameter names without persisting values."""
    receipt = mayo_module._browser_receipt(
        f"{INDEX_URL}?letter=A",
        _INDEX_HTML,
        status_code=200,
        attempts=1,
        retry_delays_seconds=[],
    )

    assert receipt["requested_url"] == f"{INDEX_URL}?letter=REDACTED"
    assert receipt["final_url"] == f"{INDEX_URL}?letter=REDACTED"
    assert len(receipt["requested_url_query_sha256"]) == 64
    assert len(receipt["final_url_query_sha256"]) == 64


def test_browser_fetch_retries_navigation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient browser navigation error uses exponential bounded backoff."""
    page = _RetryPage(
        _ARTICLE_URL,
        [mayo_module.PlaywrightError("temporary"), _FakeResponse(200)],
    )
    sleeps: list[float] = []
    monkeypatch.setattr(mayo_module.time, "sleep", sleeps.append)

    _html_text, receipt = mayo_module._playwright_fetch(page, _ARTICLE_URL)

    assert sleeps == [mayo_module.INITIAL_RETRY_DELAY_SECONDS]
    assert receipt["attempts"] == 2


def test_browser_fetch_does_not_retry_permanent_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """A confirmed missing article is classified immediately as unavailable."""
    page = _RetryPage(_ARTICLE_URL, [_FakeResponse(404)])
    monkeypatch.setattr(mayo_module.time, "sleep", lambda _delay: pytest.fail("must not retry a 404"))

    with pytest.raises(MayoClinicPageUnavailableError, match="HTTP 404"):
        mayo_module._playwright_fetch(page, _ARTICLE_URL)

    assert page.goto_calls == 1


@pytest.mark.parametrize("retry_after", ["nan", "inf", "-inf"])
def test_retry_delay_rejects_nonfinite_header(retry_after: str) -> None:
    """Malformed nonfinite Retry-After values fall back to bounded exponential delay."""
    assert mayo_module._retry_delay_seconds(attempt=2, retry_after=retry_after) == 10.0


@pytest.mark.parametrize(
    ("constant_name", "value"),
    [
        ("INITIAL_RETRY_DELAY_SECONDS", 0.0),
        ("INITIAL_RETRY_DELAY_SECONDS", -1.0),
        ("INITIAL_RETRY_DELAY_SECONDS", float("nan")),
        ("INITIAL_RETRY_DELAY_SECONDS", float("inf")),
        ("MAX_RETRY_DELAY_SECONDS", 0.0),
        ("MAX_RETRY_DELAY_SECONDS", -1.0),
        ("MAX_RETRY_DELAY_SECONDS", float("nan")),
        ("MAX_RETRY_DELAY_SECONDS", float("inf")),
    ],
)
def test_retry_delay_rejects_invalid_delay_constants(
    monkeypatch: pytest.MonkeyPatch,
    constant_name: str,
    value: float,
) -> None:
    """Retry delays must remain positive and finite after configuration changes."""
    monkeypatch.setattr(mayo_module, constant_name, value)

    with pytest.raises(ValueError, match=constant_name):
        mayo_module._retry_delay_seconds(attempt=1)


@pytest.mark.parametrize("attempts", [0, -1, 11, 1.5, float("nan"), True])
def test_browser_fetch_rejects_invalid_retry_count(monkeypatch: pytest.MonkeyPatch, attempts: object) -> None:
    """An invalid retry count fails before any browser navigation."""
    monkeypatch.setattr(mayo_module, "MAX_FETCH_ATTEMPTS", attempts)

    with pytest.raises(ValueError, match="MAX_FETCH_ATTEMPTS"):
        mayo_module._playwright_fetch(object(), _ARTICLE_URL)


def test_browser_fetch_rejects_cross_article_redirect() -> None:
    """A redirect cannot silently substitute a different condition document."""
    page = _FakePage(f"{BASE_URL}/diseases-conditions/rosacea/symptoms-causes/syc-20353815")

    with pytest.raises(MayoClinicFetchError, match="to a different article"):
        mayo_module._playwright_fetch(page, _ARTICLE_URL)


def test_scrape_article_preserves_clinical_markdown_and_publisher_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clinical sections, lists, tables and references survive while promotions do not."""

    @contextmanager
    def fake_client(*, headless: bool = True) -> Iterator[mayo_module.PageFetch]:
        yield lambda _url: (_ARTICLE_HTML, _ARTICLE_RECEIPT)

    monkeypatch.setattr(mayo_module, "_playwright_client", fake_client)
    run = scrape_mayo_clinic(
        documents=1,
        authorized=True,
        permission_id="MAYO-PERMISSION-2026-001",
        manifest=[_ARTICLE_URL],
    )
    document = next(iter(run))

    assert document.source == "mayoclinic"
    assert document.external_id == "mayo-syc-20368047"
    assert document.title == "Acne"
    assert document.url == _ARTICLE_URL
    assert document.section_count == 4
    assert document.metadata["published"] == "2024-07-20"
    assert document.metadata["authors"] == ["Mayo Clinic Staff"]
    assert document.metadata["description"] == "Causes, symptoms and care for acne."
    assert document.metadata["content_scope"] == "article_section"
    assert document.metadata["source_format_types"] == ["html"]
    assert document.metadata["source_media_types"] == ["text/html"]
    assert document.metadata["license"] == "All rights reserved"
    assert document.metadata["permission_required"] is True
    assert document.metadata["permission_id"] == "MAYO-PERMISSION-2026-001"
    assert document.metadata["ingestion_mode"] == "licensed_url_manifest"
    assert document.provenance["permission_id"] == "MAYO-PERMISSION-2026-001"
    assert document.provenance["retrievals"][0]["transport"] == "playwright-ephemeral-browser"
    assert len(document.provenance["retrievals"][0]["sha256"]) == 64
    assert document.provenance["phase_timings_ms"]["article_retrieval"] == 12
    assert document.provenance["phase_timings_ms"]["html_normalization"] >= 0
    assert "## Overview" in document.content
    assert "- Whiteheads\n- Blackheads" in document.content
    assert "[policy](https://www.mayoclinic.org/about-this-site/health-information-policy)" in document.content
    assert "| Sign | Action |" in document.content
    assert "## References" in document.content
    assert "Clinical reference one." in document.content
    assert "Products & Services" not in document.content
    assert "Request an appointment" not in document.content
    assert "From Mayo Clinic to your inbox" not in document.content
    assert "Unrelated recommendation" not in document.content
    assert "Diagnosis & treatment" not in document.content


def test_scrape_article_honors_strip_link_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip mode retains policy text without a destination."""

    @contextmanager
    def fake_client(*, headless: bool = True) -> Iterator[mayo_module.PageFetch]:
        yield lambda _url: (_ARTICLE_HTML, _ARTICLE_RECEIPT)

    monkeypatch.setattr(mayo_module, "_playwright_client", fake_client)
    run = scrape_mayo_clinic(
        documents=1,
        link_mode=LinkMode.STRIP,
        authorized=True,
        permission_id="MAYO-PERMISSION-2026-001",
        manifest=[_ARTICLE_URL],
    )
    document = next(iter(run))

    assert "policy" in document.content
    assert "[policy]" not in document.content


def test_unauthorized_call_is_zero_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The permission gate runs before browser creation, even for a direct URL."""
    monkeypatch.setattr(
        mayo_module,
        "_playwright_client",
        lambda **_kwargs: pytest.fail("browser must not start before authorization"),
    )

    with pytest.raises(MayoClinicPermissionError, match="disabled by default"):
        scrape_mayo_clinic(documents=1, link_mode=LinkMode.KEEP, url=_ARTICLE_URL)


def test_index_parser_deduplicates_supported_article_urls() -> None:
    """A-Z discovery keeps primary results and ignores synonym and index links."""
    refs, retrieval = list_mayo_clinic_index(
        lambda url: (_INDEX_HTML, {**_INDEX_RECEIPT, "requested_url": url}),
        letter="A",
    )

    assert [ref.page_url for ref in refs] == [_ARTICLE_URL]
    assert retrieval["requested_url"] == f"{INDEX_URL}?letter=A"


def test_build_article_text_supports_current_aem_template() -> None:
    """The current cmp-article template keeps clinical content without its surrounding chrome."""
    content, section_count = mayo_module.build_article_text(_AEM_ARTICLE_HTML)

    assert section_count == 2
    assert "## Overview" in content
    assert "Acanthosis nigricans causes areas of dark, thick skin." in content
    assert "## Symptoms" in content
    assert "On this page" not in content
    assert "Newsletter" not in content
    assert "Products & Services" not in content
    assert "Buy a book" not in content
    assert "Request an appointment" not in content


def test_scrape_article_supports_current_aem_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Current-template timestamps and staff bylines normalize into shared metadata."""

    @contextmanager
    def fake_client(*, headless: bool = True) -> Iterator[mayo_module.PageFetch]:
        yield lambda _url: (_AEM_ARTICLE_HTML, _ARTICLE_RECEIPT)

    monkeypatch.setattr(mayo_module, "_playwright_client", fake_client)
    document = next(
        iter(
            scrape_mayo_clinic(
                documents=1,
                url=_AEM_ARTICLE_URL,
                authorized=True,
                permission_id="MAYO-PERMISSION-2026-001",
            )
        )
    )

    assert document.title == "Acanthosis nigricans"
    assert document.metadata["published"] == "2025-04-30"
    assert document.metadata["authors"] == ["Mayo Clinic Staff"]
    assert "Newsletter" not in document.content


def test_authorized_corpus_uses_sitemap_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documented permission enables official-sitemap discovery and article reads."""
    calls: list[str] = []

    @contextmanager
    def fake_client(*, headless: bool = True) -> Iterator[mayo_module.PageFetch]:
        def fetch(url: str) -> mayo_module.FetchedHtml:
            calls.append(url)
            if url == SITEMAP_URL:
                return _SITEMAP_XML, _INDEX_RECEIPT
            return _ARTICLE_HTML, _ARTICLE_RECEIPT

        yield fetch

    monkeypatch.setattr(mayo_module, "_playwright_client", fake_client)

    documents = list(
        scrape_mayo_clinic(
            documents=1,
            link_mode=LinkMode.KEEP,
            authorized=True,
            permission_id="MAYO-PERMISSION-2026-001",
        )
    )

    assert calls == [SITEMAP_URL, _ARTICLE_URL]
    assert len(documents) == 1
    assert documents[0].metadata["ingestion_mode"] == "authorized_sitemap_discovery"
    assert documents[0].metadata["discovery_method"] == "official_condition_sitemap"
    assert documents[0].metadata["discovery_url"] == SITEMAP_URL
    assert documents[0].metadata["sitemap_last_modified"] == "2026-08-20"
    assert documents[0].metadata["inventory_index"] == 0
    assert documents[0].metadata["inventory_total"] == 2
    assert [receipt["sha256"] for receipt in documents[0].provenance["retrievals"]] == [
        "b" * 64,
        "a" * 64,
    ]


def test_sitemap_discovery_records_and_skips_unavailable_article(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale sitemap result does not abort a bounded corpus run or disappear silently."""
    sitemap_xml = f"""
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>{_UNAVAILABLE_URL}</loc></url>
      <url><loc>{_ARTICLE_URL}</loc></url>
    </urlset>
    """

    @contextmanager
    def fake_client(*, headless: bool = True) -> Iterator[mayo_module.PageFetch]:
        def fetch(url: str) -> mayo_module.FetchedHtml:
            if url == SITEMAP_URL:
                return sitemap_xml, _INDEX_RECEIPT
            if url == _UNAVAILABLE_URL:
                raise MayoClinicPageUnavailableError("page content did not render")
            return _ARTICLE_HTML, _ARTICLE_RECEIPT

        yield fetch

    monkeypatch.setattr(mayo_module, "_playwright_client", fake_client)
    monkeypatch.setattr(mayo_module, "DOCUMENT_DELAY_SECONDS", 0)

    documents = list(
        scrape_mayo_clinic(
            documents=1,
            authorized=True,
            permission_id="MAYO-PERMISSION-2026-001",
        )
    )

    assert [document.external_id for document in documents] == ["mayo-syc-20368047"]
    expected_failure = {
        "document_id": "syc-20369976",
        "url": _UNAVAILABLE_URL,
        "error_type": "MayoClinicPageUnavailableError",
        "message": "page content did not render",
    }
    assert documents[0].metadata["skipped_unavailable_candidates"] == [expected_failure]
    assert documents[0].provenance["skipped_unavailable_candidates"] == [expected_failure]


def test_manifest_does_not_skip_unavailable_article(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operator-selected URLs remain strict even when sitemap discovery can skip staleness."""

    @contextmanager
    def fake_client(*, headless: bool = True) -> Iterator[mayo_module.PageFetch]:
        def fetch(_url: str) -> mayo_module.FetchedHtml:
            raise MayoClinicPageUnavailableError("page content did not render")

        yield fetch

    monkeypatch.setattr(mayo_module, "_playwright_client", fake_client)
    run = scrape_mayo_clinic(
        documents=1,
        authorized=True,
        permission_id="MAYO-PERMISSION-2026-001",
        manifest=[_UNAVAILABLE_URL],
    )

    with pytest.raises(MayoClinicPageUnavailableError, match="did not render"):
        list(run)


def test_authorized_call_still_requires_permission_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A boolean assertion alone cannot start the browser or consume a manifest."""
    monkeypatch.setattr(
        mayo_module,
        "_playwright_client",
        lambda **_kwargs: pytest.fail("browser must not start without a permission identifier"),
    )

    with pytest.raises(MayoClinicPermissionError, match="requires a nonempty permission_id"):
        scrape_mayo_clinic(
            documents=1,
            link_mode=LinkMode.KEEP,
            authorized=True,
            manifest=[_ARTICLE_URL],
        )


def test_authorized_manifest_uses_in_memory_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """An authorized explicit manifest is consumed through the injected browser fetcher."""
    calls: list[str] = []

    @contextmanager
    def fake_client(*, headless: bool = True) -> Iterator[mayo_module.PageFetch]:
        assert headless is True

        def fetch(url: str) -> mayo_module.FetchedHtml:
            calls.append(url)
            return _ARTICLE_HTML, _ARTICLE_RECEIPT

        yield fetch

    monkeypatch.setattr(mayo_module, "_playwright_client", fake_client)
    run = scrape_mayo_clinic(
        documents=1,
        link_mode=LinkMode.KEEP,
        authorized=True,
        permission_id="MAYO-PERMISSION-2026-001",
        manifest=[_ARTICLE_URL, _ARTICLE_URL],
    )

    documents = list(run)

    assert run.total == 1
    assert [document.external_id for document in documents] == ["mayo-syc-20368047"]
    assert calls == [_ARTICLE_URL]


def test_manifest_exhaustion_cannot_report_partial_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bounded manifest run fails explicitly when it cannot reach its requested count."""

    @contextmanager
    def fake_client(*, headless: bool = True) -> Iterator[mayo_module.PageFetch]:
        yield lambda _url: (_ARTICLE_HTML, _ARTICLE_RECEIPT)

    monkeypatch.setattr(mayo_module, "_playwright_client", fake_client)
    run = scrape_mayo_clinic(
        documents=2,
        authorized=True,
        permission_id="MAYO-PERMISSION-2026-001",
        manifest=[_ARTICLE_URL],
    )

    assert run.total == 2
    with pytest.raises(MayoClinicFetchError, match="produced 1 of 2 requested documents"):
        list(run)


def test_review_capture_retains_rendered_article_html(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review mode keeps the rendered DOM that produced the Markdown."""

    @contextmanager
    def fake_client(*, headless: bool = True) -> Iterator[mayo_module.PageFetch]:
        yield lambda _url: (_ARTICLE_HTML, _ARTICLE_RECEIPT)

    monkeypatch.setattr(mayo_module, "_playwright_client", fake_client)
    sink = _CaptureSink()
    with artifact_capture_context(sink):
        list(
            scrape_mayo_clinic(
                documents=1,
                authorized=True,
                permission_id="MAYO-PERMISSION-2026-001",
                manifest=[_ARTICLE_URL],
            )
        )

    assert len(sink.artifacts) == 1
    artifact = sink.artifacts[0]
    assert artifact["media_type"] == "text/html; charset=utf-8"
    assert artifact["filename"] == "mayo-syc-20368047.html"
    assert artifact["metadata"] == {
        "representation": "rendered_dom_serialization",
        "content_scope": "article_section",
    }
    assert b"Clinical reference one" in artifact["data"]
