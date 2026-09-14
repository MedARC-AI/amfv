"""Tests for MAGICapp scraping.

One test per behavior class, mirroring test_scraping_nice.py's density: catalogue access,
document selection, the repair pipeline's highest-stakes passes, the structural section
filter, and end-to-end rendering.
"""

import httpx
import pytest

from amfv_datasets.scraping import base, magic
from amfv_datasets.scraping.magic import (
    build_magic_guideline_text,
    list_published_guidelines,
    scrape_magic,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

JSON_PATH = "https://s3.amazonaws.com/files.magicapp.org/guideline/abc/guideline_1-1_0.json"
OTHER_JSON_PATH = "https://s3.amazonaws.com/files.magicapp.org/guideline/def/guideline_2-1_0.json"


CATALOGUE = [
    {
        "shortCode": "nyxpZL",
        "guidelineId": 10718,
        "publishedId": 9204,
        "name": "Management of juvenile idiopathic arthritis",
        "jsonPath": JSON_PATH,
        "language": "en-au",
        "institutionName": "ANZMUSC",
        "publishDate": "2025-10-22",
        "publishedRecommendationCount": "10",
    },
    {
        "shortCode": "dutch1",
        "guidelineId": 200,
        "name": "Nederlandse richtlijn",
        "jsonPath": OTHER_JSON_PATH,
        "language": "nl",
        "institutionName": "NHG",
        "publishDate": "2025-01-01",
        "publishedRecommendationCount": 4,
    },
    {
        "shortCode": "",
        "guidelineId": 300,
        "name": "Unpublished draft",
        "jsonPath": "",
        "language": "en",
        "institutionName": "Nobody",
        "publishDate": "",
        "publishedRecommendationCount": 0,
    },
]


GUIDELINE = {
    "name": "Management of juvenile idiopathic arthritis",
    "sections": [
        {
            "heading": "How To Use This Guideline",
            "text": "<p>Click a section heading to open the evidence behind it in MAGICapp.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Disease modifying therapy",
            "text": "<p>Methotrexate is the anchor drug for polyarticular disease.</p>",
            "recommendations": [
                {
                    "text": "<p>Offer methotrexate as first-line therapy.</p>",
                    "remarks": "",
                    "strength": "STRONG",
                }
            ],
            "subSections": [],
        },
    ],
}


def _client(catalogue: list[dict] | None = None, guideline: dict | None = None) -> httpx.Client:
    """Build a client whose transport serves the catalogue and one guideline."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.magicapp.org":
            return httpx.Response(200, json=CATALOGUE if catalogue is None else catalogue)
        if str(request.url) == JSON_PATH:
            return httpx.Response(200, json=GUIDELINE if guideline is None else guideline)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _catalogue_entry(short_code: str, name: str, json_path: str, institution: str = "ANZMUSC") -> dict:
    return {
        "shortCode": short_code,
        "guidelineId": 1,
        "name": name,
        "jsonPath": json_path,
        "language": "en",
        "institutionName": institution,
        "publishedRecommendationCount": 1,
    }


def _run_catalogue(
    monkeypatch: pytest.MonkeyPatch,
    catalogue: list[dict],
    bodies: dict[str, dict],
    *,
    include_drafts: bool = False,
) -> list:
    """Run scrape_magic over an in-memory catalogue, serving `bodies` by json path."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.magicapp.org":
            return httpx.Response(200, json=catalogue)
        body = bodies.get(str(request.url))
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404)

    # scrape_magic opens one client for the catalogue and another for the documents,
    # so the factory has to hand back a fresh client each time.
    monkeypatch.setattr(magic, "default_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)
    return list(scrape_magic(documents=None, include_drafts=include_drafts))


def _render(guideline: dict) -> str:
    """Render a guideline payload through the real code path."""
    with _client(guideline=guideline) as client:
        ref = list_published_guidelines(client).refs[0]
        content, _section_count, _title, _emitted = build_magic_guideline_text(client, ref)
    return content


def _render_body(html: str) -> str:
    """Render one section body through the real code path."""
    return _render(
        {
            "name": "Malaria",
            "sections": [
                {
                    "heading": "Treating malaria",
                    "text": html,
                    "recommendations": [{"text": "<p>Treat promptly.</p>", "strength": "STRONG"}],
                    "subSections": [],
                }
            ],
        }
    )


# ---------------------------------------------------------------------------
# Catalogue access and document selection
# ---------------------------------------------------------------------------


def test_list_published_guidelines_parses_the_catalogue() -> None:
    """English entries become refs; unpublished rows and other languages are dropped by default.

    The recommendation count arrives as a string in real catalogue rows; it must parse rather
    than crash.
    """
    with _client() as client:
        page = list_published_guidelines(client)

    assert [ref.short_code for ref in page.refs] == ["nyxpZL"]
    ref = page.refs[0]
    assert ref.title == "Management of juvenile idiopathic arthritis"
    assert ref.institution == "ANZMUSC"
    assert ref.recommendation_count == 10


def test_scrape_magic_skips_tutorial_and_workshop_organisations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Training material is dropped, and a real guideline is not."""
    monkeypatch.setattr(magic, "MIN_CONTENT_CHARS", 1)
    scraped = _run_catalogue(
        monkeypatch,
        [
            _catalogue_entry("tutor1", "TUTORIAL - BMJ RapidRecs", OTHER_JSON_PATH, "MAGICapp Tutorials"),
            _catalogue_entry("nyxpZL", "Management of juvenile idiopathic arthritis", JSON_PATH),
        ],
        {OTHER_JSON_PATH: GUIDELINE, JSON_PATH: GUIDELINE},
    )

    assert [document.external_id for document in scraped] == ["magic-nyxpZL"]


def test_scrape_magic_drops_publisher_marked_drafts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A title the publisher marks a draft is dropped by default and kept with include_drafts."""
    monkeypatch.setattr(magic, "MIN_CONTENT_CHARS", 1)
    catalogue = [
        _catalogue_entry("j97pAn", "DRAFT FOR PUBLIC CONSULTATION: Dementia Guidelines", OTHER_JSON_PATH),
        _catalogue_entry("nyxpZL", "Management of juvenile idiopathic arthritis", JSON_PATH),
    ]
    bodies = {OTHER_JSON_PATH: GUIDELINE, JSON_PATH: GUIDELINE}

    scraped = _run_catalogue(monkeypatch, catalogue, bodies)
    kept = _run_catalogue(monkeypatch, catalogue, bodies, include_drafts=True)

    assert [document.external_id for document in scraped] == ["magic-nyxpZL"]
    assert [document.external_id for document in kept] == ["magic-j97pAn", "magic-nyxpZL"]


def test_scrape_magic_skips_unreadable_guidelines_instead_of_aborting(monkeypatch: pytest.MonkeyPatch) -> None:
    """One refused guideline is logged and skipped, leaving the rest of the run intact."""
    monkeypatch.setattr(magic, "MIN_CONTENT_CHARS", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.magicapp.org":
            return httpx.Response(
                200,
                json=[
                    _catalogue_entry("broken", "Beta-blockers for hypertension", OTHER_JSON_PATH),
                    _catalogue_entry("nyxpZL", "Management of juvenile idiopathic arthritis", JSON_PATH),
                ],
            )
        if str(request.url) == JSON_PATH:
            return httpx.Response(200, json=GUIDELINE)
        return httpx.Response(500)

    monkeypatch.setattr(magic, "default_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)
    scraped = [document for document in scrape_magic(documents=None) if document.content]

    assert [document.external_id for document in scraped] == ["magic-nyxpZL"]
    assert "methotrexate" in scraped[0].content.lower()


# ---------------------------------------------------------------------------
# The repair pipeline's highest-stakes passes
# ---------------------------------------------------------------------------


def test_build_magic_guideline_text_drops_editor_deletions() -> None:
    """Track-changes deletions are dropped rather than concatenated with their replacement.

    MAGICapp publishes suggestion markup as-is, so 'may' next to a deleted 'can' and a
    deleted 'may' read 'maycanmay', and an inserted '6' beside a deleted '8' turned 96%
    into 968% - corrupted numbers inside otherwise perfect passages.
    """
    content = _render(
        {
            "name": "Stroke rehabilitation",
            "sections": [
                {
                    "heading": "Arm activity",
                    "text": "",
                    "subSections": [],
                    "recommendations": [
                        {
                            "text": (
                                "<p>Mechanically assisted arm training "
                                '<span class="ck-suggestion-marker ck-suggestion-marker-insertion">may</span>'
                                '<span class="ck-suggestion-marker ck-suggestion-marker-deletion">'
                                '<span class="ck-suggestion-marker ck-suggestion-marker-insertion">can</span></span>'
                                '<span class="ck-suggestion-marker ck-suggestion-marker-deletion">may</span>'
                                " be used. Two trials contributed 9"
                                '<span class="ck-suggestion-marker ck-suggestion-marker-insertion">6</span>'
                                '<span class="ck-suggestion-marker ck-suggestion-marker-deletion">8</span>'
                                "% of the data.</p>"
                            ),
                            "strength": "WEAK",
                        }
                    ],
                }
            ],
        }
    )

    assert "maycanmay" not in content
    assert "968" not in content
    assert "may be used" in content
    assert "96% of the data" in content


def test_build_magic_guideline_text_emits_no_embedded_image_data() -> None:
    """No embedded image reaches the corpus - inline base64 or URL, alt text included.

    Image encoding was 53.9% of everything this scraper emitted before the fix, and one
    guideline was 98.2% image data.
    """
    content = _render(
        {
            "name": "Stroke care",
            "sections": [
                {
                    "heading": "Imaging",
                    "text": (
                        '<p>Perform CT.<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAABEs" alt=""></p>'
                        '<p>Repeat imaging at 24 hours.<img src="https://images.magicapp.org/abc123.jpeg"'
                        ' alt="A diagram of a patient\'s health\n\ncare pathway" width="800"></p>'
                    ),
                    "recommendations": [
                        {
                            "text": '<p>Offer thrombolysis.<img src="data:image/png;base64,AAAABBBB" alt=""></p>',
                            "strength": "STRONG",
                        }
                    ],
                    "subSections": [],
                }
            ],
        }
    )

    assert "base64" not in content
    assert "data:image" not in content
    assert "images.magicapp.org" not in content
    assert "care pathway" not in content
    assert "Perform CT." in content
    assert "Repeat imaging at 24 hours." in content
    assert "Offer thrombolysis." in content


def test_build_magic_guideline_text_expands_table_spans() -> None:
    """A spanned cell must not shift later cells into the wrong column.

    In a GRADE diagnostic-accuracy table, flattening a three-column span put
    true-positive counts under the wrong headers; in a dosing table, a drug name
    spanning two rows pushed 'Children' into the drug-name column.
    """
    content = _render_body(
        "<table><tr><td>Threshold</td><td>Studies</td><td colspan='3'>Prevalence of 5%</td></tr>"
        "<tr><td>&gt;30%</td><td>4</td><td>TP 91</td><td>FP 56</td><td>FN 8</td></tr></table>"
        "<table><tr><td rowspan='2'>Artesunate</td><td>Adults</td><td>2.4 mg/kg</td></tr>"
        "<tr><td>Children</td><td>3.0 mg/kg</td></tr></table>"
    )

    assert "| Threshold | Studies | Prevalence of 5% |  |  |" in content
    assert "| >30% | 4 | TP 91 | FP 56 | FN 8 |" in content
    assert "| Artesunate | Adults | 2.4 mg/kg |" in content
    assert "|  | Children | 3.0 mg/kg |" in content


# ---------------------------------------------------------------------------
# The structural section filter
# ---------------------------------------------------------------------------


def test_build_magic_guideline_text_drops_sections_without_clinical_structure() -> None:
    """A non-clinical section is dropped whole; the clinical section beside it is kept.

    No recommendations, no PICOs, and no prose guidance means the section carries nothing a
    verifier could cite.
    """
    content = _render(GUIDELINE)

    assert "How To Use This Guideline" not in content
    assert "Click a section heading" not in content
    assert "Disease modifying therapy" in content
    assert "Methotrexate is the anchor drug" in content
    assert "Offer methotrexate as first-line therapy." in content


def test_build_magic_guideline_text_keeps_guidance_written_as_prose() -> None:
    """Guidance written as prose survives the structural filter.

    Five corpus guidelines carry ALL their advice as prose, with no recommendation or PICO objects;
    executive summaries often restate recommendations only in prose. The filter must see that
    language, or whole guidelines render to nothing.
    """
    content = _render(
        {
            "name": "Cancer pain management in adults",
            "sections": [
                {
                    "heading": "Opioid therapy",
                    "text": "<p>We recommend immediate-release oral morphine for breakthrough pain.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
                {
                    "heading": "Acknowledgements",
                    "text": "<p>The committee thanks the funders and the secretariat.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
            ],
        }
    )

    assert "immediate-release oral morphine" in content
    assert "Acknowledgements" not in content
    assert "thanks the funders" not in content


def test_build_magic_guideline_text_drops_bibliographies() -> None:
    """Bibliographies go whether they are sections or pasted inside a section body.

    Cited paper titles quote guidance language ("...is recommended: a systematic review"), so
    the prose signal cannot tell a reference list from advice; the one name-based drop rule
    handles both shapes.
    """
    content = _render(
        {
            "name": "Melanoma",
            "sections": [
                {
                    "heading": "Excision margins",
                    "text": (
                        "<p>We recommend a 1 cm margin for melanomas under 1 mm.</p>"
                        "<h2>References</h2><ol><li>Smith J. Wide excision is recommended: a trial.</li></ol>"
                    ),
                    "recommendations": [],
                    "subSections": [
                        {
                            "heading": "References",
                            "text": "<ol><li>Jones K. Sentinel biopsy is recommended: a review.</li></ol>",
                            "recommendations": [],
                            "subSections": [],
                        }
                    ],
                },
            ],
        }
    )

    assert "1 cm margin" in content
    assert "References" not in content
    assert "Smith J." not in content
    assert "Jones K." not in content
