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


def test_absolute_unique_urls_normalizes_relative_urls() -> None:
    """Relative URLs are absolutized, stripped, and deduplicated."""
    assert absolute_unique_urls(
        ["/guidance/ng1?tab=contents", "https://example.org/guidance/ng1#section", "/guidance/ng2"],
        base_url="https://example.org",
    ) == ["https://example.org/guidance/ng1", "https://example.org/guidance/ng2"]


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


def test_html_to_markdown_absolutizes_images() -> None:
    """Relative image sources are preserved as absolute markdown image URLs."""
    html_text = '<img src="/images/flowchart.png" alt="Treatment flowchart">'

    assert html_to_markdown(html_text, base_url="https://example.org/guideline/") == (
        "![Treatment flowchart](https://example.org/images/flowchart.png)"
    )
