"""Tests for the AAFP clinical-guidance scraper.

Every test here is offline. Synthetic HTML and XML stand in for AAFP pages, HTTP
is served by `httpx.MockTransport`, and an autouse guard fails the test if any
code under test opens a real socket.
"""

import json
import socket

import httpx
import pytest
from typer.testing import CliRunner

from amfv_datasets.scraping import aafp
from amfv_datasets.scraping.aafp import (
    AafpFetchError,
    SitemapLinks,
    discover_topic_urls,
    is_topic_url,
    parse_sitemap,
    parse_topic_page,
    scrape_aafp,
    scrape_topic_page,
)
from amfv_datasets.scraping.base import ScrapeRun
from amfv_datasets.scraping.cli import ALL_SOURCES, SCRAPERS, app
from amfv_datasets.scraping.html import LinkMode

_TOPIC_URL = "https://www.aafp.org/clinical-insights/cardiometabolic-health/hypertension"
_TOPIC_SLUG = "cardiometabolic-health-hypertension"
_SITEMAP_URL = "https://www.aafp.org/sitemap.xml"

_TOPIC_HTML = """
<html>
  <body>
    <main id="main-content">
      <h1>Hypertension and cardiovascular disease</h1>

      <h2>Guidelines and recommendations</h2>

      <div class="container accordion--layout-standard">
        <h3 class="accordion__heading type-h3">
          Clinical practice guidelines
        </h3>

        <div class="accordion__drawers">
          <div class="standalone-drawer container">
            <div class="drawer" data-testid="drawer">
              <div class="drawer__wrapper">
                <h3 class="drawer-header type-h3">
                  Depression following acute coronary syndrome
                </h3>
                <h4>
                  Screening and treatment of depression following acute
                  coronary syndrome
                </h4>
                <h4>Key Recommendations</h4>
                <p>(Developed by the AAFP, March 2019)</p>
                <p>Screen adults following an acute coronary event.</p>
                <p>
                  See the
                  <a href="/afp/2019/0615/od2">full recommendation</a>
                  for details.
                </p>
              </div>
            </div>
          </div>

          <div class="standalone-drawer container">
            <div class="drawer" data-testid="drawer">
              <div class="drawer__wrapper">
                <h3 class="drawer-header type-h3">
                  High blood pressure – AHA/ACC
                </h3>
                <h4>High blood pressure in adults</h4>
                <h4>(Affirmation of Value*, October 2025)</h4>
                <p>Topline recommendations include:</p>
                <ul>
                  <li>Use validated blood-pressure measurements.</li>
                </ul>
                <p>
                  Read the
                  <a href="https://example.org/full-guideline">
                    full guideline
                  </a>.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="container accordion--layout-standard">
        <h3 class="accordion__heading type-h3">
          Clinical preventive service recommendations
        </h3>

        <div class="accordion__drawers">
          <div class="standalone-drawer container">
            <div class="drawer" data-testid="drawer">
              <div class="drawer__wrapper">
                <h3 class="drawer-header type-h3">
                  Abdominal aortic aneurysm
                </h3>
                <p>Screen an appropriate at-risk population.</p>
                <p>
                  See the
                  <a href="https://example.org/aaa-recommendation">
                    full recommendation
                  </a>.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <h2>Practice and payment resources</h2>
      <div class="drawer__wrapper">
        <h3 class="drawer-header type-h3">
          Coding for hypertension
        </h3>
        <p>This is not a guideline recommendation.</p>
      </div>
    </main>
  </body>
</html>
"""


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if anything in this module opens a real network connection."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("AAFP tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)


def _drawer(*, title: str, body: str, drawer_id: str | None = None) -> str:
    header_id = f' id="{drawer_id}"' if drawer_id else ""
    return f"""
      <div class="standalone-drawer container">
        <div class="drawer" data-testid="drawer">
          <div class="drawer__wrapper">
            <h3 class="drawer-header type-h3"{header_id}>{title}</h3>
            {body}
          </div>
        </div>
      </div>
    """


def _topic_page(
    *,
    sections: str,
    topic: str = "Example topic",
    guidelines_heading: bool = True,
) -> str:
    heading = "<h2>Guidelines and recommendations</h2>" if guidelines_heading else ""
    return f"""
    <html><body><main id="main-content">
      <h1>{topic}</h1>
      {heading}
      {sections}
    </main></body></html>
    """


def _category_section(*, label: str, drawers: str) -> str:
    return f"""
      <div class="container accordion--layout-standard">
        <h3 class="accordion__heading type-h3">{label}</h3>
        <div class="accordion__drawers">{drawers}</div>
      </div>
    """


def _single_drawer_page(body: str, *, label: str = "Clinical practice guidelines") -> str:
    return _topic_page(
        sections=_category_section(label=label, drawers=_drawer(title="Example recommendation", body=body))
    )


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_parse_topic_page_returns_one_document_per_recommendation() -> None:
    """Each relevant recommendation drawer becomes one document with page provenance."""
    documents = parse_topic_page(_TOPIC_HTML, url=_TOPIC_URL, link_mode=LinkMode.KEEP)

    assert [document.title for document in documents] == [
        "Depression following acute coronary syndrome",
        "High blood pressure – AHA/ACC",
        "Abdominal aortic aneurysm",
    ]
    assert all(document.source == "aafp" for document in documents)
    assert all(document.url == _TOPIC_URL for document in documents)
    assert all(document.section_count == 1 for document in documents)
    assert all(document.metadata["scrape_strategy"] == "drawer" for document in documents)
    assert all(document.title != "Coding for hypertension" for document in documents)

    depression = documents[0]
    assert depression.metadata["topic"] == "Hypertension and cardiovascular disease"
    assert depression.metadata["category"] == "Clinical practice guidelines"
    assert depression.metadata["recommendation_subtitle"] == (
        "Screening and treatment of depression following acute coronary syndrome"
    )
    assert depression.metadata["full_guideline_url"] == "https://www.aafp.org/afp/2019/0615/od2"
    assert depression.metadata["source_links"] == [
        {"text": "full recommendation", "url": "https://www.aafp.org/afp/2019/0615/od2"}
    ]
    assert depression.content.startswith("# Depression following acute coronary syndrome")
    assert "Topic: Hypertension and cardiovascular disease" in depression.content
    assert "Category: Clinical practice guidelines" in depression.content
    assert "Screen adults following an acute coronary event." in depression.content
    assert "[full recommendation](https://www.aafp.org/afp/2019/0615/od2)" in depression.content

    aha = documents[1]
    assert aha.metadata["full_guideline_url"] == "https://example.org/full-guideline"
    # markdownify escapes the trailing asterisk of "Affirmation of Value*".
    assert "Affirmation of Value" in aha.content
    assert "- Use validated blood-pressure measurements." in aha.content

    preventive = documents[2]
    assert preventive.metadata["category"] == "Clinical preventive service recommendations"
    assert preventive.metadata["recommendation_subtitle"] is None


def test_direct_and_categorized_drawers_stay_inside_guidelines_section() -> None:
    """Direct and accordion drawers are accepted only inside the guidelines section."""
    html = _topic_page(
        sections=(
            _drawer(
                title="Direct recommendation",
                body="<p>Use the direct clinical recommendation.</p>",
            )
            + _category_section(
                label="Clinical practice guidelines",
                drawers=_drawer(
                    title="Accordion recommendation",
                    body="<p>Use the categorized clinical recommendation.</p>",
                ),
            )
            + """
              <h2>Practice and payment resources</h2>
              <div class="drawer__wrapper">
                <h3 class="drawer-header type-h3">
                  Unrelated coding resource
                </h3>
                <p>This must not become a recommendation document.</p>
              </div>
            """
        )
    )

    documents = parse_topic_page(html, url=_TOPIC_URL)

    assert [document.title for document in documents] == [
        "Direct recommendation",
        "Accordion recommendation",
    ]
    assert [document.metadata["category"] for document in documents] == [
        "Guidelines and recommendations",
        "Clinical practice guidelines",
    ]


def test_parse_topic_page_pins_deterministic_external_ids() -> None:
    """External ids are stable strings, not merely distinct ones."""
    documents = parse_topic_page(_TOPIC_HTML, url=_TOPIC_URL)

    assert [document.external_id for document in documents] == [
        f"aafp-{_TOPIC_SLUG}-clinical-practice-guidelines-depression-following-acute-coronary-syndrome",
        # The en dash in "High blood pressure – AHA/ACC" and the slash both normalize to "-".
        f"aafp-{_TOPIC_SLUG}-clinical-practice-guidelines-high-blood-pressure-aha-acc",
        f"aafp-{_TOPIC_SLUG}-clinical-preventive-service-recommendations-abdominal-aortic-aneurysm",
    ]


def test_parse_topic_page_can_strip_links() -> None:
    """Strip mode keeps link text while removing Markdown link destinations."""
    documents = parse_topic_page(_TOPIC_HTML, url=_TOPIC_URL, link_mode=LinkMode.STRIP)

    depression = documents[0]
    assert "full recommendation" in depression.content
    assert "[full recommendation]" not in depression.content
    assert "https://www.aafp.org/afp/2019/0615/od2" not in depression.content
    # Metadata provenance survives even when the markdown drops link destinations.
    assert depression.metadata["full_guideline_url"] == "https://www.aafp.org/afp/2019/0615/od2"


def test_external_ids_include_category_to_prevent_collisions() -> None:
    """Identical drawer titles in different categories keep distinct stable ids."""
    html = _topic_page(
        topic="Example topic",
        sections="".join(
            [
                _category_section(
                    label="Clinical practice guidelines",
                    drawers=_drawer(title="Hypertension in children", body="<p>Clinical guideline text.</p>"),
                ),
                _category_section(
                    label="Clinical preventive service recommendations",
                    drawers=_drawer(title="Hypertension in children", body="<p>Preventive text.</p>"),
                ),
            ]
        ),
    )

    documents = parse_topic_page(html, url="https://www.aafp.org/clinical-insights/example/topic")

    assert [document.external_id for document in documents] == [
        "aafp-example-topic-clinical-practice-guidelines-hypertension-in-children",
        "aafp-example-topic-clinical-preventive-service-recommendations-hypertension-in-children",
    ]


def test_duplicate_external_ids_raise_rather_than_overwrite() -> None:
    """Two drawers that would share an id are reported, never silently merged."""
    drawers = _drawer(title="Same title", body="<p>First.</p>") + _drawer(title="Same title", body="<p>Second.</p>")
    html = _topic_page(sections=_category_section(label="Clinical practice guidelines", drawers=drawers))

    with pytest.raises(AafpFetchError, match="Duplicate AAFP document id"):
        parse_topic_page(html, url="https://www.aafp.org/clinical-insights/example/topic")


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Clinical practice guideline", 1),
        ("Clinical practice guidelines", 1),
        ("Clinical preventive service recommendation", 1),
        ("Clinical preventive service recommendations", 1),
        ("CLINICAL PRACTICE GUIDELINES", 1),
        ("Practice and payment resources", 0),
        ("Implementation tools", 0),
        ("Podcasts", 0),
        ("Patient education", 0),
    ],
    ids=[
        "guideline-singular",
        "guideline-plural",
        "preventive-singular",
        "preventive-plural",
        "case-insensitive",
        "payment-resources",
        "implementation-tools",
        "podcasts",
        "patient-education",
    ],
)
def test_only_recommendation_categories_produce_documents(label: str, expected: int) -> None:
    """Accepted category labels gate which accordion drawers become documents."""
    html = _topic_page(
        guidelines_heading=False,
        sections=_category_section(
            label=label,
            drawers=_drawer(title="Example recommendation", body="<p>Recommendation text.</p>"),
        ),
    )

    documents = parse_topic_page(html, url="https://www.aafp.org/clinical-insights/example/topic")

    assert len(documents) == expected


@pytest.mark.parametrize(
    ("status_html", "expected_events", "expected_date"),
    [
        (
            "<p>(Developed by the AAFP, March 2019)</p>",
            [{"status": "developed_by_aafp", "date": "March 2019"}],
            "March 2019",
        ),
        (
            "<p>Jointly Developed, January 2017; reaffirmed, 2022</p>",
            [
                {"status": "jointly_developed", "date": "January 2017"},
                {"status": "reaffirmed", "date": "2022"},
            ],
            "2022",
        ),
        ("<p>Endorsed 2014</p>", [{"status": "endorsed", "date": "2014"}], "2014"),
        (
            "<h4>(Affirmation of Value*, October 2025)</h4>",
            [{"status": "affirmation_of_value", "date": "October 2025"}],
            "October 2025",
        ),
        # Live wording seen on the AAFP Dyslipidemia recommendation.
        (
            "<p>(Endorsement, July 2026)</p>",
            [{"status": "endorsed", "date": "July 2026"}],
            "July 2026",
        ),
        ("<p>Not Endorsed</p>", [{"status": "not_endorsed", "date": None}], None),
        ("<p>Reaffirmed, April 2020</p>", [{"status": "reaffirmed", "date": "April 2020"}], "April 2020"),
        (
            "<p>Endorsed 2014, reaffirmed 2020</p>",
            [{"status": "endorsed", "date": "2014"}, {"status": "reaffirmed", "date": "2020"}],
            "2020",
        ),
    ],
    ids=[
        "developed-by-aafp",
        "jointly-developed-then-reaffirmed",
        "endorsed",
        "affirmation-of-value",
        "endorsement-wording",
        "not-endorsed",
        "reaffirmed",
        "endorsed-then-reaffirmed",
    ],
)
def test_status_history_is_preserved_as_structured_events(
    status_html: str,
    expected_events: list[dict[str, str | None]],
    expected_date: str | None,
) -> None:
    """Compound AAFP status histories keep every event and the latest date."""
    html = _single_drawer_page(f"{status_html}<p>Recommendation text.</p>")

    documents = parse_topic_page(html, url="https://www.aafp.org/clinical-insights/example/topic")

    metadata = documents[0].metadata
    assert metadata["status_events"] == expected_events
    assert metadata["status_date"] == expected_date
    # The complete human-readable label survives alongside the structured events.
    assert metadata["status_label"] is not None
    assert json.loads(json.dumps(metadata["status_events"])) == expected_events


def test_not_endorsed_never_reads_as_endorsed() -> None:
    """A refusal must not match the endorsement pattern hidden inside it."""
    html = _single_drawer_page("<p>Not Endorsed</p><p>Recommendation text.</p>")

    documents = parse_topic_page(html, url="https://www.aafp.org/clinical-insights/example/topic")

    assert [event["status"] for event in documents[0].metadata["status_events"]] == ["not_endorsed"]


def test_status_date_uses_the_most_recent_event_not_the_last_written() -> None:
    """status_date is the newest dated event, whatever order the page lists them in."""
    html = _single_drawer_page("<p>Reaffirmed, 2022; endorsed, January 2017</p><p>Recommendation text.</p>")

    metadata = parse_topic_page(html, url="https://www.aafp.org/clinical-insights/example/topic")[0].metadata

    assert metadata["status_date"] == "2022"


def test_recommendation_prose_is_never_read_as_a_status() -> None:
    """A drawer with no status label leaves every status field empty."""
    html = _single_drawer_page(
        "<p>The AAFP developed this summary after endorsed guidelines were reviewed in 2019.</p>"
    )

    metadata = parse_topic_page(html, url="https://www.aafp.org/clinical-insights/example/topic")[0].metadata

    assert metadata["status_label"] is None
    assert metadata["status_events"] == []
    assert metadata["status_date"] is None


def test_a_partly_unreadable_status_line_is_rejected_whole() -> None:
    """If any segment of a status line cannot be read, no status is recorded.

    Keeping the readable half would attach a date and stance to a label the
    parser did not actually understand, so the whole line is dropped instead.
    """
    html = _single_drawer_page("<p>Endorsed 2014; superseded by a newer statement</p><p>Recommendation text.</p>")

    metadata = parse_topic_page(html, url="https://www.aafp.org/clinical-insights/example/topic")[0].metadata

    assert metadata["status_events"] == []
    assert metadata["status_label"] is None
    assert metadata["status_date"] is None


@pytest.mark.parametrize(
    "body",
    [
        "<p><strong>Endorsed 2014</strong> with important reservations</p>",
        "<p><em>Endorsed 2014</em>, but see the newer statement</p>",
        "<h4><span>(Endorsement, July 2026)</span> for adults only</h4>",
        "<ul><li><p>Endorsed 2014</p> for adults without diabetes</li></ul>",
    ],
    ids=["strong", "em", "span", "block-inside-block"],
)
def test_a_status_nested_inside_qualifying_prose_is_not_accepted(body: str) -> None:
    """A status is read from the whole block, never from an inline fragment.

    Reading only the bold part would drop the qualifier that changes what the
    stance actually means.
    """
    html = _single_drawer_page(f"{body}<p>Recommendation text.</p>")

    metadata = parse_topic_page(html, url="https://www.aafp.org/clinical-insights/example/topic")[0].metadata

    assert metadata["status_events"] == []
    assert metadata["status_label"] is None
    assert metadata["status_date"] is None


@pytest.mark.parametrize(
    "body",
    [
        "<p>(Endorsement, July 2026)</p>",
        "<h4>(Endorsement, July 2026)</h4>",
        "<p><strong>(Endorsement, July 2026)</strong></p>",
    ],
    ids=["paragraph", "heading", "wholly-bold-paragraph"],
)
def test_ordinary_status_blocks_still_parse(body: str) -> None:
    """Excluding inline fragments must not break normal status labels."""
    html = _single_drawer_page(f"{body}<p>Recommendation text.</p>")

    metadata = parse_topic_page(html, url="https://www.aafp.org/clinical-insights/example/topic")[0].metadata

    assert metadata["status_events"] == [{"status": "endorsed", "date": "July 2026"}]
    assert metadata["status_date"] == "July 2026"


@pytest.mark.parametrize(
    "html_text",
    ["", "   \n  ", "<!-- nothing here -->"],
    ids=["empty", "whitespace", "comment-only"],
)
def test_unparseable_page_html_becomes_a_source_error(html_text: str) -> None:
    """An empty or malformed body raises AafpFetchError, not a bare lxml error."""
    with pytest.raises(AafpFetchError, match="Could not parse AAFP page HTML"):
        parse_topic_page(html_text, url=_TOPIC_URL)


def test_missing_full_guideline_link_is_reported_as_none() -> None:
    """A drawer without a full-guideline link records None, not a guessed URL."""
    html = _single_drawer_page('<p>See the <a href="/podcast/42">related podcast</a>.</p>')

    metadata = parse_topic_page(html, url=_TOPIC_URL)[0].metadata

    assert metadata["full_guideline_url"] is None
    assert metadata["source_links"] == [{"text": "related podcast", "url": "https://www.aafp.org/podcast/42"}]


def test_read_the_recommendation_is_selected_over_an_unrelated_reference() -> None:
    """The dedicated guideline URL selects the observed AAFP recommendation wording."""
    html = _single_drawer_page(
        """
        <p>
          <a href="/references/background">Related reference</a>
        </p>
        <p>
          <a href="/recommendations/example">Read the recommendation</a>
        </p>
        """
    )

    metadata = parse_topic_page(html, url=_TOPIC_URL)[0].metadata

    assert metadata["full_guideline_url"] == ("https://www.aafp.org/recommendations/example")
    assert metadata["source_links"] == [
        {
            "text": "Related reference",
            "url": "https://www.aafp.org/references/background",
        },
        {
            "text": "Read the recommendation",
            "url": "https://www.aafp.org/recommendations/example",
        },
    ]


def test_drawer_anchor_produces_a_fragment_url() -> None:
    """A stable drawer anchor gives citation-precise provenance."""
    html = _topic_page(
        sections=_category_section(
            label="Clinical practice guidelines",
            drawers=_drawer(title="Anchored recommendation", body="<p>Text.</p>", drawer_id="rec-1"),
        )
    )

    document = parse_topic_page(html, url=_TOPIC_URL)[0]

    assert document.url == f"{_TOPIC_URL}#rec-1"
    assert document.metadata["anchor"] == "rec-1"
    assert document.metadata["topic_url"] == _TOPIC_URL


def test_valid_page_without_a_guidelines_section_returns_no_documents() -> None:
    """A Clinical Insights page with no recommendations is empty, not an error."""
    html = _topic_page(
        guidelines_heading=False,
        sections="""
          <h2>Practice and payment resources</h2>
          <div class="drawer__wrapper">
            <h3 class="drawer-header">Coding guidance</h3>
            <p>Billing text.</p>
          </div>
        """,
    )

    assert parse_topic_page(html, url=_TOPIC_URL) == []


def test_visible_guidelines_section_with_unparseable_drawers_raises() -> None:
    """Silently returning zero documents would hide a layout change."""
    html = _topic_page(
        sections="""
          <div class="container accordion--layout-standard">
            <h3 class="accordion-heading-renamed">Clinical practice guidelines</h3>
            <div class="drawer-wrapper-renamed">
              <h3 class="drawer-header">Some recommendation</h3>
              <p>Recommendation text.</p>
            </div>
          </div>
        """,
    )

    with pytest.raises(AafpFetchError, match="no recommendation drawers could be parsed"):
        parse_topic_page(html, url=_TOPIC_URL)


def test_missing_main_content_raises() -> None:
    """A response without the expected page boundary is a source error."""
    with pytest.raises(AafpFetchError, match="no main#main-content"):
        parse_topic_page("<html><body><div>Something else</div></body></html>", url=_TOPIC_URL)


def test_service_interruption_page_raises() -> None:
    """A known AAFP error page is reported instead of parsed as empty."""
    html = (
        "<html><head><title>Service Interruption | AAFP</title></head><body><h1>Service Interruption</h1></body></html>"  # noqa: E501
    )

    with pytest.raises(AafpFetchError, match="error page"):
        parse_topic_page(html, url=_TOPIC_URL)


def test_parse_sitemap_reads_a_namespaced_url_set() -> None:
    """URL-set sitemaps are parsed regardless of XML namespace."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.aafp.org/clinical-insights/cardiometabolic-health/hypertension</loc></url>
      <url><loc>https://www.aafp.org/about/policies.html</loc></url>
    </urlset>
    """

    assert parse_sitemap(xml) == SitemapLinks(
        page_urls=[
            "https://www.aafp.org/clinical-insights/cardiometabolic-health/hypertension",
            "https://www.aafp.org/about/policies.html",
        ],
        sitemap_urls=[],
    )


def test_parse_sitemap_reads_a_sitemap_index_and_rejects_other_domains() -> None:
    """Nested sitemap references are restricted to the AAFP domain."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://www.aafp.org/sitemap-clinical.xml</loc></sitemap>
      <sitemap><loc>https://cdn.example.org/sitemap-other.xml</loc></sitemap>
    </sitemapindex>
    """

    assert parse_sitemap(xml) == SitemapLinks(
        page_urls=[],
        sitemap_urls=["https://www.aafp.org/sitemap-clinical.xml"],
    )


def test_parse_sitemap_rejects_malformed_xml() -> None:
    """Malformed sitemap XML is a source-specific error."""
    with pytest.raises(AafpFetchError, match="Could not parse AAFP sitemap XML"):
        parse_sitemap("<urlset><url><loc>broken")


@pytest.mark.parametrize(
    "xml",
    [
        "<html><body><h1>Page not found</h1></body></html>",
        "<rss version='2.0'><channel></channel></rss>",
        "<error>Service Interruption</error>",
    ],
    ids=["html-error-page", "rss-feed", "error-document"],
)
def test_parse_sitemap_rejects_an_unexpected_root_element(xml: str) -> None:
    """A well-formed document that is not a sitemap is rejected, not read as empty."""
    with pytest.raises(AafpFetchError, match="expected one of sitemapindex, urlset"):
        parse_sitemap(xml)


def test_parse_sitemap_does_not_resolve_external_entities() -> None:
    """The sitemap parser resolves no entities and opens no external resource."""
    xml = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE urlset [<!ENTITY leak SYSTEM "file:///etc/passwd">]>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>&leak;</loc></url>"
        "<url><loc>https://www.aafp.org/clinical-insights/heart/hypertension</loc></url>"
        "</urlset>"
    )

    assert parse_sitemap(xml) == SitemapLinks(
        page_urls=["https://www.aafp.org/clinical-insights/heart/hypertension"],
        sitemap_urls=[],
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.aafp.org/clinical-insights/cardiometabolic-health/hypertension", True),
        ("https://www.aafp.org/clinical-insights/cardiometabolic-health/hypertension/", True),
        ("https://www.aafp.org/clinical-insights/cardiometabolic-health", False),
        ("https://www.aafp.org/clinical-insights", False),
        ("https://www.aafp.org/about/policies.html", False),
        ("https://example.org/clinical-insights/a/b", False),
        ("http://www.aafp.org/clinical-insights/a/b", False),
        ("https://www.aafp.org/clinical-insights/a/b.pdf", False),
        ("https://www.aafp.org/clinical-insights/search/results", False),
    ],
    ids=[
        "topic-page",
        "trailing-slash",
        "category-landing",
        "hub",
        "other-section",
        "other-domain",
        "insecure-scheme",
        "pdf-asset",
        "excluded-segment",
    ],
)
def test_is_topic_url_filters_discovery_candidates(url: str, expected: bool) -> None:
    """Discovery only keeps Clinical Insights topic pages on the AAFP domain."""
    assert is_topic_url(url) is expected


def test_discover_topic_urls_follows_nested_sitemaps_and_deduplicates() -> None:
    """Discovery walks a sitemap index once and returns unique topic URLs in order."""
    requested: list[str] = []
    index_xml = """<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://www.aafp.org/sitemap-a.xml</loc></sitemap>
      <sitemap><loc>https://www.aafp.org/sitemap-a.xml</loc></sitemap>
    </sitemapindex>
    """
    child_xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.aafp.org/clinical-insights/heart/hypertension</loc></url>
      <url><loc>https://www.aafp.org/clinical-insights/heart/hypertension/</loc></url>
      <url><loc>https://www.aafp.org/clinical-insights/heart</loc></url>
      <url><loc>https://www.aafp.org/about/policies.html</loc></url>
      <url><loc>https://www.aafp.org/clinical-insights/lungs/asthma</loc></url>
    </urlset>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text=index_xml if request.url.path == "/sitemap.xml" else child_xml)

    with _mock_client(handler) as client:
        topic_urls = discover_topic_urls(client, sitemap_url=_SITEMAP_URL)

    assert requested == [_SITEMAP_URL, "https://www.aafp.org/sitemap-a.xml"]
    assert topic_urls == [
        "https://www.aafp.org/clinical-insights/heart/hypertension",
        "https://www.aafp.org/clinical-insights/lungs/asthma",
    ]


def test_scrape_topic_page_fetches_the_page_once_for_many_documents() -> None:
    """A page that yields several documents still costs one HTTP request."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text=_TOPIC_HTML)

    with _mock_client(handler) as client:
        documents = scrape_topic_page(client, _TOPIC_URL)

    assert len(documents) == 3
    assert requested == [_TOPIC_URL]


def test_scrape_topic_page_rejects_a_non_aafp_url() -> None:
    """The single-URL path refuses URLs from other domains before fetching."""
    with _mock_client(lambda request: httpx.Response(200, text=_TOPIC_HTML)) as client:
        with pytest.raises(AafpFetchError, match="Enter an AAFP URL"):
            scrape_topic_page(client, "https://example.org/clinical-insights/a/b")


def test_scrape_topic_page_wraps_http_errors() -> None:
    """A failed response becomes a source-specific error, not a bare httpx error."""
    with _mock_client(lambda request: httpx.Response(503, text="unavailable")) as client:
        with pytest.raises(AafpFetchError, match="Could not fetch AAFP page"):
            scrape_topic_page(client, _TOPIC_URL)


@pytest.mark.parametrize(
    ("documents", "expected_titles"),
    [
        (1, ["Depression following acute coronary syndrome"]),
        (
            None,
            [
                "Depression following acute coronary syndrome",
                "High blood pressure – AHA/ACC",
                "Abdominal aortic aneurysm",
            ],
        ),
    ],
    ids=["documents-1", "documents-all"],
)
def test_scrape_aafp_url_mode_limits_output_documents(
    monkeypatch: pytest.MonkeyPatch,
    documents: int | None,
    expected_titles: list[str],
) -> None:
    """--url scrapes one page, and --documents limits the documents it returns."""
    monkeypatch.setattr(
        aafp,
        "default_client",
        lambda: _mock_client(lambda request: httpx.Response(200, text=_TOPIC_HTML)),
    )

    scrape_run = scrape_aafp(documents=documents, url=_TOPIC_URL)
    scraped = list(scrape_run)

    assert [document.title for document in scraped] == expected_titles
    assert scrape_run.total == len(expected_titles)


def test_scrape_aafp_discovery_limits_documents_and_fetches_each_page_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery mode counts output documents and delays between pages, not drawers."""
    requested: list[str] = []
    delays: list[float] = []
    sitemap_xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.aafp.org/clinical-insights/heart/hypertension</loc></url>
      <url><loc>https://www.aafp.org/clinical-insights/lungs/asthma</loc></url>
    </urlset>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap_xml)
        return httpx.Response(200, text=_TOPIC_HTML)

    monkeypatch.setattr(aafp, "default_client", lambda: _mock_client(handler))
    monkeypatch.setattr(aafp.time, "sleep", delays.append)

    scrape_run = scrape_aafp(documents=4)
    scraped = list(scrape_run)

    assert scrape_run.total is None
    assert len(scraped) == 4
    assert requested == [
        _SITEMAP_URL,
        "https://www.aafp.org/clinical-insights/heart/hypertension",
        "https://www.aafp.org/clinical-insights/lungs/asthma",
    ]
    # Three documents came from the first page for one delay-free request.
    assert delays == [aafp.PAGE_DELAY_SECONDS]


def test_scrape_aafp_discovery_supports_all_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    """--documents all scrapes every document from every discovered page."""
    sitemap_xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.aafp.org/clinical-insights/heart/hypertension</loc></url>
      <url><loc>https://www.aafp.org/clinical-insights/lungs/asthma</loc></url>
    </urlset>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap_xml)
        return httpx.Response(200, text=_TOPIC_HTML)

    monkeypatch.setattr(aafp, "default_client", lambda: _mock_client(handler))
    monkeypatch.setattr(aafp.time, "sleep", lambda seconds: None)

    assert len(list(scrape_aafp(documents=None))) == 6


def test_scrape_aafp_discovery_skips_a_page_it_cannot_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """One drifted page is logged and skipped instead of ending a long crawl."""
    sitemap_xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.aafp.org/clinical-insights/broken/page</loc></url>
      <url><loc>https://www.aafp.org/clinical-insights/heart/hypertension</loc></url>
    </urlset>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap_xml)
        if request.url.path.startswith("/clinical-insights/broken"):
            return httpx.Response(200, text="<html><body><div>drifted</div></body></html>")
        return httpx.Response(200, text=_TOPIC_HTML)

    monkeypatch.setattr(aafp, "default_client", lambda: _mock_client(handler))
    monkeypatch.setattr(aafp.time, "sleep", lambda seconds: None)

    assert len(list(scrape_aafp(documents=None))) == 3


def test_scrape_aafp_raises_when_discovery_finds_no_topic_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Broken discovery must not look like an AAFP site with nothing on it."""
    sitemap_xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.aafp.org/about/policies.html</loc></url>
      <url><loc>https://www.aafp.org/clinical-insights</loc></url>
    </urlset>
    """

    monkeypatch.setattr(
        aafp,
        "default_client",
        lambda: _mock_client(lambda request: httpx.Response(200, text=sitemap_xml)),
    )

    with pytest.raises(AafpFetchError, match="found no eligible topic pages"):
        list(scrape_aafp(documents=None))


def test_scrape_aafp_raises_when_a_whole_crawl_produces_no_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    """Topic pages that all yield nothing mean discovery or parsing is broken."""
    sitemap_xml = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.aafp.org/clinical-insights/heart/hypertension</loc></url>
      <url><loc>https://www.aafp.org/clinical-insights/lungs/asthma</loc></url>
    </urlset>
    """
    # A valid Clinical Insights page with no guidelines section: zero documents
    # is correct for one page, but never for the entire crawl.
    empty_page = _topic_page(guidelines_heading=False, sections="<h2>Practice and payment resources</h2>")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap_xml)
        return httpx.Response(200, text=empty_page)

    monkeypatch.setattr(aafp, "default_client", lambda: _mock_client(handler))
    monkeypatch.setattr(aafp.time, "sleep", lambda seconds: None)

    with pytest.raises(AafpFetchError, match="produced no documents"):
        list(scrape_aafp(documents=None))


def test_cli_discovery_failure_is_not_a_successful_empty_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI must not print 'scraped 0 documents from aafp' when discovery breaks."""
    runner = CliRunner()

    def failing_scrape(*, documents: int | None, link_mode: LinkMode, url: str | None) -> ScrapeRun:
        def documents_iter():
            raise AafpFetchError("AAFP discovery found no eligible topic pages")
            yield  # pragma: no cover - makes this a generator

        return ScrapeRun(documents_iter(), total=None)

    monkeypatch.setitem(SCRAPERS, "aafp", failing_scrape)

    result = runner.invoke(app, ["--source", "aafp", "--documents", "all", "--no-progress"])

    assert result.exit_code != 0
    assert "scraped 0 documents" not in result.stderr


def test_aafp_is_a_registered_cli_source() -> None:
    """The CLI registry exposes the AAFP scraper."""
    assert SCRAPERS["aafp"] is aafp.scrape_aafp


def test_cli_runs_the_aafp_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """--source aafp dispatches to the AAFP scraper with the CLI's arguments."""
    runner = CliRunner()

    def fake_scrape_aafp(*, documents: int | None, link_mode: LinkMode, url: str | None) -> ScrapeRun:
        assert documents == 1
        assert link_mode is LinkMode.KEEP
        assert url == _TOPIC_URL
        return ScrapeRun(parse_topic_page(_TOPIC_HTML, url=_TOPIC_URL)[:1], total=1)

    monkeypatch.setitem(SCRAPERS, "aafp", fake_scrape_aafp)

    result = runner.invoke(app, ["--source", "aafp", "--url", _TOPIC_URL, "--no-progress"])

    assert result.exit_code == 0
    record = json.loads(result.stdout.splitlines()[0])
    assert record["source"] == "aafp"
    assert record["external_id"].startswith(f"aafp-{_TOPIC_SLUG}-clinical-practice-guidelines-")


def test_cli_all_sources_runs_every_registered_scraper(monkeypatch: pytest.MonkeyPatch) -> None:
    """--source all runs every registered scraper in registry order."""
    runner = CliRunner()
    ran: list[str] = []

    def fake(name: str):
        def scrape(*, documents: int | None, link_mode: LinkMode, url: str | None) -> ScrapeRun:
            ran.append(name)
            return ScrapeRun([], total=0)

        return scrape

    for name in SCRAPERS:
        monkeypatch.setitem(SCRAPERS, name, fake(name))

    result = runner.invoke(app, ["--source", ALL_SOURCES, "--no-progress"])

    assert result.exit_code == 0
    assert ran == list(SCRAPERS)


def test_network_guard_is_active() -> None:
    """The autouse guard proves these tests cannot reach the real AAFP site."""
    with pytest.raises(AssertionError, match="must not use the network"):
        socket.socket().connect(("www.aafp.org", 443))
