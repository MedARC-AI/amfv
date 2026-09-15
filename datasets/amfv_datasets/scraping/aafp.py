"""Scrape AAFP clinical recommendations into normalized markdown documents.

AAFP (American Academy of Family Physicians) publishes clinical guidance on
Clinical Insights topic pages. One topic page groups several independent
recommendations under category accordions, and each recommendation lives in its
own drawer with its own title, AAFP status, dates, and source links.

One drawer therefore becomes one `ScrapedDocument`. Merging a whole topic page
into a single document would mix recommendations from different organizations,
populations, and dates, which a retriever cannot separate again.

Because one fetched page yields several documents, this module does not use
`scrape_listing_documents`: that helper maps one listed item to at most one
document, so reusing it would refetch the same topic page once per drawer. The
polite delay here is applied per page request, not per produced document.

Responsible use:
AAFP website content is copyrighted. Crawler access allowed by robots.txt is
not a redistribution licence, and this module makes no legal determination.
Anyone running this scraper is responsible for confirming that their intended
use is permitted, and scraped AAFP output must not be committed to this
repository without appropriate permission.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from lxml import etree
from lxml import html as lxml_html

from amfv_datasets.scraping.base import ScrapedDocument, ScrapeError, ScrapeRun, default_client
from amfv_datasets.scraping.html import LinkMode, clean_text, html_to_markdown

BASE_URL = "https://www.aafp.org"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
CLINICAL_INSIGHTS_PATH = "/clinical-insights/"
AAFP_DATASET_NAME = "aafp-webscrape"
AAFP_DATASET_DISPLAY_NAME = "AAFP Webscrape"
PAGE_DELAY_SECONDS = 5.0
SCRAPE_STRATEGY = "drawer"
MAX_SITEMAPS = 50
MAX_SITEMAP_DEPTH = 3

logger = logging.getLogger(__name__)

_HOSTS = {"www.aafp.org"}
_GUIDELINES_HEADING = "guidelines and recommendations"
_ACCEPTED_CATEGORIES = frozenset(
    {
        "clinical practice guideline",
        "clinical practice guidelines",
        "clinical preventive service recommendation",
        "clinical preventive service recommendations",
    }
)
_ERROR_PAGE_MARKERS = (
    "service interruption",
    "page not found",
    "page cannot be found",
    "temporarily unavailable",
    "site maintenance",
)
# Only whole blocks are candidate status lines. Inline tags are excluded on
# purpose: a status must be read from the complete paragraph or heading, never
# from a <strong>/<span>/<em> fragment inside qualifying prose.
_STATUS_BLOCK_TAGS = frozenset({"p", "h3", "h4", "h5", "h6", "li"})
_NON_SUBTITLE_HEADINGS = frozenset({"key recommendation", "key recommendations", "recommendation", "recommendations"})
_EXCLUDED_PATH_SEGMENTS = frozenset(
    {"account", "cme", "login", "logout", "my-academy", "rss", "search", "shop", "sitemap", "store"}
)
_EXCLUDED_PATH_SUFFIXES = (".pdf", ".xml", ".json", ".jpg", ".jpeg", ".png", ".zip")
_SITEMAP_ROOTS = frozenset({"sitemapindex", "urlset"})
_SKIPPED_LINK_SCHEMES = ("#", "mailto:", "tel:", "javascript:")

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_MONTH_INDEX = {month.lower(): index for index, month in enumerate(_MONTHS, start=1)}
_DATE_PATTERN = rf"(?:(?:{'|'.join(_MONTHS)})\s+)?(?:19|20)\d{{2}}"

# "not endorsed" is matched before "endorsed" so a refusal is never read as an
# endorsement. Every pattern is also anchored at the start of its segment.
_STATUS_PATTERNS = (
    ("not_endorsed", r"not\s+endorsed"),
    ("developed_by_aafp", r"developed\s+by\s+(?:the\s+)?aafp"),
    ("jointly_developed", r"jointly\s+developed"),
    ("affirmation_of_value", r"affirmation\s+of\s+value"),
    ("reaffirmed", r"reaffirmed"),
    # AAFP writes both "Endorsed" and "Endorsement"; both are the same stance.
    ("endorsed", r"endorse(?:d|ment)"),
)
_STATUS_SEGMENT_RES = tuple(
    (
        status,
        re.compile(
            rf"^\s*{pattern}\s*\*?\s*[,:]?\s*(?P<date>{_DATE_PATTERN})?\s*[.*]?\s*$",
            re.IGNORECASE,
        ),
    )
    for status, pattern in _STATUS_PATTERNS
)
_REAFFIRMED_SPLIT_RE = re.compile(r",\s*(?=reaffirmed\b)", re.IGNORECASE)
_FULL_SOURCE_LINK_RE = re.compile(
    r"(?:full\s+(?:guideline|guidance|recommendation|report|statement)|read\s+the\s+recommendation)",
    re.IGNORECASE,
)
# ‐-― covers hyphen, en dash, em dash and horizontal bar.
_SLUG_SEPARATOR_RE = re.compile(r"[\s‐-―/\\_,]+")
_SLUG_DROP_RE = re.compile(r"[^a-z0-9-]+")
_SLUG_COLLAPSE_RE = re.compile(r"-{2,}")
_TITLE_SUFFIXES = (" | AAFP", " - AAFP", " | American Academy of Family Physicians")


class AafpFetchError(ScrapeError):
    """Raised when an AAFP page cannot be fetched or parsed."""


@dataclass(frozen=True)
class SitemapLinks:
    """Locations parsed from one sitemap document.

    Attributes:
        page_urls: Content URLs listed by a `<urlset>` sitemap.
        sitemap_urls: Nested sitemap URLs listed by a `<sitemapindex>` sitemap.
    """

    page_urls: list[str]
    sitemap_urls: list[str]


def parse_sitemap(xml_text: str) -> SitemapLinks:
    """Parse a sitemap index or URL set into content and nested sitemap URLs.

    The document root must be `<urlset>` or `<sitemapindex>`, so an error page
    served with a 200 status is rejected instead of read as an empty sitemap.

    Args:
        xml_text: Sitemap XML text. Both `<sitemapindex>` and `<urlset>`
            documents are accepted, with or without XML namespaces.
    """
    try:
        root = etree.fromstring(xml_text.encode("utf-8"), parser=_sitemap_parser())
    except etree.XMLSyntaxError as exc:
        raise AafpFetchError(f"Could not parse AAFP sitemap XML: {exc}") from exc
    root_name = etree.QName(root).localname
    if root_name not in _SITEMAP_ROOTS:
        raise AafpFetchError(
            f"AAFP sitemap root was {root_name!r}; expected one of {', '.join(sorted(_SITEMAP_ROOTS))}"
        )
    return SitemapLinks(
        page_urls=_locations(root, parent="url"),
        sitemap_urls=[url for url in _locations(root, parent="sitemap") if _is_aafp_url(url)],
    )


def is_topic_url(url: str) -> bool:
    """Report whether a URL is an AAFP Clinical Insights topic page candidate.

    A candidate is a Clinical Insights page at least two path segments deep, so
    the Clinical Insights hub and its category landing pages are excluded. This
    only decides what is worth fetching; whether a page actually holds
    recommendations is decided by `parse_topic_page`.

    Args:
        url: Absolute URL to classify.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() not in _HOSTS:
        return False
    path = parsed.path
    if not path.startswith(CLINICAL_INSIGHTS_PATH) or path.lower().endswith(_EXCLUDED_PATH_SUFFIXES):
        return False
    segments = [segment for segment in path[len(CLINICAL_INSIGHTS_PATH) :].split("/") if segment]
    if len(segments) < 2:
        return False
    return not any(segment.lower() in _EXCLUDED_PATH_SEGMENTS for segment in segments)


def discover_topic_urls(
    client: httpx.Client,
    *,
    sitemap_url: str = SITEMAP_URL,
    max_sitemaps: int = MAX_SITEMAPS,
    max_depth: int = MAX_SITEMAP_DEPTH,
) -> list[str]:
    """Discover Clinical Insights topic-page URLs from the AAFP sitemap.

    Nested sitemaps are followed breadth-first so the returned order is stable
    across runs. Search-engine results are never used as a discovery source.

    Args:
        client: HTTP client used to fetch sitemap documents.
        sitemap_url: Sitemap entry point to read (default: SITEMAP_URL).
        max_sitemaps: Maximum number of sitemap documents to fetch (default:
            MAX_SITEMAPS).
        max_depth: Maximum nested-sitemap depth to follow (default:
            MAX_SITEMAP_DEPTH).
    """
    pending: list[tuple[str, int]] = [(sitemap_url, 0)]
    visited: set[str] = set()
    topic_urls: list[str] = []
    seen: set[str] = set()
    fetched = 0

    while pending:
        current, depth = pending.pop(0)
        current = current.split("#")[0]
        if current in visited:
            continue
        visited.add(current)
        if fetched >= max_sitemaps:
            logger.warning("Stopped AAFP sitemap discovery after %d sitemaps", max_sitemaps)
            break
        links = parse_sitemap(_page_text(client, current))
        fetched += 1
        for page_url in links.page_urls:
            canonical = _canonical_url(page_url)
            if canonical in seen or not is_topic_url(canonical):
                continue
            seen.add(canonical)
            topic_urls.append(canonical)
        if depth < max_depth:
            pending.extend((nested, depth + 1) for nested in links.sitemap_urls)

    return topic_urls


def parse_topic_page(
    html_text: str,
    *,
    url: str,
    link_mode: LinkMode = LinkMode.KEEP,
) -> list[ScrapedDocument]:
    """Parse one AAFP topic page into one document per recommendation drawer.

    A Clinical Insights page without a guidelines section legitimately produces
    no documents. A page that shows a guidelines section but whose categories or
    drawers cannot be read raises, because silently returning nothing is how a
    layout change stops a source without anyone noticing.

    Args:
        html_text: Topic page HTML.
        url: Topic page URL, used for provenance and relative links.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    try:
        root = lxml_html.fromstring(html_text)
    except (etree.ParserError, ValueError) as exc:
        # An empty or otherwise unparseable body reaches us as a bare lxml
        # error; callers should only ever have to handle AafpFetchError.
        raise AafpFetchError(f"Could not parse AAFP page HTML for {url}: {exc}") from exc
    _raise_for_error_page(root, url=url)

    mains = [element for element in root.iter("main") if element.get("id") == "main-content"]
    if not mains:
        raise AafpFetchError(f"AAFP page markup changed (no main#main-content) for {url}")
    main = mains[0]

    topic = _topic_title(root, main)
    documents: list[ScrapedDocument] = []
    by_external_id: dict[str, str] = {}
    for category, wrapper in _category_drawers(main):
        document = _drawer_document(
            wrapper,
            topic=topic,
            category=category,
            page_url=url,
            link_mode=link_mode,
        )
        if document is None:
            continue
        if document.external_id in by_external_id:
            raise AafpFetchError(
                f"Duplicate AAFP document id {document.external_id!r} on {url}: "
                f"{by_external_id[document.external_id]!r} and {document.title!r}"
            )
        by_external_id[document.external_id] = document.title
        documents.append(document)

    if not documents and _has_guidelines_section(main):
        raise AafpFetchError(
            f"AAFP page {url} shows a guidelines section but no recommendation drawers could be "
            "parsed; the page markup or category labels likely changed"
        )
    return documents


def scrape_topic_page(
    client: httpx.Client,
    url: str,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> list[ScrapedDocument]:
    """Fetch one AAFP topic page once and parse every recommendation drawer.

    Args:
        client: HTTP client used to fetch the topic page.
        url: AAFP topic page URL to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    canonical = _canonical_url(url)
    if not _is_aafp_url(canonical):
        raise AafpFetchError(f"Enter an AAFP URL from www.aafp.org; got {url!r}")
    return parse_topic_page(_page_text(client, canonical), url=canonical, link_mode=link_mode)


def scrape_aafp(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
) -> ScrapeRun:
    """Scrape AAFP recommendations from a URL or from discovered topic pages.

    Args:
        documents: Number of documents to scrape. When unset, every discovered
            document is scraped (default: None).
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        url: AAFP topic page URL to scrape on its own (default: None).
    """
    if documents is not None and documents < 1:
        raise ValueError(f"documents must be at least 1; got {documents}")

    if url is not None:
        with default_client() as client:
            page_documents = scrape_topic_page(client, url, link_mode=link_mode)
        limited = page_documents if documents is None else page_documents[:documents]
        return ScrapeRun(documents=limited, total=len(limited))

    return ScrapeRun(
        documents=_discovered_documents(documents=documents, link_mode=link_mode),
        total=None,
    )


def _discovered_documents(
    *,
    documents: int | None,
    link_mode: LinkMode,
    sitemap_url: str = SITEMAP_URL,
    page_delay_seconds: float = PAGE_DELAY_SECONDS,
) -> Iterator[ScrapedDocument]:
    """Yield documents from discovered topic pages, one fetch per page.

    A single drifted page is skipped, but a crawl that discovers no topic pages
    at all, or that finishes without producing one document, raises. Exiting
    successfully with nothing scraped would report broken discovery as an empty
    source.
    """
    with default_client() as client:
        topic_urls = discover_topic_urls(client, sitemap_url=sitemap_url)
        if not topic_urls:
            raise AafpFetchError(
                f"AAFP discovery found no eligible topic pages from {sitemap_url}; "
                "the sitemap layout or the Clinical Insights URL scheme likely changed"
            )
        scraped = 0
        fetched = 0
        for topic_url in topic_urls:
            if documents is not None and scraped >= documents:
                return
            if fetched and page_delay_seconds:
                time.sleep(page_delay_seconds)
            fetched += 1
            try:
                page_documents = scrape_topic_page(client, topic_url, link_mode=link_mode)
            except AafpFetchError as exc:
                # One drifted or unreachable page must not end a long polite
                # crawl; the single-page path still raises so drift stays loud.
                logger.warning("Skipping AAFP page %s: %s", topic_url, exc)
                continue
            for document in page_documents:
                yield document
                scraped += 1
                if documents is not None and scraped >= documents:
                    return
        if not scraped:
            raise AafpFetchError(
                f"AAFP discovery fetched {fetched} topic pages from {sitemap_url} but produced no "
                "documents; the page markup or category labels likely changed"
            )


def _page_text(client: httpx.Client, url: str) -> str:
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AafpFetchError(f"Could not fetch AAFP page {url}: {exc}") from exc
    return response.text


def _sitemap_parser() -> etree.XMLParser:
    """Build an XML parser that resolves no entities and reaches no network."""
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)


def _locations(root: etree._Element, *, parent: str) -> list[str]:
    values = root.xpath(
        "//*[local-name()=$parent]/*[local-name()='loc']/text()",
        parent=parent,
    )
    return [text for text in (clean_text(value, drop_numeric_citations=False) for value in values) if text]


def _is_aafp_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() in _HOSTS


def _canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def _raise_for_error_page(root: lxml_html.HtmlElement, *, url: str) -> None:
    """Reject known AAFP error and service-interruption pages."""
    for element in (*root.iter("title"), *root.iter("h1")):
        text = clean_text(element.text_content())
        lowered = text.lower()
        if any(marker in lowered for marker in _ERROR_PAGE_MARKERS):
            raise AafpFetchError(f"AAFP returned an error page for {url}: {text!r}")


def _topic_title(root: lxml_html.HtmlElement, main: lxml_html.HtmlElement) -> str:
    for element in (*main.iter("h1"), *root.iter("h1"), *root.iter("title")):
        title = clean_text(element.text_content())
        for suffix in _TITLE_SUFFIXES:
            title = title.removesuffix(suffix).strip()
        if title:
            return title
    raise AafpFetchError("AAFP page markup changed (no page title)")


def _has_class(element: lxml_html.HtmlElement, token: str) -> bool:
    return token in (element.get("class") or "").split()


def _normalized(value: str) -> str:
    return clean_text(value).lower().rstrip(":.").strip()


def _accepted_category(value: str) -> str | None:
    label = clean_text(value)
    return label if _normalized(label) in _ACCEPTED_CATEGORIES else None


def _has_guidelines_section(main: lxml_html.HtmlElement) -> bool:
    return any(_normalized(heading.text_content()) == _GUIDELINES_HEADING for heading in main.iter("h2"))


def _category_drawers(main: lxml_html.HtmlElement) -> Iterator[tuple[str, lxml_html.HtmlElement]]:
    """Yield recommendation drawers while excluding unrelated page sections.

    The guidelines heading supplies a fallback category for drawers placed
    directly beneath it. An accepted accordion heading supplies a more specific
    category. Any unrelated h2 closes the active category.
    """
    category: str | None = None

    for element in main.iter():
        if not isinstance(element.tag, str):
            continue

        if element.tag == "h2":
            heading = clean_text(element.text_content())
            category = heading if _normalized(heading) == _GUIDELINES_HEADING else None
        elif _has_class(element, "accordion__heading"):
            category = _accepted_category(element.text_content())
        elif _has_class(element, "drawer__wrapper") and category is not None:
            yield category, element


def _drawer_header(wrapper: lxml_html.HtmlElement) -> lxml_html.HtmlElement | None:
    for element in wrapper.iter():
        if isinstance(element.tag, str) and _has_class(element, "drawer-header"):
            return element
    return next(iter(wrapper.iter("h3")), None)


def _drop_element(element: lxml_html.HtmlElement) -> None:
    """Remove an element from its parent, keeping the text that followed it."""
    parent = element.getparent()
    if parent is None:
        return
    if element.tail:
        previous = element.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + element.tail
        else:
            parent.text = (parent.text or "") + element.tail
    parent.remove(element)


def _parse_status_segment(segment: str) -> dict[str, str | None] | None:
    for status, pattern in _STATUS_SEGMENT_RES:
        match = pattern.match(segment)
        if match:
            date = match.group("date")
            return {"status": status, "date": clean_text(date) if date else None}
    return None


def _parse_status_events(text: str) -> list[dict[str, str | None]]:
    """Parse an AAFP status label into structured events, or return no events.

    Every segment must parse, so ordinary recommendation prose never turns into
    a medical status.
    """
    inner = clean_text(text)
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1].strip()
    if not inner:
        return []
    segments = [part for chunk in inner.split(";") for part in _REAFFIRMED_SPLIT_RE.split(chunk)]
    events: list[dict[str, str | None]] = []
    for segment in segments:
        if not segment.strip():
            continue
        event = _parse_status_segment(segment)
        if event is None:
            return []
        events.append(event)
    return events


def _date_sort_key(date: str) -> tuple[int, int]:
    parts = date.split()
    year = int(parts[-1])
    month = _MONTH_INDEX.get(parts[0].lower(), 0) if len(parts) > 1 else 0
    return year, month


def _latest_status_date(events: list[dict[str, str | None]]) -> str | None:
    """Return the date of the most recent dated status event, keeping it as written."""
    dated = [(index, event["date"]) for index, event in enumerate(events) if event["date"]]
    if not dated:
        return None
    return max(dated, key=lambda pair: (*_date_sort_key(str(pair[1])), pair[0]))[1]


def _is_nested_block(element: lxml_html.HtmlElement, root: lxml_html.HtmlElement) -> bool:
    """Report whether a candidate block sits inside another candidate block."""
    parent = element.getparent()
    while parent is not None and parent is not root:
        if isinstance(parent.tag, str) and parent.tag in _STATUS_BLOCK_TAGS:
            return True
        parent = parent.getparent()
    return False


def _drawer_status(body: lxml_html.HtmlElement) -> tuple[str | None, list[dict[str, str | None]], str | None]:
    """Read a drawer's status from whole blocks only, never from nested fragments."""
    for element in body.iter():
        if not isinstance(element.tag, str) or element.tag not in _STATUS_BLOCK_TAGS:
            continue
        if _is_nested_block(element, body):
            continue
        text = clean_text(element.text_content())
        if not text:
            continue
        events = _parse_status_events(text)
        if events:
            return text, events, _latest_status_date(events)
    return None, [], None


def _drawer_subtitle(body: lxml_html.HtmlElement) -> str | None:
    for element in body.iter("h4"):
        text = clean_text(element.text_content())
        if not text or _normalized(text) in _NON_SUBTITLE_HEADINGS or _parse_status_events(text):
            continue
        return text
    return None


def _source_links(wrapper: lxml_html.HtmlElement, *, page_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in wrapper.iter("a"):
        href = (anchor.get("href") or "").strip()
        if not href or href.lower().startswith(_SKIPPED_LINK_SCHEMES):
            continue
        url = urljoin(page_url, href)
        if url in seen:
            continue
        seen.add(url)
        links.append({"text": clean_text(anchor.text_content()), "url": url})
    return links


def _full_guideline_url(links: list[dict[str, str]]) -> str | None:
    return next((link["url"] for link in links if _FULL_SOURCE_LINK_RE.search(link["text"])), None)


def _drawer_anchor(wrapper: lxml_html.HtmlElement, header: lxml_html.HtmlElement) -> str | None:
    """Return a stable in-page anchor for a drawer, or None when the page has none."""
    parent = wrapper.getparent()
    container = parent if parent is not None and _is_drawer_container(parent) else None
    for element in (header, wrapper, container):
        if element is None:
            continue
        anchor = (element.get("id") or "").strip()
        if anchor:
            return anchor
    return None


def _is_drawer_container(element: lxml_html.HtmlElement) -> bool:
    return _has_class(element, "drawer") or _has_class(element, "standalone-drawer")


def _slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).lower()
    text = _SLUG_SEPARATOR_RE.sub("-", text)
    text = _SLUG_DROP_RE.sub("", text)
    return _SLUG_COLLAPSE_RE.sub("-", text).strip("-")


def _topic_page_slug(page_url: str) -> str:
    """Slugify the topic page path below /clinical-insights/.

    The whole sub-path is used, not only the last segment, so two topics that
    share a leaf name under different categories keep distinct document ids.
    """
    path = urlparse(page_url).path
    tail = path[len(CLINICAL_INSIGHTS_PATH) :] if path.startswith(CLINICAL_INSIGHTS_PATH) else path
    return _slug(tail) or "topic"


def _drawer_document(
    wrapper: lxml_html.HtmlElement,
    *,
    topic: str,
    category: str,
    page_url: str,
    link_mode: LinkMode,
) -> ScrapedDocument | None:
    header = _drawer_header(wrapper)
    if header is None:
        return None
    title = clean_text(header.text_content())
    if not title:
        return None

    body = deepcopy(wrapper)
    body_header = _drawer_header(body)
    if body_header is not None:
        _drop_element(body_header)
    body_markdown = html_to_markdown(
        lxml_html.tostring(body, encoding="unicode"),
        link_mode=link_mode,
        base_url=page_url,
    )
    if not body_markdown:
        return None

    status_label, status_events, status_date = _drawer_status(body)
    subtitle = _drawer_subtitle(body)
    links = _source_links(wrapper, page_url=page_url)
    anchor = _drawer_anchor(wrapper, header)
    metadata: dict[str, Any] = {
        "topic": topic,
        "category": category,
        "recommendation_subtitle": subtitle,
        "status_label": status_label,
        "status_events": status_events,
        "status_date": status_date,
        "full_guideline_url": _full_guideline_url(links),
        "source_links": links,
        "topic_url": page_url,
        "anchor": anchor,
        "scrape_strategy": SCRAPE_STRATEGY,
    }
    return ScrapedDocument(
        source="aafp",
        external_id=f"aafp-{_topic_page_slug(page_url)}-{_slug(category)}-{_slug(title)}",
        title=title,
        url=f"{page_url}#{anchor}" if anchor else page_url,
        content=_document_markdown(title=title, topic=topic, category=category, body=body_markdown),
        metadata=metadata,
    )


def _document_markdown(*, title: str, topic: str, category: str, body: str) -> str:
    """Prefix a drawer's markdown with the context a retrieved document needs alone."""
    return "\n".join([f"# {title}", "", f"Topic: {topic}", f"Category: {category}", "", body]).strip()


__all__ = [
    "AAFP_DATASET_DISPLAY_NAME",
    "AAFP_DATASET_NAME",
    "AafpFetchError",
    "BASE_URL",
    "CLINICAL_INSIGHTS_PATH",
    "MAX_SITEMAPS",
    "MAX_SITEMAP_DEPTH",
    "PAGE_DELAY_SECONDS",
    "SCRAPE_STRATEGY",
    "SITEMAP_URL",
    "SitemapLinks",
    "discover_topic_urls",
    "is_topic_url",
    "parse_sitemap",
    "parse_topic_page",
    "scrape_aafp",
    "scrape_topic_page",
]
