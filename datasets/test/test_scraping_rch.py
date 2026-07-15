"""Tests for RCH clinical guideline scraping helpers."""

import httpx
import pytest

from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.rch import (
    BASE_URL,
    GUIDELINE_INDEX_URL,
    RchGuidelineRef,
    _scrape_or_skip_guideline,
    guideline_ref_from_url,
    list_guidelines,
    scrape_guideline,
)


def test_list_guidelines_discovers_unique_html_pages() -> None:
    """The A-Z index yields unique HTML guideline references and nested paths."""
    html_text = """
    <html>
      <div id="tabnav-letter-blocks">
        <a href="/clinicalguide/guideline_index/Acute_asthma/">Acute asthma</a>
        <a href="/clinicalguide/guideline_index/Acute_asthma/">Asthma acute (see Acute asthma)</a>
        <a href="/clinicalguide/guideline_index/fractures/Elbow_Dislocations/">Elbow dislocations</a>
        <a href="/Persistent_nasal_discharge_rhinosinusitis/.aspx">Nasal discharge</a>
        <a href="/Persistent_nasal_discharge_rhinosinusitis/.aspx">Rhinosinusitis</a>
        <a href="/clinicalguide/guideline_index/CPG_Committee_Calendar/">Committee calendar</a>
        <a href="/clinicalguide/guideline_index/files/package.pdf">PDF package</a>
      </div>
      <footer><a href="/clinicalguide/guideline_index/not-a-guideline/">Footer link</a></footer>
    </html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == GUIDELINE_INDEX_URL
        return httpx.Response(200, text=html_text)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert list_guidelines(client) == [
        RchGuidelineRef(
            slug="Acute_asthma",
            title="Acute asthma",
            page_url=f"{BASE_URL}/clinicalguide/guideline_index/Acute_asthma/",
            aliases=("Asthma acute",),
        ),
        RchGuidelineRef(
            slug="fractures/Elbow_Dislocations",
            title="Elbow dislocations",
            page_url=f"{BASE_URL}/clinicalguide/guideline_index/fractures/Elbow_Dislocations/",
        ),
        RchGuidelineRef(
            slug="Persistent_nasal_discharge_rhinosinusitis",
            title="Nasal discharge",
            page_url=f"{BASE_URL}/Persistent_nasal_discharge_rhinosinusitis/.aspx",
            aliases=("Rhinosinusitis",),
        ),
    ]


def test_guideline_ref_from_url_normalizes_host_and_query() -> None:
    """RCH guideline URLs are normalized to canonical HTTPS page URLs."""
    assert guideline_ref_from_url(
        "http://rch.org.au/clinicalguide/guideline_index/Acute_asthma/?print=yes#management"
    ) == RchGuidelineRef(
        slug="Acute_asthma",
        title="Acute asthma",
        page_url="https://www.rch.org.au/clinicalguide/guideline_index/Acute_asthma/",
    )


def test_guideline_ref_from_url_preserves_legacy_aspx_route() -> None:
    """Legacy ASPX guideline paths do not receive a breaking trailing slash."""
    assert (
        guideline_ref_from_url("https://www.rch.org.au/clinicalguide/guideline_index/Gastrostomy.aspx/").page_url
        == "https://www.rch.org.au/clinicalguide/guideline_index/Gastrostomy.aspx"
    )


def test_guideline_ref_from_url_accepts_direct_rhinosinusitis_guideline() -> None:
    """The clinical A-Z index's direct rhinosinusitis route remains discoverable."""
    assert guideline_ref_from_url(
        "https://rch.org.au/Persistent_nasal_discharge_rhinosinusitis/.aspx?print=yes"
    ) == RchGuidelineRef(
        slug="Persistent_nasal_discharge_rhinosinusitis",
        title="Persistent nasal discharge rhinosinusitis",
        page_url=f"{BASE_URL}/Persistent_nasal_discharge_rhinosinusitis/.aspx",
    )


def test_scrape_guideline_extracts_primary_widgets_as_markdown() -> None:
    """Guideline content, references, metadata, lists, tables, and images are preserved."""
    html_text = """
    <html>
      <h1>Acute asthma</h1>
      <div id="rch-primary" class="col-md-10">
        <div class="widgetBody"><img alt="PIC Endorsed" src="/pic-logo.png"></div>
        <div class="widgetBody">
          <h2>Key points</h2>
          <p><strong>Treat urgently.</strong><strong></strong></p>
          <ul>
            <li>Assess severity:</li>
            <ul><li>Check breathing.</li><li>Check activity.</li></ul>
          </ul>
          <h3><strong><strong>Management</strong> details</strong></h3>
          <p>See the <a href="/clinicalguide/guideline_index/Anaphylaxis/">anaphylaxis guideline</a>.</p>
          <p>Jump to <a href="#management">management</a>.</p>
          <table><tr><th>Severity</th><th>Action</th></tr><tr><td>Severe</td><td>Escalate</td></tr></table>
          <h4><br></h4>
          <p><img src="/uploadedImages/asthma-flowchart.png" alt="Asthma flowchart"></p>
          <p><img src="http://webedit.rch.org.au/uploadedImages/legacy.png" alt="Legacy image"></p>
          <p><img alt="Missing image source"></p>
          <p>Last updated July 2025</p>
          <script>window.rchWidget = {"tracking": true};</script>
        </div>
        <div class="widgetBody"><h2>Reference List</h2><ol><li>Reference one.</li></ol></div>
      </div>
      <footer><h2>Contact us</h2><p>Footer content.</p></footer>
    </html>
    """
    ref = RchGuidelineRef(
        slug="Acute_asthma",
        title="Acute asthma listing title",
        page_url="https://www.rch.org.au/clinicalguide/guideline_index/Acute_asthma/",
        aliases=("Asthma acute", "Wheeze"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == ref.page_url
        return httpx.Response(200, text=html_text)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    document = scrape_guideline(client, ref)

    assert document.source == "rch"
    assert document.external_id == "rch-acute-asthma"
    assert document.title == "Acute asthma"
    assert document.section_count == 2
    assert document.metadata == {
        "slug": "Acute_asthma",
        "aliases": ["Asthma acute", "Wheeze"],
        "last_updated": "July 2025",
    }
    assert "- Assess severity:\n  - Check breathing.\n  - Check activity." in document.content
    assert "### Management details" in document.content
    assert (
        "[anaphylaxis guideline](https://www.rch.org.au/clinicalguide/guideline_index/Anaphylaxis/)" in document.content
    )
    assert (
        "[management](https://www.rch.org.au/clinicalguide/guideline_index/Acute_asthma/#management)"
        in document.content
    )
    assert "| Severity | Action |" in document.content
    assert "![Asthma flowchart](https://www.rch.org.au/uploadedImages/asthma-flowchart.png)" in document.content
    assert "![Legacy image](https://www.rch.org.au/uploadedImages/legacy.png)" in document.content
    assert "Missing image source" not in document.content
    assert "## Reference List" in document.content
    assert "****" not in document.content
    assert "PIC Endorsed" not in document.content
    assert "Footer content" not in document.content
    assert "rchWidget" not in document.content


def test_scrape_guideline_keeps_substantial_headingless_legacy_content() -> None:
    """Legacy clinical tables without section headings remain valid guideline content."""
    clinical_rows = "".join(f"<tr><td>Disease {index}</td><td>Dose {index}</td></tr>" for index in range(10))
    html_text = f"""
    <html><h1>Empiric treatment</h1><div id="rch-primary"><div class="widgetBody">
      <p>Start empiric treatment based on clinical features and local antimicrobial susceptibility patterns.</p>
      <table><tr><th>Disease</th><th>Treatment</th></tr>{clinical_rows}</table>
    </div></div></html>
    """
    ref = RchGuidelineRef("Empiric_treatment", "Empiric treatment", f"{BASE_URL}/guideline/empiric")

    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, text=html_text))) as client:
        document = scrape_guideline(client, ref)

    assert "| Disease | Treatment |" in document.content
    assert "local antimicrobial susceptibility patterns" in document.content


def test_scrape_guideline_resolves_same_site_link_wrapper() -> None:
    """Link-only A-Z entries resolve to their substantial same-site content pages."""
    index_url = f"{BASE_URL}/clinicalguide/guideline_index/IV_Immunoglobulin/"
    target_url = f"{BASE_URL}/bloodtrans/about_blood_products/Intravenous_Immunoglobulin_Guideline"
    wrapper_html = f"""
    <html><h1>IV Immunoglobulin</h1><div id="rch-primary"><div class="widgetBody">
      <p><a href="{target_url}">Go to the IVIg guideline</a></p>
    </div></div></html>
    """
    target_html = """
    <html><h1>Intravenous immunoglobulin</h1><div id="rch-primary"><div class="widgetBody">
      <h2>Administration</h2><p>Monitor the patient throughout the infusion.</p>
    </div></div></html>
    """
    ref = RchGuidelineRef("IV_Immunoglobulin", "IV Immunoglobulin", index_url)

    def handler(request: httpx.Request) -> httpx.Response:
        pages = {index_url: wrapper_html, target_url: target_html}
        return httpx.Response(200, text=pages[str(request.url)])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        document = scrape_guideline(client, ref)

    assert document.url == target_url
    assert document.title == "Intravenous immunoglobulin"
    assert document.metadata == {"slug": "IV_Immunoglobulin", "index_url": index_url}
    assert "Monitor the patient throughout the infusion." in document.content


def test_scrape_or_skip_guideline_logs_non_guideline_index_entry(caplog: pytest.LogCaptureFixture) -> None:
    """Resource pages discovered in the guideline index are reported and skipped."""
    ref = RchGuidelineRef(
        slug="Immigrant_health_resources",
        title="Immigrant health resources",
        page_url="https://www.rch.org.au/clinicalguide/guideline_index/Immigrant_health_resources/",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == ref.page_url
        html_text = "<html><div id='rch-primary'><div class='widgetBody'>Resource link</div></div></html>"
        return httpx.Response(200, text=html_text)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert _scrape_or_skip_guideline(client, ref, link_mode=LinkMode.KEEP) is None

    assert "Skipping unsupported RCH index entry" in caplog.text


@pytest.mark.parametrize("status_code", [404, 410])
def test_scrape_or_skip_guideline_logs_stale_index_entry(
    status_code: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Permanently missing pages in the live index are reported and skipped."""
    ref = RchGuidelineRef("stale", "Stale", f"{BASE_URL}/clinicalguide/guideline_index/stale/")

    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(status_code))) as client:
        assert _scrape_or_skip_guideline(client, ref, link_mode=LinkMode.KEEP) is None

    assert f"HTTP {status_code}" in caplog.text


def test_scrape_or_skip_guideline_raises_transient_http_error() -> None:
    """Server errors still abort so incomplete runs are not silently accepted."""
    ref = RchGuidelineRef("error", "Error", f"{BASE_URL}/clinicalguide/guideline_index/error/")

    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(503))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            _scrape_or_skip_guideline(client, ref, link_mode=LinkMode.KEEP)
