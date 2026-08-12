"""Tests for Canadian Paediatric Society statement scraping helpers."""

import httpx
import pytest

from amfv_datasets.scraping.cps import (
    BASE_URL,
    STATEMENTS_URL,
    CpsFetchError,
    CpsStatementRef,
    list_statements,
    listing_page_url,
    scrape_statement,
    statement_ref_from_url,
)


def test_listing_page_url_uses_cps_offsets() -> None:
    """One-based pages map to the CPS P30 offset convention."""
    assert listing_page_url(1) == STATEMENTS_URL
    assert listing_page_url(2) == f"{STATEMENTS_URL}/P10"
    assert listing_page_url(4) == f"{STATEMENTS_URL}/P30"


def test_list_statements_discovers_unique_position_pages() -> None:
    """The index yields unique position statements and ignores unrelated links."""
    html_text = """
    <html><div class="cell main-body">
      <div class="stmt-title"><a href="/en/documents/position/acute-asthma">Acute asthma</a></div>
      <div class="stmt-title"><a href="https://cps.ca/documents/position/acute-asthma">Duplicate asthma</a></div>
      <div class="stmt-title"><a href="/en/documents/position/newborn-glucose">Newborn glucose</a></div>
      <a href="/en/documents/about-position-statements">About statements</a>
    </div><footer><a href="/en/documents/position/footer-link">Footer</a></footer></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == STATEMENTS_URL
        return httpx.Response(200, text=html_text)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert list_statements(client, 1) == [
            CpsStatementRef("acute-asthma", "Acute asthma", f"{BASE_URL}/en/documents/position/acute-asthma"),
            CpsStatementRef(
                "newborn-glucose",
                "Newborn glucose",
                f"{BASE_URL}/en/documents/position/newborn-glucose",
            ),
        ]


def test_statement_ref_from_url_normalizes_supported_routes() -> None:
    """English and language-neutral CPS routes become canonical HTTPS URLs."""
    assert statement_ref_from_url("http://www.cps.ca/documents/position/Acute-Asthma?print=1#dose") == (
        CpsStatementRef(
            "Acute-Asthma",
            "Acute Asthma",
            "https://cps.ca/en/documents/position/Acute-Asthma",
        )
    )


def test_statement_ref_from_url_rejects_non_statement_pages() -> None:
    """CPS navigation pages cannot be mistaken for clinical statements."""
    with pytest.raises(CpsFetchError):
        statement_ref_from_url("https://cps.ca/en/documents")


def test_scrape_statement_preserves_clinical_structure_and_metadata() -> None:
    """Statement content is normalized while navigation and sharing chrome are removed."""
    html_text = """
    <html>
      <head><title>Fallback | Canadian Paediatric Society</title></head>
      <body>
        <header><h1>Site heading</h1></header>
        <main id="main-content">
          <div class="statement-wrapper">
            <div class="breadcrumb">Home / Documents</div>
            <p>Position statement</p>
            <h1>Management of well-appearing febrile young infants</h1>
            <p>Posted: Oct 27, 2023 | Reaffirmed: Jan 12, 2026 | Updated: May 27, 2026</p>
            <h2>Recommendations</h2>
            <ul><li>Assess the infant:<ul><li>Check vital signs.</li></ul></li></ul>
            <p>Use the <a href="/en/tools/risk-calculator">risk calculator</a>.</p>
            <p>Current citation<a class="reference" href="#ref1">[1]</a>.</p>
            <p>Legacy citation<sup>[<a class="reference" href="#ref2">2</a>]</sup>.</p>
            <p>Maternal fever above 38<sup>o</sup>C and counts of 10<sup>9</sup>/L require attention.</p>
            <p><a href="/documents/full-statement.pdf">Full statement PDF</a>
              <img src="/assets/img/file-pdf.svg" alt="PDF icon"></p>
            <p><a href="/empty-target"></a></p>
            <table><tr><th>Age</th><th>Action</th></tr><tr><td>0-28 days</td><td>Investigate</td></tr></table>
            <h2><strong>Algorithm</strong></h2>
            <p><img src="/uploads/flowcharts/febrile-infant.png" alt=""></p>
            <h2>References</h2>
            <div class="reference-print-style"><ol>
              <li><a name="ref1"></a>Reference one.</li>
              <li><a name="ref2"></a>Reference two.</li>
            </ol></div>
            <a href="/en/education/test-your-knowledge"><img src="/assets/img/test.png" alt="Quiz"></a>
            <div class="share-tools">Share this statement</div>
            <aside class="related-content">Related news</aside>
            <script>window.track = true;</script>
          </div>
        </main>
        <footer>Contact CPS</footer>
      </body>
    </html>
    """
    ref = CpsStatementRef(
        "febrile-young-infants",
        "Listing title",
        f"{BASE_URL}/en/documents/position/febrile-young-infants",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == ref.page_url
        return httpx.Response(200, text=html_text)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        document = scrape_statement(client, ref)

    assert document.source == "cps"
    assert document.external_id == "cps-febrile-young-infants"
    assert document.title == "Management of well-appearing febrile young infants"
    assert document.section_count == 3
    assert document.metadata == {
        "slug": "febrile-young-infants",
        "posted": "Oct 27, 2023",
        "reaffirmed": "Jan 12, 2026",
        "updated": "May 27, 2026",
    }
    assert "- Assess the infant:\n  - Check vital signs." in document.content
    assert "[risk calculator](https://cps.ca/en/tools/risk-calculator)" in document.content
    assert "Current citation[1](#ref1)." in document.content
    assert "Legacy citation[2](#ref2)." in document.content
    assert "38<sup>o</sup>C" in document.content
    assert "10<sup>9</sup>/L" in document.content
    assert "# Management of well-appearing" not in document.content
    assert "## Algorithm" in document.content
    assert "## **Algorithm**" not in document.content
    assert "| Age | Action |" in document.content
    assert "![](https://cps.ca/uploads/flowcharts/febrile-infant.png)" in document.content
    assert "## References" in document.content
    assert "1. Reference one." in document.content
    assert "2. Reference two." in document.content
    assert "[Full statement PDF](https://cps.ca/documents/full-statement.pdf)" in document.content
    assert "file-pdf.svg" not in document.content
    assert "empty-target" not in document.content
    assert "Share this statement" not in document.content
    assert "Related news" not in document.content
    assert "test-your-knowledge" not in document.content
    assert "assets/img/test.png" not in document.content
    assert "window.track" not in document.content
    assert "Contact CPS" not in document.content


def test_scrape_statement_flags_pdf_landing_page() -> None:
    """A thin PDF landing page is explicit in document metadata."""
    page_url = f"{BASE_URL}/en/documents/position/technical-report"
    pdf_url = f"{BASE_URL}/uploads/technical-report.pdf"
    html_text = f"""
    <main id="main-content"><div class="statement-wrapper">
      <h1>Technical report</h1>
      <p>Posted: Nov 24, 2023</p>
      <h3>Principal author(s)</h3>
      <p>Example Author</p>
      <a href="{pdf_url}">Technical report PDF</a>
      <p>Disclaimer: Refer to the complete report.</p>
    </div></main>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == page_url
        return httpx.Response(200, text=html_text)

    ref = CpsStatementRef("technical-report", "Technical report", page_url)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        document = scrape_statement(client, ref)

    assert "Technical report PDF" in document.content
    assert document.section_count == 1
    assert document.metadata == {
        "slug": "technical-report",
        "posted": "Nov 24, 2023",
        "content_length": len(document.content),
        "content_scope": "landing_page",
        "download_url": pdf_url,
    }
