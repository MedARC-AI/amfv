"""Scrape NICE guidance primitives and materialize cached NICE downloads.

NICE (National Institute for Health and Care Excellence) publishes its guidance
as structured HTML chapters. The admin import worker uses the listing and
chapter helpers here to discover guidance, scrape HTML chapters into clean
markdown, and persist cached ``NiceDownload`` rows before public create-flow
routes can materialize them into retrieval datasets.

We deliberately scrape the HTML chapter pages rather than the downloadable PDFs:
PDF text extraction loses reading order and interleaves page headers/footers and
copyright boilerplate into the body text, whereas the HTML carries the semantic
document structure (headings, paragraphs, lists) directly.

Attribution
-----------
The HTML-scraping approach and the chapter/section CSS selectors are adapted from
Meditron's NICE guideline scraper (epfLLM/meditron, gap-replay/guidelines):
https://github.com/epfLLM/meditron/tree/main/gap-replay/guidelines/scrapers/nice
Their scraper is a Puppeteer/TypeScript crawler; this is a Python (httpx + lxml)
port updated to match the current NICE website markup (``div.chapter`` content,
``__NEXT_DATA__`` listing JSON, and the ``/guidance/<ref>/chapter/<name>`` URLs).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from lxml import html as lxml_html
from sqlmodel import Session, select

from app.models import Dataset, Document, EvalType, NiceDownload
from app.services.documents import create_document_with_chunks

BASE_URL = "https://www.nice.org.uk"
# Default dataset that pulled NICE guidance lands in when the user has not
# selected one.
NICE_DATASET_NAME = "nice-webscrape"
NICE_DATASET_DISPLAY_NAME = "NICE Webscrape"
# "NICE guidelines" is the listing category that contains the substantive
# recommendation documents (both NG and CG reference codes).
GUIDANCE_TYPE = "NICE guidelines"
USER_AGENT = "amfv-web/1.0 (+https://github.com/amfv) NICE recommendation fetcher"
REQUEST_TIMEOUT = 60.0
PAGE_SIZE = 50
logger = logging.getLogger(__name__)

# NICE reference codes we treat as recommendation documents.
_REF_RE = re.compile(r"^(NG|CG)\d+$", re.IGNORECASE)
_GUIDANCE_PATH_RE = re.compile(r"^/guidance/(?P<slug>(?:ng|cg)\d+)(?:/|$)", re.IGNORECASE)
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
# Chapters that are pure boilerplate (no guidance content) — skipped when present.
_SKIP_CHAPTER_SUFFIXES = (
    "finding-more-information-and-committee-details",
)


class NiceFetchError(RuntimeError):
    """Raised when a recommendation cannot be sourced from NICE."""


@dataclass(frozen=True)
class GuidanceRef:
    """A published NICE guidance reference from the listing."""

    ref: str  # e.g. "NG235"
    slug: str  # e.g. "ng235"
    title: str
    page_url: str  # canonical overview page


def _listing_url(*, page: int, page_size: int) -> str:
    return (
        f"{BASE_URL}/guidance/published"
        f"?ndt=Guidance&ngt={GUIDANCE_TYPE}&ps={page_size}&pa={page}"
    )


def guidance_ref_from_url(url: str) -> GuidanceRef:
    """Parse a NICE guidance URL into a canonical guidance reference."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "www.nice.org.uk",
        "nice.org.uk",
    }:
        raise NiceFetchError("Enter a NICE guidance URL from nice.org.uk")

    match = _GUIDANCE_PATH_RE.match(parsed.path.rstrip("/") + "/")
    if not match:
        raise NiceFetchError("Enter a URL like https://www.nice.org.uk/guidance/ng123")

    slug = match.group("slug").lower()
    ref = slug.upper()
    return GuidanceRef(ref=ref, slug=slug, title=ref, page_url=f"{BASE_URL}/guidance/{slug}")


def _parse_listing(html_text: str) -> tuple[list[GuidanceRef], int]:
    """Parse a published-guidance listing page into ``(refs, total_count)``.

    The modern NICE listing is a Next.js page whose results are embedded as JSON
    in a ``__NEXT_DATA__`` script tag rather than rendered into the static HTML.
    """
    match = _NEXT_DATA_RE.search(html_text)
    if not match:
        raise NiceFetchError("NICE listing markup changed (no __NEXT_DATA__)")
    try:
        results = json.loads(match.group(1))["props"]["pageProps"]["results"]
    except (KeyError, ValueError) as exc:
        raise NiceFetchError("Could not parse NICE listing JSON") from exc

    total = int(results.get("resultCount", 0))
    refs: list[GuidanceRef] = []
    for doc in results.get("documents", []):
        ref = (doc.get("guidanceRef") or "").strip()
        if not _REF_RE.match(ref):
            continue
        path = doc.get("pathAndQuery") or f"/guidance/{ref.lower()}"
        page_url = f"{BASE_URL}{path}" if path.startswith("/") else path
        refs.append(
            GuidanceRef(
                ref=ref.upper(),
                slug=ref.lower(),
                title=(doc.get("title") or ref).strip(),
                page_url=page_url,
            )
        )
    return refs, total


def list_published_guidance(
    client: httpx.Client, *, page: int = 1, page_size: int = PAGE_SIZE
) -> tuple[list[GuidanceRef], int]:
    """Return one page of published guidance refs and the total result count."""
    response = client.get(_listing_url(page=page, page_size=page_size))
    response.raise_for_status()
    return _parse_listing(response.text)


def _chapter_links(html_text: str, slug: str) -> list[str]:
    """Absolute URLs of chapter links from a guidance overview table of contents."""
    doc = lxml_html.fromstring(html_text)

    nav_xpaths = (
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' stacked-nav ')]"
        f"//a[contains(@href, '/guidance/{slug}/chapter/')]/@href",
        "//ul[contains(concat(' ', normalize-space(@class), ' '), ' nav-list ')]"
        f"//li//a[contains(@href, '/guidance/{slug}/chapter/')]/@href",
    )

    for nav_xpath in nav_xpaths:
        hrefs = doc.xpath(nav_xpath)
        if not hrefs:
            continue
        seen: set[str] = set()
        links: list[str] = []
        for href in hrefs:
            url = urljoin(BASE_URL, href).split("#")[0].split("?")[0]
            if url in seen:
                continue
            seen.add(url)
            links.append(url)
        return links
    return []


def _guidance_title(html_text: str, fallback: str) -> str:
    doc = lxml_html.fromstring(html_text)
    for xpath in ("//h1[1]/text()", "//title[1]/text()"):
        values = [_clean_text(value) for value in doc.xpath(xpath)]
        title = next((value for value in values if value), "")
        if title:
            return title.replace(" | Guidance | NICE", "").strip() or fallback
    return fallback


def _clean_text(value: str) -> str:
    return re.sub(r"\[\d+\]", "", re.sub(r"\s+", " ", value)).strip()


def _markdown_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|")


def _table_markdown(table: lxml_html.HtmlElement) -> str:
    rows: list[list[str]] = []
    for tr in table.xpath(".//tr"):
        cells = [_clean_text(cell.text_content()) for cell in tr.xpath("./th|./td")]
        if any(cells):
            rows.append([_markdown_cell(cell) for cell in cells])
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    body = normalized[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _chapter_markdown(html_text: str) -> str:
    """Convert a NICE chapter page's ``div.chapter`` content into markdown.

    Headings, paragraphs, list items, and tables are emitted in document order,
    mapping NICE's heading levels onto markdown hashes. Citation markers like
    ``[1]`` are dropped, mirroring Meditron's cleaning.
    """
    doc = lxml_html.fromstring(html_text)
    chapters = doc.xpath(
        "//div[contains(concat(' ', normalize-space(@class), ' '), ' chapter ')]"
    )
    if not chapters:
        return ""

    blocks: list[str] = []
    for el in chapters[0].xpath(
        ".//*[self::h2 or self::h3 or self::h4 or self::p or self::li or self::table]"
    ):
        if el.tag != "table" and el.xpath("ancestor::table"):
            continue
        # Text inside list items (and paragraphs nested in lists) is captured by
        # the enclosing <li>; skip it here to avoid duplication.
        if el.xpath("ancestor::li"):
            continue
        if el.tag == "p" and el.xpath("ancestor::*[self::ul or self::ol]"):
            continue
        if el.tag == "table":
            table = _table_markdown(el)
            if table:
                blocks.append(table)
            continue

        text = _clean_text(el.text_content())
        if not text:
            continue
        if el.tag == "h2":
            blocks.append(f"# {text}")
        elif el.tag == "h3":
            blocks.append(f"## {text}")
        elif el.tag == "h4":
            blocks.append(f"### {text}")
        elif el.tag == "li":
            blocks.append(f"- {text}")
        else:
            blocks.append(text)
    return "\n\n".join(blocks)


def _is_skipped_chapter(url: str) -> bool:
    tail = url.rstrip("/").rsplit("/", 1)[-1].lower()
    return any(tail.endswith(suffix) for suffix in _SKIP_CHAPTER_SUFFIXES)


def build_guideline_text(client: httpx.Client, ref: GuidanceRef) -> tuple[str, int, str]:
    """Scrape a guideline's chapters into ``(markdown_text, section_count, title)``."""
    overview = client.get(ref.page_url)
    overview.raise_for_status()
    title = ref.title if ref.title != ref.ref else _guidance_title(overview.text, ref.ref)
    chapter_urls = _chapter_links(overview.text, ref.slug)
    if not chapter_urls:
        raise NiceFetchError(f"No chapters found for guidance '{ref.ref}'")

    sections: list[str] = []
    for url in chapter_urls:
        if _is_skipped_chapter(url):
            continue
        chapter = client.get(url)
        chapter.raise_for_status()
        markdown = _chapter_markdown(chapter.text)
        if markdown:
            sections.append(markdown)

    content = "\n\n".join(sections).strip()
    if not content:
        raise NiceFetchError(f"No readable content for guidance '{ref.ref}'")
    return content, len(sections), title


def get_or_create_nice_dataset(session: Session) -> Dataset:
    """Return the shared "NICE Webscrape" retrieval dataset, creating it if needed.

    Pulled NICE guidance lands here when the user has not selected a dataset.
    Seeded at startup so it is always offered in the create-retrieval dropdown.
    """
    dataset = session.exec(
        select(Dataset).where(Dataset.name == NICE_DATASET_NAME)
    ).first()
    if dataset is not None:
        return dataset
    dataset = Dataset(
        name=NICE_DATASET_NAME,
        display_name=NICE_DATASET_DISPLAY_NAME,
        description="Source documents pulled from NICE published guidance.",
        eval_type=EvalType.RETRIEVAL,
    )
    session.add(dataset)
    session.flush()
    return dataset


def materialize_nice_document(
    session: Session, *, dataset_id: int, download: NiceDownload
) -> Document:
    """Create (or reuse) a source ``Document`` for a NICE download in a dataset.

    NICE guidance is stored as a complete source document. Re-requesting an
    already-materialized guideline returns the existing document rather than
    violating the ``(dataset_id, external_id)`` uniqueness constraint.
    """
    external_id = f"nice-{download.slug}"
    existing = session.exec(
        select(Document).where(
            Document.dataset_id == dataset_id,
            Document.external_id == external_id,
        )
    ).first()
    if existing is not None:
        return existing

    return create_document_with_chunks(
        session,
        dataset_id=dataset_id,
        title=download.title,
        content=download.content,
        external_id=external_id,
    )
