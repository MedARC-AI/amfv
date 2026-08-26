"""Tests for reusable scraping HTML helpers."""

from amfv_datasets.scraping.html import (
    LinkMode,
    absolute_unique_urls,
    clean_text,
    document_title,
    first_matching_urls,
    html_to_markdown,
)


def test_clean_text_normalizes_whitespace_and_citations() -> None:
    """Whitespace is collapsed and bracketed numeric citations are removed."""
    assert clean_text(" Alpha\n beta   [12] ") == "Alpha beta"


def test_clean_text_removes_unsafe_controls_without_splitting_words() -> None:
    """Embedded publisher control bytes do not survive normalized text."""
    assert clean_text(" recom\x02mendations\tremain\x85readable\r ") == "recommendations remain readable"


def test_absolute_unique_urls_normalizes_relative_urls() -> None:
    """Relative URLs are absolutized, stripped, and deduplicated."""
    assert absolute_unique_urls(
        ["/guidance/ng1?tab=contents", "https://example.org/guidance/ng1#section", "/guidance/ng2"],
        base_url="https://example.org",
    ) == ["https://example.org/guidance/ng1", "https://example.org/guidance/ng2"]


def test_absolute_unique_urls_enforces_authority_policy() -> None:
    """Host policies reject lookalike paths, credentials, and unusual ports."""
    assert absolute_unique_urls(
        [
            "/guidance/ng1/chapter/recommendations?tab=contents",
            "https://evil.example/guidance/ng1/chapter/recommendations",
            "https://user@www.nice.org.uk/guidance/ng1/chapter/credentials",
            "https://www.nice.org.uk:444/guidance/ng1/chapter/port",
            "javascript:/guidance/ng1/chapter/script",
            "https://www.nice.org.uk/guidance/ng1/chapter/recommendations#duplicate",
        ],
        base_url="https://www.nice.org.uk",
        allowed_hosts=("nice.org.uk", "www.nice.org.uk"),
    ) == ["https://www.nice.org.uk/guidance/ng1/chapter/recommendations"]


def test_absolute_unique_urls_canonicalization_is_idempotent() -> None:
    """Canonical URLs remain unchanged when normalized again."""
    first_pass = absolute_unique_urls(
        ["/guidance/ng1?tab=contents#heading"],
        base_url="https://example.org",
    )
    assert absolute_unique_urls(first_pass, base_url="https://example.org") == first_pass


def test_first_matching_urls_uses_first_xpath_with_matches() -> None:
    """URL extraction falls back across XPath selectors."""
    html_text = """
    <html>
      <nav><a href="/first">First</a></nav>
      <main><a href="/second">Second</a></main>
    </html>
    """

    assert first_matching_urls(
        html_text,
        xpaths=("//aside/a/@href", "//nav/a/@href", "//main/a/@href"),
        base_url="https://example.org",
    ) == ["https://example.org/first"]


def test_first_matching_urls_falls_back_when_policy_rejects_first_xpath() -> None:
    """Rejected candidates do not prevent an accepted fallback selector."""
    html_text = """
    <html>
      <nav><a href="https://evil.example/guidance/ng1/chapter/lookalike">Bad</a></nav>
      <main><a href="/guidance/ng1/chapter/good">Good</a></main>
    </html>
    """

    assert first_matching_urls(
        html_text,
        xpaths=("//nav/a/@href", "//main/a/@href"),
        base_url="https://www.nice.org.uk",
        allowed_hosts=("nice.org.uk", "www.nice.org.uk"),
    ) == ["https://www.nice.org.uk/guidance/ng1/chapter/good"]


def test_document_title_uses_heading_and_strips_suffix() -> None:
    """Document titles are read from common title locations."""
    html_text = "<html><h1>Guideline | Guidance | NICE</h1><title>Fallback</title></html>"

    assert document_title(html_text, fallback="NG1", suffixes=(" | Guidance | NICE",)) == "Guideline"


def test_html_to_markdown_keeps_links_and_tables() -> None:
    """HTML conversion preserves markdown links by default."""
    html_text = """
    <div>
      <h2>Recommendations</h2>
      <p>Offer <a href="/guidance/ng1">treatment</a> [1].</p>
      <table><tr><th>Drug</th><th>Dose</th></tr><tr><td>A</td><td>5 mg</td></tr></table>
    </div>
    """

    assert html_to_markdown(html_text, base_url="https://www.nice.org.uk") == (
        "## Recommendations\n\n"
        "Offer [treatment](https://www.nice.org.uk/guidance/ng1) .\n\n"
        "| Drug | Dose |\n| --- | --- |\n| A | 5 mg |"
    )


def test_html_to_markdown_can_strip_links() -> None:
    """HTML conversion can strip links while keeping their visible text."""
    html_text = '<p>Offer <a href="https://example.org">treatment</a>.</p>'

    assert html_to_markdown(html_text, link_mode=LinkMode.STRIP) == "Offer treatment."


def test_html_to_markdown_removes_unsafe_controls() -> None:
    """C0, DEL, and C1 controls are dropped while ordinary whitespace remains readable."""
    html_text = "<p>recom\x02mendations\tstay\nreadable\rwith\x7f dose\x85limits.</p>"

    markdown = html_to_markdown(html_text)

    assert markdown == "recommendations stay\nreadable\nwith dose limits."
    assert not any(
        (ord(character) < 32 and character not in "\t\n\r") or 127 <= ord(character) <= 159 for character in markdown
    )


def test_html_to_markdown_preserves_medical_superscripts_and_same_page_citations() -> None:
    """Medical notation and fragment links survive source-specific conversion."""
    html_text = '<p>Count 10<sup>9</sup>/L.<a href="#reference-1">[1]</a></p>'

    assert (
        html_to_markdown(
            html_text,
            base_url="https://example.org/guideline",
            drop_numeric_citations=False,
        )
        == "Count 10<sup>9</sup>/L.[[1]](#reference-1)"
    )


def test_html_to_markdown_keeps_images_inside_tables() -> None:
    """Clinical flowcharts embedded in table cells retain their URLs."""
    html_text = '<table><tr><td><img src="/flowchart.png" alt="Flowchart"></td></tr></table>'

    assert "![Flowchart](https://example.org/flowchart.png)" in html_to_markdown(
        html_text,
        base_url="https://example.org/guideline",
    )


def test_html_to_markdown_absolutizes_images() -> None:
    """Relative image sources are preserved as absolute markdown image URLs."""
    html_text = '<img src="/images/flowchart.png" alt="Treatment flowchart">'

    assert html_to_markdown(html_text, base_url="https://example.org/guideline/") == (
        "![Treatment flowchart](https://example.org/images/flowchart.png)"
    )


def test_html_to_markdown_keeps_in_page_anchors_relative() -> None:
    """A link to a heading in the same document is not rewritten to the base URL."""
    html_text = '<p>See <a href="#recommendations">recommendations</a>.</p>'

    assert html_to_markdown(html_text, base_url="https://example.org/guideline/") == (
        "See [recommendations](#recommendations)."
    )
