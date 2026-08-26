"""Scrape IDSA practice guidelines into normalized markdown documents."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from lxml import html as lxml_html

from amfv_datasets.scraping.base import (
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    default_client,
    scrape_listing_documents,
)
from amfv_datasets.scraping.html import LinkMode, absolute_unique_urls, clean_text, document_title, html_to_markdown

BASE_URL = "https://www.idsociety.org"
LISTING_URL = f"{BASE_URL}/practice-guideline/all-practice-guidelines"
DOCUMENT_DELAY_SECONDS = 5.0
_SHORT_CONTENT_CHARS = 10_000

_IDSA_PATH_RE = re.compile(r"^/practice-guideline/(?P<slug>[^/]+)(?:/|$)", re.IGNORECASE)
_BACK_TO_TOP_RE = re.compile(r"^\s*back to top\s*$", re.IGNORECASE)
_TABLE_OF_CONTENTS_RE = re.compile(r"^\s*table\s+of\s+contents\s*$", re.IGNORECASE)
_PDF_TEXT_RE = re.compile(r"\b(download\s+)?pdf\b", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


class IDSAFetchError(ScrapeError):
    """Raised when a practice guideline cannot be sourced from IDSA."""


@dataclass(frozen=True)
class IDSAGuidelineRef:
    """An IDSA practice guideline reference from the A-Z listing."""

    title: str
    slug: str
    page_url: str
    year: int | None
    statuses: tuple[str, ...]


@dataclass(frozen=True)
class IDSAGuidelineListingPage:
    """An IDSA practice guideline listing page."""

    refs: list[IDSAGuidelineRef]
    total: int | None


def idsa_ref_from_url(url: str) -> IDSAGuidelineRef:
    """Parse an IDSA practice guideline URL into a canonical guideline reference.

    Args:
        url: IDSA practice guideline URL to parse.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "www.idsociety.org",
        "idsociety.org",
    }:
        raise IDSAFetchError(f"Enter an IDSA practice guideline URL from idsociety.org; got {url!r}")

    match = _IDSA_PATH_RE.match(parsed.path.rstrip("/") + "/")
    if not match:
        raise IDSAFetchError(f"Enter a URL like https://www.idsociety.org/practice-guideline/example/; got {url!r}")

    slug = match.group("slug").lower()
    return IDSAGuidelineRef(
        title=slug.replace("-", " ").title(),
        slug=slug,
        page_url=_page_url(slug),
        year=None,
        statuses=(),
    )


def list_practice_guidelines(
    client: httpx.Client,
) -> IDSAGuidelineListingPage:
    """Return IDSA practice guideline refs from the A-Z listing.

    Args:
        client: HTTP client used to fetch the listing page.
    """
    response = client.get(LISTING_URL)
    response.raise_for_status()
    refs = _parse_listing(response.text)
    return IDSAGuidelineListingPage(refs=refs, total=len(refs))


def _build_guideline_text(
    client: httpx.Client,
    ref: IDSAGuidelineRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> tuple[str, str, dict[str, list[str]], tuple[str, ...]]:
    response = client.get(ref.page_url)
    response.raise_for_status()
    title = document_title(response.text, fallback=ref.title)
    statuses = tuple(dict.fromkeys((*ref.statuses, *_page_statuses(response.text))))
    content_html = _guideline_content_html(response.text)
    content = html_to_markdown(content_html, link_mode=link_mode, base_url=BASE_URL)
    if not content:
        raise IDSAFetchError(f"No readable content for IDSA guideline '{ref.slug}'")
    return content, title, _links_metadata(content_html), statuses


def scrape_guideline(
    client: httpx.Client,
    ref: IDSAGuidelineRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> ScrapedDocument:
    """Scrape an IDSA practice guideline into a normalized document.

    Args:
        client: HTTP client used to fetch the guideline page.
        ref: IDSA guideline reference to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    content, title, links_metadata, statuses = _build_guideline_text(
        client,
        ref,
        link_mode=link_mode,
    )
    return ScrapedDocument(
        source="idsa",
        external_id=f"idsa-{ref.slug}",
        title=title,
        url=ref.page_url,
        content=content,
        metadata={
            "year": ref.year,
            "statuses": list(statuses),
            "slug": ref.slug,
            "listing_url": LISTING_URL,
            "content_length_chars": len(content),
            "quality_flags": _quality_flags(content, statuses=statuses),
            **links_metadata,
        },
    )


def scrape_idsa(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
) -> ScrapeRun:
    """Scrape IDSA practice guidelines from a URL or the A-Z listing.

    Args:
        documents: Number of documents to scrape. Ignored when `url` is set.
            When unset, every included IDSA listing item is scraped (default:
            None).
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        url: IDSA source URL to scrape as a single document (default: None).
    """
    if url is not None:

        def scrape_url() -> Iterable[ScrapedDocument]:
            with default_client() as client:
                yield scrape_guideline(client, idsa_ref_from_url(url), link_mode=link_mode)

        return ScrapeRun(documents=scrape_url(), total=1)

    with default_client() as client:
        listing = list_practice_guidelines(client)
    total = listing.total if documents is None or listing.total is None else min(documents, listing.total)
    return ScrapeRun(
        total=total,
        documents=scrape_listing_documents(
            documents=documents,
            client_factory=default_client,
            first_page_items=listing.refs,
            list_page=lambda _client, _page: (),
            scrape_item=lambda client, ref: scrape_guideline(client, ref, link_mode=link_mode),
            document_delay_seconds=DOCUMENT_DELAY_SECONDS,
        ),
    )


def _parse_listing(
    html_text: str,
) -> list[IDSAGuidelineRef]:
    doc = lxml_html.fromstring(html_text)
    items = doc.xpath(
        "//div[contains(concat(' ', normalize-space(@class), ' '), ' alpha-listing ')]"
        "//li[.//a[contains(concat(' ', normalize-space(@class), ' '), ' list-pages__link ')]]"
    )
    refs: list[IDSAGuidelineRef] = []
    for item in items:
        link = item.xpath(".//a[contains(concat(' ', normalize-space(@class), ' '), ' list-pages__link ')][1]")
        if not link:
            continue
        statuses = tuple(
            clean_text(status, drop_numeric_citations=False)
            for status in item.xpath(
                ".//*[contains(concat(' ', normalize-space(@class), ' '), ' category-dot ')]/text()"
            )
        )
        if not _included_statuses(statuses):
            continue
        href = link[0].get("href")
        if not href:
            continue
        page_url = urljoin(BASE_URL, href).split("#")[0].split("?")[0]
        match = _IDSA_PATH_RE.match(urlparse(page_url).path.rstrip("/") + "/")
        if not match:
            continue
        refs.append(
            IDSAGuidelineRef(
                title=clean_text(link[0].text_content(), drop_numeric_citations=False),
                slug=match.group("slug").lower(),
                page_url=page_url,
                year=_parse_year(item),
                statuses=statuses,
            )
        )
    return refs


def _included_statuses(
    statuses: tuple[str, ...],
) -> bool:
    has_current = "Current" in statuses
    has_archived = "Archived" in statuses
    has_in_development = "In Development" in statuses
    return not has_in_development and (has_current or has_archived)


def _parse_year(item: lxml_html.HtmlElement) -> int | None:
    values = item.xpath(".//*[contains(concat(' ', normalize-space(@class), ' '), ' list-pages__year ')]/text()")
    if not values:
        return None
    value = clean_text(values[0], drop_numeric_citations=False)
    return int(value) if value.isdigit() else None


def _guideline_content_html(html_text: str) -> str:
    doc = lxml_html.fromstring(html_text)
    candidates = doc.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' idsaPracticeGuidelinePage ')]")
    if not candidates:
        candidates = doc.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' body-container ')]")
    if not candidates:
        candidates = doc.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' standardpage-col-left ')]")
    if not candidates:
        raise IDSAFetchError("Page markup changed (no IDSA guideline content container)")
    content = candidates[-1]
    _remove_noise(content)
    content_html = lxml_html.tostring(content, encoding="unicode")
    if not clean_text(content.text_content(), drop_numeric_citations=False):
        raise IDSAFetchError("Page markup changed (empty IDSA guideline content container)")
    return content_html


def _page_statuses(html_text: str) -> tuple[str, ...]:
    doc = lxml_html.fromstring(html_text)
    values = doc.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' status-section ')]"
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' status ')]/text()"
    )
    statuses: list[str] = []
    for value in values:
        status = clean_text(value, drop_numeric_citations=False).title()
        if status:
            statuses.append(status)
    return tuple(statuses)


def _remove_noise(content: lxml_html.HtmlElement) -> None:
    noise_xpath = (
        ".//*[self::script or self::style or self::noscript or self::svg or self::button"
        " or contains(concat(' ', normalize-space(@class), ' '), ' table-of-contents ')"
        " or contains(concat(' ', normalize-space(@class), ' '), ' toc ')"
        " or contains(concat(' ', normalize-space(@class), ' '), ' TableOfContents ')"
        " or contains(concat(' ', normalize-space(@class), ' '), ' status-section ')"
        " or contains(concat(' ', normalize-space(@class), ' '), ' view-all-guidance ')"
        " or contains(concat(' ', normalize-space(@class), ' '), ' share ')"
        " or contains(concat(' ', normalize-space(@class), ' '), ' addthis ')"
        " or contains(concat(' ', normalize-space(@class), ' '), ' social ')]"
    )
    for element in content.xpath(noise_xpath):
        _drop_element(element)
    _remove_table_of_contents(content)
    _remove_empty_headings(content)
    for element in content.xpath(".//*[self::a or self::p or self::div or self::span]"):
        if _BACK_TO_TOP_RE.match(element.text_content()):
            _drop_element(element)


def _remove_table_of_contents(content: lxml_html.HtmlElement) -> None:
    headings = content.xpath(".//*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6]")
    for heading in headings:
        if not _TABLE_OF_CONTENTS_RE.match(heading.text_content()):
            continue
        for sibling in list(heading.itersiblings()):
            if _heading_level(sibling) is not None:
                break
            _drop_element(sibling)
        _drop_element(heading)


def _heading_level(element: lxml_html.HtmlElement) -> int | None:
    tag = element.tag.lower() if isinstance(element.tag, str) else ""
    if len(tag) == 2 and tag.startswith("h") and tag[1].isdigit():
        return int(tag[1])
    return None


def _remove_empty_headings(content: lxml_html.HtmlElement) -> None:
    headings = content.xpath(".//*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6]")
    for heading in headings:
        if not clean_text(heading.text_content(), drop_numeric_citations=False):
            _drop_element(heading)


def _drop_element(element: lxml_html.HtmlElement) -> None:
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


def _links_metadata(content_html: str) -> dict[str, list[str]]:
    doc = lxml_html.fromstring(content_html)
    external_links: list[str] = []
    pdf_links: list[str] = []
    for link in doc.xpath(".//a[@href]"):
        url = urljoin(BASE_URL, link.get("href")).split("#")[0].split("?")[0]
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc.lower() in {"www.idsociety.org", "idsociety.org"}:
            continue
        external_links.append(url)
        link_text = _WHITESPACE_RE.sub(" ", link.text_content()).strip()
        if url.lower().endswith(".pdf") or _PDF_TEXT_RE.search(link_text):
            pdf_links.append(url)
    return {
        "external_links": absolute_unique_urls(external_links, base_url=BASE_URL),
        "pdf_links": absolute_unique_urls(pdf_links, base_url=BASE_URL),
    }


def _quality_flags(content: str, *, statuses: tuple[str, ...]) -> list[str]:
    flags: list[str] = []
    if len(content) < _SHORT_CONTENT_CHARS:
        flags.append("short_content")
    if "Archived" in statuses:
        flags.append("outdated")
    return flags


def _page_url(slug: str) -> str:
    return f"{BASE_URL}/practice-guideline/{slug}/"


__all__ = [
    "BASE_URL",
    "DOCUMENT_DELAY_SECONDS",
    "IDSAFetchError",
    "IDSAGuidelineListingPage",
    "IDSAGuidelineRef",
    "LISTING_URL",
    "idsa_ref_from_url",
    "list_practice_guidelines",
    "scrape_guideline",
    "scrape_idsa",
]
