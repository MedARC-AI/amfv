"""Tests for MAGICapp scraping helpers.

Sections mirror magic.py's layout: shared fixtures, then catalogue access, HTML repair,
conversion and markdown cleanup, section keep/drop policy, recommendation and PICO
rendering, document assembly, and the scrape API.
"""

import time

import httpx
import pytest

from amfv_datasets.scraping import base, magic
from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.magic import (
    _INLINE_PAPERWORK_HINT_RE,
    _INLINE_PAPERWORK_LABELS,
    GuidelineListingPage,
    GuidelineRef,
    MagicFetchError,
    build_magic_guideline_text,
    list_published_guidelines,
    magic_ref_from_url,
    scrape_magic,
    scrape_magic_guideline,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

JSON_PATH = "https://s3.amazonaws.com/files.magicapp.org/guideline/abc/guideline_1-1_0.json"


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
        "publishedRecommendationCount": 10,
    },
    {
        "shortCode": "dutch1",
        "guidelineId": 200,
        "name": "Nederlandse richtlijn",
        "jsonPath": "https://s3.amazonaws.com/files.magicapp.org/guideline/def/guideline_2-1_0.json",
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
            "text": "<p>Click a recommendation to open the evidence behind it in MAGICapp.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Disease modifying therapy",
            "text": "<p>Therapy is chosen by disease severity.</p>",
            "recommendations": [],
            "subSections": [
                {
                    "heading": "Methotrexate",
                    "text": '<p>See the <a href="https://example.org/dose">dosing table</a>.</p>',
                    "recommendations": [
                        {
                            "text": "<h3>Recommendation 1</h3><p>Consider methotrexate at 15mg/m2 once a week.</p>",
                            "remarks": "<p>Preferred over leflunomide.</p>",
                            "strength": "WEAK",
                        },
                        {
                            "text": "<p>Review response after three months.</p>",
                            "remarks": "",
                            "strength": "NOTSET",
                        },
                        {
                            "text": "<p>Box 1.1 Sustainable Development Goals.</p>",
                            "remarks": "",
                            "strength": "INFO",
                        },
                    ],
                    "subSections": [],
                }
            ],
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
            "sections": [{"heading": "Treating malaria", "text": html, "recommendations": [], "subSections": []}],
        }
    )


# ---------------------------------------------------------------------------
# Catalogue access and document selection
# ---------------------------------------------------------------------------


def test_list_published_guidelines_parses_catalogue() -> None:
    """Catalogue entries become refs, skipping non-English and incomplete rows."""
    with _client() as client:
        listing_page = list_published_guidelines(client)

    assert listing_page == GuidelineListingPage(
        total=1,
        refs=[
            GuidelineRef(
                short_code="nyxpZL",
                guideline_id=10718,
                title="Management of juvenile idiopathic arthritis",
                json_path=JSON_PATH,
                # The catalogue's publishedId, not guidelineId, is what version-pinned
                # MAGICapp URLs are built from; the two differ on every entry.
                published_id=9204,
                language="en-au",
                institution="ANZMUSC",
                publish_date="2025-10-22",
                recommendation_count=10,
            )
        ],
    )


def test_list_published_guidelines_is_empty_after_the_first_page() -> None:
    """The catalogue arrives in one response, so later pages stop the listing loop."""
    with _client() as client:
        assert list_published_guidelines(client, page=2) == GuidelineListingPage(refs=[], total=None)


def test_guideline_ref_page_url_points_at_the_viewer() -> None:
    """Refs expose the human-readable MAGICapp URL for the document."""
    ref = GuidelineRef(short_code="nyxpZL", guideline_id=1, title="t", json_path=JSON_PATH)
    assert ref.page_url == "https://app.magicapp.org/#/guideline/nyxpZL"


def test_magic_ref_from_url_resolves_a_short_code() -> None:
    """A viewer URL is resolved to its catalogue entry."""
    with _client() as client:
        ref = magic_ref_from_url(client, "https://app.magicapp.org/#/guideline/nyxpZL")

    assert ref.short_code == "nyxpZL"
    assert ref.json_path == JSON_PATH


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.org/#/guideline/nyxpZL", "Enter a URL like"),
        ("https://app.magicapp.org/#/dashboard", "Enter a URL like"),
        ("ftp://app.magicapp.org/#/guideline/nyxpZL", "Enter a URL like"),
    ],
    ids=["wrong-host", "not-a-guideline-path", "wrong-scheme"],
)
def test_magic_ref_from_url_rejects_unusable_urls(url: str, expected: str) -> None:
    """Unusable URLs fail with a message naming the expected shape."""
    with _client() as client:
        with pytest.raises(MagicFetchError, match=expected):
            magic_ref_from_url(client, url)


def test_magic_ref_from_url_rejects_an_unknown_short_code() -> None:
    """A well-formed URL for a guideline that is not published fails clearly."""
    with _client() as client:
        with pytest.raises(MagicFetchError, match="No published MAGICapp guideline"):
            magic_ref_from_url(client, "https://app.magicapp.org/#/guideline/zzzzzz")


def test_list_published_guidelines_upgrades_http_content_urls_to_https() -> None:
    """The file host refuses plain HTTP with 403, so http:// entries are upgraded.

    Six English catalogue entries carry an http:// jsonPath and every one of them
    is refused over HTTP but served over HTTPS. Without this they would be skipped
    as unreachable, losing guidelines that carry real recommendations.
    """
    catalogue = [
        {
            "shortCode": "eEZlLD",
            "guidelineId": 387,
            "name": "VTE, Thrombophilia, Antithrombotic Therapy and Pregnancy",
            "jsonPath": "http://files.magicapp.org/guideline/abc/json/guideline_387-0_2.json",
            "language": "en",
            "institutionName": "Norsk Selskap for Trombose og Hemostase",
            "publishedRecommendationCount": 39,
        }
    ]

    with _client(catalogue=catalogue) as client:
        ref = list_published_guidelines(client).refs[0]

    assert ref.json_path == "https://files.magicapp.org/guideline/abc/json/guideline_387-0_2.json"


def test_list_published_guidelines_tolerates_junk_numeric_fields() -> None:
    """A non-numeric numeric field in one catalogue entry does not abort the run."""
    catalogue = [dict(CATALOGUE[0], guidelineId="unknown", publishedRecommendationCount="N/A")]
    with _client(catalogue=catalogue) as client:
        refs = list_published_guidelines(client).refs

    assert refs[0].guideline_id == 0
    assert refs[0].recommendation_count == 0


# ---------------------------------------------------------------------------
# HTML repair, before conversion
# ---------------------------------------------------------------------------


def test_markdown_moves_punctuation_out_of_an_emphasis_edge_that_cannot_parse() -> None:
    """A bolded colon against a word leaves both markers visible; moving it out fixes them.

    CommonMark will not open emphasis whose first content character is punctuation when a
    word character sits against the marker, so `<i>Judgement</i><strong>:</strong>`
    converts to `*Judgement***:**` and a reader sees stray asterisks. 60% of the corpus's
    stray asterisks are an emphasis element wrapping no letter or digit at all.
    """
    content = _render_body("<p>Timing<i>Judgement</i><strong>:</strong> Small effect</p>")

    assert "Timing*Judgement*: Small effect" in content


def test_markdown_leaves_an_emphasis_edge_that_already_parses() -> None:
    """`**haemorrhage?**` closes correctly, so nothing moves.

    A first version trimmed every edge and rewrote this into `**haemorrhage**?` - same
    to a reader, repairing nothing, and changing markup across the corpus for no gain.
    The trigger is a word character *outside* the marker, not punctuation inside it.
    """
    content = _render_body("<p>Should nurses <strong>prevent haemorrhage?</strong></p>")

    assert "**prevent haemorrhage?**" in content


def test_markdown_keeps_a_nested_tag_when_unwrapping_an_emphasis_element() -> None:
    """Unwrapping promotes the children, it does not flatten to text.

    `<strong><sup>#</sup></strong>` holds a superscript `_render_scripts` has not seen
    yet. Replacing the element with its text dropped the anti-weld caret in E83abn -
    the only word the whole corpus changed, caught by a word-level diff.
    """
    content = _render_body("<p>stage <strong><sup>#</sup></strong> who achieve</p>")

    assert "^#" in content


def test_markdown_keeps_prose_after_the_last_line_of_a_split_emphasis_run() -> None:
    """Prose following a bold run that crosses a break belongs after its LAST line.

    `_split_emphasis_once` inserts the segments in reverse at one index, so the first
    node inserted ends up last in the document. The tail was being assigned to
    `parent[index]`, which is the *first* segment - putting the following prose after
    line one and leaving line two stranded at the end. Three places in the corpus, and
    what moved was substantive: a cohorting instruction in Jn37kn, the EAFT trial
    description in jbzG8j, and "There is insufficient evidence to provide a
    recommendation." in jz7xeL.
    """
    content = _render_body(
        "<p><strong>Cohorting of patients<br>Separation of beds</strong> is recommended "
        "where single rooms are unavailable, at a distance of at least one metre.</p>"
    )

    assert "**Separation of beds** is recommended where single rooms are unavailable" in content
    assert "**Cohorting of patients** is recommended" not in content


BASE64_GUIDELINE = {
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
                    "text": '<p>Offer thrombolysis.<img src="data:image/png;base64,AAAABBBBCCCCDDDD" alt=""></p>',
                    "strength": "STRONG",
                }
            ],
            "subSections": [],
        }
    ],
}


def test_build_magic_guideline_text_emits_no_embedded_image_data() -> None:
    """No embedded image reaches the corpus - inline base64 or URL, alt text included.

    MAGICapp inlines most figures as data URIs (100,361 of 101,955 img tags; the rest
    embed by URL). Left alone the encoding was 53.9% of everything this scraper
    emitted across 63 guidelines, and one guideline was 98.2% image data. The alt
    texts are visual descriptions, kept out deliberately. This is the regression
    guard for that, and for the newline the real alt texts carry mid-tag.
    """
    content = _render(BASE64_GUIDELINE)

    assert "base64" not in content
    assert "data:image" not in content
    assert "images.magicapp.org" not in content  # 1,587 figures embed by URL; dropped alike
    assert "care pathway" not in content  # the alt text goes with its image
    assert "Perform CT." in content
    # Not a pointer sentence: `_drop_pointer_paragraphs` would remove one, and this
    # assertion is here to prove the prose beside a dropped image survives.
    assert "Repeat imaging at 24 hours." in content
    assert "Offer thrombolysis." in content


TRACKED_CHANGES_GUIDELINE = {
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


def test_build_magic_guideline_text_drops_editor_deletions() -> None:
    """Track-changes deletions are dropped rather than concatenated with their replacement.

    Guidelines edited with track changes on ship CKEditor suggestion markup in the
    published JSON. Keeping both sides yields "maycanmay" and turns 96% into 968% -
    corruption landing on exactly the modal verbs and figures a verifier reads.
    """
    with _client(guideline=TRACKED_CHANGES_GUIDELINE) as client:
        ref = list_published_guidelines(client).refs[0]
        content, _, _, _ = build_magic_guideline_text(client, ref)

    assert "Mechanically assisted arm training may be used." in content
    assert "Two trials contributed 96% of the data." in content
    assert "maycanmay" not in content
    assert "968" not in content


BARE_LEADING_TRACKED_GUIDELINE = {
    "name": "Stroke rehabilitation",
    "sections": [
        {
            "heading": "Arm activity",
            "text": (
                "Electromechanical arm training "
                '<span class="ck-suggestion-marker ck-suggestion-marker-deletion">could</span>'
                " may be considered for people with mild arm weakness."
            ),
            "subSections": [],
            "recommendations": [],
        }
    ],
}


def test_build_magic_guideline_text_keeps_text_before_the_first_tag() -> None:
    """A fragment opening with bare text keeps that text when deletions are dropped.

    Text before the first tag lands on the parser's created parent rather than on
    any child, so serializing only the children silently deleted the head of any
    bare-text fragment carrying a tracked change - worst case the whole fragment.
    """
    content = _render(BARE_LEADING_TRACKED_GUIDELINE)

    assert "Electromechanical arm training may be considered for people with mild arm weakness." in content
    assert "could" not in content


BOLD_BODY_GUIDELINE = {
    "name": "Hypertension",
    "sections": [
        {
            "heading": "Therapy",
            "text": "",
            "subSections": [],
            "recommendations": [
                {
                    "text": "<p><strong>Recommendation 1:</strong> <strong>Offer an ACE inhibitor</strong> first.</p>",
                    "strength": "STRONG",
                }
            ],
        }
    ],
}


def test_build_magic_guideline_text_keeps_bold_that_belongs_to_the_body() -> None:
    """Bold immediately after the label colon keeps its markers.

    The cut consumes the label's own bold opener, so only its orphan closer is
    removed from the body. Stripping every leading asterisk instead ate the body's
    opener too and left "Offer an ACE inhibitor** first."
    """
    content = _render(BOLD_BODY_GUIDELINE)

    assert "### Recommendation 1 (STRONG)\n\n**Offer an ACE inhibitor** first." in content
    assert content.count("**") % 2 == 0


def test_build_magic_guideline_text_replaces_nested_blank_inline_tags() -> None:
    """A blank inline tag nested inside another still becomes a space, not a weld.

    One substitution pass replaces the inner tag but leaves the newly emptied outer
    one behind, and conversion then discards it together with its space, welding
    the words: "treatment andwith no contraindications".
    """
    guideline = {
        "name": "Anticoagulation",
        "sections": [
            {
                "heading": "Therapy",
                "text": "<p>treatment and<strong><em>&nbsp;</em></strong>with no contraindications</p>",
                "subSections": [],
                "recommendations": [],
            }
        ],
    }
    content = _render(guideline)

    assert "treatment and with no contraindications" in content
    assert "andwith" not in content


def test_build_magic_guideline_text_survives_a_long_non_breaking_space_run() -> None:
    r"""A long non-breaking-space run after an open tag must not stall the scraper.

    The German emergency-medicine guideline j7q2zn lays out its OPQRST mnemonic by
    padding with non-breaking spaces, giving `<span style="color:#000000">` followed by
    49 of them and then ordinary words - so the blank-inline-tag pattern's closing tag
    never arrives and the match fails. `\s` already matches `\xa0`, so while that
    pattern spelled its whitespace as the alternation `(?:\s|&nbsp;|\xa0)` each of
    those spaces matched two branches, the run had 2**49 parses, and the failing match
    walked all of them: measured at a clean doubling per added space, the whole
    guideline never finished rendering.

    The `<br>` matters and is not decoration. It is what sends the fragment through
    `_split_emphasis_across_breaks`, whose parse turns the `&nbsp;` entities into
    literal `\xa0` characters - the ambiguous form. Written as entities the run is
    harmless, which is why the shape has to be built this way to reproduce.
    """
    body = (
        '<p><span style="color:#000000">Onset<br><span style="color:#000000">'
        + "&nbsp;" * 60
        + "Wann begannen die Beschwerden?</span></span></p>"
        "<p>treatment and<strong><em>&nbsp;</em></strong>with no contraindications</p>"
    )
    guideline = {
        "name": "Notfallmedizin",
        "sections": [{"heading": "Anamnese", "text": body, "recommendations": [], "subSections": []}],
    }

    start = time.perf_counter()
    content = _render(guideline)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"inline-tag normalization took {elapsed:.1f}s on a 60-space run"
    assert "Wann begannen die Beschwerden?" in content
    # The run must still be normalized, not merely skipped over quickly.
    assert "treatment and with no contraindications" in content
    assert "andwith" not in content


MULTI_SPAN_BOLD_GUIDELINE = {
    "name": "Task shifting",
    "sections": [
        {
            "heading": "Auxiliary nurses",
            "text": "",
            "subSections": [],
            "recommendations": [
                {
                    "text": (
                        "<p><strong>RECOMMENDATION 2.3: Should nurses administer oxytocin to</strong> "
                        "<strong>prevent</strong> <strong>haemorrhage?</strong></p><p>We recommend this option.</p>"
                    ),
                    "strength": "WEAK",
                }
            ],
        }
    ],
}


def test_build_magic_guideline_text_balances_a_label_bolded_in_several_spans() -> None:
    """A label bolded together with part of the body, in several spans, stays balanced.

    WHO's task-shifting guideline bolds "RECOMMENDATION N: question" as adjacent
    bold spans, so the colon cut consumes an opener whose closer sits mid-body.
    The orphan closer is the body's first "**" and only that one is removed.
    """
    content = _render(MULTI_SPAN_BOLD_GUIDELINE)

    assert "Should nurses administer oxytocin to **prevent** **haemorrhage?**" in content
    assert "### RECOMMENDATION 2.3 (WEAK)" in content
    assert content.count("**") % 2 == 0


def test_build_magic_guideline_text_moves_whitespace_out_of_emphasis_tags() -> None:
    """Whitespace just inside an emphasis tag moves outside before conversion.

    markdownify keeps it inside, and CommonMark forbids an emphasis delimiter beside
    a space, so "<i>…)&nbsp;</i>" rendered a stray literal asterisk — 29 of the 46
    recommendations with unbalanced markers measured across the catalogue.
    """
    guideline = {
        "name": "PPH",
        "sections": [
            {
                "heading": "Acceptability",
                "text": "<p>a concern for women taking the tablets (<i>moderate confidence)&nbsp;</i>.</p>",
                "subSections": [],
                "recommendations": [],
            }
        ],
    }
    content = _render(guideline)

    assert "(*moderate confidence)*" in content


def test_markdown_removes_a_citation_without_stranding_its_separator() -> None:
    """The space holding a citation must not survive in front of the following full stop.

    WHO separates a word from its reference with a non-breaking space, so stripping
    only the marker leaves "monitoring data, respectively ." — 293 of these in the
    malaria guideline alone, each verified against WHO's own published PDF.
    """
    content = _render_body(
        "<p>Resistance was detected in 34% of sites that reported monitoring data, "
        'respectively&nbsp;<cite class="magic-cite" data-ref-id="839066" data-label="">[3]</cite>.</p>'
    )

    assert "monitoring data, respectively." in content
    assert "respectively ." not in content
    assert "[3]" not in content


def test_markdown_keeps_one_space_where_a_citation_sat_between_two_words() -> None:
    """Removing a marker between two words must leave exactly one space, not none and not two."""
    content = _render_body(
        '<p>launched in 2018&nbsp;<cite class="magic-cite">[5]</cite>&nbsp;and the effort continued.</p>'
    )

    assert "launched in 2018 and the effort continued." in content


def test_markdown_removes_a_citation_the_publisher_formatted_by_hand() -> None:
    """`<i>[66]</i>` would otherwise convert to `*[66]*` and strip to a stray `**`.

    An unpaired bold marker mid-sentence pairs with the next one and bolds everything
    between them, so the damage is not confined to where it appears.
    """
    content = _render_body("<p>lymphatic filariasis&nbsp;<i>[66]</i>. This manual is designed to assist.</p>")

    assert "lymphatic filariasis. This manual is designed to assist." in content
    assert "**" not in content


def test_markdown_removes_a_deleted_citation_whose_placeholder_is_not_a_number() -> None:
    """A deleted citation carries the placeholder `[?]`, which no number-shaped rule catches.

    Left in, it reads as the guideline doubting whether India was included in the
    review, rather than as leftover markup.
    """
    content = _render_body(
        '<p>Studies were conducted in Ethiopia, India&nbsp;<cite class="magic-cite">[78]</cite>'
        '<cite class="magic-cite" data-label="DELETED">[?]</cite>, Kenya and Pakistan.</p>'
    )

    assert "India, Kenya and Pakistan." in content
    assert "[?]" not in content


def test_markdown_stops_publisher_dashes_from_turning_a_paragraph_into_a_heading() -> None:
    """A dashes-only line under text is a setext heading underline in markdown.

    WHO ends a paragraph `...for use in all age groups.<br />--</p>`, which silently
    promoted that whole clinical paragraph to an `<h2>`.
    """
    content = _render_body(
        "<p>They judged the accumulated indirect evidence to be sufficient to recommend "
        "parenteral artesunate for use in all age groups.<br />--</p>"
        "<p>Is parenteral artesunate superior to parenteral quinine?</p>"
    )

    assert "\\--" in content
    assert "\n--\n" not in content


def test_markdown_leaves_real_tables_and_horizontal_rules_alone() -> None:
    """The escape must not touch a table's separator row or a genuine horizontal rule."""
    table = _render_body("<table><tr><th>Weight</th><th>Dose</th></tr><tr><td>8</td><td>20 mg</td></tr></table>")
    rule = _render_body("<p>alpha</p><hr><p>beta</p>")

    assert "| --- | --- |" in table
    assert "\\-" not in table
    assert "\n---\n" in rule


def test_markdown_does_not_weld_words_when_only_a_citation_separated_them() -> None:
    """Removing a marker must never join two words, the damage blank tags used to do.

    Two real shapes: the space sits *inside* the marker
    (`counselling<sup>[11] </sup>and`), and the publisher relies on the superscript
    itself to separate the words (`NICE<a><sup>[1]</sup></a>modelling`). Both were
    found by rendering the whole corpus and diffing word counts, not by the tests.
    """
    inside = _render_body("<p>referring patients for telephone counselling<sup>[11] </sup>and in a study.</p>")
    relied_on = _render_body(
        '<p>In addition to the NICE<a href="https://wiki.cancer.org.au/x#cite_note-Y-1">'
        "<sup>[1]</sup></a>modelling study, we considered others.</p>"
    )

    assert "telephone counselling and in a study." in inside
    assert "the NICE modelling study" in relied_on


def test_markdown_removes_a_citation_link_without_leaving_an_empty_link() -> None:
    """Cancer Council hangs the marker inside the reference link.

    Removing only the marker left `[](https://wiki.cancer.org.au/…)` — a link that
    displays nothing, carrying a 300-character URL into the corpus, 569 times.
    """
    content = _render_body(
        "<p>malnourished patients had an increased risk post surgery"
        '<a href="https://wiki.cancer.org.au/australia/COSA#cite_note-Citation:Siroen_2006-1">'
        "<sup>[1]</sup></a>; reduced survival at baseline <sup>[2][5]</sup>.</p>"
    )

    assert "post surgery; reduced survival at baseline." in content
    assert "wiki.cancer.org.au" not in content
    assert "[]" not in content


def test_markdown_expands_a_cell_that_spans_columns() -> None:
    """A spanned header must not shift every cell after it into the wrong column.

    In a GRADE diagnostic-accuracy table the headers "Prevalence of 5/10/20%" span the
    last three of nine columns, over the true-positive and false-positive counts.
    Flattened to one cell each, the row came out short and the labels landed under
    Threshold, Studies and Participants instead.
    """
    content = _render_body(
        "<table><tr><td>Threshold</td><td>Studies</td><td colspan='3'>Prevalence of 5%</td></tr>"
        "<tr><td>&gt;30%</td><td>4</td><td>TP 91</td><td>FP 56</td><td>FN 8</td></tr></table>"
    )
    rows = [line for line in content.splitlines() if line.startswith("|")]

    assert [row.count("|") for row in rows] == [6, 6, 6, 6]
    assert "| Threshold | Studies | Prevalence of 5% |  |  |" in content
    assert "| >30% | 4 | TP 91 | FP 56 | FN 8 |" in content


def test_markdown_expands_a_cell_that_spans_rows() -> None:
    """A drug name spanning two rows must not push the second row's data into its column.

    Without the filler, "Children" lands in the column that holds the drug name and
    reads as one.
    """
    content = _render_body(
        "<table><tr><td rowspan='2'>Artesunate</td><td>Adults</td><td>2.4 mg/kg</td></tr>"
        "<tr><td>Children</td><td>3.0 mg/kg</td></tr></table>"
    )

    assert "| Artesunate | Adults | 2.4 mg/kg |" in content
    assert "|  | Children | 3.0 mg/kg |" in content


def test_markdown_keeps_the_number_when_a_link_label_is_only_digits() -> None:
    """A cross-reference numbered 3, 4, 5 must not come out as three bare URLs.

    `html.py` strips bracketed citation markers from the converted markdown, where a
    numbered link has already become `[4](url)`. It ate the `[4]` and left `(url)`, so
    WHO's cross-reference to its own recommendations reached the corpus as
    "see Recommendations Nos. (https://…),(https://…)" - the numbers gone, and what
    replaced them not even a link. Unwrapping the anchor first leaves nothing for that
    rule to match. 22 occurrences across 6 guidelines.
    """
    content = _render_body(
        "<p>Recommendations on aspects of care focus on methods to assess the progress of "
        "labour and on intrapartum interventions used routinely to prevent delay, "
        'see Recommendations Nos. <a href="https://app.magicapp.org/a">3</a>,'
        '<a href="https://app.magicapp.org/b">4</a>,'
        '<a href="https://app.magicapp.org/c">5</a>.</p>'
    )

    assert "see Recommendations Nos. 3,4,5" in content
    assert "https://app.magicapp.org" not in content


def test_markdown_leaves_other_links_and_real_citations_alone() -> None:
    """Only a label that is entirely digits is unwrapped, and citation markers still go."""
    content = _render_body(
        "<p>Antenatal corticosteroids reduce neonatal respiratory morbidity when birth is "
        'expected before 34 weeks, see <a href="https://x/g">Guidance</a> and '
        '<a href="https://x/h">4x</a>, with a bracketed citation [4] in prose.</p>'
    )

    assert "[Guidance](https://x/g)" in content
    assert "[4x](https://x/h)" in content
    assert "[4]" not in content


def test_markdown_places_a_row_span_filler_by_column_not_by_position() -> None:
    """A row-span filler must land in its own column, even after a column-spanning cell.

    The WHO antibiotic-prophylaxis dosing table (`Eg947L` Annex 4) pairs two three-column
    halves either side of a "VS" divider that spans many rows. A later row opens with a
    cell spanning the three left-hand columns, so the divider's filler belonged at column
    4 - but the filler was inserted by list position, before the column spans had been
    expanded, and landed one place before the row's last cell. The row read
    `|  |  |  | Mezlocillin | 2 g single dose |  | 32 |`: the comparator drug shifted into
    the divider column, its dose into the drug column, and the count left alone under
    "No. of women". A drug name in the wrong column of a dosing table.
    """
    content = _render_body(
        "<table>"
        "<tr><td colspan='3'>Non-anti-staphylococcal</td><td rowspan='3'>VS</td>"
        "<td colspan='3'>Broad-spectrum penicillins</td></tr>"
        "<tr><td>Cefotaxime</td><td>1 g x 3 doses</td><td>55</td>"
        "<td>Ampicillin</td><td>2 g single dose</td><td>148</td></tr>"
        "<tr><td colspan='3'></td><td>Mezlocillin</td><td>2 g single dose</td><td>32</td></tr>"
        "</table>"
    )
    rows = [line for line in content.splitlines() if line.startswith("|")]

    assert "|  |  |  |  | Mezlocillin | 2 g single dose | 32 |" in content
    assert "| Cefotaxime | 1 g x 3 doses | 55 |  | Ampicillin | 2 g single dose | 148 |" in content
    # Header, separator, and three data rows, every one seven columns wide.
    assert [row.count("|") for row in rows] == [8, 8, 8, 8, 8]


def test_markdown_keeps_a_row_span_filler_past_the_last_cell() -> None:
    """A span reaching beyond a short row's cells still holds its column open."""
    content = _render_body(
        "<table>"
        "<tr><td>Drug</td><td>Dose</td><td rowspan='2'>Ongoing</td></tr>"
        "<tr><td>Artesunate</td><td>2.4 mg/kg</td></tr>"
        "</table>"
    )

    assert "| Artesunate | 2.4 mg/kg |  |" in content


def test_markdown_ignores_an_implausible_span() -> None:
    """A publisher typo must not produce a row hundreds of columns wide."""
    content = _render_body("<table><tr><td colspan='200'>Dose</td><td>2.4 mg/kg</td></tr></table>")
    rows = [line for line in content.splitlines() if line.startswith("|")]

    assert all(row.count("|") <= 4 for row in rows)
    assert "Dose" in content
    assert "2.4 mg/kg" in content


HEADING_DEPTH = {
    "name": "Malaria",
    "sections": [
        {
            "heading": "Case management",
            "text": "<p>Treat with an artemisinin-based combination therapy.</p>",
            "recommendations": [],
            "subSections": [
                {
                    "heading": "Treating malaria",
                    "text": "<h4><strong>Choosing among formulations</strong></h4><p>Use fixed-dose combinations.</p>",
                    "recommendations": [
                        {
                            "text": "<p>Give artesunate for at least 24 hours.</p>",
                            "strength": "STRONG",
                            "remarks": "<h4><strong>Remarks</strong></h4><p>Artemether is second line.</p>",
                        }
                    ],
                    "subSections": [],
                }
            ],
        }
    ],
}


def test_build_magic_guideline_text_puts_publisher_headings_below_their_own_section() -> None:
    """A publisher's `<h4>` must not outrank the recommendation that contains it.

    MAGICapp gives a tree of sections with no heading levels, so ours are computed from
    depth; the body HTML carries `<h4>` chosen in a rich-text editor. Below depth three
    they collided, and 1,594 recommendation headings across the corpus were immediately
    followed by a shallower one. Ben's NICE scraper has 0 in 181 headings, because NICE
    publishes nested chapters and he passes the levels straight through.
    """
    content = _render(HEADING_DEPTH)
    levels = {
        line.split(" ", 1)[1]: len(line.split(" ", 1)[0]) for line in content.splitlines() if line.startswith("#")
    }

    assert levels["Case management"] == 2
    assert levels["Treating malaria"] == 3
    # The publisher's body heading sits under its section, not above it.
    assert levels["**Choosing among formulations**"] == 4
    assert levels["Recommendation (STRONG)"] == 4
    # And the recommendation's own field heading sits under the recommendation.
    assert levels["**Remarks**"] == 5


def test_markdown_keeps_the_publishers_relative_nesting_when_rebasing() -> None:
    """What the publisher chose as a starting number carries no meaning; their structure does."""
    from amfv_datasets.scraping.magic import _markdown as render

    deep = render("<h4>Parent</h4><p>a</p><h5>Child</h5><p>b</p>", link_mode=LinkMode.KEEP, base_level=2)
    shallow = render("<h2>Parent</h2><p>a</p><h3>Child</h3><p>b</p>", link_mode=LinkMode.KEEP, base_level=2)

    assert deep == shallow
    assert "### Parent" in deep
    assert "#### Child" in deep


def test_markdown_does_not_overflow_past_the_deepest_markdown_heading() -> None:
    """Markdown stops at six, so a deep section with nested body headings flattens."""
    from amfv_datasets.scraping.magic import _markdown as render

    content = render("<h1>A</h1><p>x</p><h2>B</h2><p>y</p>", link_mode=LinkMode.KEEP, base_level=6)

    assert "####### " not in content
    assert "###### A" in content
    assert "###### B" in content


def test_markdown_clears_a_cell_that_held_nothing_but_reference_markers() -> None:
    """An evidence table's References column is a list of markers and the commas between them.

    Cancer Council Australia writes it as `<strong><sup>[3]</sup>, <sup>[26]</sup>,
    …</strong>`, so removing the markers left the cell reading ",,,,,,,,,". 228 cells
    across 6 guidelines. The commas never said anything on their own.
    """
    content = _render_body(
        "<table><tr><td>Colorectal cancer incidence</td><td>I, II</td>"
        "<td><strong><sup>[3]</sup>, <sup>[26]</sup>, <sup>[5]</sup></strong></td></tr></table>"
    )

    assert "| Colorectal cancer incidence | I, II |  |" in content
    assert "," * 2 not in content


def test_markdown_keeps_a_cell_that_held_more_than_reference_markers() -> None:
    """Only a cell left with nothing but separators is cleared."""
    content = _render_body(
        "<table><tr><td>Sensitivity</td><td>0.96 (95% CI, 0.86 to 0.99)<sup>[7]</sup></td></tr></table>"
    )

    assert "| Sensitivity | 0.96 (95% CI, 0.86 to 0.99) |" in content


def test_markdown_keeps_a_comma_that_belongs_to_the_sentence() -> None:
    """A comma between two clauses is not a separator between markers."""
    content = _render_body("<p>Resistance was detected<sup>[3]</sup>, and treatment continued<sup>[4]</sup>.</p>")

    assert "Resistance was detected, and treatment continued." in content


def test_markdown_removes_wiki_page_furniture_pasted_in_with_the_text() -> None:
    """`[edit source]` and `Back to top` are the publisher's website controls, not their writing.

    The caption fixture carries a line of text under its heading because a heading with
    nothing at all under it is now removed as dead, and this test is about the furniture.
    """
    caption = _render_body(
        '<h4>Table 1.1 Incidence and mortality rates<span style="color:rgb(84,89,93)">[</span>'
        'edit source<span style="color:rgb(84,89,93)">]</span></h4>'
        "<p>Rates are given per 100 000 people per year.</p>"
    )
    nav = _render_body("<p>Seven RCTs reported distant metastases.</p><p><u>Back to top</u></p><p>Next.</p>")

    assert "Table 1.1 Incidence and mortality rates" in caption
    assert "edit source" not in caption
    assert "Back to top" not in nav
    assert "Seven RCTs reported distant metastases.\n\nNext." in nav


def test_markdown_keeps_prose_that_merely_mentions_references() -> None:
    """Only a whole heading reading "References" goes; the word in a sentence stays."""
    content = _render_body("<p>References to prior trials informed the panel and are discussed below.</p>")

    assert "References to prior trials informed the panel" in content


def test_markdown_removes_back_to_top_in_every_form_the_publisher_uses() -> None:
    """It appears four ways, and the first version of the rule caught only one.

    Plain text, underlined text, a real link to the page anchor, and any of those glued
    onto the end of a clinical paragraph. A third pass over the same guideline found the
    linked form still in the corpus.
    """
    own_line = _render_body(
        '<p>Alpha.</p><p><a href="https://wiki.cancer.org.au/x#top">Back to top</a></p><p>Beta.</p>'
    )
    trailing = _render_body(
        "<p>Patients with metastatic disease rarely survive 12 months. "
        '<a href="https://wiki.cancer.org.au/y#top">Back to top</a></p>'
    )
    bare_trailing = _render_body("<p>26% had adverse events leading to discontinuation. Back to top</p>")

    assert "Back to top" not in own_line
    assert "Alpha.\n\nBeta." in own_line
    assert trailing.endswith("rarely survive 12 months.")
    assert bare_trailing.endswith("leading to discontinuation.")


def test_markdown_keeps_prose_that_happens_to_say_back_to_top() -> None:
    """Only a standalone line, or a trailing run after a full stop, is chrome."""
    content = _render_body("<p>Scroll back to top of the page for the summary of findings.</p>")

    assert "Scroll back to top of the page for the summary of findings." in content


def test_markdown_keeps_a_superscript_as_a_character_rather_than_flattening_it() -> None:
    """`10<sup>9</sup>/L` flattened to "109/L" — a different number, in every recommendation.

    The McMaster ITP guideline expresses every platelet threshold in scientific notation,
    so dropping the tag turned "ten to the ninth per litre" into "one hundred and nine
    per litre" twenty times over. The same loss welds footnote markers onto the word
    before them.
    """
    threshold = _render_body("<p>a platelet count less than 20 x 10<sup>9</sup>/L who present</p>")
    dose = _render_body("<p>Consider methotrexate at 15 mg/m<sup>2</sup> once a week.</p>")
    footnote = _render_body("<p>not registered in Australia by the TGA<sup>i</sup>.</p>")

    assert "20 x 10⁹/L" in threshold
    assert "109/L" not in threshold
    assert "15 mg/m² once a week" in dose
    assert "the TGAⁱ." in footnote
    assert "TGAi" not in footnote


def test_markdown_falls_back_to_a_caret_when_a_superscript_has_no_character_form() -> None:
    """A run that cannot be converted still must not merge with the word before it."""
    content = _render_body("<p>Dosing is described in note<sup>1**</sup> at the foot of the table.</p>")

    assert "note1" not in content
    assert "note^1" in content


def test_markdown_gives_a_table_recommendation_a_heading_from_its_own_label() -> None:
    """Cancer Council writes recommendations as tables, with nothing marking them.

    144-plus real recommendations sit in two-row tables - a label cell, optionally a
    Grade column, then the statement - invisible to anything that finds
    recommendations by their headings. The label cell already speaks the
    `_LABEL_OPENERS` vocabulary, so it is promoted to a heading before the table;
    the glossary table that defines the labels opens "Type of recommendation" and
    is left alone.
    """
    content = _render_body(
        "<table><tbody>"
        "<tr><td>Evidence-based recommendation</td><td>Grade</td></tr>"
        "<tr><td>Offer screening every two years from age 50.</td><td>B</td></tr>"
        "</tbody></table>"
        "<table><tbody>"
        "<tr><td>Practice point</td></tr>"
        "<tr><td>Follow the primary prevention messages.</td></tr>"
        "</tbody></table>"
        "<table><tbody>"
        "<tr><td>Type of recommendation</td><td>Definition</td></tr>"
        "<tr><td>Practice point</td><td>A recommendation on a subject outside the search scope.</td></tr>"
        "</tbody></table>"
    )

    assert "Evidence-based recommendation (Grade B)" in content
    # The label cell stays inside its table, so "Practice point" appears three
    # times: the new heading, the kept label cell, and the glossary cell - and
    # only one of them is a heading. The glossary table gains no heading at all.
    assert content.count("### Practice point") == 1
    assert content.count("Practice point") == 3
    assert "### Type of recommendation" not in content
    assert content.index("Evidence-based recommendation (Grade B)") < content.index("Offer screening")
    assert "| Offer screening every two years from age 50. | B |" in content


def test_markdown_converts_trademark_superscripts_and_separator_runs() -> None:
    """Two caret shapes from the literal-carets audit that have clean conversions.

    Publishers set trademark signs as superscript letters (Precivity<sup>TM</sup>,
    thirty-two times in one guideline) and file reference pairs as scripts
    (satisfaction<sub>2,3</sub>); the all-or-nothing rule sent both to the caret
    fallback. The letter pair maps to the real sign; a separator may pass through
    at normal size inside an otherwise convertible run.
    """
    trademark = _render_body("<p>The Precivity<sup>TM</sup> assay was compared with mass spectrometry.</p>")
    references = _render_body("<p>the policy made little difference to satisfaction<sub>2,3</sub> in trials.</p>")

    assert "Precivity™ assay" in trademark
    assert "^TM" not in trademark
    assert "satisfaction₂,₃" in references
    assert "^2,3" not in references


def test_markdown_drops_a_caption_whose_picture_is_not_kept() -> None:
    """Images are dropped deliberately; the bolded line naming one survived with nothing under it.

    "PRISMA flow chart", "Risk of bias graph", "Blood Products and ITP Treatments
    Administered During Any Visit with Critical Bleeding (N=15)" — each names a figure
    and asserts nothing. 94 across 68 guidelines. Found by a reader asked only the
    keep-or-drop question, after a corruption reader had checked the same lines and
    correctly cleared them.
    """
    content = _render_body(
        "<p>Fifteen adults had a critical bleed.</p>"
        "<p><strong><u>Blood Products Administered (N=15)</u></strong></p>"
        '<figure class="image"><img src="data:image/png;base64,AAAA"></figure>'
        "<p>Treatment was escalated in twelve.</p>"
    )

    assert "Fifteen adults had a critical bleed." in content
    assert "Treatment was escalated in twelve." in content
    assert "Blood Products Administered" not in content


def test_markdown_keeps_the_caption_on_a_table() -> None:
    """MAGICapp wraps tables in <figure> too, and those captions label content that is kept.

    227 captions across the catalogue introduce a table rather than an image; stripping
    them would take the heading off real content.
    """
    content = _render_body(
        "<p><strong>Table 8.3 AJCC prognostic stage groups</strong></p>"
        '<figure class="table"><table><tr><td>IIIA</td><td>T1-T2</td></tr></table></figure>'
    )

    assert "Table 8.3 AJCC prognostic stage groups" in content
    assert "| IIIA | T1-T2 |" in content


def test_markdown_keeps_a_paragraph_of_prose_that_happens_to_precede_a_figure() -> None:
    """A caption is a line that is entirely emphasised; a sentence is not."""
    content = _render_body(
        "<p>Seven RCTs reported shorter hospital stay in the laparoscopic group.</p>"
        '<figure class="image"><img src="x"></figure>'
    )

    assert "Seven RCTs reported shorter hospital stay" in content


SUPERSCRIPT_INSIDE_LINK = {
    "name": "Prophylactic antibiotics",
    "sections": [
        {
            "heading": "Notes",
            "text": (
                "<p><sup>1 These outcomes reflect those used in the 2015 </sup>"
                '<a href="https://app.magicapp.org/#/guideline/7963"><sup>WHO recommendations for '
                "maternal peripartum infections</sup></a><sup>.</sup></p>"
                '<p>Trametinib in <a href="https://example.org/braf">patients with '
                "BRAF<sup>V600</sup>-mutant melanoma</a>.</p>"
            ),
            "recommendations": [],
            "subSections": [],
        },
    ],
}


def test_build_magic_guideline_text_keeps_links_whole_around_superscripts() -> None:
    """A superscript that is a link's whole text loses its caret; one inside it keeps it.

    The caret stops a marker welding onto the word before it, but markdown reads `[^` as a
    footnote reference, so a link whose entire text sat in a `<sup>` came out as
    `[^WHO recommendations ...](url)` - 150 broken links across 34 guidelines, one of them
    splitting a URL as `https:/[^/www.cerqual.org/]`. Dropping the caret everywhere inside
    a link is the wrong cure: it welded `BRAF<sup>V600</sup>-mutant` into `BRAFV600-mutant`.
    """
    content = _render(SUPERSCRIPT_INSIDE_LINK)

    assert "[^" not in content
    # The space before the link lived inside the preceding <sup> and must survive.
    assert "2015 [WHO recommendations" in content or "2015 [" in content
    assert "2015[" not in content
    # A marker inside longer link text is still notation.
    assert "BRAF^V600-mutant" in content
    assert "BRAFV600" not in content


ADJACENT_CITATIONS = {
    "name": "Prophylactic antibiotics",
    "sections": [
        {
            "heading": "Introduction",
            "text": (
                "<p>Maternal infections cause an estimated 1 million newborn deaths annually&nbsp;"
                '<cite class="magic-cite" data-ref-id="799594">[5]</cite>,'
                '<cite class="magic-cite" data-ref-id="799620">[6]</cite>. '
                "Infection-related morbidity follows.</p>"
                "<p>Rates vary by setting, region and season.</p>"
            ),
            "recommendations": [],
            "subSections": [],
        },
    ],
}


def test_build_magic_guideline_text_drops_the_comma_between_two_citations() -> None:
    """The separator between two adjacent markers goes with them; real commas stay.

    WHO writes `annually&nbsp;<cite>[5]</cite>,<cite>[6]</cite>.` and keeping that comma
    once both markers were gone ended the sentence `annually,.` - 294 of these across 41
    guidelines. Only a separator sitting strictly between two doomed citations is touched,
    which is what makes it provably citation punctuation rather than the publisher's prose.
    """
    content = _render(ADJACENT_CITATIONS)

    assert "newborn deaths annually." in content
    assert ",." not in content
    # A comma in ordinary prose is untouched.
    assert "Rates vary by setting, region and season." in content


BOLD_ACROSS_A_LINE_BREAK = {
    "name": "Alcohol guidelines",
    "sections": [
        {
            "heading": "Guideline 2",
            "text": (
                "<p><strong>To reduce the risk of injury, children under 18 should not drink."
                "<br /><br />Why not drinking is important for young people</strong></p>"
            ),
            "recommendations": [],
            "subSections": [],
        },
    ],
}


def test_build_magic_guideline_text_balances_bold_across_a_line_break() -> None:
    """Emphasis spanning a `<br>` gets its own pair of markers on each line.

    Markdown emphasis has to open and close inside one line. A run containing a break puts
    `**` at the start of one line and its partner two lines later, and every renderer then
    reads the pair as one bold run swallowing what lies between - 158 lines in the corpus
    carried an odd number of markers. Each line now closes its own.
    """
    content = _render(BOLD_ACROSS_A_LINE_BREAK)

    for line in content.splitlines():
        assert line.count("**") % 2 == 0, f"unbalanced emphasis on: {line!r}"
    assert "children under 18 should not drink" in content
    assert "Why not drinking is important for young people" in content


CITATION_BETWEEN_A_CLAUSE_AND_A_WORD = {
    "name": "Transient ischaemic attack",
    "sections": [
        {
            "heading": "Results",
            "text": (
                "<p>Patients had concurrent perfusion and diffusion deficits."
                '<span class="magic-ref"><cite class="magic-cite">[50]</cite></span>'
                "One third of the acute PWI lesion progressed to infarction. Two RCTs "
                "(the Chinese Acute Stroke Trial (CAST)"
                '<span class="magic-ref"><cite class="magic-cite">[74]</cite></span>'
                "and the International Stroke Trial) were pooled.</p>"
            ),
            "recommendations": [],
            "subSections": [],
        },
    ],
}


def test_build_magic_guideline_text_does_not_weld_a_clause_to_the_next_word() -> None:
    """A citation doing the work of a space leaves one behind, after punctuation too.

    The guard used to require a letter or digit on both sides, so it caught
    `NICE<cite/>modelling` and missed `deficits.<cite/>One` and `(CAST)<cite/>and` - the
    publisher relied on the marker to separate a finished clause from the next word, and
    removing it produced "deficits.One" and "(CAST)and". Neither string is in the source.
    """
    content = _render(CITATION_BETWEEN_A_CLAUSE_AND_A_WORD)

    assert "deficits. One third" in content
    assert "(CAST) and the International" in content
    assert "deficits.One" not in content
    assert "(CAST)and" not in content


CITATION_RANGE_DASH = {
    "name": "Cerebral venous thrombosis",
    "sections": [
        {
            "heading": "Method",
            "text": (
                "<p>These guidelines were prepared following the GRADE methodology"
                '<cite class="magic-cite">[106]</cite>–<cite class="magic-cite">[109]</cite>'
                " and the European Stroke Organization standard operating procedures.</p>"
            ),
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Ethical approval",
            "text": "<p>No ethical approval was needed for this manuscript.</p>",
            "recommendations": [],
            "subSections": [],
        },
    ],
}


def test_build_magic_guideline_text_drops_the_dash_between_a_cited_range() -> None:
    """A cited range's dash goes with the markers it separated, and journal fields go too.

    Publishers write a range as `[106] – [109]`. Keeping the dash once both markers are
    removed welds it to the word before - "following the GRADE methodology– and the" - and
    the residue reads as ordinary punctuation, so nothing in the text reveals it as debris.
    """
    content = _render(CITATION_RANGE_DASH)

    assert "GRADE methodology and the European Stroke Organization" in content
    assert "methodology–" not in content
    assert "No ethical approval was needed" not in content


NESTED_EMPHASIS_ACROSS_A_BREAK = {
    "name": "Cerebral venous thrombosis",
    "sections": [
        {
            "heading": "Topic: Steroids",
            "text": (
                "<p><em><strong>PICO 1: does therapeutic lumbar puncture improve outcome?"
                "<br />\n<br />\nPICO 2: does it improve headache?</strong></em></p>"
            ),
            "recommendations": [],
            "subSections": [],
        },
    ],
}


def test_build_magic_guideline_text_balances_nested_emphasis_across_a_break() -> None:
    """A bold-italic run spanning a break closes with three markers, not two.

    The split has to reach the outer tag as well as the inner one. `<em>` is visited first
    in document order and holds no break of its own, so it is passed over; only once
    `<strong>` has been split does it hold breaks directly. Splitting once left `***PICO 1:
    ...**` - three openers against two closers, which CommonMark renders with a literal
    asterisk showing at the start of the line.
    """
    content = _render(NESTED_EMPHASIS_ACROSS_A_BREAK)

    for line in content.splitlines():
        opened = len(line) - len(line.lstrip("*"))
        closed = len(line) - len(line.rstrip("*"))
        if opened or closed:
            assert opened == closed, f"markers do not match on: {line!r}"
    assert "PICO 1: does therapeutic lumbar puncture improve outcome?" in content
    assert "PICO 2: does it improve headache?" in content


def test_markdown_merges_a_word_split_across_two_colour_spans() -> None:
    """A word the editor split into two coloured runs comes back as one word.

    Cancer Council Australia's colposcopy heading is
    `<span><strong>T</strong></span><strong>ype 3 TZ colposcopy</strong>`, which converts
    to `**T****ype 3 TZ colposcopy**` - four asterisks inside a word, which no renderer
    reads as emphasis. The runs are cousins rather than siblings, because the editor wraps
    each in its own colour span, so a rule comparing sibling tags never saw them meet.
    """
    content = _render_body(
        '<p><span style="color:#454545"><strong>T</strong></span>'
        "<strong>ype 3 TZ colposcopy</strong> is recommended.</p>"
    )

    assert "**Type 3 TZ colposcopy** is recommended." in content
    assert "****" not in content


def test_markdown_keeps_text_after_a_merged_run_exactly_once() -> None:
    """Text following the second run survives the merge, and is not duplicated.

    Removing the merged-away element already carries its trailing text to whatever now
    precedes it. Re-attaching that text separately duplicated a clause of a WHO Ebola
    recommendation, and no asterisk count would have caught it.
    """
    content = _render_body(
        '<p>WHO <span style="color:#345"><strong>suggest</strong></span>'
        "<strong>s</strong> health workers who had an exposure be excluded for 21 days.</p>"
    )

    assert content.count("health workers who had an exposure be excluded for 21 days.") == 1
    assert "**suggests** health workers" in content


def test_markdown_unwraps_a_bolded_colon_behind_a_colour_span() -> None:
    """A bolded colon is unwrapped even when the word before it sits in a span.

    `<span><i>Judgement</i></span><strong>:</strong>` converts to `*Judgement***:**`:
    three markers in a row, none of which can pair off. The colon means something
    different from the word, so the two runs must not be merged - the colon's own
    emphasis is dropped instead.
    """
    content = _render_body(
        '<p>Timing<span style="color:#345"><i>Judgement</i></span><strong>:</strong> Small effect</p>'
    )

    assert "*Judgement*: Small effect" in content
    assert "***" not in content


def test_markdown_does_not_merge_runs_that_mean_different_things() -> None:
    """An underlined run never merges into the plain bold run beside it.

    Merging is only safe where both runs carry exactly the same emphasis. WHO's midwives
    recommendation alternates bold and bold-underline mid-sentence, and joining them would
    report the publisher as having underlined words they left plain.
    """
    content = _render_body(
        "<p><strong>Should midwives deliver a </strong>"
        "<strong><u>loading dose</u></strong>"
        "<strong> of magnesium sulphate?</strong></p>"
    )

    assert "loading dose" in content
    assert "of magnesium sulphate?" in content
    assert "****" not in content


def test_markdown_drops_an_emphasis_tag_holding_only_a_zero_width_space() -> None:
    """A zero-width space is emptiness, not content, so its tag leaves no markers.

    A dengue guideline brackets each citation with `<i>&#8203;</i>`. Python does not treat
    the character as whitespace, so every emptiness test answered no and the tag converted
    to a pair of markers with nothing between them - 30 stray asterisks in one guideline.
    """
    content = _render_body(
        "<p>It belongs to the family <i>Flaviviridae</i> <i>​</i>"
        '<cite class="magic-cite" data-ref-id="832800" data-label="">[4]</cite>'
        "<i>​</i>. Although most people infected have no symptoms.</p>"
    )

    assert "*Flaviviridae*" in content
    assert "Although most people infected have no symptoms." in content
    assert "​" not in content
    assert "**" not in content


# ---------------------------------------------------------------------------
# Conversion and markdown cleanup passes
# ---------------------------------------------------------------------------


def test_build_magic_guideline_text_keeps_appendix_content_that_is_not_administrative() -> None:
    """A clinical appendix section survives, so the container is not dropped wholesale."""
    content = _render(DECORATED_HEADINGS)

    assert "Safety Monitoring" in content
    assert "Report adverse events within 24 hours" in content


INLINE_PAPERWORK = {
    "name": "Inflammatory arthritis",
    "sections": [
        {
            "heading": "Initial DMARD therapy",
            "text": (
                "<p>Methotrexate is usually first line.</p>"
                "<p><strong>Authorship: </strong>The following Expert Advisory Panel members "
                "participated in the development of this recommendation:</p>"
                "<figure class='table'><table><tbody><tr><td>Rachelle Buchbinder</td>"
                "<td>Monash University</td></tr></tbody></table></figure>"
                "<p>Triple therapy is the usual combination.</p>"
            ),
            "recommendations": [{"text": "<p>Consider methotrexate in combination.</p>", "strength": "WEAK"}],
            "subSections": [],
        },
        {
            "heading": "Introduction",
            "text": (
                "<p><strong>Background</strong><br>Inflammatory arthritis causes joint damage."
                "<br><br><strong>About this living guideline</strong><br>Funded by ANZMUSC with "
                "$1,320,000.<br><br><strong>Copyright</strong><br>This work is copyright.</p>"
            ),
            "recommendations": [],
            "subSections": [],
        },
    ],
}


def test_build_magic_guideline_text_drops_authorship_tables_inside_kept_sections() -> None:
    """A panel table sits in the body of a clinical section, out of the skip list's reach.

    The section holds the recommendation as well, so it cannot be dropped whole;
    the label and the table it introduces are removed on their own.
    """
    content = _render(INLINE_PAPERWORK)

    assert "Consider methotrexate in combination." in content
    assert "Methotrexate is usually first line." in content
    assert "Triple therapy is the usual combination." in content
    assert "Rachelle Buchbinder" not in content
    assert "participated in the development" not in content


def test_build_magic_guideline_text_drops_paperwork_written_between_line_breaks() -> None:
    """Some publishers write a whole introduction as one paragraph split by <br>.

    The clinical opening has to survive while the funding and copyright blocks
    after it are removed, so the paragraph is cut at the publisher's own labels
    rather than dropped whole.
    """
    content = _render(INLINE_PAPERWORK)

    assert "Inflammatory arthritis causes joint damage." in content
    assert "About this living guideline" not in content
    assert "$1,320,000" not in content
    assert "This work is copyright" not in content


def test_markdown_drops_a_bibliography_the_publisher_typed_inside_a_section_body() -> None:
    """A References heading is on the skip list, but only a section's own heading is checked.

    Cancer Council types `<h2>References</h2>` into the same text field as real guidance,
    so it is promoted straight to a markdown heading with no skip-list check and rides
    through — 19 bibliographies across 4 guidelines, one carrying a live Lancet citation.
    """
    content = _render_body(
        "<p>Offer supportive care early in the disease course.</p>"
        "<h2>References</h2><ol><li>NCCN. Survivorship. Version 1.2017.</li>"
        "<li>Temel JS et al. N Engl J Med 2010.</li></ol>"
    )

    assert "Offer supportive care early in the disease course." in content
    assert "References" not in content
    assert "Temel" not in content


def test_build_magic_guideline_text_drops_a_chapter_that_is_only_a_pointer() -> None:
    """A chapter saying "click here for this section" is a topical match that delivers nothing.

    22 of these survive into the corpus. The worst is headed "Recommendation" and its
    whole body is a link to a BMJ page. Nothing is lost: where the pointer goes
    elsewhere in the same document that section is in the corpus, and where it goes to
    another MAGICapp guideline that guideline is in the corpus as its own document.
    """
    guideline = {
        "name": "Colorectal cancer",
        "sections": [
            {
                "heading": "The symptomatic patient",
                "text": "<p>Refer patients with rectal bleeding for colonoscopy.</p>",
                "recommendations": [],
                "subSections": [],
            },
            {
                "heading": "Population screening for colorectal cancer",
                "text": '<p>Please click <a href="https://app.magicapp.org/x">here</a> for this section</p>',
                "recommendations": [],
                "subSections": [],
            },
        ],
    }
    content = _render(guideline)

    assert "Refer patients with rectal bleeding" in content
    assert "Population screening for colorectal cancer" not in content
    assert "click" not in content


def test_build_magic_guideline_text_keeps_a_sentence_that_points_and_also_states_a_dose() -> None:
    """A pointer only directs; a sentence carrying a measurement is making a claim."""
    guideline = {
        "name": "Malaria",
        "sections": [
            {
                "heading": "Dosing",
                "text": "<p>See the table below, which gives artesunate at 2.4 mg/kg for at least 24 hours.</p>",
                "recommendations": [],
                "subSections": [],
            }
        ],
    }
    content = _render(guideline)

    assert "2.4 mg/kg for at least 24 hours" in content


def test_markdown_drops_a_contributor_byline_written_into_a_clinical_section() -> None:
    """A journal byline inside a clinical section goes; the guidance around it stays.

    `rj1KVn` opens "Corticosteroids for community-acquired pneumonia" with a Contributors
    line naming eleven authors with their degrees. The section holds the recommendation,
    so the skip list must not touch it - only the byline block can go.
    """
    content = _render_body(
        "<p><strong>Contributors:</strong></p>"
        "<p>Reed A.C. Siemieniuk, MD; Per O. Vandvik, MD, PhD; Gordon H. Guyatt, MD, MSc</p>"
        "<p><strong>Corticosteroids</strong></p>"
        "<p>We suggest corticosteroids for patients with severe pneumonia.</p>"
    )

    assert "Siemieniuk" not in content
    assert "We suggest corticosteroids for patients with severe pneumonia." in content


def test_markdown_drops_a_paperwork_label_sharing_a_line_with_its_value() -> None:
    """A paperwork label sharing its line with its value still goes.

    The block rule needs a label with a line to itself, because it removes the paragraphs
    that follow it. WHO writes `<p><strong>Funding</strong>: WHO.</p>` instead, so the
    whole-line-bold test failed and four guidelines carried a visible "Funding: WHO." into
    the corpus under a label the list already knew.
    """
    content = _render_body(
        "<p><strong>Funding</strong>: WHO.</p>"
        "<p>Oral rehydration salts are recommended for children with dehydration.</p>"
    )

    assert "Funding" not in content
    assert "Oral rehydration salts are recommended" in content


def test_every_inline_paperwork_label_is_reachable_by_its_hint() -> None:
    """No label may be stranded behind the pre-filter that guards the parse.

    `_drop_inline_paperwork` returns immediately when `_INLINE_PAPERWORK_HINT_RE` does not
    match, so a label the hint cannot reach is dead code that fails silently - adding
    "contributors" to the label set without adding it to the hint left the byline in the
    corpus with nothing to show anything was wrong. Two lists that must agree need a check
    that they do.
    """
    stranded = sorted(label for label in _INLINE_PAPERWORK_LABELS if not _INLINE_PAPERWORK_HINT_RE.search(label))

    assert not stranded, f"labels the hint regex can never reach: {stranded}"


def test_markdown_drops_a_paragraph_that_is_only_a_pointer() -> None:
    """A pointer paragraph inside a section that carries guidance goes; the guidance stays.

    `_is_pointer_only` reaches a whole section; publishers also write the same sentence as
    a paragraph beside real advice, which it cannot see. 624 of these across 72 guidelines.
    """
    content = _render_body(
        "<p>Patients with persistent weight loss should be urgently reviewed.</p>"
        '<p>Please also refer to the topic Early Nutrition in <a href="https://x.org">Managing '
        "Complications</a>.</p>"
        "<p>All staff should be trained in feeding techniques.</p>"
    )

    assert "Please also refer to the topic" not in content
    assert "Patients with persistent weight loss should be urgently reviewed." in content
    assert "All staff should be trained in feeding techniques." in content


def test_markdown_keeps_a_pointer_that_carries_a_measurement() -> None:
    """A sentence with a number is making a claim, whatever it opens with.

    The same guard the section rule uses. 130 of the 624 pointer-shaped paragraphs carry a
    measurement and are kept. A bare number is not enough and must not be: "Section 5.2"
    is a section number, and treating it as a measurement would keep every pointer.
    """
    content = _render_body(
        "<p>Advise against drinking in pregnancy.</p>"
        "<p>Please see Section 5.2, where 20 mg daily is compared with placebo.</p>"
    )

    assert "Please see Section 5.2" in content


def test_markdown_drops_a_cross_reference_sentence_and_keeps_the_rest_of_its_paragraph() -> None:
    """A signpost written as the last sentence of a paragraph that opens with real content.

    `_drop_pointer_blocks` cannot reach these, because it anchors at the start of a paragraph
    and this arrives after a clause that does assert something. The paragraph cannot be the
    unit either: dropping it would take the clinical sentence with it.
    """
    content = _render_body(
        "<p>Disseminated cryptococcosis skin lesions may resemble mpox under some "
        "circumstances. Guidelines for diagnosing cryptococcal disease from WHO can be found "
        'here: <a href="https://www.who.int/publications/i/item/9789240052178">WHO</a>.</p>'
    )

    assert "Disseminated cryptococcosis skin lesions may resemble mpox" in content
    assert "can be found here" not in content


def test_markdown_drops_a_scope_disclaimer_written_beside_a_cross_reference() -> None:
    """The disclaimer and the address of the document that does cover the subject are a pair.

    jW0ZbL opens "Secondary postpartum haemorrhage" with two paragraphs that between them
    say only that NICE and RANZCOG cover the topic. The first is one sentence, so the whole
    paragraph goes; in the second the disclaimer travels with the cross-reference beside it.
    """
    content = _render_body(
        "<p>These recommendations focus on the provision of information related to secondary "
        'postpartum haemorrhage and are based on the <a href="https://www.nice.org.uk/ng194">'
        "NICE 2021 Postnatal Care Guidelines</a>.</p>"
        "<p>As primary postpartum haemorrhage often occurs during the third stage of labour, "
        "it is not within scope of the LEAPP Postnatal Care Guidelines. Clinical management "
        'is covered by the <a href="https://ranzcog.edu.au/pph.pdf">RANZCOG Clinical Guidance '
        "Statement</a>, available here.</p>"
        "<p>Women with heavy bleeding should be referred urgently.</p>"
    )

    assert "NICE 2021" not in content
    assert "not within scope" not in content
    assert "RANZCOG" not in content
    assert "Women with heavy bleeding should be referred urgently." in content


def test_markdown_keeps_a_recommendation_that_cites_its_source() -> None:
    """A sentence that instructs is making a claim, whatever address it also carries.

    The guard that makes the rule safe. It is spelled with verb forms rather than a trailing
    wildcard, because a wildcard on "recommend" also matches the noun in "These
    recommendations are based on the NICE guidelines" - a cross-reference, not a
    recommendation, and it was kept for exactly that reason until the pattern was narrowed.
    """
    content = _render_body(
        '<p>We recommend aspirin for 14 days, based on the <a href="https://x.org">CAST trial</a>.</p>'
    )

    assert "We recommend aspirin for 14 days" in content
    assert "CAST trial" in content


def test_markdown_keeps_a_scope_statement_standing_on_its_own() -> None:
    """Who a guideline applies to is load-bearing; only a disclaimer beside a pointer goes."""
    content = _render_body(
        "<p>These guidelines are not within scope of paediatric practice and apply only to adults aged 18 and over.</p>"
    )

    assert "apply only to adults aged 18 and over" in content


def test_markdown_keeps_a_cross_reference_inside_a_list_item() -> None:
    """Removing one bullet from a list leaves the list saying something it did not say.

    Headings, table rows, quotes and list items are all left to the rules that own them -
    `_drop_link_directories` weighs a list as a whole, rather than a bullet at a time.
    """
    content = _render_body(
        '<ul><li>First-line dosing is covered by the <a href="https://bnf.org">BNF</a>.</li><li>Second item.</li></ul>'
    )

    assert "First-line dosing is covered by the" in content


def test_build_magic_guideline_text_keeps_a_heading_whose_body_was_all_cross_references() -> None:
    """A section whose body renders away still has its recommendations to stand over."""
    guideline = {
        "name": "Postnatal care",
        "sections": [
            {
                "heading": "Secondary postpartum haemorrhage",
                "text": (
                    "<p>These recommendations are based on the "
                    '<a href="https://www.nice.org.uk/ng194">NICE 2021 Guidelines</a>.</p>'
                ),
                "recommendations": [
                    {
                        "strength": "STRONG",
                        "text": "<p>Within 24 hours of birth, discuss expected bleeding.</p>",
                    }
                ],
                "subSections": [],
            }
        ],
    }
    content = _render(guideline)

    assert "Secondary postpartum haemorrhage" in content
    assert "Within 24 hours of birth, discuss expected bleeding." in content
    assert "NICE 2021" not in content


def test_build_magic_guideline_text_drops_a_section_whose_body_was_all_cross_references() -> None:
    """With nothing underneath it, the heading names a subject the document does not cover.

    13 of these across the corpus, among them "Access to all recommendations and flowcharts",
    whose whole body is two links to Cancer Council PDFs.
    """
    guideline = {
        "name": "Cervical screening",
        "sections": [
            {
                "heading": "The symptomatic patient",
                "text": "<p>Refer patients with bleeding for colposcopy.</p>",
                "recommendations": [],
                "subSections": [],
            },
            {
                "heading": "Access to all recommendations and flowcharts",
                "text": (
                    '<p>All recommendations are available <a href="https://cancer.org.au/a">here</a>. '
                    'All flowcharts are available <a href="https://cancer.org.au/b">here</a>.</p>'
                ),
                "recommendations": [],
                "subSections": [],
            },
        ],
    }
    content = _render(guideline)

    assert "Refer patients with bleeding for colposcopy." in content
    assert "Access to all recommendations and flowcharts" not in content


def test_markdown_drops_a_directory_of_other_organisations_resources() -> None:
    """A list of organisations and their web addresses goes; the advice around it stays.

    jW0ZbL ends its breastfeeding advice with thirteen organisations and their URLs. It is
    a phone book, and the skip list cannot reach it because it sits inside a section that
    carries real guidance.
    """
    content = _render_body(
        "<p>Advise exclusive breastfeeding to six months.</p>"
        "<ul>"
        '<li>Pregnancy, Birth and Baby<ul><li><a href="https://pbb.org.au/">https://pbb.org.au/</a></li></ul></li>'
        '<li>Australian Breastfeeding Association<ul><li><a href="https://aba.asn.au/">https://aba.asn.au/</a></li></ul></li>'
        '<li>Baby Friendly Health Initiative<ul><li><a href="https://bfhi.org.au/">https://bfhi.org.au/</a></li></ul></li>'
        "</ul>"
    )

    assert "Advise exclusive breastfeeding to six months." in content
    assert "bfhi.org.au" not in content
    assert "Australian Breastfeeding Association" not in content


def test_markdown_keeps_a_list_of_recommendations_that_carry_citations() -> None:
    """A clinical list is not a directory, even when every item links to its source.

    The rule matches only items that are nothing but a link, so guidance with a citation
    beside it never qualifies - which is what stops this from eating recommendation lists.
    """
    content = _render_body(
        "<ul>"
        '<li>Offer 400 mcg folic acid daily <a href="https://x.org/1">[1]</a></li>'
        '<li>Screen for anaemia at 28 weeks <a href="https://x.org/2">[2]</a></li>'
        '<li>Advise against alcohol in pregnancy <a href="https://x.org/3">[3]</a></li>'
        '<li>Review iron status postpartum <a href="https://x.org/4">[4]</a></li>'
        "</ul>"
    )

    assert "folic acid daily" in content
    assert "Screen for anaemia at 28 weeks" in content
    assert "Review iron status postpartum" in content


def test_markdown_drops_a_single_resource_pair() -> None:
    """One resource named on a line with its address beneath it is still a directory.

    jW0ZbL carries these as single pairs as well as long runs: "Lochia. A guide for lay
    people, including visual representations of each stage" over a Cleveland Clinic URL.
    """
    content = _render_body(
        "<p>Advise women about normal blood loss after birth.</p>"
        "<ul><li>Lochia. A guide for lay people"
        '<ul><li><a href="https://my.clevelandclinic.org/health/symptoms/22485-lochia">'
        "https://my.clevelandclinic.org/health/symptoms/22485-lochia</a></li></ul></li></ul>"
    )

    assert "Advise women about normal blood loss after birth." in content
    assert "clevelandclinic" not in content
    assert "A guide for lay people" not in content


def test_markdown_keeps_a_recommendation_that_carries_its_source_as_a_nested_link() -> None:
    """The same shape must not eat guidance that happens to cite a link beneath it.

    A directory entry names a resource; it never gives advice. The guard is on the label
    line, so an item reading "should" or carrying a dose keeps its whole run.
    """
    content = _render_body(
        "<ul><li>Women with secondary postpartum haemorrhage should be referred urgently"
        '<ul><li><a href="https://x.org/evidence">https://x.org/evidence</a></li></ul></li></ul>'
    )

    assert "should be referred urgently" in content


def test_markdown_drops_an_approval_stamp_from_a_remarks_block() -> None:
    """Who signed the recommendation off and when is provenance, not clinical content.

    The Australian Postnatal Care Guidelines repeat "Approved by NHMRC on 22 December 2025,
    expires 21 December 2030" under 85 recommendations. No verdict can rest on it. Almost all
    of these sit inside the italic Remarks block, so the markers have to be seen through.
    """
    content = _render_body(
        "<p><em>Approved by NHMRC on 2 January 2025, expires 1 January 2030. "
        "Evidence surveillance: inactive.</em></p>"
        "<p>Bleeding heavier than a normal period warrants urgent review.</p>"
    )

    assert "Approved by NHMRC" not in content
    assert "Evidence surveillance" not in content
    assert "Bleeding heavier than a normal period warrants urgent review." in content


def test_markdown_keeps_a_real_remark_beside_the_stamp_it_was_written_with() -> None:
    """The stamp is one sentence of the remarks; the rest of them are guidance."""
    content = _render_body(
        "<p><em>Do not use in renal impairment. Approved by NHMRC on 9 July 2024, expires 9 July 2029.</em></p>"
    )

    assert "Do not use in renal impairment." in content
    assert "Approved by NHMRC" not in content


def test_markdown_keeps_an_approval_that_is_a_fact_about_a_drug() -> None:
    """The rule anchors at the start of the sentence, and that is its whole safety argument.

    "Oral semaglutide tablet is now approved by the FDA" is a fact about a medicine.
    "The guideline was then reviewed and approved by the WHO Guideline Review Committee" is
    the document's history. Neither opens with the stamp wording, so neither matches.
    """
    content = _render_body(
        "<p>Oral semaglutide tablet is now approved by the United States Food and Drug "
        "Administration (FDA), but not widely accessible in other countries.</p>"
        "<p>The guideline was then reviewed and approved by the WHO Guideline Review "
        "Committee.</p>"
    )

    assert "Oral semaglutide tablet is now approved by" in content
    assert "reviewed and approved by the WHO Guideline Review Committee" in content


def test_markdown_drops_a_cross_reference_that_ends_without_a_full_stop() -> None:
    """Publishers stop the sentence at the address, with no punctuation after it.

    Requiring terminal punctuation kept "Details about the availability of medicines on the
    Pharmaceutical Benefits Scheme can be found at www.pbs.gov.au/" in two guidelines. Only
    the last sentence of a block gets the exemption; a mid-block fragment still needs one.
    """
    content = _render_body(
        "<p>Financial accessibility should be considered when prescribing. Details about the "
        "availability of medicines on the Pharmaceutical Benefits Scheme can be found at "
        '<a href="http://www.pbs.gov.au/">www.pbs.gov.au/</a></p>'
    )

    assert "Financial accessibility should be considered when prescribing." in content
    assert "Pharmaceutical Benefits Scheme" not in content


def test_markdown_drops_a_service_directory_under_its_bold_label() -> None:
    """A bold label naming a category of services, and the bulleted directory beneath it.

    The conjunction is what makes this safe. The header word alone is unusable: across the
    corpus "resources" names GRADE's cost-and-staffing domain some three hundred times
    against a handful of real directories, and those domains are followed by prose or a
    table rather than bullets.
    """
    content = _render_body(
        "<p><strong>Support for stillbirth and neonatal death</strong></p>"
        '<ul><li><a href="https://x.org/a">Guiding Conversations</a> was developed for '
        "parents who have experienced perinatal loss.</li>"
        '<li><a href="https://x.org/b">Living well with loss</a> is an online support '
        "program.</li></ul>"
        "<p>Women should be offered bereavement follow-up before discharge.</p>"
    )

    assert "Support for stillbirth and neonatal death" not in content
    assert "Guiding Conversations" not in content
    assert "Women should be offered bereavement follow-up before discharge." in content


def test_markdown_keeps_a_grade_resource_domain_that_happens_to_use_bullets() -> None:
    """The label "Certainty of evidence for required resources" opens a costing, not a directory.

    Ee4mAn puts a costing of paediatric intensive care under that label, in bullets. It is
    the one block the header-plus-list shape would otherwise have taken.
    """
    content = _render_body(
        "<p><strong>Certainty of evidence for required resources</strong></p>"
        "<ul><li>PICU costs were estimated from primary sources and extensive "
        "consultation.</li><li>The average cost per child was estimated by sensitivity "
        "analysis.</li></ul>"
    )

    assert "Certainty of evidence for required resources" in content
    assert "PICU costs were estimated from primary sources" in content


def test_markdown_keeps_a_recommendation_written_as_a_bold_label() -> None:
    """j98OoE writes two recommendations as labels, with their detail in bullets under them."""
    content = _render_body(
        "<p><strong>We recommend self-management support (initiatives and programmes) "
        "should be:</strong></p>"
        "<ul><li>Available to patients across all stages of chronic kidney disease</li>"
        "<li>Ongoing and collaborative</li></ul>"
    )

    assert "We recommend self-management support" in content
    assert "Available to patients across all stages of chronic kidney disease" in content


def test_markdown_keeps_an_implementation_checklist_headed_as_a_question() -> None:
    """jO0lNL heads a hand-hygiene checklist with a question containing "resources"."""
    content = _render_body(
        "<p><strong>How can you provide appropriate resources and a supportive "
        "institutional environment for good hand hygiene?</strong></p>"
        "<ul><li>Do staff have access to running water, soap and alcohol-based "
        "sanitiser?</li></ul>"
    )

    assert "good hand hygiene?" in content
    assert "Do staff have access to running water" in content


def test_markdown_drops_a_signpost_that_mentions_screening() -> None:
    """Screening is a noun far more often than a verb, and the claim guard was shielding signposts.

    The claim guard treats an instruction as a reason to keep a sentence. Screening was in
    it, so "The AIHW Report, Screening for Domestic Violence During Pregnancy, provides
    information on the different screening tools used in different States and Territories"
    survived, along with 13 others - six of them copies of "For more information and
    resources regarding screening for X, see the NCSP toolkit".
    """
    content = _render_body(
        "<p>The AIHW Report, Screening for Domestic Violence During Pregnancy, provides "
        "information on the different screening tools used in different States and "
        'Territories: <a href="https://www.aihw.gov.au/reports/x">summary</a></p>'
        "<p>Ask about safety at every visit.</p>"
    )

    assert "AIHW Report" not in content
    assert "Ask about safety at every visit." in content


def test_markdown_keeps_an_instruction_to_screen() -> None:
    """A sentence that really does instruct screening carries another verb with it."""
    content = _render_body(
        "<p>Offer screening for psychological birth trauma before discharge, see "
        '<a href="https://x.org/p">the protocol</a>.</p>'
        '<p>All women should be screened using the EPDS at <a href="https://x.org/e">this '
        "page</a>.</p>"
    )

    assert "Offer screening for psychological birth trauma before discharge" in content
    assert "All women should be screened using the EPDS" in content


def test_markdown_drops_advice_that_only_says_where_the_advice_lives() -> None:
    """Saying where advice lives is a signpost; saying what the advice is, is advice.

    14 sentences open this way. The locating test splits them exactly: 8 name a document,
    and the 6 kept are guidance, among them "Advice on diet and lifestyle is recommended to
    prevent and relieve heartburn in pregnancy".
    """
    content = _render_body(
        "<p>Advice on follow-up and retesting those with positive results is available in "
        "the Australian STI Management Guidelines for Use in Primary Care.</p>"
        "<p>Advice on avoiding constipation may assist women to prevent haemorrhoids.</p>"
    )

    assert "Australian STI Management Guidelines" not in content
    assert "Advice on avoiding constipation may assist women" in content


def test_markdown_drops_a_directory_label_whose_entry_is_prose() -> None:
    """One service under a label is still a directory, even without bullets."""
    content = _render_body(
        "<p><strong>Family violence support services</strong></p>"
        "<p>1800RESPECT can provide support and resources for women, and can also assist "
        "health professionals in identifying support services in a local area. "
        '<a href="https://1800respect.org.au/">1800respect.org.au</a></p>'
        "<p>Ask about safety at every postnatal visit.</p>"
    )

    assert "Family violence support services" not in content
    assert "1800RESPECT" not in content
    assert "Ask about safety at every postnatal visit." in content


def test_markdown_keeps_a_topic_section_labelled_with_the_word_resource() -> None:
    """EPY83j's "Human resource gaps in maternal and newborn health" is a topic, not a list.

    It is 1,002 characters of prose about the global midwife shortage. Every real directory
    entry measured between 176 and 362, which is what the length cap is drawn from.
    """
    content = _render_body(
        "<p><strong>Human resource gaps in maternal and newborn health</strong></p>"
        "<p>The low proportion of women assisted by skilled birth attendants is an important "
        "indicator of the global personnel shortage in the health sector. Approximately 60 "
        "million births each year occur in settings other than a health facility, and of "
        "those a large share are attended by relatives or by nobody at all, which remains "
        "the central obstacle to reducing maternal mortality in the poorest regions of the "
        'world. See <a href="https://x.org/a">the workforce report</a> for the full series.</p>'
    )

    assert "Human resource gaps in maternal and newborn health" in content
    assert "the global personnel shortage in the health sector" in content


def test_markdown_drops_a_table_footnote_that_only_credits_a_source() -> None:
    """The legend stays; the line saying where it was adapted from goes.

    nJW8bE closes its recommendation-strength table with "* Adapted from GRADE working group
    (www.gradeworkinggroup.org)". The table is the key explaining what Level 1 and Level 2
    mean, which is exactly what the content guards protect. 26 credits across 12 guidelines.
    """
    content = _render_body(
        "<p><strong>Table A2.</strong> Nomenclature for grading recommendations</p>"
        "<p>Level 1 means most patients should receive the recommended course of action.</p>"
        '<p>* Adapted from GRADE working group (<a href="http://www.gradeworkinggroup.org/">'
        "www.gradeworkinggroup.org</a>)</p>"
    )

    assert "Adapted from GRADE working group" not in content
    assert "Level 1 means most patients should receive" in content
    assert "Nomenclature for grading recommendations" in content


def test_markdown_keeps_a_table_footnote_that_defines_something() -> None:
    """The other kind of footnote is often the only place a threshold is written down."""
    content = _render_body(
        "<p>* Severe disease defined as a systolic blood pressure below 90 mmHg.</p>"
        "<p>† Certainty downgraded one level for imprecision.</p>"
    )

    assert "systolic blood pressure below 90 mmHg" in content
    assert "downgraded one level for imprecision" in content


def test_markdown_drops_a_caption_that_is_only_a_table_number() -> None:
    """A bare "Table 2." labels nothing. A numbered caption with a title is kept.

    Three of the 90 numbered captions in the corpus are bare; the other 87 carry a title,
    and those titles are 9,415 characters of what each table actually holds.
    """
    content = _render_body("<p><strong>Table 1.</strong></p><p><strong>Table 2.</strong> Scope of the guidelines</p>")

    assert "Scope of the guidelines" in content
    assert content.count("Table") == 1


def test_markdown_drops_a_search_strategy_written_inside_a_section_body() -> None:
    """The skip list matches sections; publishers also write this as a heading in the HTML.

    nJW8bE puts "SEARCH STRATEGY" between "BACKGROUND" and the recommendation in every one of
    its sections. "search strategies" has been on the skip list throughout and never touched
    it, because the list matches section objects in the source data and this is not one.
    """
    content = _render_body(
        "<h4>BACKGROUND</h4><p>Progressive renal dysfunction remains a major contributor to "
        "morbidity in ADPKD.</p>"
        "<h4>SEARCH STRATEGY</h4><p>Databases Searched: medical subject headings for "
        "polycystic kidney disease were combined with terms relating to medications. Date of "
        "search: November 2014</p>"
        "<h4>RATIONALE</h4><p>Tolvaptan slows the decline in kidney function.</p>"
    )

    assert "Databases Searched" not in content
    assert "SEARCH STRATEGY" not in content
    assert "Progressive renal dysfunction remains a major contributor" in content
    assert "Tolvaptan slows the decline in kidney function." in content


def test_markdown_keeps_clinical_questions_filed_under_a_search_heading() -> None:
    """Publishers file the questions under the heading that describes answering them.

    ERx1yL lists six under "Literature search", among them "In patients presenting to primary
    care, what signs, symptoms and clinical features are predictive of cancer?" Those are the
    questions the guideline exists to answer, not a description of how it looked for them.
    """
    content = _render_body(
        "<h4>Literature search</h4>"
        "<p>A literature search was conducted in June 2011 for the following clinical "
        "questions:</p>"
        "<ul><li>In patients presenting to primary care, what signs and clinical features are "
        "predictive of cancer?</li>"
        "<li>What are the major known risk factors for cancer?</li></ul>"
        "<p>Databases searched included PubMed and Embase.</p>"
    )

    assert "what signs and clinical features are predictive of cancer?" in content
    assert "What are the major known risk factors for cancer?" in content


def test_markdown_keeps_a_search_block_that_reports_what_the_search_found() -> None:
    """A search section states dates and databases. One that states a finding is not just that."""
    content = _render_body(
        "<h4>Search methods</h4><p>The databases were searched to July 2023. No studies were "
        "identified for this question.</p>"
    )

    assert "No studies were identified for this question." in content


def test_markdown_stops_a_search_block_at_the_next_heading_of_the_same_depth() -> None:
    """The extent is everything up to the next heading no deeper than the one being removed."""
    content = _render_body(
        "<h2>Methods</h2><h4>Search strategy</h4><p>Databases searched: Medline and Embase.</p>"
        "<h2>Results</h2><p>Twelve trials were included in the review.</p>"
    )

    assert "Medline and Embase" not in content
    assert "Twelve trials were included in the review." in content
    assert "Results" in content


def test_markdown_drops_a_sentence_that_only_says_a_figure_exists() -> None:
    """The figure stays in the document; the sentence announcing it adds nothing to it.

    Anchored at the sentence start, which is what lets the judgement written beside it
    survive: "Fig. 2 summarizes the above categories" goes, "The GDG acknowledged that the
    presence of multiple factors simultaneously could confer higher risk" stays. 75 of these
    across 41 guidelines.
    """
    content = _render_body(
        "<p>Fig. 2 summarizes the above categories and associated criteria as well as the "
        "potential for overlap. The GDG acknowledged that the presence of multiple factors "
        "simultaneously could confer higher risk.</p>"
    )

    assert "Fig. 2 summarizes" not in content
    assert "The GDG acknowledged that the presence of multiple factors" in content


def test_markdown_keeps_a_table_pointer_that_instructs() -> None:
    """Five of the 80 assert something, and the claim guard is what separates them."""
    content = _render_body(
        "<p>Table 3 outlines information that should be provided to patients and their "
        "carers.</p>"
        "<p>The detection rate was higher with contrast agents, as shown in Figure 2.</p>"
    )

    assert "Table 3 outlines information that should be provided" in content
    assert "The detection rate was higher with contrast agents" in content


def test_markdown_drops_a_pointer_whose_wording_is_split_by_emphasis() -> None:
    """Emphasis markers land anywhere and break a wording test wherever they do.

    "**A Summary of Living Recommendations (Guideline Version 5.0) is available**
    [**HERE**](url)" renders as "available** **HERE", so "available here" never matched and
    the line survived in both guidelines carrying it.
    """
    content = _render_body(
        "<p><strong>A Summary of Living Recommendations (Guideline Version 5.0) is "
        'available</strong> <a href="https://drive.google.com/file/d/1_Zp/view"><strong>HERE'
        "</strong></a></p>"
        "<p>Methotrexate is usually first line.</p>"
    )

    assert "Summary of Living Recommendations" not in content
    assert "Methotrexate is usually first line." in content


def test_build_magic_guideline_text_strips_a_question_number_from_a_heading() -> None:
    """The internal question number and the label in front of the topic both go.

    "PICO 5 Executive Summary: Subcutaneous methotrexate versus oral methotrexate for JIA"
    carries one useful phrase and two that mean nothing outside the document that wrote them.
    The markers arrive escaped, because the heading is rendered through _markdown first.
    """
    guideline = {
        "name": "Juvenile idiopathic arthritis",
        "sections": [
            {
                "heading": (
                    "PICO 5 <strong>Executive Summary:</strong> Subcutaneous methotrexate "
                    "versus oral methotrexate for JIA"
                ),
                "text": "<p>Registry data were searched in the absence of trials.</p>",
                "recommendations": [],
                "subSections": [],
            }
        ],
    }
    content = _render(guideline)

    assert "## Subcutaneous methotrexate versus oral methotrexate for JIA" in content
    assert "PICO 5" not in content


def test_build_magic_guideline_text_keeps_a_heading_that_is_only_a_question_number() -> None:
    """One section is headed "PICO 4 Executive Summary:" with no topic after it.

    Emptying that heading would take the label off the two recommendations underneath, so
    the strip only applies when something is left.
    """
    guideline = {
        "name": "Juvenile idiopathic arthritis",
        "sections": [
            {
                "heading": "PICO 4 <strong>Executive Summary:</strong>",
                "text": "<p>4a) Intra-articular glucocorticoid treatment is suggested.</p>",
                "recommendations": [],
                "subSections": [],
            }
        ],
    }
    content = _render(guideline)

    assert "PICO 4" in content
    assert "Intra-articular glucocorticoid treatment is suggested." in content


def test_markdown_drops_an_appendix_written_as_a_bold_line_in_a_body() -> None:
    """Paperwork filed as an appendix inside a section that also carries evidence.

    jzb7Xj puts five appendices under one "Appendices" heading. Two are how the panel was
    picked and which organizations were written to; the rest carry patients' values and the
    adverse-event evidence, so nothing at section level can separate them.
    """
    body = (
        "<p><strong>APPENDIX 2. ADDITIONAL DESCRIPTION OF THE METHODS</strong><br />"
        "The panel was selected following internal processes established by the sponsor.</p>"
        "<p><strong>APPENDIX 5. ADVERSE EVENTS ASSOCIATED WITH OPIOIDS</strong><br />"
        "Filling a perioperative opioid prescription was associated with an increased risk "
        "of 0.8% for persistent opioid use.</p>"
    )
    rendered = _render_body(body)

    assert "ADDITIONAL DESCRIPTION OF THE METHODS" not in rendered
    assert "The panel was selected" not in rendered
    assert "an increased risk of 0.8% for persistent opioid use" in rendered


def test_markdown_keeps_an_appendix_whose_title_is_not_paperwork() -> None:
    """Only titles on the list go. An appendix label is not by itself a reason to remove."""
    body = (
        "<p><strong>APPENDIX 4. PATIENTS' VALUES AND PREFERENCES STATEMENT</strong><br />"
        "Most people prefer to manage any level of acute dental pain with nonopioid "
        "medications.</p>"
    )
    rendered = _render_body(body)

    assert "PATIENTS' VALUES AND PREFERENCES STATEMENT" in rendered
    assert "prefer to manage any level of acute dental pain" in rendered


def test_markdown_drops_an_unlabelled_bibliography() -> None:
    """A reference list with nothing announcing it, which no label rule can reach.

    jzb7Xj ends its appendices with four entries numbered e1 to e4 - the journal's
    online-only reference list - under no heading at all.
    """
    body = (
        "<p>Give artemether-lumefantrine within 24 hours of a positive test.</p>"
        "<p>e1. Alhazzani W, Lewis K, Jaeschke R, et al. Conflicts of interest disclosure "
        "forms in critical care guidelines. Intensive Care Med. 2018;44(10):1691-1698.<br />"
        "e2. Chua KP, Kenney BC, Waljee JF. Dental opioid prescriptions and overdose risk. "
        "Am J Prev Med. 2021;61(2):165-173.</p>"
    )
    rendered = _render_body(body)

    assert "Alhazzani" not in rendered
    assert "Chua KP" not in rendered
    assert "Give artemether-lumefantrine within 24 hours" in rendered


def test_markdown_keeps_a_numbered_list_of_findings() -> None:
    """The same numbering carries the evidence itself, so the journal signature decides.

    jzb7Xj writes its adverse-event appendix as a numbered list of studies. Those entries
    name an author and a year but never a volume, issue and page range, which is what
    separates them from the bibliography above.
    """
    body = (
        "<p>1. Harbaugh and colleagues determined the association between filling an opioid "
        "prescription after a third-molar extraction and persistent use (low certainty).<br />"
        "2. Schroeder and colleagues found a 2.5% increased risk of filling more than one "
        "opioid prescription 90 to 365 days after the initial prescription.</p>"
    )
    rendered = _render_body(body)

    assert "Harbaugh and colleagues" in rendered
    assert "2.5% increased risk" in rendered


def test_markdown_drops_a_publication_announcement_and_its_citation() -> None:
    """The "how to cite" block under wording no label list can match.

    nyO1Yj writes it as a bolded sentence ending in a colon, then the reference. Reaching it
    through the inline-label rule was tried and rejected because it needed three separate
    loosenings of a rule that deletes text; this keys on the pair instead.
    """
    body = (
        "<p><strong>This guideline manuscript was published in </strong>"
        "<em><strong>Alzheimer's and Dementia: The Journal of the Alzheimer's Association</strong></em>"
        "<strong> on July 29th, 2025:</strong></p>"
        "<p>Palmqvist S, Whitson HE, et al. Alzheimer's Association Clinical Practice Guideline on "
        "the use of blood-based biomarkers. Alzheimer's Dement. 2025;e70535. "
        "https://doi.org/10.1002/alz.70535</p>"
        "<p>Blood-based biomarkers should be used only in specialized care settings.</p>"
    )
    rendered = _render_body(body)

    assert "was published in" not in rendered
    assert "Palmqvist" not in rendered
    assert "alz.70535" not in rendered
    assert "Blood-based biomarkers should be used only in specialized care settings." in rendered


def test_markdown_keeps_a_sentence_about_publication_with_no_citation_under_it() -> None:
    """The citation is what makes the rule safe, so without one nothing is removed."""
    body = (
        "<p><strong>This Guideline should be used in tandem with other published resources:</strong></p>"
        "<p>- National Strategic Framework for Aboriginal and Torres Strait Islander Mental Health</p>"
    )
    rendered = _render_body(body)

    assert "published resources" in rendered
    assert "National Strategic Framework" in rendered


def test_markdown_drops_a_paragraph_that_is_only_a_date() -> None:
    """The line a publisher signs a foreword off with, left stranded by the scrape."""
    rendered = _render_body(
        "<p>Nigeria still struggles to reduce health inequalities.</p>"
        "<p>13<sup>th</sup> June, 2025</p>"
        "<p>Give kangaroo mother care immediately after birth.</p>"
    )

    assert "June, 2025" not in rendered
    assert "Nigeria still struggles to reduce health inequalities." in rendered
    assert "Give kangaroo mother care immediately after birth." in rendered


def test_markdown_keeps_a_date_inside_a_sentence() -> None:
    """Only a block that is nothing but a date goes, so a date in prose is untouched."""
    rendered = _render_body("<p>The last evidence search was run in June 2025 and found 14 new trials.</p>")

    assert "run in June 2025 and found 14 new trials" in rendered


def test_markdown_drops_a_list_of_document_links_and_its_label() -> None:
    """Links to reports the scraper never fetched, under the label that introduces them."""
    rendered = _render_body(
        "<p><strong>SUPPORTING DOCUMENTS</strong></p>"
        '<p>Evidence summary: <a href="https://www.cancer.org.au/a.pdf">SR-Evidence Summary</a><br />'
        'Systematic review report: <a href="https://www.cancer.org.au/b.pdf">SR report</a></p>'
        "<p>Repeat testing at 12 months is recommended for this group.</p>"
    )

    assert "SUPPORTING DOCUMENTS" not in rendered
    assert "SR-Evidence Summary" not in rendered
    assert "Repeat testing at 12 months is recommended for this group." in rendered


def test_markdown_keeps_a_paragraph_that_merely_contains_a_link() -> None:
    """The whole block has to be links, so prose carrying one is untouched."""
    rendered = _render_body(
        '<p>Screening is offered every 5 years, as set out in the <a href="https://x.org/p">'
        "National Cervical Screening Policy</a>.</p>"
    )

    assert "Screening is offered every 5 years" in rendered


def test_markdown_drops_a_sentence_pointing_at_a_flowchart() -> None:
    """A flowchart is a document, and the picture did not survive the scrape either.

    Kj2WZL closes seven paragraphs with "See the flowchart for the literature search under
    reference" and points at its dystocia flowcharts four more times.
    """
    rendered = _render_body(
        "<p>Amniotomy may be considered when progress is slow. "
        "See the flowchart for dystocia in the second stage of labour under reference.</p>"
    )

    assert "Amniotomy may be considered when progress is slow." in rendered
    assert "flowchart" not in rendered


def test_markdown_keeps_a_sentence_describing_what_a_flowchart_shows() -> None:
    """Only "see the flowchart" goes. A sentence that states what one contains is content."""
    rendered = _render_body("<p>The flowchart for dystocia sets a two-hour limit before reassessment.</p>")

    assert "two-hour limit before reassessment" in rendered


# ---------------------------------------------------------------------------
# Section keep/drop policy
# ---------------------------------------------------------------------------


def test_build_magic_guideline_text_skips_platform_boilerplate() -> None:
    """MAGICapp's repeated 'how to use' section is dropped, not indexed."""
    with _client() as client:
        ref = list_published_guidelines(client).refs[0]
        content, _, _, _ = build_magic_guideline_text(client, ref)

    assert "How To Use This Guideline" not in content
    assert "open the evidence behind it" not in content
    assert "Disease modifying therapy" in content


BOILERPLATE_WITH_GUIDANCE = {
    "name": "Patient blood management",
    "sections": [
        {
            "heading": "Glossary",
            "text": "<p>Confidence interval: a range of values.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Introduction",
            "text": "<p>Context for the guideline.</p>",
            "recommendations": [
                {
                    "text": "<p>Pregnancies at risk of fetal anaemia should be assessed by Doppler ultrasound.</p>",
                    "strength": "NOTSET",
                }
            ],
            "subSections": [],
        },
    ],
}


def test_build_magic_guideline_text_skips_front_matter_without_recommendations() -> None:
    """A glossary carrying no guidance is dropped as boilerplate."""
    content = _render(BOILERPLATE_WITH_GUIDANCE)

    assert "Glossary" not in content
    assert "Confidence interval" not in content


DECORATED_HEADINGS = {
    "name": "Cervical screening",
    "sections": [
        {
            "heading": "<p><strong>How to use these guidelines</strong></p>",
            "text": "<p>Read the recommendations first.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "7.2 Conflicts of interest",
            "text": "<p>Panel members declared the following.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Appendices",
            "text": "",
            "recommendations": [],
            "subSections": [
                {
                    "heading": "Appendix A. Guideline development process",
                    "text": "<p>Each recommendation was tabled as an agenda item.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
                {
                    "heading": "App E - Working Party members and project team contributions",
                    "text": "<p>Ms Chloe Jennett, Program Coordinator.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
                {
                    "heading": "Appendix F. Safety Monitoring",
                    "text": "<p>Report adverse events within 24 hours.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
            ],
        },
    ],
}


def test_build_magic_guideline_text_skips_decorated_front_matter_headings() -> None:
    """Emphasis, section numbers and appendix labels no longer defeat the skip list.

    The lookup is an exact string match, so a publisher that bolds its headings -
    Cancer Council Australia bolds all of them - used to bypass the skip list
    entirely. Numbering and "Appendix A." labels defeated it the same way.
    """
    content = _render(DECORATED_HEADINGS)

    assert "How to use these guidelines" not in content
    assert "Read the recommendations first" not in content
    assert "Conflicts of interest" not in content
    assert "Panel members declared" not in content


def test_build_magic_guideline_text_skips_admin_sections_inside_an_appendix() -> None:
    """Administrative topics inside an appendix go; the appendix itself stays.

    "Appendices" is a container word - it says where a section sits, not what it
    holds - so matching it would drop a whole back-of-document container on no
    evidence. The topics one level down are what name methodology.

    "Appendix A. Guideline development process" is deliberately *not* asserted here
    any more: reading all 59 sections with that wording found 20 of 58 carry a real
    clinical statement, so only the "Guideline Development Group" wording is matched
    now. The rest of this fixture still goes.
    """
    content = _render(DECORATED_HEADINGS)

    assert "Working Party members" not in content
    assert "Chloe Jennett" not in content


INTEREST_DISCLOSURES = {
    "name": "Atrial fibrillation",
    "sections": [
        {
            "heading": "Anticoagulation",
            "text": "<p>Offer anticoagulation to patients at elevated stroke risk.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Acronyms and abbreviations",
            "text": "<p>NOAC: non-vitamin K oral anticoagulant.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Declaration of conflicting interests",
            "text": "<p>Dr X: national co-ordinator for Boehringer Ingelheim on dabigatran.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Annex 3. Summary and management of declared interests from GDG members",
            "text": "<p>Member honoraria are tabled below.</p>",
            "recommendations": [],
            "subSections": [],
        },
    ],
}


ACKNOWLEDGMENT_WORDINGS = {
    "name": "Dementia care",
    "sections": [
        {
            "heading": "Risk reduction",
            "text": "<p>Encourage regular physical activity.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Publication and acknowledgments",
            "text": "<p>Funded by the Department of Health. Co-Chairs: Professor V. Srikanth.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Acknowledgement",
            "text": "<p>We thank the working group members.</p>",
            "recommendations": [],
            "subSections": [],
        },
    ],
}


def test_build_magic_guideline_text_skips_acknowledgment_wordings() -> None:
    """Publishers word acknowledgment sections a dozen ways; one stem catches them all.

    "Publication and acknowledgments" (j97pAn) is 22,242 characters of funders and
    contributors that no exact name on the skip list covered, and the singular
    "Acknowledgement" missed the plural-only exact names.
    """
    content = _render(ACKNOWLEDGMENT_WORDINGS)

    assert "physical activity" in content
    assert "Funded by the Department" not in content
    assert "thank the working group" not in content


GUIDELINE_PAPERWORK = {
    "name": "Inflammatory arthritis",
    "sections": [
        {
            "heading": "Executive Summary",
            "text": "<p>The panel does not recommend routine use of MDMA-assisted psychotherapy.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Initial DMARD therapy",
            "text": "<p>Methotrexate is usually first line.</p>",
            "recommendations": [{"text": "<p>Consider methotrexate in combination.</p>", "strength": "WEAK"}],
            "subSections": [],
        },
        {
            "heading": "Methods and Processes - Evidence Review",
            "text": "<p>Questions were validated by stakeholder consultation.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Guideline Expert Advisory Panel and Technical Team",
            "text": "<p>Prof R. Buchbinder, Monash University, Rheumatology.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Guideline Meeting Attendance Record and Recommendation Authorship",
            "text": "<p>Click here to view attendance at each meeting.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Appendices - Living evidence updates, forest plots and other supplementary information",
            "text": "<p>Update 1 (July 2024) - no new evidence.</p>",
            "recommendations": [],
            "subSections": [],
        },
    ],
}


def test_build_magic_guideline_text_skips_guideline_paperwork_sections() -> None:
    """How the guideline was made, who wrote it, and links to evidence files are dropped."""
    content = _render(GUIDELINE_PAPERWORK)

    assert "Consider methotrexate in combination." in content
    assert "Methotrexate is usually first line." in content
    assert "stakeholder consultation" not in content
    assert "Buchbinder" not in content
    assert "attendance at each meeting" not in content
    assert "no new evidence" not in content


def test_build_magic_guideline_text_keeps_executive_summaries_by_name() -> None:
    """An "Executive summary" names a container, not a kind of content, so it is not a rule.

    It was one, corpus-wide, and that was wrong. 61 of the 212 guidelines have such a
    section and 36 of them put their whole recommendation set in it: the WHO postpartum
    haemorrhage guideline states 18 of its 46 recommendations there and nowhere else,
    including the dose "30mg to 60mg of elemental iron and 400ug (0.4mg) of folic acid".
    Removing the section removed the recommendation from the corpus entirely.

    A rule may only name a heading whose content is paperwork whatever the publisher put
    under it. This one fails that test in both directions, so the guidelines whose summary
    really is front matter say so themselves, in _GUIDELINE_SKIP_HEADINGS, each one checked
    against the rendered text first.
    """
    content = _render(GUIDELINE_PAPERWORK)

    assert "does not recommend routine use of MDMA-assisted psychotherapy" in content


def test_build_magic_guideline_text_keeps_an_executive_summary_that_states_scope() -> None:
    """The content guards reach an executive summary like any other skipped section.

    Nine of the 74 are held this way. Their scope statements are what a verifier needs in
    order not to confirm an out-of-scope claim, and dropping the section by name would take
    them with it.
    """
    guideline = {
        "name": "WHO guidelines for malaria",
        "sections": [
            {
                "heading": "Executive summary",
                "text": (
                    "<p>WHO malaria recommendations are intended to be short, actionable "
                    "statements.</p>"
                    "<p>These guidelines do not apply to people travelling from non-endemic "
                    "settings.</p>"
                ),
                "recommendations": [],
                "subSections": [],
            }
        ],
    }
    content = _render(guideline)

    assert "do not apply to people travelling from non-endemic settings" in content


def test_build_magic_guideline_text_skips_interest_disclosure_sections() -> None:
    """Named individuals' payment disclosures leave the corpus; clinical text stays.

    A claim about patient care cannot be verified against "Dr X received honoraria
    from company Y", and these are real people's payment records headed for an open
    corpus, so every observed disclosure-heading shape is skip-listed.
    """
    content = _render(INTEREST_DISCLOSURES)

    assert "Offer anticoagulation" in content
    assert "NOAC" not in content
    assert "Boehringer Ingelheim" not in content
    assert "honoraria" not in content


def test_build_magic_guideline_text_measures_the_skip_ceiling_as_text_not_markup() -> None:
    """The oversize guard reads what a reader would lose, not the markup around it.

    One catalogue search-strategy section is 1,673,817 characters of HTML holding
    2,664 of readable text; measuring HTML kept it on markup weight alone.
    """
    bloated = '<p data-pad="' + "x" * (2 * magic.MAX_SKIPPED_SECTION_CHARS) + '">Databases were searched.</p>'
    guideline = {
        "name": "Any guideline",
        "sections": [
            {"heading": "Treatment", "text": "<p>Treat early.</p>", "recommendations": [], "subSections": []},
            {"heading": "Search strategy", "text": bloated, "recommendations": [], "subSections": []},
        ],
    }
    content = _render(guideline)

    assert "Treat early." in content
    assert "Databases were searched." not in content


def test_build_magic_guideline_text_keeps_a_skip_listed_section_too_big_to_drop_on_its_heading() -> None:
    """A skip-listed heading over a huge readable body is not front matter, so it stays."""
    prose = "<p>" + "Give oxytocin. " * (magic.MAX_SKIPPED_SECTION_CHARS // 10) + "</p>"
    guideline = {
        "name": "Any guideline",
        "sections": [
            {"heading": "Methods", "text": prose, "recommendations": [], "subSections": []},
        ],
    }
    content = _render(guideline)

    assert "Give oxytocin." in content


def test_build_magic_guideline_text_keeps_a_generic_heading_that_carries_guidance() -> None:
    """Publishers file real recommendations under generic headings, so those stay.

    The National Blood Authority puts four recommendations under 'Introduction'; a
    skip driven by the heading alone would delete them.
    """
    content = _render(BOILERPLATE_WITH_GUIDANCE)

    assert "Introduction" in content
    assert "Doppler ultrasound" in content


def test_build_magic_guideline_text_skips_hidden_and_bare_outcomes() -> None:
    """A hidden outcome is the publisher's call, and a bare name asserts nothing.

    `LOW` also has to map to "low": the per-outcome certainty field spells the scale
    differently from `keyInfo.evidenceStrength`, which uses WEAK for the same level, so
    sharing one table would drop every LOW rating silently.
    """
    content = _render(EFFECT_ESTIMATE_PICO)

    assert "An outcome the publisher hid" not in content
    assert "A name and nothing else" not in content
    assert "certainty low" in content


CONTRIBUTOR_CHAPTER = {
    "name": "WHO guidelines for malaria",
    "sections": [
        {
            "heading": "Case management",
            "text": "<p>Treat uncomplicated malaria with an artemisinin-based combination therapy.</p>",
            "recommendations": [{"text": "<p>Give artesunate for at least 24 hours.</p>", "strength": "STRONG"}],
            "subSections": [],
        },
        {
            "heading": "Contributors and interests",
            "text": "<p>The many contributors are acknowledged in the sub-sections below.</p>",
            "recommendations": [],
            "subSections": [
                {
                    "heading": "Recommendations for vector control",
                    "text": (
                        "<h4><strong>Members of the Guidelines Development Group (GDG) (2019)</strong></h4>"
                        "<p>Dr Constance Bart-Plange, Independent Malaria Consultant, Accra, Ghana</p>"
                    ),
                    "recommendations": [],
                    "subSections": [],
                }
            ],
        },
    ],
}


def test_build_magic_guideline_text_skips_contributor_chapters() -> None:
    """A contributor chapter goes, even though its subsections are headed "Recommendations for...".

    WHO files 117,757 characters of member rosters and interest declarations under
    "Contributors and interests" (LwRMXj) and heads each block "Recommendations for
    vector control", "Recommendations for treatment" and so on — they group the
    contributors by which recommendations those people worked on. So nothing keyed on
    the word "recommendation" can identify this block; the parent heading is the only
    handle on it.
    """
    content = _render(CONTRIBUTOR_CHAPTER)

    assert "Give artesunate for at least 24 hours." in content
    assert "artemisinin-based combination therapy" in content
    assert "Bart-Plange" not in content
    assert "Contributors and interests" not in content


def test_build_magic_guideline_text_skips_plural_guidelines_development_group() -> None:
    """The plural defeats the singular "guideline development" topic word.

    WHO writes "Guidelines Development Group", so the substring "guideline
    development" does not occur and the section escaped the skip list.
    """
    guideline = {
        "name": "WHO guidelines for malaria",
        "sections": [
            {
                "heading": "Prevention",
                "text": "<p>Deploy insecticide-treated nets for malaria prevention.</p>",
                "recommendations": [],
                "subSections": [],
            },
            {
                "heading": "Guidelines development group",
                "text": "<p>Dr John Gimnig (Chair), Centers for Disease Control and Prevention.</p>",
                "recommendations": [],
                "subSections": [],
            },
            {
                "heading": "Guidelines Steering Group (2019)",
                "text": "<p>Dr Rabindra Abeyasinghe, WHO Regional Office for the Western Pacific.</p>",
                "recommendations": [],
                "subSections": [],
            },
            {
                "heading": "Members of the External Review Group (ERG)",
                "text": "<p>Professor Ahmed Adeel, Independent Consultant.</p>",
                "recommendations": [],
                "subSections": [],
            },
        ],
    }
    content = _render(guideline)

    assert "insecticide-treated nets" in content
    assert "Gimnig" not in content
    assert "Abeyasinghe" not in content
    assert "Adeel" not in content


PLATFORM_ADMINISTRATION = {
    "name": "WHO guidelines for malaria",
    "sections": [
        {
            "heading": "Executive summary",
            "text": (
                "<p>WHO malaria recommendations are intended to be short, actionable statements.</p>"
                "<h4><strong>Scope</strong></h4>"
                "<p>No guidance is given on the use of antimalarial agents to prevent malaria in "
                "people travelling from non-endemic settings.</p>"
                "<h4><strong>Link to WHO prequalification</strong></h4>"
                "<p>The prequalification process consists of a transparent assessment.</p>"
                "<h4><strong>Updating evidence-based guidance</strong></h4>"
                "<p>The first edition was released in early 2021.</p>"
                "<h4><strong>Dissemination</strong></h4>"
                "<p>These Guidelines are available on the MAGICapp online platform.</p>"
                "<h4><strong>Feedback</strong></h4>"
                "<p>Write to gmpfeedback@who.int to identify recommendations needing update.</p>"
            ),
            "recommendations": [],
            "subSections": [],
        }
    ],
}


def test_build_magic_guideline_text_drops_platform_administration_from_executive_summary() -> None:
    """WHO opens its executive summary with several blocks of platform administration."""
    content = _render(PLATFORM_ADMINISTRATION)

    assert "short, actionable statements" in content
    assert "prequalification process" not in content
    assert "released in early 2021" not in content
    assert "available on the MAGICapp online platform" not in content
    assert "gmpfeedback@who.int" not in content


def test_build_magic_guideline_text_keeps_the_scope_block_beside_the_dropped_ones() -> None:
    """Scope sits in the same run of blocks and is kept deliberately.

    A scope statement says what the guideline does *not* cover, which is what stops a
    verifier confirming an out-of-scope claim against it.

    Rendered under a short code no real guideline uses, because the default catalogue entry
    borrows `nyxpZL`, and that guideline has a rule of its own dropping an inline "Scope"
    heading. This test is about the general behaviour, not about that guideline.
    """
    catalogue = [{**CATALOGUE[0], "shortCode": "zzTEST"}]
    with _client(catalogue=catalogue, guideline=PLATFORM_ADMINISTRATION) as client:
        ref = list_published_guidelines(client).refs[0]
        content, _sections, _title, _emitted = build_magic_guideline_text(client, ref)

    assert "people travelling from non-endemic settings" in content


def test_build_magic_guideline_text_drops_the_definition_of_whos_own_vocabulary() -> None:
    """WHO explains what a guideline and a good practice statement are; that is a glossary.

    Matched as a whole heading, never on the words inside it: 50 headings across the
    catalogue read "Good practice statement 2", "Good practice statement 3" and so on,
    and those are the recommendations themselves.
    """
    content = _render_body(
        "<p>WHO malaria recommendations are intended to be short, actionable statements.</p>"
        "<h4><strong>WHO guidelines, recommendations and good practice statements</strong></h4>"
        "<p>A WHO guideline is any document developed by WHO containing recommendations.</p>"
        "<p>The primary purpose of these Guidelines is to support policy-makers.</p>"
    )

    assert "short, actionable statements" in content
    assert "any document developed by WHO" not in content
    assert "support policy-makers" not in content


def test_build_magic_guideline_text_skips_a_guideline_translations_section() -> None:
    """Links to the same guideline in other languages are navigation, not evidence.

    An exact name rather than a topic word: the only other section in the catalogue
    whose heading contains "translation" is 19,291 characters of "Knowledge translation
    for self-care interventions" (Lr21gL), which is real content.
    """
    guideline = {
        "name": "WHO guidelines for malaria",
        "sections": [
            {
                # Not an executive summary: that heading is on the skip list now, and the
                # question here is only whether the translations child is dropped.
                "heading": "Treating malaria",
                "text": "<p>WHO recommendations are short, actionable statements.</p>",
                "recommendations": [],
                "subSections": [
                    {
                        "heading": "Guideline translations",
                        "text": '<ul><li><a href="https://app.magicapp.org/x">Lignes directrices</a></li></ul>',
                        "recommendations": [],
                        "subSections": [],
                    },
                    {
                        "heading": "Knowledge translation for self-care interventions",
                        "text": "<p>Health workers need training to deliver self-care interventions.</p>",
                        "recommendations": [],
                        "subSections": [],
                    },
                ],
            }
        ],
    }
    content = _render(guideline)

    assert "short, actionable statements" in content
    assert "Lignes directrices" not in content
    assert "Health workers need training" in content


def test_build_magic_guideline_text_keeps_a_narrative_guideline_development_section() -> None:
    """A "Guideline development process" write-up opens with background, and that is evidence.

    Reading all 59 such sections in the catalogue found 20 of 58 carry a checkable
    clinical statement — disease burden, a risk factor, a threshold, or a statement
    that no studies were found. The same heading sits on clean roster sections too, so
    only the narrow "group" wording is safe to match.
    """
    guideline = {
        "name": "Head and neck cancer nutrition",
        "sections": [
            {
                "heading": "Guideline development process",
                "text": (
                    "<p>Head and neck cancer is the fifth most common cancer worldwide. Tobacco and "
                    "alcohol account for up to 80% of all cases, and malnutrition rates are reported "
                    "between 30-50%.</p><p>The Working Group applied the GRADE methodology.</p>"
                ),
                "recommendations": [],
                "subSections": [],
            },
            {
                "heading": "Guideline Development Group",
                "text": "<p>Professor A. Smith, Monash University, Dietetics.</p>",
                "recommendations": [],
                "subSections": [],
            },
        ],
    }
    content = _render(guideline)

    assert "malnutrition rates are reported between 30-50%" in content
    assert "fifth most common cancer worldwide" in content
    assert "Professor A. Smith" not in content


def test_build_magic_guideline_text_keeps_a_section_that_points_and_then_recommends() -> None:
    """A pointer above an actual recommendation is not a pointer-only section."""
    guideline = {
        "name": "Malaria",
        "sections": [
            {
                "heading": "Pre-referral treatment options",
                "text": "<p>See recommendation.</p>",
                "recommendations": [{"text": "<p>Give rectal artesunate before referral.</p>", "strength": "STRONG"}],
                "subSections": [],
            }
        ],
    }
    content = _render(guideline)

    assert "Pre-referral treatment options" in content
    assert "Give rectal artesunate before referral." in content


def test_build_magic_guideline_text_keeps_a_discussion_section() -> None:
    """A Discussion section is where a panel states what the evidence showed.

    Reading all 73 in the catalogue found 30 of the 32 checked carry a statement a
    verdict could rest on. It had been dropped by name because no such section holds a
    recommendation *object* — true, and useless, the same way it was for executive
    summaries.
    """
    guideline = {
        "name": "Transient ischaemic attack",
        "sections": [
            {
                "heading": "Discussion",
                "text": (
                    "<p>Early initiation of dual antiplatelet therapy with aspirin and clopidogrel in "
                    "high risk non-cardioembolic TIA patients for up to 21 days reduces the risk of "
                    "stroke recurrence over single antiplatelet treatment. For every 50 at-risk "
                    "patients treated in this way, one patient will avoid having a recurrent stroke.</p>"
                ),
                "recommendations": [],
                "subSections": [],
            }
        ],
    }
    content = _render(guideline)

    assert "For every 50 at-risk patients treated in this way" in content
    assert "reduces the risk of stroke recurrence" in content


def test_build_magic_guideline_text_skips_a_directory_of_other_organisations_guidelines() -> None:
    """A resources section is a table of links to other bodies’ guidelines.

    "companion resources" was already an exact name; "additional resources" and "other
    resources" are the near-miss variants, and one is a 7,221-character directory. Worse
    than useless: a chunk full of guideline names retrieves on a clinical query and
    delivers only links.
    """
    guideline = {
        "name": "Colorectal cancer",
        "sections": [
            {
                "heading": "Follow-up after curative resection",
                "text": "<p>Offer colonoscopy at one year after resection.</p>",
                "recommendations": [],
                "subSections": [],
            },
            {
                "heading": "Additional resources",
                "text": (
                    "<p>There are many evidence-based resources on colorectal cancer available.</p>"
                    '<table><tr><td>NICE</td><td><a href="https://www.nice.org.uk/guidance/cg131">'
                    "Colorectal cancer: diagnosis and management</a></td></tr></table>"
                ),
                "recommendations": [],
                "subSections": [],
            },
        ],
    }
    content = _render(guideline)

    assert "Offer colonoscopy at one year after resection." in content
    assert "Additional resources" not in content
    assert "nice.org.uk" not in content


PICO_FRAME_UNDER_SKIPPED_PARENT = {
    "name": "VTE prophylaxis after ischaemic stroke",
    "sections": [
        {
            "heading": "About the Guidelines",
            "text": "",
            "recommendations": [],
            "subSections": [
                {
                    "heading": "Population",
                    "text": (
                        "<p>These recommendations refer to patients who have suffered an ischaemic "
                        "stroke. It specifically does not consider patients who have had "
                        "intracerebral haemorrhage.</p>"
                    ),
                    "recommendations": [],
                    "subSections": [],
                },
                {
                    "heading": "3.2 Outcomes",
                    "text": "<p>Death or dependency at follow up, measured with the Barthel Index.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
                {
                    "heading": "3.4 Values and preferences",
                    "text": "<p>We had insufficient information to describe patient experiences.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
                {
                    "heading": "Literature search by SRT teams",
                    "text": "<p>A search was run in Web of Science on 26 January 2024.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
                {
                    "heading": "Panel meetings",
                    "text": "<p>We held five virtual panel meetings.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
            ],
        },
    ],
}


METHODS_SECTION = {
    "name": "Blood biomarkers",
    "sections": [
        {
            "heading": "2 Methods",
            "text": "<p>We followed the standard operating procedures in the WHO handbook.</p>",
            "recommendations": [],
            "subSections": [
                {
                    "heading": "2.5.2 EtD framework",
                    "text": (
                        "<p>'The panel recommends' indicates a strong recommendation, and 'the "
                        "panel suggests' indicates a conditional recommendation.</p>"
                    ),
                    "recommendations": [],
                    "subSections": [],
                }
            ],
        }
    ],
}


def test_build_magic_guideline_text_keeps_a_methods_section_holding_the_legend() -> None:
    """A "Methods" section is kept when it explains what the guideline's labels mean.

    "Methods" is on the skip list, and for most guidelines that is right: it is search
    dates, database lists and panel rosters. `nyO1Yj` is the exception that made the
    heading untrustworthy - it files its recommendation legend under "2 Methods", so
    dropping the heading left the corpus carrying "the panel suggests" with nothing
    anywhere saying whether that meant strong or conditional guidance.

    The heading cannot tell those apart, so the body is asked instead: the legend keeps
    the section, and the process prose under it rides along, which is the price of not
    having a way to cut inside a section.
    """
    content = _render(METHODS_SECTION)

    assert "indicates a conditional recommendation" in content
    assert "standard operating procedures" in content
    assert "## 2 Methods" in content


def test_build_magic_guideline_text_drops_a_methods_section_that_is_only_process() -> None:
    """The same heading goes when the body is nothing but how the work was done.

    This is the case the skip list exists for, and the one that pays for keeping the
    entry: 76 of the corpus's 89 Methods sections are this, 1.3 million characters of
    search strategies and meeting counts.
    """
    content = _render(
        {
            "name": "Blood biomarkers",
            "sections": [
                {
                    "heading": "Methods",
                    "text": "<p>A search was run in Web of Science on 26 January 2024. We held five meetings.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
                {
                    "heading": "Treatment",
                    "text": "<p>Start therapy within 24 hours.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
            ],
        }
    )

    assert "Web of Science" not in content
    assert "five meetings" not in content
    assert "Start therapy within 24 hours." in content


def test_build_magic_guideline_text_keeps_a_skipped_section_stating_who_it_applies_to() -> None:
    """A section says who the guideline covers, so it survives its own heading.

    The North Star's non-negotiable: a fact can be true for adults and false for
    children. `jz7xeL` says "These guidelines only refer to adults." in its Methods
    section and nowhere else, and `jxxdwj` excludes children in as many words. Dropping
    the heading takes the sentence that stops a paediatric claim being confirmed against
    an adult guideline.
    """
    content = _render(
        {
            "name": "Transient ischaemic attack",
            "sections": [
                {
                    "heading": "Methods",
                    "text": ("<p>These guidelines only refer to adults. A search was run in Embase.</p>"),
                    "recommendations": [],
                    "subSections": [],
                },
            ],
        }
    )

    assert "These guidelines only refer to adults." in content


def test_build_magic_guideline_text_keeps_the_pico_frame_under_a_skipped_heading() -> None:
    """A skipped parent keeps the PICO frame filed under it and drops the process prose.

    Skipping a section normally takes its whole subtree, which is right for who sat on
    the panel and which databases were searched. Publishers file the PICO frame under
    those headings too, and it is the only place the corpus says who a recommendation
    applies to. Three guidelines rely on this today - the two Australian Pregnancy and
    Postnatal Care guidelines and the Malawi malaria guideline all hang their scope
    statement under "About the Guidelines". ("Methods" was the fourth and largest case
    until 2026-08-05, when it left the skip list entirely and its subtree stopped needing
    a rescue.)
    """
    content = _render(PICO_FRAME_UNDER_SKIPPED_PARENT)

    assert "does not consider patients who have had intracerebral haemorrhage" in content
    assert "Barthel Index" in content
    # An evidence-gap statement in a GRADE Evidence-to-Decision domain, which eight
    # recommendations in the ASH thrombocytopenia guideline cite as "(see section 3.4)".
    assert "insufficient information to describe patient experiences" in content
    assert "Web of Science" not in content
    assert "five virtual panel meetings" not in content
    # The parent's own heading still goes, and the children take its level so no
    # heading level is skipped where it stood.
    assert "# Methods" not in content
    assert "## Population" in content
    # The number stays in the rendered heading - only the skip-list lookup strips it.
    assert "## 3.2 Outcomes" in content


WHO_ROSTER_ANNEX = {
    "name": "Prophylactic antibiotics for caesarean section",
    "sections": [
        {
            "heading": "Annex 1. External experts and WHO staff involved in the preparation of the recommendation",
            "text": "<p><strong>Edgardo ABALOS</strong></p><p>Vice Director</p><p>Rosario, Argentina</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Annex 2. Priority outcomes used in decisionmaking",
            "text": "<p>Maternal sepsis, wound infection and endometritis were rated critical.</p>",
            "recommendations": [],
            "subSections": [],
        },
    ],
}


def test_build_magic_guideline_text_skips_the_who_contributor_annex() -> None:
    """WHO's roster annex goes; the annex beside it, which carries outcomes, stays.

    Every WHO guideline carries "Annex 1. External experts and WHO staff involved in the
    preparation of ..." - name, job title, department, city, thirty times over. 32 of them
    hold 193,791 characters between them and not one clinical sentence. The heading never
    says "panel" or "membership", which is the only reason the existing list missed it.
    """
    content = _render(WHO_ROSTER_ANNEX)

    assert "Edgardo ABALOS" not in content
    assert "External experts and WHO staff" not in content
    assert "Maternal sepsis, wound infection and endometritis were rated critical" in content


ABSTRACT_WITH_EVIDENCE = {
    "name": "Transient ischaemic attack",
    "sections": [
        {
            "heading": "Abstract",
            "text": (
                "<p>These guidelines only refer to adults. High risk TIA was defined as an "
                "ABCD2 score of 4 or greater. There are no data from randomised controlled "
                "trials on prediction tool use.</p>"
            ),
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Acknowledgements",
            "text": "<p>We thank the reviewers for their comments.</p>",
            "recommendations": [],
            "subSections": [],
        },
    ],
}


def test_build_magic_guideline_text_keeps_the_abstract() -> None:
    """An abstract states the question, the population and what the evidence showed.

    It reads like front matter and is not. Across all 26 in the English catalogue only
    3.3% of their sentences appear anywhere else in their own document, and all 26 repeat
    less than half of themselves - the redundancy argument that had it on the skip list
    fails outright. The European Stroke Organisation TIA guideline's abstract holds the
    only statement in the document that its recommendations apply to adults.
    """
    content = _render(ABSTRACT_WITH_EVIDENCE)

    assert "These guidelines only refer to adults" in content
    assert "no data from randomised controlled trials" in content
    # The genuine front matter beside it still goes.
    assert "We thank the reviewers" not in content


JOURNAL_SUBMISSION_FIELDS = {
    "name": "Transient ischaemic attack",
    "sections": [
        {
            "heading": "Authors",
            "text": "<p>Guillaume Turc 1, Georgios Tsivgoulis 2,3, Heinrich J. Audebert 4</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Guarantor",
            "text": "<p>A specific guarantor does not exist. The working group developed the manuscript.</p>",
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Authors' conclusions",
            "text": "<p>Aspirin reduced recurrent stroke in the pooled analysis.</p>",
            "recommendations": [],
            "subSections": [],
        },
    ],
}


def test_build_magic_guideline_text_skips_journal_submission_fields() -> None:
    """The byline and the guarantor go; a heading that merely starts with "Authors" stays.

    21 "Authors" sections hold 32,901 characters of names with affiliation numbers, and 14
    "Guarantor" sections average 45 characters of "a specific guarantor does not exist".
    Both are matched as exact headings: the substring "author" would take "Authors'
    conclusions", which is where a systematic review states what it found.
    """
    content = _render(JOURNAL_SUBMISSION_FIELDS)

    assert "Guillaume Turc" not in content
    assert "specific guarantor does not exist" not in content
    assert "Aspirin reduced recurrent stroke" in content


GRADE_TABLE_UNDER_A_ROSTER_HEADING = {
    "name": "Antenatal nutrition",
    "sections": [
        {
            "heading": "Annex 7: Guideline Development Group (GDG) judgements",
            "text": (
                "<table><tr><td>Recommendation</td><td>A.1.1</td></tr>"
                "<tr><td>Certainty of the evidence</td><td>Moderate (macrosomia)</td></tr>"
                "<tr><td>Effects</td><td>Favours this option</td></tr>"
                "<tr><td>Certainty of the evidence</td><td>Low (LBW)</td></tr>"
                "<tr><td>Effects</td><td>Favours other options</td></tr></table>"
            ),
            "recommendations": [],
            "subSections": [],
        },
        {
            "heading": "Annex 8: Guideline Development Group members",
            "text": "<p>Dr Ariful Alam, Nutrition. None declared.</p>",
            "recommendations": [],
            "subSections": [],
        },
    ],
}


def test_build_magic_guideline_text_keeps_a_grade_table_under_a_skipped_heading() -> None:
    """One heading, two kinds of content: the judgements table stays, the roster goes.

    "Guideline Development Group" names a roster in 19 of the 20 sections carrying it, and
    in the twentieth it names 27,750 characters of certainty ratings and effect directions,
    one row per recommendation. No wording in the heading separates them, so the body is
    asked instead - both GRADE signals, more than once, so a roster that mentions GRADE in
    passing is not rescued.
    """
    content = _render(GRADE_TABLE_UNDER_A_ROSTER_HEADING)

    assert "Certainty of the evidence" in content
    assert "Favours this option" in content
    assert "Dr Ariful Alam" not in content


def test_build_magic_guideline_text_keeps_a_working_group_that_reports_findings() -> None:
    """A roster heading over a review's own results is kept; the rosters still go.

    Nine of the ten sections named for a working group are lists of names. The tenth, in
    WHO's mpox guideline, is where the review reports what it found - including that it
    found nothing, which the corpus keeps deliberately, because a verifier that cannot see
    "no evidence" has to guess instead.
    """
    content = _render(
        {
            "name": "Mpox",
            "sections": [
                {
                    "heading": "GDG topic-specific working groups",
                    "text": (
                        "<p>The first stage appraised available evidence from comparative interventional "
                        "trials, which yielded no evidence. Only one small cohort study addressed the timing "
                        "of ART initiation; this study did not show a difference in outcomes.</p>"
                    ),
                    "recommendations": [],
                    "subSections": [],
                },
                {
                    "heading": "Working group members",
                    "text": "<p>Dr A. Mwale, Ministry of Health, Lilongwe. Dr B. Banda, WHO, Geneva.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
            ],
        }
    )

    assert "yielded no evidence" in content
    assert "did not show a difference in outcomes" in content
    assert "Dr A. Mwale" not in content


def test_build_magic_guideline_text_keeps_conclusions_and_target_audience() -> None:
    """Two headings left the skip list because they carry findings and scope.

    Of the 19 Conclusion sections the list used to delete, 7 held a sentence a verdict
    could rest on. Of the 9 Target audience sections, 7 stated what the guideline covers or
    where it applies - the category that already took `scope and audience` off the list.
    """
    content = _render(
        {
            "name": "Melanoma",
            "sections": [
                {
                    "heading": "Conclusion",
                    "text": (
                        "<p>Active surveillance will offer equivalent survival rates to immediate completion "
                        "lymph node dissection.</p>"
                    ),
                    "recommendations": [],
                    "subSections": [],
                },
                {
                    "heading": "Target audience",
                    "text": (
                        "<p>This guideline is relevant for all settings and should be considered as global "
                        "guidance. It does not cover children under 18.</p>"
                    ),
                    "recommendations": [],
                    "subSections": [],
                },
            ],
        }
    )

    assert "equivalent survival rates" in content
    assert "It does not cover children under 18." in content


def test_build_magic_guideline_text_drops_the_front_matter_block_whole() -> None:
    """A skipped container now takes its summaries and its scope block with it.

    Both were rescued from under a skipped parent earlier and are not any more - Evan's
    call on 2026-08-06, wanting everything above the first clinical chapter gone. The
    scope statement is the deletion that matters, and it is deliberate: `jW0ZbL` loses
    "The Guidelines do not include ... preterm or low birthweight babies."

    What still holds is `_states_guideline_scope`, which keeps a section stating scope in
    its own body text whatever it is called - so a Methods section saying "These guidelines
    only refer to adults" survives. It is the dedicated front-matter block that goes.
    """
    content = _render(
        {
            "name": "Postnatal care",
            "sections": [
                {
                    "heading": "About the Guidelines",
                    "text": "<p>Developed by the National Health and Medical Research Council.</p>",
                    "recommendations": [],
                    "subSections": [
                        {
                            "heading": "Summaries",
                            "text": "<p>Routine iron supplementation is not recommended.</p>",
                            "recommendations": [],
                            "subSections": [],
                        },
                        {
                            "heading": "Scope and audience",
                            "text": "<p>The Guidelines do not include care for preterm babies.</p>",
                            "recommendations": [],
                            "subSections": [],
                        },
                    ],
                },
                {
                    "heading": "Postnatal assessment",
                    "text": "<p>Check blood pressure before discharge.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
            ],
        }
    )

    assert "Routine iron supplementation is not recommended." not in content
    assert "The Guidelines do not include care for preterm babies." not in content
    assert "Check blood pressure before discharge." in content


def test_build_magic_guideline_text_still_keeps_scope_stated_in_a_section_body() -> None:
    """Dropping the scope block does not drop every scope sentence in the corpus.

    `_states_guideline_scope` reads a section's own text, so `jz7xeL`'s "These guidelines
    only refer to adults." in its Methods section is untouched by the change above.
    """
    content = _render(
        {
            "name": "Transient ischaemic attack",
            "sections": [
                {
                    "heading": "Methods",
                    "text": "<p>These guidelines only refer to adults. A search was run in Embase.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
            ],
        }
    )

    assert "These guidelines only refer to adults." in content


def test_build_magic_guideline_text_keeps_a_heading_that_never_carried_a_body() -> None:
    """An outline level with no text of its own is not an emptied section."""
    guideline = {
        "name": "Stroke",
        "sections": [
            {
                "heading": "Acute management",
                "text": "",
                "recommendations": [],
                "subSections": [
                    {
                        "heading": "Thrombolysis",
                        "text": "<p>Give alteplase within 4.5 hours.</p>",
                        "recommendations": [],
                        "subSections": [],
                    }
                ],
            }
        ],
    }
    content = _render(guideline)

    assert "Acute management" in content
    assert "Give alteplase within 4.5 hours." in content


def test_build_magic_guideline_text_keeps_a_strength_key_written_as_a_table() -> None:
    """A guideline that draws its key as a grid is still defining its own labels.

    The CARI guidelines have no sentence reading "indicates a strong recommendation" - they
    write a row headed Level 1 "We recommend" and a row headed Level 2 "We suggest", with
    columns for patients, clinicians and policy. Adding "guideline development methodology"
    to the skip list deleted that key from three guidelines until the guard learned this
    shape. Without it a reader cannot tell what Level 1 means.
    """
    guideline = {
        "name": "ADPKD",
        "sections": [
            {
                "heading": "Guideline development methodology",
                "text": (
                    "<p>The guideline was developed by a working group over 18 months.</p>"
                    "<table><tr><td>Level 1 &ldquo;We recommend&rdquo;</td>"
                    "<td>Most people in your situation would want the recommended course of "
                    "action and only a small proportion would not.</td></tr>"
                    "<tr><td>Level 2 &ldquo;We suggest&rdquo;</td>"
                    "<td>The majority of people would want the recommended course of action, "
                    "but many would not.</td></tr></table>"
                ),
                "recommendations": [],
                "subSections": [],
            }
        ],
    }
    content = _render(guideline)

    assert "Level 1" in content
    assert "Most people in your situation would want the recommended course of action" in content


def test_build_magic_guideline_text_drops_a_publishing_programme_write_up() -> None:
    """A programme heading explains the publisher, not the care given to a patient.

    Keyed on the programme name rather than on "background and methods", because the two
    sections carrying that wording without a programme name are disease background: 8nyb0E
    opens "Chronic non-cancer pain comprises any painful condition that persists for three
    months or longer... 15-19% of Canadian adults experience chronic non-cancer pain".
    """
    guideline = {
        "name": "Uncomplicated skin abscesses",
        "sections": [
            {
                "heading": "Adults and children with uncomplicated skin abscesses",
                "text": "<p>Incision and drainage is the mainstay of treatment.</p>",
                "recommendations": [],
                "subSections": [],
            },
            {
                "heading": "BMJ Rapid Recommendations: Background and Methods",
                "text": (
                    "<p>Translating research to clinical practice is challenging. BMJ Rapid "
                    "Recommendations aims to create trustworthy clinical practice "
                    "recommendations in record time.</p>"
                ),
                "recommendations": [],
                "subSections": [],
            },
        ],
    }
    content = _render(guideline)

    assert "Incision and drainage is the mainstay of treatment." in content
    assert "BMJ Rapid Recommendations" not in content


def test_build_magic_guideline_text_keeps_a_bare_background_and_methods_section() -> None:
    """Without a programme name the same heading sits on disease background."""
    guideline = {
        "name": "Chronic non-cancer pain",
        "sections": [
            {
                "heading": "Background and methods",
                "text": (
                    "<p>Chronic non-cancer pain comprises any painful condition that persists "
                    "for three months or longer and is not associated with malignancy. "
                    "According to seven national surveys, 15-19% of Canadian adults experience "
                    "chronic non-cancer pain.</p>"
                ),
                "recommendations": [],
                "subSections": [],
            }
        ],
    }
    content = _render(guideline)

    assert "persists for three months or longer" in content
    assert "15-19% of Canadian adults" in content


def test_build_magic_guideline_text_applies_a_publisher_specific_heading_rule() -> None:
    """A heading that is paperwork for one publisher and content for everyone else.

    The Stroke Foundation opens all eight of its guidelines with an "Introduction" of about
    12,100 characters, every one beginning "The Stroke Foundation is a national charity that
    partners with the community to prevent, treat and beat stroke" - 96,945 characters of the
    same blurb. Introduction is emphatically not on the general skip list: 86% of them across
    the corpus carry something a verdict could rest on.
    """
    guideline = {
        "name": "Stroke management",
        "sections": [
            {
                "heading": "Introduction",
                "text": (
                    "<p>The Stroke Foundation is a national charity that partners with the "
                    "community to prevent, treat and beat stroke.</p>"
                ),
                "recommendations": [],
                "subSections": [],
            },
            {
                "heading": "Acute management",
                "text": "<p>Give alteplase within 4.5 hours of onset.</p>",
                "recommendations": [],
                "subSections": [],
            },
        ],
    }
    stroke_catalogue = [{**CATALOGUE[0], "institutionName": "Stroke Foundation"}]
    with _client(catalogue=stroke_catalogue, guideline=guideline) as client:
        stroke = scrape_magic_guideline(client, list_published_guidelines(client).refs[0])
    with _client(guideline=guideline) as client:
        other = scrape_magic_guideline(client, list_published_guidelines(client).refs[0])

    assert "national charity" not in stroke.content
    assert "Give alteplase within 4.5 hours of onset." in stroke.content
    # The same document from any other publisher keeps its introduction.
    assert "national charity" in other.content


def test_institution_skip_headings_are_keyed_the_way_refs_are_spelled() -> None:
    """A publisher key must match `ref.institution`, not the raw catalogue field.

    `institutionName` in the catalogue JSON is "World Health Organization (WHO) " with a
    trailing space, and `_parse_catalogue` strips it. A key copied from the catalogue
    therefore matches nothing, silently: 32 WHO headings were dead until the rendered corpus
    was checked against the per-publisher totals and WHO was absent from them.
    """
    from amfv_datasets.scraping.magic import (
        _GUIDELINE_INLINE_SKIP_HEADINGS,
        _GUIDELINE_KEEP_HEADINGS,
        _GUIDELINE_SKIP_HEADINGS,
        _INSTITUTION_SKIP_HEADINGS,
        _PAPERWORK_APPENDIX_TITLES,
    )

    for institution in _INSTITUTION_SKIP_HEADINGS:
        assert institution == institution.strip(), f"{institution!r} would never match a ref"

    # Both tables are looked up with the key `_skip_lookup_key` produces, which is lowercase
    # and single-spaced. An entry in any other form is unreachable for the same silent
    # reason. Idempotence is deliberately not asserted: the function is not idempotent -
    # "appendix 3. interest-holders" is what a heading normalizes to, and normalizing it a
    # second time would strip the appendix label again.
    tables = (
        list(_INSTITUTION_SKIP_HEADINGS.values())
        + list(_GUIDELINE_SKIP_HEADINGS.values())
        + list(_GUIDELINE_INLINE_SKIP_HEADINGS.values())
        + list(_GUIDELINE_KEEP_HEADINGS.values())
        + [_PAPERWORK_APPENDIX_TITLES]
    )
    for headings in tables:
        for heading in headings:
            assert heading == " ".join(heading.lower().split()), f"{heading!r} is not a lookup key"
            # An empty key is compared against every heading that reduces to nothing -
            # "Appendix", "Appendices" - so the rule fires far beyond whatever was read.
            assert heading, "an empty key would match every unnamed heading"

    # Every entry must also be reachable in practice. An inline rule whose heading only ever
    # appears inside a rendered recommendation is dead: `_drop_inline_sections` runs on
    # section bodies and never sees it. 66 such rules were written and removed again, and
    # several named clinical blocks - "perfusion mismatch thresholds", "eligibility criteria"
    # - which would have fired if the pass were ever extended.
    assert all(_GUIDELINE_INLINE_SKIP_HEADINGS.values()), "an empty rule set does nothing"


def test_a_guideline_exemption_beats_the_corpus_wide_skip_list() -> None:
    """A section the general list would drop, in the guideline where it must not.

    WHO maternal files the misoprostol induction dose under "Applicability issues", a
    heading that is administrative in the other 28 sections it names. The audit of what the
    corpus-wide list removes found 20 such entries dropping clinical content, and the fix is
    per guideline rather than per entry, because the entries earn their place everywhere
    else.
    """
    guideline = {
        "name": "Induction of labour",
        "sections": [
            {
                "heading": "Applicability issues",
                "text": (
                    "<p>The recommended dose of oral misoprostol for induction of labour is 25 &micro;g, 2-hourly.</p>"
                ),
                "recommendations": [],
                "subSections": [],
            },
            {
                "heading": "Care in labour",
                "text": "<p>Monitor the fetal heart rate every 30 minutes.</p>",
                "recommendations": [],
                "subSections": [],
            },
        ],
    }
    with _client(guideline=guideline) as client:
        dropped = scrape_magic_guideline(client, list_published_guidelines(client).refs[0]).content

    assert "25" not in dropped or "2-hourly" not in dropped

    from amfv_datasets.scraping.magic import _GUIDELINE_KEEP_HEADINGS

    real = _GUIDELINE_KEEP_HEADINGS.get("nyxpZL", frozenset())
    _GUIDELINE_KEEP_HEADINGS["nyxpZL"] = frozenset({"applicability issues"})
    try:
        with _client(guideline=guideline) as client:
            kept = scrape_magic_guideline(client, list_published_guidelines(client).refs[0]).content
    finally:
        if real:
            _GUIDELINE_KEEP_HEADINGS["nyxpZL"] = real
        else:
            del _GUIDELINE_KEEP_HEADINGS["nyxpZL"]

    assert "2-hourly" in kept
    assert "Monitor the fetal heart rate every 30 minutes." in kept


# ---------------------------------------------------------------------------
# Recommendation and PICO rendering
# ---------------------------------------------------------------------------


def test_build_magic_guideline_text_renders_sections_and_recommendations() -> None:
    """Nested sections become headings and recommendations are inlined beneath them."""
    with _client() as client:
        ref = list_published_guidelines(client).refs[0]
        content, section_count, title, _ = build_magic_guideline_text(client, ref)
        stripped, _, _, _ = build_magic_guideline_text(client, ref, link_mode=LinkMode.STRIP)

    assert title == "Management of juvenile idiopathic arthritis"
    assert section_count == 1
    assert content == (
        "## Disease modifying therapy\n"
        "\n"
        "Therapy is chosen by disease severity.\n"
        "\n"
        "### Methotrexate\n"
        "\n"
        "See the [dosing table](https://example.org/dose).\n"
        "\n"
        "#### Recommendation 1 (WEAK)\n"
        "\n"
        "Consider methotrexate at 15mg/m2 once a week.\n"
        "\n"
        "*Remarks:*\n"
        "\n"
        "Preferred over leflunomide.\n"
        "\n"
        "#### Recommendation\n"
        "\n"
        "Review response after three months.\n"
        "\n"
        "Box 1.1 Sustainable Development Goals."
    )
    assert "[dosing table](https://example.org/dose)" not in stripped
    assert "See the dosing table." in stripped


def test_build_magic_guideline_text_does_not_label_info_boxes_as_recommendations() -> None:
    """Strength INFO marks an editorial callout box, so it is emitted as plain content.

    MAGICapp reuses the recommendation structure for boxes, and the catalogue's
    publishedRecommendationCount excludes them. Labelling them would present
    editorial material as clinical advice.
    """
    with _client() as client:
        ref = list_published_guidelines(client).refs[0]
        content, _, _, _ = build_magic_guideline_text(client, ref)

    assert "Box 1.1 Sustainable Development Goals." in content
    assert content.count("#### Recommendation") == 2


def test_build_magic_guideline_text_uses_the_publishers_own_label() -> None:
    """A body opening with its own label supplies the label, rather than repeating it.

    The publisher's wording carries meaning a generic "Recommendation" would lose:
    a practice point is explicitly what a panel wrote where the evidence review
    found insufficient data.
    """
    with _client() as client:
        ref = list_published_guidelines(client).refs[0]
        content, _, _, _ = build_magic_guideline_text(client, ref)

    assert "#### Recommendation 1 (WEAK)\n\nConsider methotrexate" in content
    assert "#### Recommendation (WEAK)" not in content


def test_build_magic_guideline_text_keeps_a_wholly_bolded_recommendation() -> None:
    """A wholly bolded recommendation keeps its text and balanced markup.

    WHO publishes these, e.g. "**RECOMMENDATION 3: A companion of choice is
    recommended for all women throughout labour and childbirth.**". The span is
    unwrapped before the label is cut at the colon; cutting inside it left the
    closing half behind as a literal "**" at the end of the body.
    """
    guideline = {
        "name": "Intrapartum care",
        "sections": [
            {
                "heading": "Labour care",
                "text": "",
                "subSections": [],
                "recommendations": [
                    {
                        "text": (
                            "<p><strong>RECOMMENDATION 3: A companion of choice is recommended "
                            "for all women throughout labour and childbirth.</strong></p>"
                        ),
                        "strength": "STRONG",
                    }
                ],
            }
        ],
    }
    with _client(guideline=guideline) as client:
        ref = list_published_guidelines(client).refs[0]
        content, _, _, _ = build_magic_guideline_text(client, ref)

    assert "A companion of choice is recommended for all women throughout labour and childbirth." in content
    assert "### RECOMMENDATION 3 (STRONG)" in content
    assert content.count("**") % 2 == 0


PICO_GUIDELINE = {
    "name": "Chronic pain",
    "sections": [
        {
            "heading": "Opioid therapy",
            "text": "",
            "recommendations": [],
            "subSections": [],
            "picos": [
                {
                    "population": "Patients with chronic non-cancer pain",
                    "intervention": "Trial of opioids",
                    "comparator": "Continue established therapy without opioids",
                    "summary": "<p>Minimally important difference for pain on a 10-cm VAS is 1 cm.</p>",
                    "outcomes": {
                        "dichotomousOutcomes": [{"absoluteDifference": 0.23, "interventionTotalParticipants": 900}],
                        "continuousOutcomes": [],
                    },
                }
            ],
        }
    ],
}


EFFECT_ESTIMATE_PICO = {
    "name": "Caesarean prophylaxis",
    "sections": [
        {
            "heading": "Antibiotic choice",
            "text": "",
            "recommendations": [],
            "subSections": [],
            "picos": [
                {
                    "population": "Women receiving routine antibiotic prophylaxis for caesarean section",
                    "intervention": "First-generation cephalosporins",
                    "comparator": "Broad-spectrum penicillins",
                    "summary": "<p>It is unclear whether cephalosporins reduce maternal sepsis.</p>",
                    "outcomes": {
                        "dichotomousOutcomes": [
                            {
                                "outcome": "Severe infectious morbidity: sepsis",
                                "relativeEffectType": "RR",
                                "relativeEffect": 2.37,
                                "relativeEffectConfidenceLow": 0.1,
                                "relativeEffectConfidenceHigh": 56.41,
                                "interventionTotalParticipants": 75.0,
                                "interventionStudies": "1",
                                "qualityOfEvidenceLevel": "VERY_LOW",
                            },
                            {
                                "outcome": "Puerperal infection: endometritis",
                                "relativeEffectType": "RR",
                                "relativeEffect": 1.1,
                                "relativeEffectConfidenceLow": 0.76,
                                "relativeEffectConfidenceHigh": 1.6,
                                "interventionTotalParticipants": 1161.0,
                                "interventionStudies": "7",
                                "qualityOfEvidenceLevel": "LOW",
                            },
                            {
                                "outcome": "An outcome the publisher hid",
                                "relativeEffect": 9.9,
                                "relativeEffectType": "RR",
                                "isHidden": True,
                                "qualityOfEvidenceLevel": "HIGH",
                            },
                            {"outcome": "A name and nothing else", "qualityOfEvidenceLevel": "NOTSET"},
                        ]
                    },
                }
            ],
        }
    ],
}


def test_build_magic_guideline_text_prints_the_effect_estimates_behind_a_pico() -> None:
    """The numbers the panel weighed, which its prose often does not state.

    WHO's caesarean-prophylaxis guideline says "it is unclear whether ... reduce maternal
    sepsis" eleven times while the results table it carries records RR 2.37 from 75
    participants in one study at very low certainty. The prose is right and the interval
    does span no effect, but a verifier could only ever quote the word "unclear" and
    never how thin the evidence behind it was. 22,187 outcomes across the corpus.
    """
    content = _render(EFFECT_ESTIMATE_PICO)

    assert "*Effect estimates:*" in content
    sepsis = "- Severe infectious morbidity: sepsis: RR 2.37 (95% CI 0.1 to 56.41), "
    assert sepsis + "75 participants, 1 study, certainty very low" in content
    assert (
        "- Puerperal infection: endometritis: RR 1.1 (95% CI 0.76 to 1.6), 1161 participants, 7 studies, certainty low"
        in content
    )
    # the written summary is still there, above the numbers
    assert content.index("It is unclear whether") < content.index("*Effect estimates:*")


NUMBERS_WITHOUT_PROSE = {
    "name": "Pancreas transplant",
    "sections": [
        {
            "heading": "Immunosuppression",
            "text": "",
            "recommendations": [],
            "subSections": [],
            "picos": [
                {
                    "population": "Adult pancreas transplant recipients with suspected COVID-19",
                    "intervention": "Adjustment to maintenance immunosuppression therapy",
                    "comparator": "Routine care",
                    "summary": "",
                    "outcomes": {
                        "dichotomousOutcomes": [
                            {
                                "outcome": "Graft loss",
                                "relativeEffectType": "RR",
                                "relativeEffect": 1.4,
                                "relativeEffectConfidenceLow": 0.9,
                                "relativeEffectConfidenceHigh": 2.2,
                                "interventionTotalParticipants": 210.0,
                                "interventionStudies": "3",
                                "qualityOfEvidenceLevel": "MODERATE",
                            }
                        ]
                    },
                },
                {
                    "population": "Patients with carotid stenosis",
                    "intervention": "Trans-carotid artery revascularisation",
                    "comparator": "Carotid endarterectomy",
                    "summary": "",
                },
            ],
        }
    ],
}


def test_build_magic_guideline_text_prints_a_pico_whose_only_answer_is_numbers() -> None:
    """The outcome table is an answer, so a question carrying one is not empty.

    The gate used to require written prose, so an evidence question with a full results
    table and no paragraph printed nothing at all - numbers included. That hid 7,119
    outcomes across 118 of the 212 corpus documents. A question with neither prose nor
    numbers still goes: 172 of those exist, all three parts filled in and nothing
    reported, which is a perfect topical match that answers nothing.
    """
    content = _render(NUMBERS_WITHOUT_PROSE)

    assert "- Population: Adult pancreas transplant recipients with suspected COVID-19" in content
    assert "- Graft loss: RR 1.4 (95% CI 0.9 to 2.2), 210 participants, 3 studies, certainty moderate" in content
    # no prose, so no summary label - the numbers stand on their own
    assert "*Summary of findings:*" not in content
    # and the question with neither prose nor numbers is still suppressed
    assert "carotid stenosis" not in content


def test_build_magic_guideline_text_keeps_the_readable_head_of_a_pico() -> None:
    """The clinical question and its findings are kept; the effect estimates are not."""
    content = _render(PICO_GUIDELINE)

    assert "- Population: Patients with chronic non-cancer pain" in content
    assert "- Intervention: Trial of opioids" in content
    assert "*Summary of findings:*\n\nMinimally important difference for pain on a 10-cm VAS is 1 cm." in content
    assert "absoluteDifference" not in content
    assert "interventionTotalParticipants" not in content


TABLE_SUMMARY_PICO = {
    "name": "Dental diagnostics",
    "sections": [
        {
            "heading": "Detection of caries",
            "text": "",
            "recommendations": [],
            "subSections": [],
            "picos": [
                {
                    "population": "Adults with primary caries",
                    "intervention": "Visual examination",
                    "comparator": "Radiographs",
                    "summary": "<table><tr><td>Pooled sensitivity</td><td>0.96 (95% CI)</td></tr></table>",
                }
            ],
        }
    ],
}


def test_build_magic_guideline_text_starts_a_pico_summary_table_on_its_own_line() -> None:
    """A summary that opens with a table still renders as one.

    A markdown table must begin at the start of a line, so a first row glued
    onto the label line turned the whole table into a paragraph of literal pipes.
    """
    content = _render(TABLE_SUMMARY_PICO)

    assert "*Summary of findings:*\n\n|" in content
    assert "Pooled sensitivity" in content


def _diphtheria_pico(pico_id: int, intervention: str, summary: str) -> dict:
    return {
        "picoId": pico_id,
        "population": "People with diphtheria",
        "intervention": intervention,
        "comparator": "No treatment",
        "summary": f"<p>{summary}</p>",
    }


MISFILED_PICO_GUIDELINE = {
    "name": "Diphtheria",
    "sections": [
        {
            # The publisher lists every PICO here, including two answered two sections
            # later - the Ea7gOL shape.
            "heading": "5. Recommendation for antibiotics treatment",
            "text": "",
            "subSections": [],
            "picos": [
                _diphtheria_pico(129843, "Antibiotics", "Antibiotics shorten carriage."),
                _diphtheria_pico(129844, "Sensitivity testing before antitoxin", "Testing rarely changes management."),
                _diphtheria_pico(129850, "Antitoxin timing", "Earlier administration lowers mortality."),
            ],
            "recommendations": [{"text": "<p>Offer antibiotics to all cases.</p>", "strength": "STRONG"}],
        },
        {
            "heading": "6.3 Recommendation on DAT sensitivity testing",
            "text": "",
            "subSections": [],
            "picos": [],
            "recommendations": [
                {
                    "text": "<p>Do not delay antitoxin for sensitivity testing.</p>",
                    "strength": "STRONG",
                    "picos": [
                        _diphtheria_pico(
                            129844, "Sensitivity testing before antitoxin", "Testing rarely changes management."
                        )
                    ],
                }
            ],
        },
        {
            "heading": "6.4 Recommendation on DAT dose",
            "text": "",
            "subSections": [],
            "picos": [],
            "recommendations": [
                {
                    "text": "<p>Give antitoxin within 48 hours.</p>",
                    "strength": "STRONG",
                    "picos": [_diphtheria_pico(129850, "Antitoxin timing", "Earlier administration lowers mortality.")],
                },
                {
                    "text": "<p>Use the higher dose in severe disease.</p>",
                    "strength": "WEAK",
                    "picos": [_diphtheria_pico(129850, "Antitoxin timing", "Earlier administration lowers mortality.")],
                },
            ],
        },
    ],
}


def test_build_magic_guideline_text_files_a_pico_under_the_recommendation_owning_it() -> None:
    """Evidence prints where its recommendation is, not where the publisher listed it.

    A PICO appears twice in the source - on the section introducing the question and
    on the recommendation answering it, same `picoId`, identical text. Rendering only
    the section-level list files evidence about antitoxin under the antibiotics
    heading, telling a reader the wrong drug was studied. The move is volume-neutral:
    the block prints once either way.
    """
    content = _render(MISFILED_PICO_GUIDELINE)

    assert content.count("Testing rarely changes management.") == 1
    assert content.index("Do not delay antitoxin for sensitivity testing.") < content.index(
        "Testing rarely changes management."
    )
    # Answered inside the section listing it, so it stays put.
    assert content.count("Antibiotics shorten carriage.") == 1
    assert content.index("Antibiotics shorten carriage.") < content.index(
        "Do not delay antitoxin for sensitivity testing."
    )


def test_build_magic_guideline_text_leaves_a_pico_shared_by_several_recommendations() -> None:
    """A PICO with more than one owner stays at its section: each extra copy is a duplicate.

    662 PICOs across the catalogue are owned by several recommendations, and printing
    them under each owner adds 1.16 million characters of repeated evidence to the
    English corpus - the measured reason the first attempt at this was reverted.
    """
    content = _render(MISFILED_PICO_GUIDELINE)

    assert content.count("Earlier administration lowers mortality.") == 1
    assert content.index("Earlier administration lowers mortality.") < content.index("Give antitoxin within 48 hours.")


ARMS_GUIDELINE = {
    "name": "Diabetes technology",
    "sections": [
        {
            "heading": "Glucose monitoring",
            "text": "",
            "subSections": [],
            "recommendations": [
                {
                    "text": "<p>Offer continuous glucose monitoring.</p>",
                    "strength": "WEAK",
                    "keyInfo": {
                        "interventions": [
                            {"picoElement": "I", "intervention": "CGM with alerts + MDI"},
                            {"picoElement": "C", "intervention": "SMBG + MDI"},
                        ]
                    },
                }
            ],
        }
    ],
}


def test_build_magic_guideline_text_names_the_compared_arms() -> None:
    """keyInfo.interventions names what the recommendation weighed against what."""
    content = _render(ARMS_GUIDELINE)

    assert "*Compared:*\n\nCGM with alerts + MDI versus SMBG + MDI" in content


CERTAINTY_GUIDELINE = {
    "name": "Dementia care",
    "sections": [
        {
            "heading": "Biomarkers",
            "text": "",
            "subSections": [],
            "recommendations": [
                {
                    "text": "<p>Consider a blood-based biomarker test as a triaging test.</p>",
                    "strength": "WEAK",
                    "keyInfo": {"evidenceStrength": "WEAK"},
                }
            ],
        }
    ],
}


def test_build_magic_guideline_text_translates_certainty_to_grade_wording() -> None:
    """`evidenceStrength: WEAK` is GRADE's Low certainty and is emitted as such.

    MAGICapp never uses LOW in this field; WEAK occupies that slot. Emitting the raw
    token puts WEAK in one label twice meaning two different things - the strength of
    the recommendation (weak/conditional) and the certainty of its evidence (low).
    """
    with _client(guideline=CERTAINTY_GUIDELINE) as client:
        ref = list_published_guidelines(client).refs[0]
        content, _, _, _ = build_magic_guideline_text(client, ref)

    assert "### Recommendation (WEAK) — certainty of evidence: Low" in content
    assert "certainty of evidence: WEAK" not in content


ROOT_RECOMMENDATIONS_GUIDELINE = {
    "name": "Fluid and drug therapy in ARDS",
    "sections": [{"heading": "Scope", "text": "<p>Adults with ARDS.</p>", "recommendations": [], "subSections": []}],
    "recommendations": [
        {
            "text": "<p>We recommend not using corticosteroids in routine therapy of adults with ARDS.</p>",
            "strength": "STRONG_AGAINST",
        },
        {
            "text": "<p>We suggest use of a restrictive fluid therapy in adults with ARDS.</p>",
            "strength": "WEAK",
        },
    ],
}


def test_build_magic_guideline_text_keeps_recommendations_on_the_document_root() -> None:
    """Recommendations hanging off the document root are collected, not just section ones.

    The Scandinavian Society of Anaesthesiology publishes a guideline whose nine
    recommendations sit on the root with none in any section; the catalogue's
    publishedRecommendationCount of 9 confirms they are the real content. Walking
    only sections dropped all nine.
    """
    with _client(guideline=ROOT_RECOMMENDATIONS_GUIDELINE) as client:
        ref = list_published_guidelines(client).refs[0]
        content, _, _, _ = build_magic_guideline_text(client, ref)

    assert "We recommend not using corticosteroids in routine therapy of adults with ARDS." in content
    assert "We suggest use of a restrictive fluid therapy in adults with ARDS." in content
    assert "## Recommendation (STRONG_AGAINST)" in content


def test_build_magic_guideline_text_leaves_body_markers_alone_after_a_clean_label() -> None:
    """A label line carrying no markers of its own never costs the body any.

    The National Blood Authority follows a bolded "Expert opinion point" line with
    a bolded "EOP1" code opening the body; repairing balance by stripping leading
    asterisks unconditionally broke that pair.
    """
    guideline = {
        "name": "Prophylaxis",
        "sections": [
            {
                "heading": "Testing",
                "text": "",
                "subSections": [],
                "recommendations": [
                    {
                        "text": (
                            "<p><strong>Expert opinion point</strong></p>"
                            "<p><strong>EOP1</strong>: All women should have an antibody screen.</p>"
                        ),
                        "strength": "NOTSET",
                    }
                ],
            }
        ],
    }
    content = _render(guideline)

    assert "**EOP1**: All women should have an antibody screen." in content
    assert "### Expert opinion point" in content
    assert content.count("**") % 2 == 0


NUMBERED_LABEL_GUIDELINE = {
    "name": "HCC surveillance",
    "sections": [
        {
            "heading": "Surveillance",
            "text": "",
            "subSections": [],
            "recommendations": [
                {
                    "text": (
                        "<p><strong>2.1 Adapted evidence-based recommendation&nbsp;</strong></p>"
                        "<p>Do not routinely offer surveillance for people with limited life expectancy.</p>"
                    ),
                    "strength": "STRONG_AGAINST",
                }
            ],
        }
    ],
}


def test_build_magic_guideline_text_takes_a_number_prefixed_label() -> None:
    """A publisher label opening with a section number is still the label.

    Publishers write "2.1 Adapted evidence-based recommendation" (guideline E83abn);
    an opener pattern anchored on words alone stamped a generic "Recommendation" on
    top, stacking two headers.
    """
    content = _render(NUMBERED_LABEL_GUIDELINE)

    assert "### 2.1 Adapted evidence-based recommendation (STRONG_AGAINST)" in content
    assert "Do not routinely offer surveillance for people with limited life expectancy." in content
    assert "### Recommendation (STRONG_AGAINST)" not in content


def test_build_magic_guideline_text_keeps_a_research_recommendation_label() -> None:
    """A body opening "RESEARCH RECOMMENDATION" keeps that label, not "Recommendation".

    Phoenix Australia opens 41 recommendation bodies with it; stamping the generic
    label above it presented a research agenda as clinical guidance.
    """
    guideline = {
        "name": "PTSD",
        "sections": [
            {
                "heading": "Children",
                "text": "",
                "subSections": [],
                "recommendations": [
                    {
                        "text": (
                            "<p><strong>RESEARCH RECOMMENDATION</strong></p>"
                            "<p>For children and adolescents, further trials are needed.</p>"
                        ),
                        "strength": "NOTSET",
                    }
                ],
            }
        ],
    }
    content = _render(guideline)

    assert "### RESEARCH RECOMMENDATION\n\nFor children and adolescents, further trials are needed." in content


def test_build_magic_guideline_text_drops_possibly_outdated_recommendations() -> None:
    """A recommendation the publisher marks POSSIBLY_OUTDATED stays out of the corpus.

    UNDER_REVIEW and NEW_EVIDENCE still mark current guidance and are kept.
    """
    guideline = {
        "name": "Anticoagulation",
        "sections": [
            {
                "heading": "Therapy",
                "text": "",
                "subSections": [],
                "recommendations": [
                    {
                        "text": "<p>Offer warfarin as first-line.</p>",
                        "strength": "STRONG",
                        "status": "POSSIBLY_OUTDATED",
                    },
                    {
                        "text": "<p>Offer a DOAC as first-line.</p>",
                        "strength": "STRONG",
                        "status": "UNDER_REVIEW",
                    },
                ],
            }
        ],
    }
    content = _render(guideline)

    assert "warfarin" not in content
    assert "Offer a DOAC as first-line." in content


def test_build_magic_guideline_text_keeps_a_good_practice_statement_recommendation() -> None:
    """The label on a real recommendation must survive the rule that drops the definition."""
    content = _render_body(
        "<h4><strong>Good practice statement 3</strong></h4>"
        "<p>Give a single low dose of primaquine to reduce transmissibility.</p>"
    )

    assert "single low dose of primaquine" in content
    assert "Good practice statement 3" in content


def test_build_magic_guideline_text_keeps_a_section_defining_its_recommendation_symbols() -> None:
    """A legend drawn rather than written counts too.

    `j1WBYn` keys its recommendations by colour, and "The GREEN symbol denotes a
    non-GRADE-based strong recommendation" is the only place it says what its own symbols
    mean - the same interpretation contract as a worded legend.
    """
    content = _render(
        {
            "name": "COVID-19 critical care",
            "sections": [
                {
                    "heading": "Methods",
                    "text": (
                        "<p>The GREEN symbol denotes a non-GRADE-based strong recommendation "
                        "of a best practice statement.</p>"
                    ),
                    "recommendations": [],
                    "subSections": [],
                },
            ],
        }
    )

    assert "GREEN symbol denotes" in content


PICO_ARMS_WITHOUT_KEY_INFO = {
    "name": "Critical bleeding in immune thrombocytopenia",
    "sections": [
        {
            "heading": "Recommendations for children",
            "text": "",
            "subSections": [],
            "recommendations": [
                {
                    "title": "<p>Corticosteroids for children with a critical bleed</p>",
                    "strength": "STRONG",
                    "text": "<p>The panel recommends corticosteroids.</p>",
                    "keyInfo": {},
                    "picos": [
                        {
                            "intervention": "<p>Corticosteroids</p>",
                            "comparator": "<p>No corticosteroids</p>",
                            "summary": "",
                        },
                        {
                            "intervention": "<p>High-dose dexamethasone</p>",
                            "comparator": "<p>Prednisone</p>",
                            "summary": "<p>Dexamethasone raised platelet counts faster.</p>",
                        },
                    ],
                }
            ],
        },
    ],
}


def test_build_magic_guideline_text_names_the_arms_when_key_info_is_empty() -> None:
    """A recommendation still says what it compared when only its PICOs know.

    Publishers do not always fill in `keyInfo.interventions`. The comparison can still be
    on the recommendation's PICOs, but a PICO carrying no findings is suppressed, so the
    arms reached the corpus nowhere - every pediatric recommendation in the ASH
    thrombocytopenia guideline named its arms in the source and not in the output, while
    its adult half named them. Arms from a PICO that does render are not repeated.
    """
    content = _render(PICO_ARMS_WITHOUT_KEY_INFO)

    assert "*Compared:*" in content
    assert "Corticosteroids versus No corticosteroids" in content
    # A PICO carrying findings is left alone: where those render, the same intervention
    # and comparator are printed with them, and repeating the pair here would put the
    # same words in the document twice.
    assert "High-dose dexamethasone versus Prednisone" not in content


MULTI_PICO_ARMS = {
    "name": "Stroke rehabilitation",
    "sections": [
        {
            "heading": "Rehabilitation",
            "text": "",
            "subSections": [],
            "recommendations": [
                {
                    "title": "<p>Rehabilitation after stroke</p>",
                    "strength": "WEAK",
                    "text": "<p>Several programmes were assessed.</p>",
                    "keyInfo": {
                        "interventions": [
                            {"intervention": "Sertraline", "picoElement": "I", "picoId": 1},
                            {"intervention": "placebo", "picoElement": "C", "picoId": 1},
                            {"intervention": "Pelvic floor muscle training", "picoElement": "I", "picoId": 2},
                            {"intervention": "Usual rehabilitation care", "picoElement": "C", "picoId": 2},
                        ]
                    },
                    "picos": [],
                }
            ],
        },
    ],
}


def test_build_magic_guideline_text_keeps_each_comparison_separate() -> None:
    """Arms are paired by their own PICO, not merged into one combined comparison.

    A recommendation answering several questions carries every arm in one flat list.
    Merging them stated a comparison no trial performed - a stroke guideline came out as
    "Sertraline, Pelvic floor muscle training versus placebo, Usual rehabilitation care",
    from which a reader could take "Sertraline versus Usual rehabilitation care".
    """
    content = _render(MULTI_PICO_ARMS)

    assert "- Sertraline versus placebo" in content
    assert "- Pelvic floor muscle training versus Usual rehabilitation care" in content
    assert "Sertraline, Pelvic floor muscle training" not in content


def test_build_magic_guideline_text_keeps_a_section_mapping_its_own_words_to_a_strength() -> None:
    """A guideline's key to its OWN wording is kept, whatever heading it is filed under.

    nyO1Yj files "'The panel recommends' indicates a strong recommendation" under "2
    Methods", and without it the corpus carries "the panel suggests" with nothing saying
    whether that means strong or conditional guidance.

    The generic GRADE definition does not count, and the second half of this test is the
    point: "A strong recommendation is given when there is high-certainty evidence" is
    textbook material any guideline could carry, and treating it as a legend held 14
    front-matter sections in the corpus.
    """
    specific = _render_body(
        "<p>'The panel recommends' indicates a strong recommendation, and 'the panel "
        "suggests' indicates a conditional recommendation.</p>"
    )
    assert "indicates a conditional recommendation" in specific

    generic = _render(
        {
            "name": "Postnatal care",
            "sections": [
                {
                    "heading": "Reading guide",
                    "text": (
                        "<p>A strong recommendation is given when there is high-certainty evidence "
                        "that the benefits clearly outweigh the harms.</p>"
                    ),
                    "recommendations": [],
                    "subSections": [],
                },
                {
                    "heading": "Infant feeding",
                    "text": "<p>Advise exclusive breastfeeding to six months.</p>",
                    "recommendations": [],
                    "subSections": [],
                },
            ],
        }
    )
    assert "A strong recommendation is given when" not in generic
    assert "Advise exclusive breastfeeding to six months." in generic


def test_build_magic_guideline_text_drops_a_recommendation_marked_a_draft() -> None:
    """A recommendation the publisher heads "DRAFT" is not settled guidance.

    Three publishers mark a draft in the text while `status` still says UPDATED or NEW, so
    the field cannot catch it. It is the same case as POSSIBLY_OUTDATED - advice the panel
    does not yet stand behind - and goes the same way, the statement with it.
    """
    content = _render(
        {
            "name": "Stroke",
            "sections": [
                {
                    "heading": "Dysphagia",
                    "text": "<p>Screen all patients for swallowing difficulty.</p>",
                    "recommendations": [
                        {
                            "text": (
                                "<p><strong><u>DRAFT RECOMMENDATION - AUGUST 2024</u></strong></p>"
                                "<p>Acupuncture should not be used for dysphagia.</p>"
                            ),
                            "strength": "WEAK",
                            "status": "UPDATED",
                        },
                        {
                            "text": "<p>Offer texture-modified diets where indicated.</p>",
                            "strength": "STRONG",
                            "status": "NEW",
                        },
                    ],
                    "subSections": [],
                }
            ],
        }
    )

    assert "Acupuncture should not be used" not in content
    assert "DRAFT RECOMMENDATION" not in content
    assert "Offer texture-modified diets where indicated." in content


def test_build_magic_guideline_text_keeps_a_recommendation_that_merely_mentions_a_draft() -> None:
    """Only a recommendation that OPENS with the marker goes.

    Four recommendations in the catalogue mention a draft in passing - a committee that
    "will provide advice on draft recommendations" - and dropping those would delete real
    guidance on a word.
    """
    content = _render(
        {
            "name": "Pregnancy care",
            "sections": [
                {
                    "heading": "Governance",
                    "text": "<p>Oversight arrangements.</p>",
                    "recommendations": [
                        {
                            "text": (
                                "<p>The Expert Advisory Committee will convene to provide advice on "
                                "draft recommendations before publication.</p>"
                            ),
                            "strength": "PRACTICE",
                            "status": "NOTSET",
                        }
                    ],
                    "subSections": [],
                }
            ],
        }
    )

    assert "will convene to provide advice on draft recommendations" in content


def test_build_magic_guideline_text_drops_a_recommendation_marked_draft_in_its_remarks() -> None:
    """Some publishers put the draft marker in the remarks, not at the head of the text.

    "This is a draft recommendation that has not yet been approved by NHMRC" appears under 49
    recommendations whose text reads like any other. The panel has not signed them off.
    """
    guideline = {
        "name": "Postnatal care",
        "sections": [
            {
                "heading": "Perineal care",
                "text": "",
                "recommendations": [
                    {
                        "strength": "STRONG",
                        "text": "<p>Offer ice packs for perineal pain in the first 24 hours.</p>",
                        "remarks": (
                            "<p>Approved by LEAPP Steering Committee 14 May 2026. This is a draft "
                            "recommendation that has not yet been approved by NHMRC.</p>"
                        ),
                    },
                    {
                        "strength": "STRONG",
                        "text": "<p>Assess the perineum at every postnatal contact.</p>",
                    },
                ],
                "subSections": [],
            }
        ],
    }
    content = _render(guideline)

    assert "Offer ice packs for perineal pain" not in content
    assert "Assess the perineum at every postnatal contact." in content


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------


def test_build_magic_guideline_text_rejects_a_guideline_without_sections() -> None:
    """A document with no sections is an error rather than an empty record."""
    with _client(guideline={"name": "Empty", "sections": []}) as client:
        ref = list_published_guidelines(client).refs[0]
        with pytest.raises(MagicFetchError, match="No sections found"):
            build_magic_guideline_text(client, ref)


def test_build_magic_guideline_text_collapses_a_heading_that_only_repeats_its_child() -> None:
    """A wrapper section with no text whose only child repeats its name emits one heading.

    13 of these across 6 guidelines. Emitting both puts a heading in the document that
    names something with nothing under it.
    """
    guideline = {
        "name": "Colorectal cancer",
        "sections": [
            {
                "heading": "Adjuvant therapy for stage III colon cancer",
                "text": "",
                "recommendations": [],
                "subSections": [
                    {
                        "heading": "Adjuvant therapy for stage III colon cancer",
                        "text": "<p>Offer oxaliplatin-based chemotherapy after resection.</p>",
                        "recommendations": [],
                        "subSections": [],
                    }
                ],
            }
        ],
    }
    content = _render(guideline)

    assert content.count("Adjuvant therapy for stage III colon cancer") == 1
    assert "Offer oxaliplatin-based chemotherapy after resection." in content


def test_build_magic_guideline_text_keeps_both_headings_when_the_wrapper_has_content() -> None:
    """Only an empty wrapper collapses; a parent carrying its own text keeps its heading."""
    guideline = {
        "name": "Colorectal cancer",
        "sections": [
            {
                "heading": "Adjuvant therapy",
                "text": "<p>Adjuvant therapy is considered after resection.</p>",
                "recommendations": [],
                "subSections": [
                    {
                        "heading": "Adjuvant therapy",
                        "text": "<p>Offer oxaliplatin-based chemotherapy.</p>",
                        "recommendations": [],
                        "subSections": [],
                    }
                ],
            }
        ],
    }
    content = _render(guideline)

    assert content.count("Adjuvant therapy\n") == 2


def test_build_magic_guideline_text_drops_a_block_repeated_elsewhere() -> None:
    """Deduplication, not judgement: the words survive in the copy that is kept.

    A block goes only when every one of its eight-word runs is found in a block that stays,
    so nothing can be lost. 1,360 blocks across the corpus, 1,277,785 characters, and every
    removed block was checked against the render that keeps it.
    """
    guideline = {
        "name": "Sepsis",
        "sections": [
            {
                "heading": "Chapter one",
                "text": (
                    "<p>The panel considered the biology and mode of transmission of the "
                    "organism when developing this recommendation.</p>"
                ),
                "recommendations": [],
                "subSections": [],
            },
            {
                "heading": "Chapter two",
                "text": (
                    "<p>The panel considered the biology and mode of transmission of the "
                    "organism when developing this recommendation.</p>"
                ),
                "recommendations": [],
                "subSections": [],
            },
        ],
    }
    content = _render(guideline)

    assert content.count("mode of transmission of the organism") == 1
    assert "Chapter one" in content
    assert "Chapter two" in content


def test_build_magic_guideline_text_keeps_a_repeated_effect_estimate() -> None:
    """A guideline states the same estimate under every recommendation resting on it.

    The second copy is not redundant - it is that recommendation's evidence, and deleting it
    leaves a reader who finds the recommendation with no numbers under it. 1,100 of the 5,948
    repeated blocks carry one of these, 651,211 characters, and they stay.
    """
    guideline = {
        "name": "Diabetes",
        "sections": [
            {
                "heading": "Chapter one",
                "text": (
                    "<p>Diabetic ketoacidosis, no. of patients: RR 2.81 (95% CI 0.46 to "
                    "17.05), 1097 participants, 13 studies</p>"
                ),
                "recommendations": [],
                "subSections": [],
            },
            {
                "heading": "Chapter two",
                "text": (
                    "<p>Diabetic ketoacidosis, no. of patients: RR 2.81 (95% CI 0.46 to "
                    "17.05), 1097 participants, 13 studies</p>"
                ),
                "recommendations": [],
                "subSections": [],
            },
        ],
    }
    content = _render(guideline)

    assert content.count("RR 2.81 (95% CI 0.46 to 17.05)") == 2


def test_markdown_drops_an_inline_heading_named_for_one_guideline() -> None:
    """A heading inside a body, which no rule about sections can reach.

    Publishers file "Target audience" or "Evidence synthesis" as an <h3> in the middle of a
    section they also use for guidance. Removal runs to the next heading of the same or a
    shallower level, so a mistake costs one block.
    """
    body = (
        "<h3>Target audience</h3>"
        "<p>This guideline is intended for paediatric rheumatologists and general practitioners.</p>"
        "<h4>Who else may use it</h4>"
        "<p>Pharmacists and consumers may also find it relevant.</p>"
        "<h3>Treatment</h3>"
        "<p>Start methotrexate at 10 mg per square metre once weekly.</p>"
    )
    guideline = {
        "name": "Juvenile idiopathic arthritis",
        "sections": [{"heading": "Overview", "text": body, "recommendations": [], "subSections": []}],
    }
    with _client(guideline=guideline) as client:
        rendered = scrape_magic_guideline(client, list_published_guidelines(client).refs[0]).content

    # nyxpZL is the short code the default catalogue entry carries, and the real guideline
    # has an inline rule for "target population and audience" - not this heading.
    assert "Target audience" in rendered

    from amfv_datasets.scraping.magic import _GUIDELINE_INLINE_SKIP_HEADINGS

    real = _GUIDELINE_INLINE_SKIP_HEADINGS.get("nyxpZL", frozenset())
    _GUIDELINE_INLINE_SKIP_HEADINGS["nyxpZL"] = frozenset({"target audience"})
    try:
        with _client(guideline=guideline) as client:
            rendered = scrape_magic_guideline(client, list_published_guidelines(client).refs[0]).content
    finally:
        _GUIDELINE_INLINE_SKIP_HEADINGS["nyxpZL"] = real

    assert "Target audience" not in rendered
    # The deeper heading is inside the block and goes with it.
    assert "Pharmacists and consumers" not in rendered
    # The next heading of the same level ends the removal.
    assert "Start methotrexate at 10 mg per square metre once weekly." in rendered
    assert "## Treatment" in rendered or "### Treatment" in rendered


def test_build_magic_guideline_text_drops_a_block_named_for_one_guideline() -> None:
    """Prose no heading rule can reach, removed in the one guideline that writes it.

    Applied to the assembled document, so it reaches text rendered from a recommendation as
    well as from a section body - nV6X3n's technical-report pointer sits under four separate
    recommendations.
    """
    guideline = {
        "name": "Cochlear implantation",
        "sections": [
            {
                "heading": "Screening",
                "text": (
                    "<p><strong>For more detailed information on the development of this "
                    "recommendation, please see the Technical Report.</strong></p>"
                    "<p><em>Research needed:</em></p>"
                    "<p>Future updates will review the utility of mobile technology.</p>"
                    "<p><em>Certainty of the evidence:</em></p>"
                    "<p>Pooled sensitivity was 0.88.</p>"
                ),
                "recommendations": [],
                "subSections": [],
            }
        ],
    }
    with _client(guideline=guideline) as client:
        kept = scrape_magic_guideline(client, list_published_guidelines(client).refs[0]).content

    assert "Technical Report" in kept

    from amfv_datasets.scraping.magic import _GUIDELINE_DROP_BLOCKS

    _GUIDELINE_DROP_BLOCKS["nyxpZL"] = (
        ("for more detailed information on the development of this recommendation", ""),
        ("research needed:", "certainty of the evidence:"),
    )
    try:
        with _client(guideline=guideline) as client:
            dropped = scrape_magic_guideline(client, list_published_guidelines(client).refs[0]).content
    finally:
        del _GUIDELINE_DROP_BLOCKS["nyxpZL"]

    assert "Technical Report" not in dropped
    assert "mobile technology" not in dropped
    # The stop phrase and everything after it survive.
    assert "Certainty of the evidence" in dropped
    assert "Pooled sensitivity was 0.88." in dropped


def test_drop_guideline_blocks_takes_one_block_when_the_stop_is_missing() -> None:
    """A run whose end is not found must not swallow the rest of the document.

    Failing open is how an earlier version of the divider work would have deleted
    thrombectomy eligibility criteria, so the behaviour is pinned here.
    """
    from amfv_datasets.scraping.magic import _GUIDELINE_DROP_BLOCKS, _drop_guideline_blocks

    _GUIDELINE_DROP_BLOCKS["zzTEST"] = (("research needed:", "a phrase that is not there"),)
    try:
        result = _drop_guideline_blocks(
            "*Research needed:*\n\nFuture work.\n\nGive alteplase within 4.5 hours.", "zzTEST"
        )
    finally:
        del _GUIDELINE_DROP_BLOCKS["zzTEST"]

    assert "Research needed" not in result
    assert "Future work." in result
    assert "Give alteplase within 4.5 hours." in result


def test_build_magic_guideline_text_drops_a_heading_with_nothing_under_it() -> None:
    """A heading left naming content another rule already took.

    802 of these across 37 guidelines, 335 of them a bare "Practice point" in Cancer Council
    melanoma guidelines. Structural, so it cannot cost anything: the heading goes only when
    there is nothing at all between it and the next heading of the same or a shallower level.
    """
    guideline = {
        "name": "Melanoma",
        "sections": [
            {
                "heading": "Practice point",
                "text": "<p><u>Back to top</u></p>",
                "recommendations": [],
                "subSections": [],
            },
            {
                "heading": "Treatment",
                "text": "<p>Excise with a 1 cm margin.</p>",
                "recommendations": [],
                "subSections": [],
            },
        ],
    }
    content = _render(guideline)

    assert "Practice point" not in content
    assert "Excise with a 1 cm margin." in content


def test_build_magic_guideline_text_keeps_a_heading_emptied_by_deduplication() -> None:
    """Deduplication is not a reason to drop the heading over the block it took.

    The words survive in the copy that was kept, so the heading still marks where the topic
    sat. That is why the empty-heading pass runs before deduplication rather than after.
    """
    body = "<p>The panel considered the biology and mode of transmission of the organism.</p>"
    guideline = {
        "name": "Sepsis",
        "sections": [
            {"heading": "Chapter one", "text": body, "recommendations": [], "subSections": []},
            {"heading": "Chapter two", "text": body, "recommendations": [], "subSections": []},
        ],
    }
    content = _render(guideline)

    assert content.count("mode of transmission of the organism") == 1
    assert "Chapter one" in content
    assert "Chapter two" in content


# ---------------------------------------------------------------------------
# Scrape API
# ---------------------------------------------------------------------------

PLACEHOLDER_JSON_PATH = "https://s3.amazonaws.com/files.magicapp.org/guideline/ghi/guideline_3-1_0.json"


STUB_JSON_PATH = "https://s3.amazonaws.com/files.magicapp.org/guideline/jkl/guideline_4-1_0.json"


# A published catalogue entry whose body renders to nothing. MAGICapp really does
# publish these, for example 'Test guideline 3'.
PLACEHOLDER_GUIDELINE = {"name": "Beta-blockers for hypertension", "sections": [{"heading": "", "text": ""}]}


# Renders to one short line. Published, not empty, and useless: it would enter the
# corpus as a document about beta-blockers that says nothing about them.
STUB_GUIDELINE = {
    "name": "Beta-blockers for hypertension",
    "sections": [
        {
            "heading": "Beta-blockers for hypertension",
            "text": "<p>Evidence profiles for beta-blockers for hypertension, not yet publicly available.</p>",
        }
    ],
}


def test_scrape_magic_skips_unreadable_guidelines_instead_of_aborting(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty guideline is logged and skipped, leaving the rest of the run intact."""
    monkeypatch.setattr(magic, "MIN_CONTENT_CHARS", 1)
    scraped = _run_catalogue(
        monkeypatch,
        [
            _catalogue_entry("aaaaaa", "Beta-blockers for hypertension", PLACEHOLDER_JSON_PATH),
            _catalogue_entry("nyxpZL", "Management of juvenile idiopathic arthritis", JSON_PATH),
        ],
        {PLACEHOLDER_JSON_PATH: PLACEHOLDER_GUIDELINE, JSON_PATH: GUIDELINE},
    )

    assert [document.external_id for document in scraped] == ["magic-nyxpZL"]
    assert "methotrexate" in scraped[0].content.lower()


def test_scrape_magic_drops_placeholder_guidelines_below_the_content_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A published stub too short to say anything does not become a document."""
    scraped = _run_catalogue(
        monkeypatch,
        [_catalogue_entry("aaaaaa", "Beta-blockers for hypertension", STUB_JSON_PATH)],
        {STUB_JSON_PATH: STUB_GUIDELINE},
    )

    assert scraped == []


def test_scrape_magic_skips_tutorial_and_workshop_organisations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Training material is dropped, and a real guideline is not."""
    monkeypatch.setattr(magic, "MIN_CONTENT_CHARS", 1)
    scraped = _run_catalogue(
        monkeypatch,
        [
            _catalogue_entry("tutor1", "TUTORIAL - BMJ RapidRecs", PLACEHOLDER_JSON_PATH, "MAGICapp Tutorials"),
            _catalogue_entry("nyxpZL", "Management of juvenile idiopathic arthritis", JSON_PATH),
        ],
        {PLACEHOLDER_JSON_PATH: GUIDELINE, JSON_PATH: GUIDELINE},
    )

    assert [document.external_id for document in scraped] == ["magic-nyxpZL"]


def test_scrape_magic_drops_publisher_marked_drafts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A title the publisher marks a draft is dropped; a clean title with status DEV is kept.

    Monash publishes j97pAn as "DRAFT FOR PUBLIC CONSULTATION: ..." - guidance its own
    panel is still consulting readers on. The catalogue status field is never the test:
    DEV describes the authoring workspace, and jboXZL carries it on a finished,
    published guideline.
    """
    monkeypatch.setattr(magic, "MIN_CONTENT_CHARS", 1)
    published_dev = _catalogue_entry("jboXZL", "Supervised exercise for intermittent claudication", JSON_PATH)
    published_dev["status"] = "DEV"
    scraped = _run_catalogue(
        monkeypatch,
        [
            _catalogue_entry("j97pAn", "DRAFT FOR PUBLIC CONSULTATION: Dementia Guidelines", STUB_JSON_PATH),
            published_dev,
        ],
        {STUB_JSON_PATH: GUIDELINE, JSON_PATH: GUIDELINE},
    )

    assert [document.external_id for document in scraped] == ["magic-jboXZL"]


def test_scrape_magic_keeps_drafts_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """include_drafts keeps what a default catalogue run drops."""
    monkeypatch.setattr(magic, "MIN_CONTENT_CHARS", 1)
    scraped = _run_catalogue(
        monkeypatch,
        [_catalogue_entry("j97pAn", "DRAFT FOR PUBLIC CONSULTATION: Dementia Guidelines", JSON_PATH)],
        {JSON_PATH: GUIDELINE},
        include_drafts=True,
    )

    assert [document.external_id for document in scraped] == ["magic-j97pAn"]


def test_scrape_magic_survives_a_guideline_the_server_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The catalogue lists guidelines whose JSON returns 403; the run continues.

    MAGICapp really does publish catalogue entries pointing at files that are not
    publicly readable, so a catalogue run meets this in the wild.
    """
    monkeypatch.setattr(magic, "MIN_CONTENT_CHARS", 1)
    forbidden = "https://files.magicapp.org/guideline/forbidden/guideline_9-1_0.json"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.magicapp.org":
            return httpx.Response(
                200,
                json=[
                    _catalogue_entry("DjxqOL", "Demo guideline", forbidden),
                    _catalogue_entry("nyxpZL", "Management of juvenile idiopathic arthritis", JSON_PATH),
                ],
            )
        if str(request.url) == forbidden:
            return httpx.Response(403, text="Forbidden")
        return httpx.Response(200, json=GUIDELINE)

    monkeypatch.setattr(magic, "default_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    assert [document.external_id for document in scrape_magic(documents=None)] == ["magic-nyxpZL"]


def test_scrape_magic_guideline_records_how_many_recommendations_reached_content() -> None:
    """Both counts travel with the document, so a silent loss is visible later.

    Every field is read with `.get`, so a renamed or moved field would shorten a
    document without raising. Carrying the publisher's count beside our own is
    what makes that detectable after the fact.
    """
    guideline = {
        "name": "Management of juvenile idiopathic arthritis",
        "sections": [
            {
                "heading": "Therapy",
                "text": "<p>Treat early.</p>",
                "recommendations": [
                    {"text": "<p>Offer methotrexate.</p>", "strength": "WEAK"},
                    {"text": "<p>Box 1.1 Sustainable Development Goals.</p>", "strength": "INFO"},
                ],
                "subSections": [],
            }
        ],
    }
    with _client(guideline=guideline) as client:
        document = scrape_magic_guideline(client, list_published_guidelines(client).refs[0])

    # The catalogue entry claims ten; one recommendation reached content, and the
    # INFO callout box is deliberately not counted as one.
    assert document.metadata["recommendation_count"] == 10
    assert document.metadata["recommendations_in_content"] == 1
