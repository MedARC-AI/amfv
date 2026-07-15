"""Scrape RCH clinical practice guidelines into normalized markdown documents.

The Royal Children's Hospital Melbourne (RCH) publishes its clinical practice
guidelines as structured HTML pages. This module discovers guideline pages from
the A-Z index and extracts only the primary guideline and reference widgets.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace
from urllib.parse import unquote, urljoin, urlparse

import httpx
from lxml import html as lxml_html

from amfv_datasets.scraping.base import (
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    default_client,
    scrape_listing_documents,
)
from amfv_datasets.scraping.html import LinkMode, clean_text, document_title, html_to_markdown

BASE_URL = "https://www.rch.org.au"
GUIDELINE_INDEX_URL = f"{BASE_URL}/clinicalguide/guideline_index/"
RCH_DATASET_NAME = "rch-webscrape"
RCH_DATASET_DISPLAY_NAME = "RCH Clinical Practice Guidelines Webscrape"
DOCUMENT_DELAY_SECONDS = 10.0
_LOGGER = logging.getLogger(__name__)

_NON_GUIDELINE_SLUGS = {
    "CPG_Committee_Calendar",
    "Fractures",
    "Immigrant_health_resources",
}

_GUIDELINE_PATH_RE = re.compile(
    r"^/clinicalguide/guideline_index/(?P<slug>.+?)/?$",
    re.IGNORECASE,
)
_DIRECT_GUIDELINE_PATHS = {
    "/persistent_nasal_discharge_rhinosinusitis/.aspx": (
        "Persistent_nasal_discharge_rhinosinusitis",
        "/Persistent_nasal_discharge_rhinosinusitis/.aspx",
    ),
}
_SEE_ALIAS_SUFFIX_RE = re.compile(r"\s+\(see\b.*\)$", re.IGNORECASE)
_LAST_UPDATED_RE = re.compile(r"\bLast updated\s+([^\n]+)", re.IGNORECASE)
_EMPTY_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s*$", re.MULTILINE)
_EMPTY_EMPHASIS_RE = re.compile(r"(?<!\*)\*{4}(?!\*)")


class RchFetchError(ScrapeError):
    """Raised when an RCH guideline cannot be discovered or parsed."""


@dataclass(frozen=True)
class RchGuidelineRef:
    """A clinical practice guideline discovered from the RCH A-Z index."""

    slug: str
    title: str
    page_url: str
    aliases: tuple[str, ...] = ()


def guideline_ref_from_url(url: str, *, title: str | None = None) -> RchGuidelineRef:
    """Parse an RCH guideline URL into a canonical guideline reference.

    Args:
        url: RCH clinical guideline URL to parse.
        title: Optional title discovered from the A-Z index (default: None).
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "www.rch.org.au",
        "rch.org.au",
    }:
        raise RchFetchError(f"Enter an RCH clinical guideline URL from rch.org.au; got {url!r}")

    path = unquote(parsed.path)
    match = _GUIDELINE_PATH_RE.match(path)
    if not match:
        direct_guideline = _DIRECT_GUIDELINE_PATHS.get(path.rstrip("/").casefold())
        if direct_guideline is not None:
            slug, canonical_path = direct_guideline
            return RchGuidelineRef(
                slug=slug,
                title=title or slug.replace("_", " "),
                page_url=f"{BASE_URL}{canonical_path}",
            )
        raise RchFetchError(
            f"Enter a URL like https://www.rch.org.au/clinicalguide/guideline_index/Acute_asthma/; got {url!r}"
        )

    slug = match.group("slug")
    if slug.lower().endswith((".pdf", ".doc", ".docx")):
        raise RchFetchError(f"Enter an RCH HTML clinical guideline URL, not a downloadable file; got {url!r}")
    trailing_slash = "" if slug.lower().endswith(".aspx") else "/"
    page_url = f"{BASE_URL}/clinicalguide/guideline_index/{slug}{trailing_slash}"
    return RchGuidelineRef(slug=slug, title=title or slug.replace("_", " "), page_url=page_url)


def list_guidelines(client: httpx.Client) -> list[RchGuidelineRef]:
    """Return unique guideline references from the RCH A-Z index.

    Args:
        client: HTTP client used to fetch the guideline index.
    """
    response = client.get(GUIDELINE_INDEX_URL)
    response.raise_for_status()
    doc = lxml_html.fromstring(response.text)
    anchors = doc.xpath(
        "//div[@id='tabnav-letter-blocks' or "
        "contains(concat(' ', normalize-space(@class), ' '), ' tabnav-letter-blocks ')]//a[@href]"
    )

    refs_by_url: dict[str, RchGuidelineRef] = {}
    for anchor in anchors:
        href = anchor.get("href")
        title = clean_text(anchor.text_content(), drop_numeric_citations=False)
        if not href or not title:
            continue
        title = _SEE_ALIAS_SUFFIX_RE.sub("", title)
        try:
            ref = guideline_ref_from_url(urljoin(BASE_URL, href), title=title)
        except RchFetchError:
            continue
        if ref.slug in _NON_GUIDELINE_SLUGS:
            continue
        url_key = ref.page_url.casefold()
        if existing := refs_by_url.get(url_key):
            if title != existing.title and title not in existing.aliases:
                refs_by_url[url_key] = replace(existing, aliases=(*existing.aliases, title))
            continue
        refs_by_url[url_key] = ref
    refs = list(refs_by_url.values())
    if not refs:
        raise RchFetchError("Could not find clinical guideline links in the RCH A-Z index")
    return refs


def _content_widgets(html_text: str) -> list[lxml_html.HtmlElement]:
    doc = lxml_html.fromstring(html_text)
    widgets = doc.xpath(
        "//div[@id='rch-primary' or "
        "contains(concat(' ', normalize-space(@class), ' '), ' rch-primary ')]"
        "//div[contains(concat(' ', normalize-space(@class), ' '), ' widgetBody ')]"
    )
    return [widget for widget in widgets if _is_guideline_widget(widget)]


def _is_guideline_widget(widget: lxml_html.HtmlElement) -> bool:
    if widget.xpath(".//*[self::h2 or self::h3 or self::h4 or self::h5 or self::h6]"):
        return True
    text_length = len(clean_text(widget.text_content(), drop_numeric_citations=False))
    return text_length >= 500 or bool(widget.xpath(".//table")) and text_length >= 200


def _normalize_widget(widget: lxml_html.HtmlElement) -> None:
    for element in widget.xpath(".//script | .//style"):
        element.drop_tree()

    for nested in widget.xpath(".//strong//strong | .//em//em"):
        nested.drop_tag()

    for image in widget.xpath(".//img[not(@src) or not(normalize-space(@src))]"):
        image.drop_tree()

    for element, attribute in (
        *((link, "href") for link in widget.xpath(".//a[@href]")),
        *((image, "src") for image in widget.xpath(".//img[@src]")),
    ):
        value = element.get(attribute)
        parsed = urlparse(value)
        if parsed.netloc.lower() in {"rch.org.au", "www.rch.org.au", "webedit.rch.org.au"}:
            element.set(attribute, parsed._replace(scheme="https", netloc="www.rch.org.au").geturl())

    for emphasis in widget.xpath(".//strong | .//em | .//b | .//i"):
        if not clean_text(emphasis.text_content(), drop_numeric_citations=False) and not emphasis.xpath(".//img"):
            emphasis.drop_tree()

    nested_lists = widget.xpath(".//ul/ul | .//ul/ol | .//ol/ul | .//ol/ol")
    for nested_list in nested_lists:
        previous = nested_list.getprevious()
        if previous is not None and previous.tag.lower() == "li":
            previous.append(nested_list)

    for heading in widget.xpath(".//*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6]"):
        for emphasis in heading.xpath(".//strong | .//em | .//b | .//i"):
            emphasis.drop_tag()
        if clean_text(heading.text_content(), drop_numeric_citations=False):
            continue
        if heading.xpath(".//img"):
            heading.drop_tag()
        else:
            heading.drop_tree()


def build_guideline_text(
    html_text: str,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
    base_url: str = BASE_URL,
) -> tuple[str, int]:
    """Extract an RCH guideline page into markdown and a section count.

    Args:
        html_text: RCH guideline page HTML.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        base_url: Document URL used to resolve relative links and fragments
            (default: BASE_URL).
    """
    widgets = _content_widgets(html_text)
    if not widgets:
        raise RchFetchError("No readable clinical guideline content found on the RCH page")

    sections: list[str] = []
    for widget in widgets:
        _normalize_widget(widget)
        markdown = html_to_markdown(
            lxml_html.tostring(widget, encoding="unicode"),
            link_mode=link_mode,
            base_url=base_url,
        )
        markdown = _EMPTY_MARKDOWN_HEADING_RE.sub("", markdown)
        markdown = _EMPTY_EMPHASIS_RE.sub("", markdown).strip()
        if markdown:
            sections.append(markdown)
    if not sections:
        raise RchFetchError("No readable clinical guideline content found on the RCH page")
    return "\n\n".join(sections), len(sections)


def scrape_guideline(
    client: httpx.Client,
    ref: RchGuidelineRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> ScrapedDocument:
    """Scrape one RCH guideline into a normalized document.

    Args:
        client: HTTP client used to fetch the guideline page.
        ref: RCH guideline reference to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    response = client.get(ref.page_url)
    response.raise_for_status()
    content_url = ref.page_url
    metadata = {"slug": ref.slug}
    if ref.aliases:
        metadata["aliases"] = list(ref.aliases)
    try:
        content, section_count = build_guideline_text(
            response.text,
            link_mode=link_mode,
            base_url=content_url,
        )
    except RchFetchError:
        content_url = _linked_content_url(response.text, source_url=ref.page_url)
        if content_url is None:
            raise
        response = client.get(content_url)
        response.raise_for_status()
        content, section_count = build_guideline_text(
            response.text,
            link_mode=link_mode,
            base_url=content_url,
        )
        metadata["index_url"] = ref.page_url
    title = document_title(response.text, fallback=ref.title)
    last_updated_match = _LAST_UPDATED_RE.search(content)
    if last_updated_match:
        metadata["last_updated"] = last_updated_match.group(1).strip()
    return ScrapedDocument(
        source="rch",
        external_id=f"rch-{_external_id_slug(ref.slug)}",
        title=title,
        url=content_url,
        content=content,
        section_count=section_count,
        metadata=metadata,
    )


def _linked_content_url(html_text: str, *, source_url: str) -> str | None:
    doc = lxml_html.fromstring(html_text)
    hrefs = doc.xpath(
        "//div[@id='rch-primary']//div["
        "contains(concat(' ', normalize-space(@class), ' '), ' widgetBody ')"
        "]//a[@href]/@href"
    )
    candidates: list[str] = []
    for href in hrefs:
        target = urljoin(source_url, href)
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
            "rch.org.au",
            "www.rch.org.au",
        }:
            continue
        normalized = target.split("#", maxsplit=1)[0]
        if normalized != source_url and normalized not in candidates:
            candidates.append(normalized)
    return candidates[0] if len(candidates) == 1 else None


def _external_id_slug(slug: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")


def scrape_rch(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
    skip_urls: Collection[str] = (),
) -> ScrapeRun:
    """Scrape RCH guidelines from a URL or the A-Z index.

    Args:
        documents: Number of documents to scrape. Ignored when `url` is set.
            When unset, every guideline in the A-Z index is scraped (default:
            None).
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        url: RCH guideline URL to scrape as a single document (default: None).
        skip_urls: Canonical document URLs already scraped by a previous run
            (default: ()).
    """
    if url is not None:

        def scrape_url() -> Iterable[ScrapedDocument]:
            with default_client() as client:
                yield scrape_guideline(client, guideline_ref_from_url(url), link_mode=link_mode)

        return ScrapeRun(documents=scrape_url(), total=1)

    with default_client() as client:
        refs = list_guidelines(client)
    refs = [ref for ref in refs if ref.page_url not in skip_urls]
    total = len(refs) if documents is None else min(documents, len(refs))
    return ScrapeRun(
        total=total,
        documents=scrape_listing_documents(
            documents=documents,
            client_factory=default_client,
            first_page_items=refs,
            list_page=lambda _client, _page: (),
            scrape_item=lambda client, ref: _scrape_or_skip_guideline(client, ref, link_mode=link_mode),
            document_delay_seconds=DOCUMENT_DELAY_SECONDS,
        ),
    )


def _scrape_or_skip_guideline(
    client: httpx.Client,
    ref: RchGuidelineRef,
    *,
    link_mode: LinkMode,
) -> ScrapedDocument | None:
    try:
        return scrape_guideline(client, ref, link_mode=link_mode)
    except RchFetchError as exc:
        _LOGGER.warning("Skipping unsupported RCH index entry %s: %s", ref.page_url, exc)
        return None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in {404, 410}:
            raise
        _LOGGER.warning(
            "Skipping stale RCH index entry %s: HTTP %s",
            ref.page_url,
            exc.response.status_code,
        )
        return None


__all__ = [
    "BASE_URL",
    "DOCUMENT_DELAY_SECONDS",
    "GUIDELINE_INDEX_URL",
    "RCH_DATASET_DISPLAY_NAME",
    "RCH_DATASET_NAME",
    "RchFetchError",
    "RchGuidelineRef",
    "build_guideline_text",
    "guideline_ref_from_url",
    "list_guidelines",
    "scrape_guideline",
    "scrape_rch",
]
