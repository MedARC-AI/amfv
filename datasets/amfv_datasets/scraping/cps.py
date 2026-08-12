"""Scrape Canadian Paediatric Society statements into normalized markdown.

CPS retains copyright in its position statements and practice points. This
module supports a permission-gated ingestion workflow; obtain written CPS
permission before running a corpus scrape or redistributing its output.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlparse

import httpx
from lxml import html as lxml_html
from markdownify import MarkdownConverter

from amfv_datasets.scraping.base import (
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    default_client,
    scrape_listing_documents,
)
from amfv_datasets.scraping.html import LinkMode, clean_text, html_to_markdown

BASE_URL = "https://cps.ca"
STATEMENTS_URL = f"{BASE_URL}/en/documents/statements-by-date"
DOCUMENT_DELAY_SECONDS = 10.0
LISTING_PAGE_SIZE = 10
MIN_SUBSTANTIVE_HTML_CHARS = 2_000

_POSITION_PATH_RE = re.compile(r"^/(?:en/)?documents/position/(?P<slug>[^/]+)/?$", re.IGNORECASE)
_DATE_PATTERNS = {
    "posted": re.compile(r"\bPosted:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})", re.IGNORECASE),
    "reaffirmed": re.compile(r"\bReaffirmed:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})", re.IGNORECASE),
    "updated": re.compile(r"\bUpdated:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})", re.IGNORECASE),
}
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_EMPTY_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[\]\([^)]*\)")
_DECORATIVE_IMAGE_FILENAMES = {"file-pdf.svg"}


class _CpsMarkdownConverter(MarkdownConverter):
    """Preserve CPS superscripts that carry clinical meaning or citations."""

    def convert_a(self, element, text: str, parent_tags: set[str]) -> str:  # noqa: ANN001
        classes = element.get("class") or ()
        href = element.get("href")
        if "reference" in classes and href and "#ref" in href:
            marker = element.get_text(strip=True).strip("[]")
            if "a" in (self.options.get("strip") or ()):
                return f"[{marker}]"
            return f"[{marker}]({href})"
        return super().convert_a(element, text, parent_tags)

    def convert_sup(self, element, text: str, parent_tags: set[str]) -> str:  # noqa: ANN001
        if not text.strip():
            return ""
        citation = element.find("a", href=lambda href: href and "#ref" in href)
        if citation:
            marker = element.get_text(strip=True).strip("[]")
            if "a" in (self.options.get("strip") or ()):
                return f"[{marker}]"
            return f"[{marker}]({citation.get('href')})"
        return f"<sup>{text}</sup>"


_CPS_MARKDOWN_CONVERTERS = {
    LinkMode.KEEP: _CpsMarkdownConverter(bullets="-", heading_style="ATX"),
    LinkMode.STRIP: _CpsMarkdownConverter(bullets="-", heading_style="ATX", strip=("a",)),
}


class CpsFetchError(ScrapeError):
    """Raised when a CPS statement cannot be discovered or parsed."""


@dataclass(frozen=True)
class CpsStatementRef:
    """A CPS statement or practice point discovered from the date index."""

    slug: str
    title: str
    page_url: str


def statement_ref_from_url(url: str, *, title: str | None = None) -> CpsStatementRef:
    """Parse a CPS position-statement URL into a canonical reference."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"cps.ca", "www.cps.ca"}:
        raise CpsFetchError(f"Enter a CPS statement URL from cps.ca; got {url!r}")

    match = _POSITION_PATH_RE.match(unquote(parsed.path))
    if not match:
        raise CpsFetchError(f"Enter a URL like https://cps.ca/en/documents/position/example-statement; got {url!r}")
    slug = match.group("slug")
    return CpsStatementRef(
        slug=slug,
        title=title or slug.replace("-", " "),
        page_url=f"{BASE_URL}/en/documents/position/{slug}",
    )


def listing_page_url(page: int) -> str:
    """Return the CPS date-index URL for a one-based page number."""
    if page < 1:
        raise ValueError(f"page must be at least 1; got {page}")
    if page == 1:
        return STATEMENTS_URL
    return f"{STATEMENTS_URL}/P{(page - 1) * LISTING_PAGE_SIZE}"


def list_statements(client: httpx.Client, page: int) -> list[CpsStatementRef]:
    """Return unique statement references from one CPS date-index page."""
    response = client.get(listing_page_url(page))
    response.raise_for_status()
    doc = lxml_html.fromstring(response.text)
    anchors = doc.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' stmt-title ')]//a[@href]")

    refs: list[CpsStatementRef] = []
    seen_urls: set[str] = set()
    for anchor in anchors:
        href = anchor.get("href")
        title = clean_text(anchor.text_content(), drop_numeric_citations=False)
        if not href or not title:
            continue
        try:
            ref = statement_ref_from_url(urljoin(BASE_URL, href), title=title)
        except CpsFetchError:
            continue
        if ref.page_url in seen_urls:
            continue
        seen_urls.add(ref.page_url)
        refs.append(ref)
    return refs


def _content_root(html_text: str) -> lxml_html.HtmlElement:
    doc = lxml_html.fromstring(html_text)
    selectors = (
        "//div[contains(concat(' ', normalize-space(@class), ' '), ' statement-wrapper ')][1]",
        "//main//article[1]",
        "//*[@id='main-content']//article[1]",
        "//*[@id='main-content'][1]",
        "//main[1]",
    )
    for selector in selectors:
        matches = doc.xpath(selector)
        if matches:
            return matches[0]
    raise CpsFetchError("No readable statement content found on the CPS page")


def _normalize_content(root: lxml_html.HtmlElement) -> None:
    removable = root.xpath(
        ".//script | .//style | .//nav | .//form | .//button | .//noscript | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' breadcrumb ')] | "
        ".//*[contains(@class, 'share')] | "
        ".//*[contains(@class, 'social')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' related-content ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' sidebar ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' hide-for-print ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' show-for-print ')] | "
        ".//*[contains(@class, 'print:tw-hidden')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' --podcast ')]"
    )
    for element in removable:
        element.drop_tree()

    for link in root.xpath(".//a[contains(@href, '/en/education/test-your-knowledge')]"):
        link.drop_tree()

    for image in root.xpath(".//img[@src]"):
        filename = urlparse(image.get("src")).path.rsplit("/", 1)[-1].lower()
        if filename in _DECORATIVE_IMAGE_FILENAMES:
            image.drop_tree()

    for nested in root.xpath(".//strong//strong | .//em//em"):
        nested.drop_tag()
    for title in root.xpath(".//h1"):
        title.drop_tree()
    for heading in root.xpath(".//*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6]"):
        for emphasis in heading.xpath(".//strong | .//em | .//b | .//i"):
            emphasis.drop_tag()
        if not clean_text(heading.text_content(), drop_numeric_citations=False) and not heading.xpath(".//img"):
            heading.drop_tree()


def _content_to_markdown(
    root: lxml_html.HtmlElement,
    *,
    link_mode: LinkMode,
    base_url: str,
) -> str:
    source = lxml_html.tostring(root, encoding="unicode")
    markdown = html_to_markdown(
        source,
        link_mode=link_mode,
        base_url=base_url,
        converter=_CPS_MARKDOWN_CONVERTERS[link_mode],
        drop_numeric_citations=False,
    )
    markdown = _EMPTY_MARKDOWN_LINK_RE.sub("", markdown)
    lines = [line.rstrip() for line in markdown.splitlines()]
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def build_statement_text(
    html_text: str,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
    base_url: str = BASE_URL,
) -> tuple[str, int]:
    """Extract one CPS statement as markdown and return its section count."""
    root = _content_root(html_text)
    _normalize_content(root)
    section_count = max(1, len(root.xpath(".//*[self::h2 or self::h3 or self::h4 or self::h5 or self::h6]")))
    content = _content_to_markdown(root, link_mode=link_mode, base_url=base_url)
    if not content:
        raise CpsFetchError("No readable statement content found on the CPS page")
    return content, section_count


def scrape_statement(
    client: httpx.Client,
    ref: CpsStatementRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> ScrapedDocument:
    """Scrape one CPS statement into the shared document schema."""
    response = client.get(ref.page_url)
    response.raise_for_status()
    raw_root = _content_root(response.text)
    content, section_count = build_statement_text(response.text, link_mode=link_mode, base_url=ref.page_url)
    headings = [clean_text(value) for value in raw_root.xpath(".//h1[1]//text()")]
    title = " ".join(value for value in headings if value) or ref.title
    metadata: dict[str, object] = {"slug": ref.slug}
    visible_text = clean_text(raw_root.text_content(), drop_numeric_citations=False)
    for key, pattern in _DATE_PATTERNS.items():
        if match := pattern.search(visible_text):
            metadata[key] = match.group(1).strip()
    pdf_urls = _pdf_urls(raw_root, base_url=ref.page_url)
    if len(content) < MIN_SUBSTANTIVE_HTML_CHARS and section_count == 1 and pdf_urls:
        metadata.update(
            {
                "content_length": len(content),
                "content_scope": "landing_page",
                "download_url": pdf_urls[0],
            }
        )
    return ScrapedDocument(
        source="cps",
        external_id=f"cps-{ref.slug.lower()}",
        title=title,
        url=ref.page_url,
        content=content,
        section_count=section_count,
        metadata=metadata,
    )


def _pdf_urls(root: lxml_html.HtmlElement, *, base_url: str) -> list[str]:
    urls: list[str] = []
    for href in root.xpath(".//a[@href]/@href"):
        url = urljoin(base_url, href)
        if urlparse(url).path.lower().endswith(".pdf") and url not in urls:
            urls.append(url)
    return urls


def scrape_cps(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
) -> ScrapeRun:
    """Configure a CPS scrape from one URL or the current-statements index."""
    if url is not None:

        def scrape_url() -> Iterable[ScrapedDocument]:
            with default_client() as client:
                yield scrape_statement(client, statement_ref_from_url(url), link_mode=link_mode)

        return ScrapeRun(documents=scrape_url(), total=1)

    return ScrapeRun(
        documents=scrape_listing_documents(
            documents=documents,
            client_factory=default_client,
            list_page=list_statements,
            scrape_item=lambda client, ref: scrape_statement(client, ref, link_mode=link_mode),
            document_delay_seconds=DOCUMENT_DELAY_SECONDS,
        )
    )


__all__ = [
    "BASE_URL",
    "CpsFetchError",
    "CpsStatementRef",
    "MIN_SUBSTANTIVE_HTML_CHARS",
    "STATEMENTS_URL",
    "build_statement_text",
    "list_statements",
    "listing_page_url",
    "scrape_cps",
    "scrape_statement",
    "statement_ref_from_url",
]
