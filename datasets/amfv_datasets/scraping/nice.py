"""Scrape NICE guidance into normalized markdown documents.

NICE (National Institute for Health and Care Excellence) publishes its guidance
as structured HTML chapters. The helpers here discover published NICE guideline
references, scrape chapter HTML into clean markdown, and return normalized
documents that can be used by dataset builders.

We deliberately scrape the HTML chapter pages rather than downloadable PDFs:
PDF text extraction loses reading order and interleaves page headers/footers,
whereas the HTML carries the semantic document structure directly.

Attribution:
The HTML-scraping approach and chapter/section CSS selectors are adapted from
Meditron's NICE guideline scraper (epfLLM/meditron, gap-replay/guidelines):
https://github.com/epfLLM/meditron/tree/main/gap-replay/guidelines/scrapers/nice
Source license: Apache License 2.0.

Their scraper is a Puppeteer/TypeScript crawler; this is a Python httpx + lxml
port updated for the current NICE website markup.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

import httpx
from lxml import html as lxml_html

from amfv_datasets.scraping.base import (
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    default_client,
    scrape_listing_documents,
)
from amfv_datasets.scraping.html import LinkMode, absolute_unique_urls, document_title, html_to_markdown
from amfv_datasets.scraping.nextjs import script_json_by_id

BASE_URL = "https://www.nice.org.uk"
NICE_DATASET_NAME = "nice-webscrape"
NICE_DATASET_DISPLAY_NAME = "NICE Webscrape"
DOCUMENT_DELAY_SECONDS = 5.0

logger = logging.getLogger(__name__)

_NICE_PATH_RE = re.compile(r"^/(?P<section>guidance|advice)/(?P<slug>[a-z]+\d+)(?:/|$)", re.IGNORECASE)
_REF_PREFIX_RE = re.compile(r"^[A-Za-z]+")
_GUIDANCE_PREFIXES = {"AMR", "CG", "HST", "HTG", "MPG", "NG", "PH", "SC", "SG", "TA"}
_QUALITY_STANDARD_PREFIXES = {"QS"}
_ADVICE_PREFIXES = {"ES", "ESMPB", "ESNM", "ESUOM", "MIB", "NR"}
_SUPPORTED_PREFIXES = _GUIDANCE_PREFIXES | _QUALITY_STANDARD_PREFIXES | _ADVICE_PREFIXES
_UNSUPPORTED_PREFIXES = {"CSG"}
_SKIP_OVERVIEW_HEADINGS = {
    "commercial arrangement",
    "endorsing bodies",
    "guidance development process",
    "guideline development process",
    "how we develop nice guidelines",
    "how to use nice quality standards and how we develop them",
    "supporting organisations",
    "your responsibility",
}
_SKIP_OVERVIEW_TEXT_PREFIXES = (
    "how we prioritise updating our guidance",
    "decisions about updating our guidance",
    "for information about individual topics",
    "nice has developed tools and resources",
    "this guidance is part of a project with nhs england",
    "we tested a bespoke process",
)
_NUMBERED_HEADING_RE = re.compile(r"^\s*\d+(?:\.\d+)*\s+")
_SKIP_CHAPTER_SUFFIXES = ("finding-more-information-and-committee-details",)
_NICE_HOSTS = ("nice.org.uk", "www.nice.org.uk")


class _NiceScrapeStrategy(StrEnum):
    CHAPTER = "chapter"
    QUALITY_STANDARD = "quality_standard"
    ADVICE = "advice"


class NiceFetchError(ScrapeError):
    """Raised when a recommendation cannot be sourced from NICE."""


@dataclass(frozen=True)
class GuidanceRef:
    """A published NICE guidance reference from the listing."""

    ref: str
    slug: str
    title: str
    page_url: str


@dataclass(frozen=True)
class GuidanceListingPage:
    """A NICE published-guidance listing page.

    `total` is NICE's source-reported count before AMFV filters unsupported
    guidance types. It is not an exact count of eligible or emitted documents.
    """

    refs: list[GuidanceRef]
    total: int | None


@dataclass(frozen=True)
class OmittedSection:
    """A discovered source section intentionally omitted from the document."""

    url: str
    reason: str


@dataclass(frozen=True)
class GuidelineExtractionReceipt:
    """Auditable result of extracting overview/chapter source sections."""

    content: str
    section_count: int
    title: str
    discovered_count: int
    retained_urls: tuple[str, ...]
    omitted_sections: tuple[OmittedSection, ...]
    transformations: tuple[str, ...]

    # lean-spec: AMFV.Scraping.ExtractionReceipt.accounted_receipt_matches_disposition_count
    def is_accounted(self) -> bool:
        """Return whether disposition cardinalities match the discovered count."""
        return self.discovered_count == len(self.retained_urls) + len(self.omitted_sections)


@dataclass(frozen=True)
class _ChapterLinkDiscovery:
    accepted: tuple[str, ...]
    rejected: tuple[OmittedSection, ...]


def _listing_url(*, page: int) -> str:
    return f"{BASE_URL}/guidance/published?sp=on&pa={page}"


def guidance_ref_from_url(url: str) -> GuidanceRef:
    """Parse a NICE guidance URL into a canonical guidance reference.

    Args:
        url: NICE guidance URL to parse.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "www.nice.org.uk",
        "nice.org.uk",
    }:
        raise NiceFetchError(f"Enter a NICE guidance URL from nice.org.uk; got {url!r}")

    match = _NICE_PATH_RE.match(parsed.path.rstrip("/") + "/")
    if not match:
        raise NiceFetchError(f"Enter a URL like https://www.nice.org.uk/guidance/ng123 or /advice/mib123; got {url!r}")

    slug = match.group("slug").lower()
    ref = slug.upper()
    return GuidanceRef(ref=ref, slug=slug, title=ref, page_url=_page_url(ref=ref, slug=slug))


def _parse_listing(html_text: str) -> GuidanceListingPage:
    """Parse a published-guidance listing page into a list of guidance references."""
    try:
        results = script_json_by_id(html_text, "__NEXT_DATA__")["props"]["pageProps"]["results"]
    except KeyError as exc:
        raise NiceFetchError("Could not parse NICE listing JSON") from exc

    refs: list[GuidanceRef] = []
    for doc in results.get("documents", []):
        ref = (doc.get("guidanceRef") or "").strip()
        if not ref:
            continue
        ref = ref.upper()
        prefix = _ref_prefix(ref)
        if prefix in _UNSUPPORTED_PREFIXES or prefix not in _SUPPORTED_PREFIXES:
            logger.debug("Skipping unsupported NICE guidance type %s for %s", prefix, ref)
            continue
        slug = ref.lower()
        refs.append(
            GuidanceRef(
                ref=ref,
                slug=slug,
                title=(doc.get("title") or ref).strip(),
                page_url=_page_url(ref=ref, slug=slug),
            )
        )
    total = results.get("resultCount")
    return GuidanceListingPage(refs=refs, total=total if isinstance(total, int) else None)


def list_published_guidance(client: httpx.Client, page: int = 1) -> GuidanceListingPage:
    """Return one page of published guideline refs.

    Args:
        client: HTTP client used to fetch the NICE listing page.
        page: Published-guidance listing page number (default: 1).
    """
    response = client.get(_listing_url(page=page))
    response.raise_for_status()
    return _parse_listing(response.text)


# lean-spec: AMFV.Scraping.UrlPolicy.accepted_chapter_respects_source_boundary
def _chapter_links(html_text: str, slug: str) -> _ChapterLinkDiscovery:
    """Return accepted and rejected chapter URLs from a table of contents."""
    chapter_path_match = f"contains(@href, '/guidance/{slug}/chapter/') or contains(@href, '/advice/{slug}/chapter/')"
    nav_xpaths = (
        f"//*[contains(concat(' ', normalize-space(@class), ' '), ' stacked-nav ')]//a[{chapter_path_match}]/@href",
        f"//ul[contains(concat(' ', normalize-space(@class), ' '), ' nav-list ')]//li//a[{chapter_path_match}]/@href",
    )
    doc = lxml_html.fromstring(html_text)
    rejected: list[OmittedSection] = []
    for xpath in nav_xpaths:
        raw_urls = doc.xpath(xpath)
        if not raw_urls:
            continue
        authority_accepted = absolute_unique_urls(raw_urls, base_url=BASE_URL, allowed_hosts=_NICE_HOSTS)
        accepted = [url for url in authority_accepted if _is_scoped_chapter_url(url, slug)]
        for raw_url in raw_urls:
            normalized = absolute_unique_urls([raw_url], base_url=BASE_URL, allowed_hosts=_NICE_HOSTS)
            if normalized and _is_scoped_chapter_url(normalized[0], slug):
                continue
            normalized_rejection = absolute_unique_urls([raw_url], base_url=BASE_URL)
            rejected_url = normalized_rejection[0] if normalized_rejection else raw_url
            reason = "out_of_scope_url" if normalized else "unsafe_url"
            rejected.append(OmittedSection(url=rejected_url, reason=reason))
        if accepted:
            return _ChapterLinkDiscovery(accepted=tuple(accepted), rejected=tuple(dict.fromkeys(rejected)))
    return _ChapterLinkDiscovery(accepted=(), rejected=tuple(dict.fromkeys(rejected)))


def _is_scoped_chapter_url(url: str, slug: str) -> bool:
    path = urlparse(url).path.lower()
    return path.startswith(f"/guidance/{slug}/chapter/") or path.startswith(f"/advice/{slug}/chapter/")


def _overview_markdown(html_text: str, *, ref: GuidanceRef, link_mode: LinkMode) -> str:
    """Convert useful NICE overview content into markdown."""
    if _ref_prefix(ref.ref) in {"ES", "MIB"}:
        return ""

    doc = lxml_html.fromstring(html_text)
    overview_blocks = doc.xpath("//h2[normalize-space()='Overview']/following-sibling::*[1]")
    if not overview_blocks:
        return ""

    kept_children: list[lxml_html.HtmlElement] = []
    skip_until_heading_level: int | None = None
    for child in overview_blocks[0]:
        tag = child.tag.lower()
        heading = child.text_content().strip().lower()
        heading_level = _heading_level(tag)
        if skip_until_heading_level is not None:
            if heading_level is not None and heading_level <= skip_until_heading_level:
                skip_until_heading_level = None
            else:
                continue
        if heading in _SKIP_OVERVIEW_HEADINGS:
            break
        if heading == "recommendations" and heading_level is not None:
            skip_until_heading_level = heading_level
            continue
        if tag == "div" and "panel" in (child.get("class") or "").split():
            break
        if _is_skipped_overview_child(child):
            continue
        kept_children.append(child)

    if not kept_children:
        return ""
    fragment = (
        "<div><h2>Overview</h2>"
        + "".join(lxml_html.tostring(child, encoding="unicode") for child in kept_children)
        + "</div>"
    )
    return html_to_markdown(fragment, link_mode=link_mode, base_url=BASE_URL)


def _heading_level(tag: str) -> int | None:
    if len(tag) == 2 and tag.startswith("h") and tag[1].isdigit():
        return int(tag[1])
    return None


def _is_skipped_overview_child(child: lxml_html.HtmlElement) -> bool:
    text = " ".join(child.text_content().strip().lower().split())
    return any(text.startswith(prefix) for prefix in _SKIP_OVERVIEW_TEXT_PREFIXES)


def _chapter_markdown(html_text: str, *, link_mode: LinkMode) -> str:
    """Convert a NICE chapter page's ``div.chapter`` content into markdown."""
    doc = lxml_html.fromstring(html_text)
    chapters = doc.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' chapter ')]")
    if not chapters:
        return ""
    _normalize_top_level_chapter_headings(chapters[0])
    chapter_html = lxml_html.tostring(chapters[0], encoding="unicode")
    return html_to_markdown(chapter_html, link_mode=link_mode, base_url=BASE_URL)


def _normalize_top_level_chapter_headings(chapter: lxml_html.HtmlElement) -> None:
    for heading in chapter.xpath("./*[self::h1 or self::h2]"):
        text = heading.text_content()
        normalized = _NUMBERED_HEADING_RE.sub("", text).strip()
        if normalized and normalized != text:
            heading.clear()
            heading.text = normalized


def _is_skipped_chapter(url: str) -> bool:
    tail = url.rstrip("/").rsplit("/", 1)[-1].lower()
    return any(tail.endswith(suffix) for suffix in _SKIP_CHAPTER_SUFFIXES)


def build_guideline_text(
    client: httpx.Client,
    ref: GuidanceRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> tuple[str, int, str]:
    """Scrape a guideline's chapters into markdown text, section count, and title.

    Args:
        client: HTTP client used to fetch the overview and chapter pages.
        ref: NICE guidance reference to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    receipt = _build_guideline_receipt(client, ref, link_mode=link_mode)
    return receipt.content, receipt.section_count, receipt.title


def _build_guideline_receipt(
    client: httpx.Client,
    ref: GuidanceRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> GuidelineExtractionReceipt:
    """Scrape a guideline and account for retained and omitted sections."""
    page_url = _validated_page_url(ref)
    overview = client.get(page_url)
    overview.raise_for_status()
    title = (
        ref.title
        if ref.title != ref.ref
        else document_title(
            overview.text,
            fallback=ref.ref,
            suffixes=(" | Guidance | NICE", " | Advice | NICE"),
        )
    )
    chapter_links = _chapter_links(overview.text, ref.slug)
    if not chapter_links.accepted:
        raise NiceFetchError(f"No chapters found for guidance '{ref.ref}'")

    sections: list[str] = []
    retained_urls: list[str] = []
    omitted_sections = list(chapter_links.rejected)
    overview_markdown = _overview_markdown(overview.text, ref=ref, link_mode=link_mode)
    if overview_markdown:
        sections.append(overview_markdown)
        retained_urls.append(page_url)
    else:
        overview_reason = (
            "overview_omitted_by_source_policy" if _ref_prefix(ref.ref) in {"ES", "MIB"} else "empty_overview"
        )
        omitted_sections.append(OmittedSection(url=page_url, reason=overview_reason))

    for url in chapter_links.accepted:
        if _is_skipped_chapter(url):
            omitted_sections.append(OmittedSection(url=url, reason="non_content_chapter"))
            continue
        chapter = client.get(url)
        chapter.raise_for_status()
        markdown = _chapter_markdown(chapter.text, link_mode=link_mode)
        if markdown:
            sections.append(markdown)
            retained_urls.append(url)
        else:
            omitted_sections.append(OmittedSection(url=url, reason="empty_chapter"))

    content = "\n\n".join(sections).strip()
    if not content:
        raise NiceFetchError(f"No readable content for guidance '{ref.ref}'")
    transformations = [
        "canonicalize_chapter_urls",
        "convert_html_to_markdown",
        "drop_numeric_citation_markers",
        "filter_overview_boilerplate",
        "normalize_numbered_headings",
        "normalize_whitespace",
    ]
    if link_mode is LinkMode.STRIP:
        transformations.append("strip_links")
    receipt = GuidelineExtractionReceipt(
        content=content,
        section_count=len(sections),
        title=title,
        discovered_count=1 + len(chapter_links.accepted) + len(chapter_links.rejected),
        retained_urls=tuple(retained_urls),
        omitted_sections=tuple(omitted_sections),
        transformations=tuple(transformations),
    )
    if not receipt.is_accounted():
        raise AssertionError(f"Incomplete extraction receipt for guidance '{ref.ref}'")
    return receipt


def scrape_guideline(
    client: httpx.Client,
    ref: GuidanceRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> ScrapedDocument:
    """Scrape a NICE guideline into a normalized document.

    Args:
        client: HTTP client used to fetch the overview and chapter pages.
        ref: NICE guidance reference to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    receipt = _build_guideline_receipt(client, ref, link_mode=link_mode)
    return ScrapedDocument(
        source="nice",
        external_id=f"nice-{ref.slug}",
        title=receipt.title,
        url=_validated_page_url(ref),
        content=receipt.content,
        section_count=receipt.section_count,
        metadata={
            "ref": ref.ref,
            "slug": ref.slug,
            "prefix": _ref_prefix(ref.ref),
            "scrape_strategy": _scrape_strategy(ref),
            "retained_urls": list(receipt.retained_urls),
            "omitted_sections": [
                {"url": omitted.url, "reason": omitted.reason} for omitted in receipt.omitted_sections
            ],
            "transformations": list(receipt.transformations),
            "discovered_section_count": receipt.discovered_count,
        },
    )


def _ref_prefix(ref: str) -> str:
    match = _REF_PREFIX_RE.match(ref)
    return match.group(0).upper() if match else ""


def _page_path(*, ref: str, slug: str) -> str:
    return f"/advice/{slug}" if _ref_prefix(ref) in _ADVICE_PREFIXES else f"/guidance/{slug}"


def _page_url(*, ref: str, slug: str) -> str:
    return f"{BASE_URL}{_page_path(ref=ref, slug=slug)}"


def _validated_page_url(ref: GuidanceRef) -> str:
    validated = guidance_ref_from_url(ref.page_url)
    if validated.ref != ref.ref.upper() or validated.slug != ref.slug.lower():
        raise NiceFetchError(f"Guidance reference {ref.ref!r} does not match its NICE page URL {ref.page_url!r}")
    return validated.page_url


def _scrape_strategy(ref: GuidanceRef) -> str:
    prefix = _ref_prefix(ref.ref)
    if prefix in _ADVICE_PREFIXES:
        return _NiceScrapeStrategy.ADVICE.value
    if prefix in _QUALITY_STANDARD_PREFIXES:
        return _NiceScrapeStrategy.QUALITY_STANDARD.value
    return _NiceScrapeStrategy.CHAPTER.value


# lean-spec: AMFV.Scraping.ListingAccounting.source_total_is_not_exact_after_filtering
def scrape_nice(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
) -> ScrapeRun:
    """Scrape NICE documents from a URL or published-guidance listing pages.

    Args:
        documents: Number of documents to scrape. Ignored when `url` is set.
            When unset, NICE listing pages are fetched until a page returns no
            items (default: None).
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        url: NICE source URL to scrape as a single document (default: None).

    Listing runs report an indeterminate total because NICE's source count
    includes guidance types filtered out by AMFV. Single-URL runs report one.
    """
    if url is not None:

        def scrape_url() -> Iterable[ScrapedDocument]:
            with default_client() as client:
                yield scrape_guideline(client, guidance_ref_from_url(url), link_mode=link_mode)

        return ScrapeRun(documents=scrape_url(), total=1)

    with default_client() as client:
        first_page = list_published_guidance(client, page=1)
    return ScrapeRun(
        total=None,
        documents=scrape_listing_documents(
            documents=documents,
            client_factory=default_client,
            first_page_items=first_page.refs,
            list_page=lambda client, page: list_published_guidance(client, page).refs,
            scrape_item=lambda client, ref: scrape_guideline(client, ref, link_mode=link_mode),
            document_delay_seconds=DOCUMENT_DELAY_SECONDS,
        ),
    )


__all__ = [
    "BASE_URL",
    "DOCUMENT_DELAY_SECONDS",
    "GuidelineExtractionReceipt",
    "GuidanceListingPage",
    "NICE_DATASET_DISPLAY_NAME",
    "NICE_DATASET_NAME",
    "GuidanceRef",
    "NiceFetchError",
    "OmittedSection",
    "build_guideline_text",
    "guidance_ref_from_url",
    "list_published_guidance",
    "scrape_guideline",
    "scrape_nice",
]
