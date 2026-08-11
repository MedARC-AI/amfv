"""Scrape WHO guideline publications into normalized markdown documents.

WHO (World Health Organization) publishes guidelines primarily as PDFs hosted on
iris.who.int. The publication landing page carries only a short HTML Overview,
so the guideline body itself comes from the linked PDF, converted to markdown by
`amfv_datasets.scraping.pdf`. Each document is the Overview followed by the
converted guideline text; `metadata["content_scope"]` records whether the full
body was recovered.

Sampled WHO guideline PDFs are born-digital with a clean text layer, so
conversion runs without OCR.

Discovery uses WHO's Sitefinity OData publications hub API, filtered to the
Guidelines publishing office.

Attribution:
The publishing-office filter UUID and PDF-first approach in Meditron's WHO
scraper (epfLLM/meditron, gap-replay/guidelines/scrapers/scrapers.py) informed
discovery scope. Source license: Apache License 2.0.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
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
from amfv_datasets.scraping.html import LinkMode, clean_text, document_title, html_to_markdown
from amfv_datasets.scraping.pdf import (
    PdfBackend,
    PdfConversionError,
    count_markdown_sections,
    pdf_to_markdown,
)

BASE_URL = "https://www.who.int"
GUIDELINES_LISTING_URL = f"{BASE_URL}/publications/who-guidelines"
GUIDELINES_PUBLISHING_OFFICE = "c09761c0-ab8e-4cfa-9744-99509c4d306b"
SF_SITE = "15210d59-ad60-47ff-a542-7ed76645f0c7"
PUBLICATIONS_API_PATH = "/api/hubs/publications"
LISTING_PAGE_SIZE = 25
DOCUMENT_DELAY_SECONDS = 5.0
WHO_LICENSE = "CC BY-NC-SA 3.0 IGO"
WHO_ATTRIBUTION = "© World Health Organization. Licensed under CC BY-NC-SA 3.0 IGO."
CONTENT_SCOPE_FULL = "full"
CONTENT_SCOPE_OVERVIEW = "overview"
PDF_TIMEOUT_SECONDS = 180.0

type PdfMarkdownConverter = Callable[[bytes, str, str], str]

logger = logging.getLogger(__name__)

_PUBLICATION_PATH_RE = re.compile(
    r"^/publications/i/item/(?P<publication_id>[^/?#]+)(?:/|$)",
    re.IGNORECASE,
)
_ISBN_RE = re.compile(r"ISBN:\s*([\d\-]+)", re.IGNORECASE)


class WhoFetchError(ScrapeError):
    """Raised when a WHO publication cannot be fetched or parsed."""


@dataclass(frozen=True)
class WhoPublicationRef:
    """A WHO guideline publication reference from the listing."""

    publication_id: str
    title: str
    page_url: str
    publication_date: str | None = None
    tag: str | None = None
    download_url: str | None = None


@dataclass(frozen=True)
class WhoListingPage:
    """A page of WHO guideline publication references."""

    refs: list[WhoPublicationRef]
    total: int | None


def publication_ref_from_url(url: str) -> WhoPublicationRef:
    """Parse a WHO publication URL into a canonical publication reference.

    Args:
        url: WHO publication URL to parse.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "www.who.int",
        "who.int",
    }:
        raise WhoFetchError(f"Enter a WHO publication URL from who.int; got {url!r}")

    match = _PUBLICATION_PATH_RE.match(parsed.path.rstrip("/") + "/")
    if not match:
        raise WhoFetchError(f"Enter a URL like https://www.who.int/publications/i/item/9789240121805; got {url!r}")

    publication_id = match.group("publication_id")
    return WhoPublicationRef(
        publication_id=publication_id,
        title=publication_id,
        page_url=_page_url(publication_id=publication_id),
    )


def _page_url(*, publication_id: str) -> str:
    return f"{BASE_URL}/publications/i/item/{publication_id}"


def _publications_api_params(*, page: int) -> dict[str, str]:
    skip = (page - 1) * LISTING_PAGE_SIZE
    return {
        "sf_site": SF_SITE,
        "sf_provider": "OpenAccessProvider",
        "sf_culture": "en",
        "$orderby": "PublicationDateAndTime desc",
        "$select": "Title,ItemDefaultUrl,FormatedDate,Tag,DownloadUrl",
        "$filter": f"publishingoffices/any(s:s eq {GUIDELINES_PUBLISHING_OFFICE})",
        "$top": str(LISTING_PAGE_SIZE),
        "$skip": str(skip),
        "$count": "true",
    }


def _publication_id_from_item_url(item_default_url: str) -> str:
    return item_default_url.strip("/").split("/")[-1]


def _parse_api_listing(payload: dict[str, Any]) -> WhoListingPage:
    """Parse a WHO publications OData response into listing refs."""
    try:
        items = payload["value"]
    except KeyError as exc:
        raise WhoFetchError("Could not parse WHO publications API JSON") from exc

    refs: list[WhoPublicationRef] = []
    for item in items:
        item_default_url = (item.get("ItemDefaultUrl") or "").strip()
        title = (item.get("Title") or "").strip()
        if not item_default_url or not title:
            continue
        publication_id = _publication_id_from_item_url(item_default_url)
        refs.append(
            WhoPublicationRef(
                publication_id=publication_id,
                title=title,
                page_url=_page_url(publication_id=publication_id),
                publication_date=(item.get("FormatedDate") or None),
                tag=(item.get("Tag") or None),
                download_url=(item.get("DownloadUrl") or None),
            )
        )

    total = payload.get("@odata.count")
    return WhoListingPage(refs=refs, total=total if isinstance(total, int) else None)


def _parse_html_listing(html_text: str) -> WhoListingPage:
    """Parse server-rendered WHO guideline cards from the listing page."""
    doc = lxml_html.fromstring(html_text)
    refs: list[WhoPublicationRef] = []
    seen: set[str] = set()
    for link in doc.xpath("//a[contains(@href,'/publications/i/item/')]"):
        href = link.get("href", "").split("?")[0]
        match = _PUBLICATION_PATH_RE.match(href.rstrip("/") + "/")
        if not match or href in seen:
            continue
        seen.add(href)
        publication_id = match.group("publication_id")
        title = clean_text(link.xpath("string(.)")) or publication_id
        refs.append(
            WhoPublicationRef(
                publication_id=publication_id,
                title=title,
                page_url=_page_url(publication_id=publication_id),
            )
        )
    return WhoListingPage(refs=refs, total=None)


def list_publications(client: httpx.Client, page: int = 1) -> WhoListingPage:
    """Return one page of WHO guideline publication refs.

    Args:
        client: HTTP client used to fetch the publications API.
        page: Listing page number (default: 1).
    """
    response = client.get(f"{BASE_URL}{PUBLICATIONS_API_PATH}", params=_publications_api_params(page=page))
    if response.status_code >= 400:
        logger.warning("WHO publications API returned %s; falling back to HTML listing", response.status_code)
        listing = client.get(GUIDELINES_LISTING_URL)
        listing.raise_for_status()
        return _parse_html_listing(listing.text)

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise WhoFetchError("Could not decode WHO publications API JSON") from exc
    return _parse_api_listing(payload)


def _overview_html(section: lxml_html.HtmlElement) -> str:
    """Return HTML for the Overview block within a publication section."""
    overview_headings = section.xpath(".//h3[normalize-space()='Overview']")
    if not overview_headings:
        return lxml_html.tostring(section, encoding="unicode")

    heading = overview_headings[0]
    parts = [lxml_html.tostring(heading, encoding="unicode")]
    sibling = heading.getnext()
    while sibling is not None and sibling.tag.lower() not in {"h2", "h3"}:
        parts.append(lxml_html.tostring(sibling, encoding="unicode"))
        sibling = sibling.getnext()
    return "".join(parts)


def _page_isbn(html_text: str) -> str | None:
    doc = lxml_html.fromstring(html_text)
    for node in doc.xpath("//*[contains(normalize-space(.), 'ISBN')]"):
        match = _ISBN_RE.search(node.text_content())
        if match:
            return match.group(1)
    return None


def _page_publication_date(doc: lxml_html.HtmlElement) -> str | None:
    values = doc.xpath("//*[contains(@class,'dynamic-content__date')]/text()")
    return clean_text(values[0]) if values else None


def _page_tag(doc: lxml_html.HtmlElement) -> str | None:
    values = doc.xpath("//*[contains(@class,'dynamic-content__tag')]/text()")
    text = clean_text(" ".join(values))
    return text.lstrip("| ").strip() if text else None


def _page_download_url(doc: lxml_html.HtmlElement) -> str | None:
    for href in doc.xpath("//a[contains(@href,'iris.who.int')]/@href"):
        if "bitstreams" in href:
            return href.split("?")[0]
    return None


def _convert_guideline_pdf(data: bytes, title: str, publication_id: str) -> str:
    """Convert a downloaded WHO guideline PDF into markdown."""
    return pdf_to_markdown(data, running_header=title, name=f"{publication_id}.pdf")


def _guideline_body(
    client: httpx.Client,
    ref: WhoPublicationRef,
    *,
    title: str,
    download_url: str,
    pdf_converter: PdfMarkdownConverter,
) -> tuple[str, int] | None:
    """Download and convert a guideline PDF, or return None when unavailable.

    A single unconvertible PDF must not abort a long listing run, so transport
    and conversion failures are logged and reported as a missing body.
    """
    try:
        response = client.get(download_url, timeout=PDF_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.content
        body = pdf_converter(payload, title, ref.publication_id).strip()
    except (httpx.HTTPError, PdfConversionError) as exc:
        logger.warning("Falling back to Overview for '%s': %s", ref.publication_id, exc)
        return None
    if not body:
        logger.warning("PDF for '%s' produced no markdown; falling back to Overview", ref.publication_id)
        return None
    return body, len(payload)


def build_publication_text(
    client: httpx.Client,
    ref: WhoPublicationRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
    include_full_text: bool = True,
    pdf_converter: PdfMarkdownConverter | None = None,
) -> tuple[str, int, str, dict[str, Any]]:
    """Scrape a publication into markdown text and bibliographic metadata.

    The Overview from the landing page leads, followed by the guideline body
    converted from the linked PDF when one is available.

    Args:
        client: HTTP client used to fetch the publication page and PDF.
        ref: WHO publication reference to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        include_full_text: Whether to download and convert the guideline PDF.
            Disable for a fast Overview-only scrape (default: True).
        pdf_converter: Converter receiving PDF bytes, title and publication id.
            Defaults to the Docling-backed converter (default: None).
    """
    response = client.get(ref.page_url)
    response.raise_for_status()
    html_text = response.text
    doc = lxml_html.fromstring(html_text)

    sections = doc.xpath("//section[contains(@class,'dynamic-content__section')]")
    if not sections:
        raise WhoFetchError(f"No publication content section for '{ref.publication_id}'")

    title = ref.title if ref.title != ref.publication_id else document_title(html_text, fallback=ref.publication_id)
    overview_html = _overview_html(sections[0])
    overview = html_to_markdown(overview_html, link_mode=link_mode, base_url=BASE_URL).strip()
    if not overview:
        raise WhoFetchError(f"No readable Overview content for '{ref.publication_id}'")

    download_url = ref.download_url or _page_download_url(doc)
    body: tuple[str, int] | None = None
    if include_full_text and download_url:
        body = _guideline_body(
            client,
            ref,
            title=title,
            download_url=download_url,
            pdf_converter=pdf_converter or _convert_guideline_pdf,
        )
    elif include_full_text:
        logger.warning("No PDF link for '%s'; emitting Overview only", ref.publication_id)

    content = f"{overview}\n\n{body[0]}" if body else overview
    section_count = 1 + count_markdown_sections(body[0]) if body else 1

    extra_metadata: dict[str, Any] = {
        "publication_date": ref.publication_date or _page_publication_date(doc),
        "tag": ref.tag or _page_tag(doc),
        "isbn": _page_isbn(html_text),
        "download_url": download_url,
        "content_scope": CONTENT_SCOPE_FULL if body else CONTENT_SCOPE_OVERVIEW,
        "pdf_backend": PdfBackend.DOCLING.value if body else None,
        "pdf_bytes": body[1] if body else None,
        "license": WHO_LICENSE,
        "attribution": WHO_ATTRIBUTION,
        "listing_category": "who-guidelines",
    }
    return content, section_count, title, extra_metadata


def scrape_publication(
    client: httpx.Client,
    ref: WhoPublicationRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
    include_full_text: bool = True,
    pdf_converter: PdfMarkdownConverter | None = None,
) -> ScrapedDocument:
    """Scrape a WHO publication into a normalized document.

    Args:
        client: HTTP client used to fetch the publication page and PDF.
        ref: WHO publication reference to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        include_full_text: Whether to download and convert the guideline PDF
            (default: True).
        pdf_converter: Converter receiving PDF bytes, title and publication id.
            Defaults to the Docling-backed converter (default: None).
    """
    content, section_count, title, metadata = build_publication_text(
        client,
        ref,
        link_mode=link_mode,
        include_full_text=include_full_text,
        pdf_converter=pdf_converter,
    )
    return ScrapedDocument(
        source="who",
        external_id=f"who-{ref.publication_id}",
        title=title,
        url=ref.page_url,
        content=content,
        section_count=section_count,
        metadata={
            "publication_id": ref.publication_id,
            **metadata,
        },
    )


def scrape_who(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
    include_full_text: bool = True,
) -> ScrapeRun:
    """Scrape WHO documents from a URL or guideline listing pages.

    Args:
        documents: Number of documents to scrape. Ignored when `url` is set.
            When unset, WHO listing pages are fetched until a page returns no
            items (default: None).
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        url: WHO publication URL to scrape as a single document (default: None).
        include_full_text: Whether to download and convert each guideline PDF.
            Requires the `pdf` extra; without it documents fall back to the
            Overview (default: True).
    """
    if url is not None:

        def scrape_url() -> Iterable[ScrapedDocument]:
            with default_client() as client:
                yield scrape_publication(
                    client,
                    publication_ref_from_url(url),
                    link_mode=link_mode,
                    include_full_text=include_full_text,
                )

        return ScrapeRun(documents=scrape_url(), total=1)

    with default_client() as client:
        first_page = list_publications(client, page=1)
    total = first_page.total if documents is None or first_page.total is None else min(documents, first_page.total)
    return ScrapeRun(
        total=total,
        documents=scrape_listing_documents(
            documents=documents,
            client_factory=default_client,
            first_page_items=first_page.refs,
            list_page=lambda client, page: list_publications(client, page).refs,
            scrape_item=lambda client, ref: scrape_publication(
                client,
                ref,
                link_mode=link_mode,
                include_full_text=include_full_text,
            ),
            document_delay_seconds=DOCUMENT_DELAY_SECONDS,
        ),
    )


__all__ = [
    "BASE_URL",
    "CONTENT_SCOPE_FULL",
    "CONTENT_SCOPE_OVERVIEW",
    "DOCUMENT_DELAY_SECONDS",
    "GUIDELINES_LISTING_URL",
    "PdfMarkdownConverter",
    "WHO_ATTRIBUTION",
    "WHO_LICENSE",
    "WhoFetchError",
    "WhoListingPage",
    "WhoPublicationRef",
    "build_publication_text",
    "list_publications",
    "publication_ref_from_url",
    "scrape_publication",
    "scrape_who",
]
