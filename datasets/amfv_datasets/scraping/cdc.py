"""Scrape official full-text HTML CDC guidelines discovered in CDC Stacks.

CDC Stacks' Guidelines and Recommendations collection mixes full CDC
publications with records whose only complete document is a PDF and records
published by third parties. This scraper deliberately keeps the narrower,
high-confidence subset: English, public-domain records whose CDC MMWR DOI
resolves to an official ``cdc.gov`` full-text HTML article.

That boundary avoids representing a Stacks abstract as a full guideline and
implements the review direction on MedARC-AI/amfv#7: prefer the structured HTML
version over Selenium-driven PDF downloads and GROBID extraction. Discovery of
the Stacks collection and its record identifiers is adapted from that PR.

The Stacks robots.txt declares a 10-second crawl delay. Listing requests honor
it explicitly. CDC.gov declares no crawl delay; full-text document requests use
a one-second politeness interval.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

import httpx
from lxml import html as lxml_html

from amfv_datasets.scraping.base import ScrapedDocument, ScrapeError, ScrapeRun, default_client
from amfv_datasets.scraping.html import LinkMode, clean_text, html_to_markdown

STACKS_BASE_URL = "https://stacks.cdc.gov"
STACKS_COLLECTION_ID = "cdc:100"
STACKS_PAGE_SIZE = 100
STACKS_CRAWL_DELAY_SECONDS = 10.0
DOCUMENT_DELAY_SECONDS = 1.0
CDC_DATASET_NAME = "cdc-guidelines-webscrape"
CDC_DATASET_DISPLAY_NAME = "CDC Guidelines Webscrape"
ATTRIBUTION = "Centers for Disease Control and Prevention (CDC)"
LICENSE = "Public Domain (U.S. Government work)"
LICENSE_URL = "https://www.cdc.gov/other/agencymaterials.html"
CDC_USER_AGENT = "amfv/0.0.1 (+https://github.com/MedARC-AI/amfv)"
"""Identifiable user agent used because CDC Stacks rejects the shared
synthetic Chrome user agent.
"""

logger = logging.getLogger(__name__)

_STACKS_RECORD_PATH_RE = re.compile(r"^/view/cdc/(?P<identifier>\d+)/?$")
_RIS_LINE_RE = re.compile(r"^(?P<tag>[A-Z0-9]{2})  - (?P<value>.*)$")
_MMWR_DOI_RE = re.compile(r"\b10\.15585/mmwr\.[a-z0-9]+\b", re.IGNORECASE)
_TOTAL_RE = re.compile(r"\bof\s+(?P<total>[\d,]+)\s+results?\b", re.IGNORECASE)


class CdcFetchError(ScrapeError):
    """Raised when CDC discovery or full-text HTML extraction fails."""


@dataclass(frozen=True)
class CdcRecord:
    """A CDC Stacks guideline record and its reusable citation metadata."""

    identifier: str
    title: str
    stacks_url: str
    doi_url: str | None
    language: str
    public_domain: bool
    publication_type: str = ""
    published_year: str = ""
    authors: tuple[str, ...] = ()
    corporate_authors: tuple[str, ...] = ()


@dataclass(frozen=True)
class CdcListingPage:
    """One page of CDC Stacks guideline records."""

    records: list[CdcRecord]
    total: int | None


def stacks_identifier_from_url(url: str) -> str:
    """Return the numeric identifier from a CDC Stacks record URL.

    Args:
        url: CDC Stacks record URL such as
            ``https://stacks.cdc.gov/view/cdc/182510``.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "stacks.cdc.gov",
        "www.stacks.cdc.gov",
    }:
        raise CdcFetchError(f"Enter a CDC Stacks record URL from stacks.cdc.gov; got {url!r}")
    match = _STACKS_RECORD_PATH_RE.match(parsed.path)
    if not match:
        raise CdcFetchError(f"Enter a CDC Stacks URL like {STACKS_BASE_URL}/view/cdc/182510; got {url!r}")
    return match.group("identifier")


def list_cdc_records(client: httpx.Client, page: int = 1) -> CdcListingPage:
    """Fetch one CDC Stacks collection page and parse its records.

    Args:
        client: HTTP client used to fetch the collection page.
        page: One-based collection page number (default: 1).
    """
    if page < 1:
        raise ValueError(f"page must be at least 1; got {page}")
    url = _listing_url(page=page)
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise CdcFetchError(f"Could not fetch CDC Stacks listing page {page} at {url}") from exc
    return _parse_listing(response.text)


def fetch_cdc_record(client: httpx.Client, url: str) -> CdcRecord:
    """Fetch one CDC Stacks record and return its citation metadata.

    Args:
        client: HTTP client used to fetch the record page.
        url: CDC Stacks record URL.
    """
    identifier = stacks_identifier_from_url(url)
    canonical_url = f"{STACKS_BASE_URL}/view/cdc/{identifier}"
    try:
        response = client.get(canonical_url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise CdcFetchError(f"Could not fetch CDC Stacks record at {canonical_url}") from exc

    doc = lxml_html.fromstring(response.text)
    ris_nodes = doc.xpath('//textarea[starts-with(@id, "RIScitation")]')
    fields = _parse_ris(ris_nodes[0].text or "") if ris_nodes else {}
    meta_values = [
        value.strip() for value in doc.xpath("//meta[starts-with(@name, 'citation_')]/@content") if value.strip()
    ]
    doi_url = _mmwr_doi_url("\n".join([*_all_field_values(fields), *meta_values]))
    title = _first(fields, "TI") or _first_meta(doc, "citation_title") or _first_text(doc, "//h1//text()")
    if not title:
        raise CdcFetchError(f"Could not find a title in CDC Stacks record at {canonical_url}")
    language = (_first(fields, "LA") or _first_meta(doc, "citation_language")).lower()
    published_year = _first(fields, "PY") or _first_meta(doc, "citation_publication_date")[:4]
    authors = tuple(fields.get("AU", ())) or tuple(_meta_values(doc, "citation_author"))
    return CdcRecord(
        identifier=identifier,
        title=title,
        stacks_url=canonical_url,
        doi_url=doi_url,
        language=language,
        public_domain=_has_public_domain_badge(doc),
        publication_type=_first(fields, "TY"),
        published_year=published_year,
        authors=authors,
        corporate_authors=tuple(fields.get("A2", ())),
    )


def scrape_cdc_record(
    client: httpx.Client,
    record: CdcRecord,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> ScrapedDocument | None:
    """Scrape one eligible CDC Stacks record from its official HTML version.

    PDF-only, non-English, non-public-domain, and external-publisher records
    return ``None``. Fetch and parse failures are logged and skipped so one
    stale record does not abort a collection run.

    Args:
        client: HTTP client used to follow the CDC DOI and fetch the article.
        record: CDC Stacks record to scrape.
        link_mode: Whether links are kept as markdown links or stripped to
            their visible text (default: LinkMode.KEEP).
    """
    if not _supports_full_text_html(record):
        return None
    try:
        response = client.get(record.doi_url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Skipping CDC Stacks record %s: %s", record.stacks_url, exc)
        return None

    article_url = _canonical_page_url(str(response.url))
    if urlparse(article_url).netloc.lower() not in {"cdc.gov", "www.cdc.gov"}:
        logger.warning("Skipping CDC Stacks record %s: DOI did not resolve to cdc.gov", record.stacks_url)
        return None
    if "text/html" not in response.headers.get("content-type", "").lower():
        logger.warning("Skipping CDC Stacks record %s: official CDC version is not HTML", record.stacks_url)
        return None

    doc = lxml_html.fromstring(response.text)
    content_nodes = doc.xpath(
        "//main//div["
        "contains(concat(' ', normalize-space(@class), ' '), ' syndicate ') and "
        "not(contains(concat(' ', normalize-space(@class), ' '), ' no-syndicate '))"
        "]"
    )
    content_nodes = [node for node in content_nodes if len(clean_text(node.text_content())) >= 500]
    if not content_nodes:
        logger.warning("Skipping CDC Stacks record %s: no full-text HTML article body found", record.stacks_url)
        return None
    content_node = max(content_nodes, key=lambda node: len(node.text_content()))
    _remove_non_content_nodes(content_node)
    content = html_to_markdown(
        lxml_html.tostring(content_node, encoding="unicode"),
        link_mode=link_mode,
        base_url=article_url,
    )
    if not content:
        logger.warning("Skipping CDC Stacks record %s: full-text HTML article body was empty", record.stacks_url)
        return None

    title = _first_text(doc, "//main//h1[1]//text()") or record.title
    section_count = max(1, len(content_node.xpath(".//h2")))
    return ScrapedDocument(
        source="cdc",
        external_id=f"cdc-{record.identifier}",
        title=title,
        url=article_url,
        content=content,
        section_count=section_count,
        metadata={
            "attribution": ATTRIBUTION,
            "license": LICENSE,
            "license_url": LICENSE_URL,
            "stacks_url": record.stacks_url,
            "doi_url": record.doi_url,
            "language": record.language,
            "publication_type": record.publication_type,
            "published_year": record.published_year,
            "authors": list(record.authors),
            "corporate_authors": list(record.corporate_authors),
            "content_scope": "full_html",
        },
    )


def scrape_cdc(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
) -> ScrapeRun:
    """Scrape full-text CDC HTML guidelines discovered through CDC Stacks.

    Args:
        documents: Number of successfully scraped documents. Ignored when
            ``url`` is set. When unset, every collection page is inspected
            (default: None).
        link_mode: Whether links are kept as markdown links or stripped to
            their visible text (default: LinkMode.KEEP).
        url: CDC Stacks record URL to scrape as one document (default: None).
    """
    if documents is not None and documents < 1:
        raise ValueError(f"documents must be at least 1; got {documents}")
    if url is not None:

        def scrape_url() -> Iterable[ScrapedDocument]:
            with default_client(user_agent=CDC_USER_AGENT) as client:
                record = fetch_cdc_record(client, url)
                document = scrape_cdc_record(client, record, link_mode=link_mode)
            if document is None:
                raise CdcFetchError(
                    f"CDC Stacks record {record.stacks_url} has no supported English, public-domain "
                    "CDC full-text HTML version; PDF-only and external-publisher records are not scraped"
                )
            yield document

        return ScrapeRun(documents=scrape_url(), total=1)

    return ScrapeRun(
        documents=_scrape_cdc_collection(documents=documents, link_mode=link_mode),
        total=documents,
    )


def _scrape_cdc_collection(*, documents: int | None, link_mode: LinkMode) -> Iterable[ScrapedDocument]:
    with default_client(user_agent=CDC_USER_AGENT) as client:
        page_number = 1
        scraped = 0
        fetched_documents = 0
        while documents is None or scraped < documents:
            if page_number > 1:
                time.sleep(STACKS_CRAWL_DELAY_SECONDS)
            listing = list_cdc_records(client, page=page_number)
            if not listing.records:
                break
            for record in listing.records:
                if documents is not None and scraped >= documents:
                    break
                if not _supports_full_text_html(record):
                    continue
                if fetched_documents:
                    time.sleep(DOCUMENT_DELAY_SECONDS)
                document = scrape_cdc_record(client, record, link_mode=link_mode)
                fetched_documents += 1
                if document is None:
                    continue
                yield document
                scraped += 1
            if listing.total is not None and page_number * STACKS_PAGE_SIZE >= listing.total:
                break
            page_number += 1


def _listing_url(*, page: int) -> str:
    query = urlencode(
        {
            "pid": STACKS_COLLECTION_ID,
            "parentId": STACKS_COLLECTION_ID,
            "maxResults": STACKS_PAGE_SIZE,
            "start": (page - 1) * STACKS_PAGE_SIZE,
        }
    )
    return f"{STACKS_BASE_URL}/cbrowse?{query}"


def _parse_listing(html_text: str) -> CdcListingPage:
    doc = lxml_html.fromstring(html_text)
    items = doc.xpath('//ul[@id="search-results-list"]/li')
    records = [record for item in items if (record := _record_from_listing_item(item)) is not None]
    text = clean_text(doc.text_content(), drop_numeric_citations=False)
    total_match = _TOTAL_RE.search(text)
    total = int(total_match.group("total").replace(",", "")) if total_match else None
    return CdcListingPage(records=records, total=total)


def _record_from_listing_item(item: lxml_html.HtmlElement) -> CdcRecord | None:
    links = item.xpath('.//div[contains(concat(" ", normalize-space(@class), " "), " object-title ")]//a[1]')
    ris_nodes = item.xpath('.//textarea[starts-with(@id, "RIScitation")]')
    if not links or not ris_nodes:
        return None
    stacks_url = urljoin(STACKS_BASE_URL, links[0].get("href") or "")
    try:
        identifier = stacks_identifier_from_url(stacks_url)
    except CdcFetchError:
        return None
    fields = _parse_ris(ris_nodes[0].text or "")
    title = _first(fields, "TI") or clean_text(links[0].text_content(), drop_numeric_citations=False)
    if not title:
        return None
    return CdcRecord(
        identifier=identifier,
        title=title,
        stacks_url=f"{STACKS_BASE_URL}/view/cdc/{identifier}",
        doi_url=_mmwr_doi_url("\n".join(_all_field_values(fields))),
        language=_first(fields, "LA").lower(),
        public_domain=_has_public_domain_badge(item),
        publication_type=_first(fields, "TY"),
        published_year=_first(fields, "PY"),
        authors=tuple(fields.get("AU", ())),
        corporate_authors=tuple(fields.get("A2", ())),
    )


def _parse_ris(text: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for line in text.splitlines():
        match = _RIS_LINE_RE.match(line.strip())
        if not match:
            continue
        value = clean_text(match.group("value"), drop_numeric_citations=False)
        if value:
            fields.setdefault(match.group("tag"), []).append(value)
    return fields


def _mmwr_doi_url(text: str) -> str | None:
    match = _MMWR_DOI_RE.search(text)
    return f"https://doi.org/{match.group(0).lower()}" if match else None


def _supports_full_text_html(record: CdcRecord) -> bool:
    return record.language in {"", "eng"} and record.public_domain and record.doi_url is not None


def _has_public_domain_badge(node: lxml_html.HtmlElement) -> bool:
    badges = node.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " badge ")]')
    return any(clean_text(badge.text_content(), drop_numeric_citations=False) == "Public Domain" for badge in badges)


def _remove_non_content_nodes(node: lxml_html.HtmlElement) -> None:
    unwanted = node.xpath(
        ".//script | .//style | .//noscript | .//svg | .//form | .//button | "
        './/*[contains(concat(" ", normalize-space(@class), " "), " no-syndicate ")]'
    )
    for child in unwanted:
        parent = child.getparent()
        if parent is not None:
            parent.remove(child)


def _canonical_page_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(("https", parsed.netloc.lower(), parsed.path, "", "", ""))


def _first(fields: dict[str, list[str]], tag: str) -> str:
    values = fields.get(tag, ())
    return values[0] if values else ""


def _all_field_values(fields: dict[str, list[str]]) -> list[str]:
    return [value for values in fields.values() for value in values]


def _meta_values(doc: lxml_html.HtmlElement, name: str) -> list[str]:
    return [clean_text(value, drop_numeric_citations=False) for value in doc.xpath(f'//meta[@name="{name}"]/@content')]


def _first_meta(doc: lxml_html.HtmlElement, name: str) -> str:
    values = _meta_values(doc, name)
    return values[0] if values else ""


def _first_text(doc: lxml_html.HtmlElement, xpath: str) -> str:
    values = [clean_text(value, drop_numeric_citations=False) for value in doc.xpath(xpath)]
    return next((value for value in values if value), "")


__all__ = [
    "ATTRIBUTION",
    "CDC_DATASET_DISPLAY_NAME",
    "CDC_DATASET_NAME",
    "CDC_USER_AGENT",
    "DOCUMENT_DELAY_SECONDS",
    "LICENSE",
    "LICENSE_URL",
    "STACKS_BASE_URL",
    "STACKS_COLLECTION_ID",
    "STACKS_CRAWL_DELAY_SECONDS",
    "STACKS_PAGE_SIZE",
    "CdcFetchError",
    "CdcListingPage",
    "CdcRecord",
    "fetch_cdc_record",
    "list_cdc_records",
    "scrape_cdc",
    "scrape_cdc_record",
    "stacks_identifier_from_url",
]
