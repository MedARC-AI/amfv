"""Scrape Cancer Care Ontario guidance into normalized ScrapedDocument records.

Each document's `content` is markdown-formatted text; the actual output
format (markdown files, JSONL, or a HuggingFace dataset) is chosen by the
CLI's `--format` flag and has nothing to do with this module.

CCO (Cancer Care Ontario, now part of Ontario Health) publishes guidance pages
under `/en/guidelines-advice/types-of-cancer/<id>`. The `types-of-cancer`
index page is itself the full master listing of every guideline (the
category buttons on it are just filtered subsets of the same list), paged
through a Drupal "Load more" pager rather than a plain "next" link, so we
follow that pager directly instead of separately crawling each category.

Each guideline page carries a short metadata block (version, document status,
authors) and a few short narrative sections (objective, patient population,
intended users), with the full clinical recommendations distributed as a
linked PDF.

This scraper captures the HTML page's short summary
sections; the PDF is recorded as a link (or URL) in the content and as
`pdf_url` in the document metadata for a later PDF-aware pass to pick up.

CCO sits behind an Azure WAF JS challenge, so plain HTTP requests get a 403
regardless of headers. We fetch pages through a real headless browser
(Playwright) instead, which resolves the challenge the same way a normal
visitor's browser would. Every function below that touches the network takes
a plain `fetch(url) -> html` callable rather than a browser handle directly,
so the discovery and parsing logic stays unit-testable without a browser.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from lxml import etree
from lxml import html as lxml_html

from amfv_datasets.scraping.base import USER_AGENT, ScrapedDocument, ScrapeError, ScrapeRun, scrape_listing_documents
from amfv_datasets.scraping.html import LinkMode, absolute_unique_urls, document_title

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Page, sync_playwright
except ImportError:
    HAS_PLAYWRIGHT = False
else:
    HAS_PLAYWRIGHT = True

BASE_URL = "https://www.cancercareontario.ca"
CCO_DATASET_NAME = "cco-webscrape"
CCO_DATASET_DISPLAY_NAME = "CCO Webscrape"
DOCUMENT_DELAY_SECONDS = 5.0

logger = logging.getLogger(__name__)

_LISTING_PREFIX = "/en/guidelines-advice/types-of-cancer/"
_DETAIL_PATH = re.compile(r"^/en/guidelines-advice/types-of-cancer/(?P<id>[0-9]+)/?$")
_CATEGORY_INDEX_URL = f"{BASE_URL}{_LISTING_PREFIX.rstrip('/')}"

_FIELD_LABELS = ("Version", "ID", "Type of Content", "Document Status", "Authors")
_SECTION_HEADERS = (
    "Guideline Objective",
    "Patient Population",
    "Intended Guideline Users",
    "Research Question(s)",
    "Recommendations",
    "Key Evidence",
    "Qualifying Statements",
    "Related Guidelines",
)

_MAIN_CONTENT_XPATH = "//*[@role='main'] | //div[@id='content']"

_CHALLENGE_TITLE_MARKERS = ("azure waf", "just a moment", "checking your browser", "attention required")
_CHALLENGE_WAIT_SECONDS = 4.0
_CHALLENGE_MAX_ATTEMPTS = 4

PageFetch = Callable[[str], str]


class CcoFetchError(ScrapeError):
    """Raised when a guideline cannot be sourced from CCO."""


@dataclass(frozen=True)
class GuidelineRef:
    """A published CCO guideline reference."""

    id: str
    page_url: str


def guideline_ref_from_url(url: str) -> GuidelineRef:
    """Parse a CCO guideline URL into a canonical guideline reference.

    Args:
        url: CCO guideline URL to parse.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "www.cancercareontario.ca",
        "cancercareontario.ca",
    }:
        raise CcoFetchError(f"Enter a CCO guideline URL from cancercareontario.ca; got {url!r}")

    match = _DETAIL_PATH.fullmatch(parsed.path)
    if match is None:
        raise CcoFetchError(f"Enter a URL like https://www.cancercareontario.ca{_LISTING_PREFIX}64736; got {url!r}")
    guideline_id = match.group("id")
    return GuidelineRef(id=guideline_id, page_url=f"{BASE_URL}{_LISTING_PREFIX}{guideline_id}")


def _looks_like_challenge(title: str) -> bool:
    lowered = title.lower()
    return any(marker in lowered for marker in _CHALLENGE_TITLE_MARKERS)


def _content_with_retries(page: Page, attempts: int = 5, delay_ms: int = 500) -> str:
    last_error: PlaywrightError | None = None
    for _ in range(attempts):
        try:
            return page.content()
        except PlaywrightError as error:
            last_error = error
            page.wait_for_timeout(delay_ms)
    assert last_error is not None
    raise last_error


def playwright_fetch(page: Page, url: str) -> str:
    """Fetch a URL's rendered HTML through a Playwright page, riding out CCO's WAF challenge.

    Args:
        page: An open Playwright `Page` to navigate.
        url: URL to fetch.
    """
    status = None
    last_error: PlaywrightError | None = None
    for _ in range(_CHALLENGE_MAX_ATTEMPTS):
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            status = response.status if response else status
            try:
                page.wait_for_load_state("load", timeout=15_000)
            except PlaywrightError:
                pass
            page.wait_for_timeout(int(_CHALLENGE_WAIT_SECONDS * 1000))
            if not _looks_like_challenge(page.title()) and (status is None or status < 400):
                return _content_with_retries(page)
        except PlaywrightError as error:
            last_error = error
            page.wait_for_timeout(int(_CHALLENGE_WAIT_SECONDS * 1000))
    if last_error is not None:
        raise CcoFetchError(f"Could not fetch {url!r} after {_CHALLENGE_MAX_ATTEMPTS} attempts: {last_error}")
    raise CcoFetchError(f"Could not load {url!r}; challenge or HTTP error persisted (last HTTP status {status})")


@contextmanager
def playwright_client(*, headless: bool = True) -> Iterator[PageFetch]:
    """Open a headless-browser-backed page fetcher for CCO.

    Args:
        headless: Whether to run the browser headless. Set False to watch the
            challenge resolve while debugging (default: True).
    """
    if not HAS_PLAYWRIGHT:
        raise CcoFetchError(
            "Install CCO support with uv sync --extra cco --package amfv-datasets, "
            "then run uv run playwright install chromium"
        )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=USER_AGENT, locale="en-US")
        page = context.new_page()
        try:
            yield lambda url: playwright_fetch(page, url)
        finally:
            context.close()
            browser.close()


def list_cco_guidelines(fetch: PageFetch) -> list[GuidelineRef]:
    """Discover CCO guideline references from the master listing page.

    The `types-of-cancer` index page is the full listing of every guideline;
    we page through it with its "Load more" pager, collecting guideline detail
    links along the way.

    Args:
        fetch: Callable returning the rendered HTML for a listing or pagination URL.
    """
    detail_urls: set[str] = set()
    seen_pages: set[str] = set()
    queue = [_CATEGORY_INDEX_URL]
    while queue:
        page_url = queue.pop(0)
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)
        try:
            doc = lxml_html.fromstring(fetch(page_url))
        except (etree.ParserError, etree.XMLSyntaxError) as exc:
            raise CcoFetchError(f"Could not parse the CCO listing page {page_url!r}; retry the scrape") from exc
        hrefs = absolute_unique_urls(doc.xpath("//a[@href]/@href"), base_url=BASE_URL)
        for href in hrefs:
            try:
                ref = guideline_ref_from_url(href)
            except CcoFetchError:
                continue
            detail_urls.add(ref.page_url)
        for next_url in _next_page_urls(doc):
            if urlparse(next_url).netloc.lower() == urlparse(BASE_URL).netloc and next_url not in seen_pages:
                queue.append(next_url)
    return [GuidelineRef(id=url.rsplit("/", 1)[-1], page_url=url) for url in sorted(detail_urls)]


def _next_page_urls(doc: lxml_html.HtmlElement) -> list[str]:
    hrefs = doc.xpath("//a[@rel='next']/@href")
    if not hrefs:
        hrefs = doc.xpath("//li[contains(concat(' ', normalize-space(@class), ' '), ' pager-next ')]/a/@href")
    if not hrefs:
        hrefs = doc.xpath("//a[translate(normalize-space(text()), 'NEXT', 'next')='next']/@href")
    return [urljoin(BASE_URL, href) for href in hrefs]


def _main_content(doc: lxml_html.HtmlElement) -> lxml_html.HtmlElement:
    matches = doc.xpath(_MAIN_CONTENT_XPATH)
    return matches[0] if matches else doc


def _text_tokens(doc: lxml_html.HtmlElement) -> list[str]:
    return [text.strip() for text in doc.xpath(".//text()") if text.strip()]


_STOP_TOKENS = frozenset({f"{label}:" for label in _FIELD_LABELS} | set(_SECTION_HEADERS))


def _field_values(tokens: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for index, token in enumerate(tokens):
        for label in _FIELD_LABELS:
            prefix = f"{label}:"
            if token.startswith(prefix):
                value = token[len(prefix) :].strip()
                if not value:
                    continuation_limit = 2 if label == "ID" else 1
                    extra: list[str] = []
                    cursor = index + 1
                    while (
                        cursor < len(tokens) and len(extra) < continuation_limit and tokens[cursor] not in _STOP_TOKENS
                    ):
                        extra.append(tokens[cursor])
                        cursor += 1
                    value = " ".join(extra).strip()
                fields[label] = value
                break
        else:
            bare = token.rstrip(":")
            if bare in _FIELD_LABELS and index + 1 < len(tokens):
                fields[bare] = tokens[index + 1].strip()
    return fields


def _section_text(tokens: list[str], *, trim_suffix: str | None) -> dict[str, str]:
    marks = [(index, token) for index, token in enumerate(tokens) if token in _SECTION_HEADERS]
    sections: dict[str, str] = {}
    for position, (index, header) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(tokens)
        value = " ".join(tokens[index + 1 : end]).strip()
        if trim_suffix and value.endswith(trim_suffix):
            value = value[: -len(trim_suffix)].strip()
        sections[header] = value
    return sections


def _pdf_link(doc: lxml_html.HtmlElement) -> tuple[str, str] | None:
    for anchor in doc.xpath(".//a[@href]"):
        href = anchor.get("href")
        if "/en/file/" in href or href.lower().endswith(".pdf"):
            label = " ".join(anchor.text_content().split()) or "Full Report (PDF)"
            return urljoin(BASE_URL, href), label
    return None


def _document_content(
    sections: dict[str, str],
    *,
    fields: dict[str, str],
    pdf_link: tuple[str, str] | None,
    link_mode: LinkMode,
) -> str:
    parts = [f"## {header}\n\n{text}" for header, text in sections.items() if text]
    if pdf_link:
        pdf_url, pdf_label = pdf_link
        parts.append(f"Full report: [{pdf_label}]({pdf_url})" if link_mode is LinkMode.KEEP else pdf_label)
    if not parts and fields:
        parts.append("\n".join(f"{label}: {value}" for label, value in fields.items() if value))
    return "\n\n".join(parts).strip()


def _best_title(html_text: str, doc: lxml_html.HtmlElement, fallback: str) -> str:
    candidate = document_title(html_text, fallback="", suffixes=(" | Cancer Care Ontario",))
    candidate = candidate.strip().lstrip("|").strip()
    if candidate:
        return candidate
    for heading in doc.xpath(".//h1"):
        text = " ".join(heading.text_content().split())
        if text:
            return text
    return fallback


def scrape_cco_guideline(
    fetch: PageFetch,
    ref: GuidelineRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> ScrapedDocument:
    """Scrape a CCO guideline page into a normalized document.

    Args:
        fetch: Callable returning the rendered HTML for the guideline page.
        ref: CCO guideline reference to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    html_text = fetch(ref.page_url)
    try:
        doc = lxml_html.fromstring(html_text)
    except (etree.ParserError, etree.XMLSyntaxError) as exc:
        raise CcoFetchError(f"Could not parse HTML for guideline {ref.id!r}") from exc
    title = _best_title(html_text, doc, ref.id)

    full_tokens = _text_tokens(doc)
    fields = _field_values(full_tokens)
    pdf_link = _pdf_link(doc)

    content_root = _main_content(doc)
    scoped_tokens = _text_tokens(content_root) if content_root is not doc else full_tokens
    sections = _section_text(scoped_tokens, trim_suffix=pdf_link[1] if pdf_link else None)
    if not sections and content_root is not doc:
        sections = _section_text(full_tokens, trim_suffix=pdf_link[1] if pdf_link else None)

    content = _document_content(sections, fields=fields, pdf_link=pdf_link, link_mode=link_mode)
    if not content:
        raise CcoFetchError(f"No readable content for guideline '{ref.id}'")

    metadata: dict[str, str] = {key.lower().replace(" ", "_"): value for key, value in fields.items()}
    metadata["content_scope"] = "summary"
    if pdf_link:
        metadata["pdf_url"] = pdf_link[0]

    return ScrapedDocument(
        source="cco",
        external_id=f"cco-{ref.id}",
        title=title,
        url=ref.page_url,
        content=content,
        section_count=len(sections) or 1,
        metadata=metadata,
    )


def _scrape_guideline_or_skip(
    fetch: PageFetch,
    ref: GuidelineRef,
    *,
    link_mode: LinkMode,
) -> ScrapedDocument | None:
    try:
        return scrape_cco_guideline(fetch, ref, link_mode=link_mode)
    except CcoFetchError as error:
        logger.warning("Skipping CCO guideline %s: %s", ref.id, error)
        return None


_MIN_EXPECTED_GUIDELINES = 50
_DISCOVERY_ATTEMPTS = 3


def _discover_guidelines_with_retries(fetch: PageFetch) -> list[GuidelineRef]:
    refs: list[GuidelineRef] = []
    for _ in range(_DISCOVERY_ATTEMPTS):
        refs = list_cco_guidelines(fetch)
        if len(refs) >= _MIN_EXPECTED_GUIDELINES:
            return refs
    raise CcoFetchError(
        f"Discovery only found {len(refs)} guideline(s) after {_DISCOVERY_ATTEMPTS} attempts "
        "(expected several hundred); the listing page likely didn't load correctly"
    )


def scrape_cco(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
    headless: bool = True,
) -> ScrapeRun:
    """Scrape CCO documents from a URL or category listing pages.

    Failed pages are skipped with a logged warning. They do not count toward
    the requested number of successful documents. Explicit URL failures raise.

    Args:
        documents: Number of documents to scrape. Ignored when `url` is set.
            When unset, every guideline discovered by crawling the category
            index and its listing pages is scraped (default: None).
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        url: CCO source URL to scrape as a single document (default: None).
        headless: Whether to run the underlying browser headless (default: True).
    """
    if documents is not None and documents < 1:
        raise ValueError(f"documents must be at least 1; got {documents}")
    if url is not None:
        ref = guideline_ref_from_url(url)

        def scrape_url() -> Iterable[ScrapedDocument]:
            with playwright_client(headless=headless) as fetch:
                yield scrape_cco_guideline(fetch, ref, link_mode=link_mode)

        return ScrapeRun(documents=scrape_url(), total=1)

    with playwright_client(headless=headless) as fetch:
        refs = _discover_guidelines_with_retries(fetch)
    total = len(refs) if documents is None else min(documents, len(refs))
    return ScrapeRun(
        total=total,
        documents=scrape_listing_documents(
            documents=documents,
            client_factory=lambda: playwright_client(headless=headless),
            first_page_items=refs,
            list_page=lambda fetch, page: [],
            scrape_item=lambda fetch, ref: _scrape_guideline_or_skip(fetch, ref, link_mode=link_mode),
            document_delay_seconds=DOCUMENT_DELAY_SECONDS,
        ),
    )


__all__ = [
    "BASE_URL",
    "CCO_DATASET_DISPLAY_NAME",
    "CCO_DATASET_NAME",
    "DOCUMENT_DELAY_SECONDS",
    "CcoFetchError",
    "GuidelineRef",
    "PageFetch",
    "guideline_ref_from_url",
    "list_cco_guidelines",
    "playwright_client",
    "playwright_fetch",
    "scrape_cco",
    "scrape_cco_guideline",
]
