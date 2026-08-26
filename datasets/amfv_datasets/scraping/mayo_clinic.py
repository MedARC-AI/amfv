"""Permission-gated Mayo Clinic condition-article ingestion.

Mayo Clinic's current terms restrict automated scraping and reuse. This module
therefore makes no network request unless a caller explicitly asserts
authorization and records a permission identifier. Authorized callers may use
an explicit URL manifest, one direct condition URL, or the publisher's A-Z
condition index. Rendered HTML is fetched through an ephemeral browser and kept
in memory because Mayo's CDN rejects plain clients.
"""

from __future__ import annotations

import hashlib
import math
import re
import string
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlencode, urlparse

from lxml import etree
from lxml import html as lxml_html
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from amfv_datasets.scraping.base import (
    USER_AGENT,
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    capture_artifact,
    sanitize_retrieval_receipt,
    scrape_listing_documents,
)
from amfv_datasets.scraping.html import LinkMode, clean_text, html_to_markdown

BASE_URL = "https://www.mayoclinic.org"
TERMS_URL = f"{BASE_URL}/about-this-site/terms-conditions-use-policy"
DOCUMENT_DELAY_SECONDS = 10.0
INDEX_URL = f"{BASE_URL}/diseases-conditions/index"
INDEX_LETTERS = tuple(letter for letter in string.ascii_uppercase if letter != "Q") + ("#",)
MAX_HTML_BYTES = 16 * 1024 * 1024
MAX_CONSECUTIVE_DISCOVERY_FAILURES = 5
MAX_FETCH_ATTEMPTS = 4
INITIAL_RETRY_DELAY_SECONDS = 5.0
MAX_RETRY_DELAY_SECONDS = 60.0
RETRIABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
ARTICLE_SELECTOR = "#main-content, article.cmp-article"
INDEX_RESULT_SELECTOR = "#cmp-skip-to-main__content.cmp-azresults a.cmp-result-name__link"

_ARTICLE_PATH_RE = re.compile(
    r"^/diseases-conditions/(?P<slug>[a-z0-9-]+)/"
    r"(?P<section>symptoms-causes|diagnosis-treatment)/"
    r"(?P<document_id>(?:syc|drc)-\d+)/?$",
    re.IGNORECASE,
)

FetchedHtml = tuple[str, dict[str, object]]
PageFetch = Callable[[str], FetchedHtml]


class MayoClinicFetchError(ScrapeError):
    """Raised when a configured Mayo Clinic article cannot be parsed."""


class MayoClinicPageUnavailableError(MayoClinicFetchError):
    """Raised when a valid discovered article route cannot be rendered."""


class MayoClinicPermissionError(MayoClinicFetchError):
    """Raised before networking when authorization has not been asserted."""


@dataclass(frozen=True)
class MayoClinicArticleRef:
    """A manifest-provided Mayo Clinic condition article section."""

    slug: str
    section: str
    document_id: str
    title: str
    page_url: str


@dataclass(frozen=True)
class MayoClinicListingItem:
    """One discovered article plus the A-Z page receipt that exposed it."""

    ref: MayoClinicArticleRef
    discovery_retrieval: dict[str, object] | None = None


def mayo_clinic_ref_from_url(url: str, *, title: str | None = None) -> MayoClinicArticleRef:
    """Validate and normalize a Mayo Clinic condition article URL."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "mayoclinic.org",
        "www.mayoclinic.org",
    }:
        raise MayoClinicFetchError(f"Enter a Mayo Clinic article URL from mayoclinic.org; got {url!r}")
    match = _ARTICLE_PATH_RE.match(parsed.path)
    if match is None:
        raise MayoClinicFetchError(
            "Enter a Mayo Clinic symptoms/causes or diagnosis/treatment URL like "
            "https://www.mayoclinic.org/diseases-conditions/acne/symptoms-causes/syc-20368047; "
            f"got {url!r}"
        )
    slug = match.group("slug").lower()
    section = match.group("section").lower()
    document_id = match.group("document_id").lower()
    return MayoClinicArticleRef(
        slug=slug,
        section=section,
        document_id=document_id,
        title=title or slug.replace("-", " ").title(),
        page_url=f"{BASE_URL}/diseases-conditions/{slug}/{section}/{document_id}",
    )


def refs_from_manifest(urls: Iterable[str]) -> list[MayoClinicArticleRef]:
    """Validate and deduplicate an operator-supplied URL manifest."""
    refs: list[MayoClinicArticleRef] = []
    seen_urls: set[str] = set()
    for url in urls:
        ref = mayo_clinic_ref_from_url(url)
        if ref.page_url in seen_urls:
            continue
        seen_urls.add(ref.page_url)
        refs.append(ref)
    return refs


def _index_url(letter: str) -> str:
    normalized = letter.strip().upper()
    if normalized not in INDEX_LETTERS:
        raise ValueError(f"Unsupported Mayo Clinic index letter {letter!r}")
    return f"{INDEX_URL}?{urlencode({'letter': normalized})}"


def _validate_index_url(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "www.mayoclinic.org"
        or parsed.path != urlparse(INDEX_URL).path
    ):
        raise MayoClinicFetchError("Mayo Clinic index navigation left the authorized A-Z route")
    query = parse_qs(parsed.query)
    if set(query) != {"letter"} or len(query["letter"]) != 1 or query["letter"][0].upper() not in INDEX_LETTERS:
        raise MayoClinicFetchError("Mayo Clinic index navigation used an unexpected query")
    return url


def _browser_receipt(
    url: str,
    html_text: str,
    *,
    status_code: int | None,
    attempts: int,
    retry_delays_seconds: list[float],
) -> dict[str, object]:
    data = html_text.encode("utf-8")
    completed_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return sanitize_retrieval_receipt(
        {
            "requested_url": url,
            "final_url": url,
            "transport": "playwright-ephemeral-browser",
            "download_completed_at_utc": completed_at,
            "downloaded_at_utc": completed_at,
            "status_code": status_code,
            "attempts": attempts,
            "retry_delays_seconds": retry_delays_seconds,
            "content_type": "text/html; charset=utf-8",
            "byte_count": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    )


def list_mayo_clinic_index(
    fetch: PageFetch,
    *,
    letter: str,
) -> tuple[list[MayoClinicArticleRef], dict[str, object]]:
    """Read and deduplicate one publisher A-Z condition-index page."""
    html_text, retrieval = fetch(_index_url(letter))
    doc = lxml_html.fromstring(html_text)
    refs: list[MayoClinicArticleRef] = []
    seen: set[str] = set()
    anchors = doc.xpath(
        "//*[@id='cmp-skip-to-main__content']"
        "[contains(concat(' ', normalize-space(@class), ' '), ' cmp-azresults ')]"
        "//a[contains(concat(' ', normalize-space(@class), ' '), ' cmp-result-name__link ')]"
        "[contains(@href, '/diseases-conditions/')][@href]"
    )
    for anchor in anchors:
        href = anchor.get("href") or ""
        title = clean_text(anchor.text_content(), drop_numeric_citations=False)
        try:
            ref = mayo_clinic_ref_from_url(href, title=title or None)
        except MayoClinicFetchError:
            continue
        if ref.page_url in seen:
            continue
        seen.add(ref.page_url)
        refs.append(ref)
    if not refs:
        raise MayoClinicFetchError(f"Mayo Clinic index {letter!r} contained no supported condition articles")
    return refs, retrieval


@contextmanager
def _playwright_client(*, headless: bool = True) -> Iterator[PageFetch]:
    """Open an ephemeral, browser-backed Mayo page fetcher."""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=headless)
        except PlaywrightError as bundled_error:
            try:
                browser = playwright.chromium.launch(channel="chrome", headless=headless)
            except PlaywrightError as chrome_error:
                raise MayoClinicFetchError(
                    "Could not start Chromium for Mayo Clinic ingestion. Run `uv run playwright install chromium` "
                    "or install Google Chrome."
                ) from ExceptionGroup("browser launch failures", [bundled_error, chrome_error])
        context = browser.new_context(user_agent=USER_AGENT, locale="en-US")
        page = context.new_page()
        try:
            yield lambda url: _playwright_fetch(page, url)
        finally:
            context.close()
            browser.close()


def _playwright_fetch(page, url: str) -> FetchedHtml:  # noqa: ANN001
    _validate_retry_configuration()
    is_index = urlparse(url).path == urlparse(INDEX_URL).path
    requested_ref = None if is_index else mayo_clinic_ref_from_url(url)
    if is_index:
        _validate_index_url(url)
    retry_delays_seconds: list[float] = []
    last_error: PlaywrightError | None = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        response = None
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightError as error:
            last_error = error
            if attempt == MAX_FETCH_ATTEMPTS:
                break
            delay = _retry_delay_seconds(attempt=attempt)
            retry_delays_seconds.append(delay)
            time.sleep(delay)
            continue

        status_code = response.status if response is not None else None
        if status_code is not None and status_code >= 400:
            if status_code not in RETRIABLE_STATUS_CODES or attempt == MAX_FETCH_ATTEMPTS:
                raise MayoClinicPageUnavailableError(f"Mayo Clinic returned HTTP {status_code} for {url!r}")
            delay = _retry_delay_seconds(attempt=attempt, retry_after=_response_retry_after(response))
            retry_delays_seconds.append(delay)
            time.sleep(delay)
            continue

        try:
            selector = _validate_rendered_route(page, url, is_index=is_index, requested_ref=requested_ref)
            page.wait_for_selector(selector, timeout=15_000)
        except MayoClinicPageUnavailableError:
            raise
        except PlaywrightError as error:
            last_error = error
            if attempt == MAX_FETCH_ATTEMPTS:
                break
            delay = _retry_delay_seconds(attempt=attempt)
            retry_delays_seconds.append(delay)
            time.sleep(delay)
            continue

        html_text = page.content()
        if len(html_text.encode("utf-8")) > MAX_HTML_BYTES:
            raise MayoClinicFetchError(f"Mayo Clinic HTML response exceeds the {MAX_HTML_BYTES}-byte limit")
        return html_text, _browser_receipt(
            url,
            html_text,
            status_code=status_code,
            attempts=attempt,
            retry_delays_seconds=retry_delays_seconds,
        )

    raise MayoClinicPageUnavailableError(
        f"Mayo Clinic page navigation or content rendering failed after {MAX_FETCH_ATTEMPTS} attempts for {url!r}"
    ) from last_error


def _validate_rendered_route(
    page,  # noqa: ANN001
    url: str,
    *,
    is_index: bool,
    requested_ref: MayoClinicArticleRef | None,
) -> str:
    """Validate the browser's final route and return its required content selector."""
    if is_index:
        _validate_index_url(page.url)
        return INDEX_RESULT_SELECTOR
    assert requested_ref is not None
    try:
        final_ref = mayo_clinic_ref_from_url(page.url)
    except MayoClinicFetchError as error:
        raise MayoClinicPageUnavailableError(
            f"Mayo Clinic redirected {url!r} outside a supported article route"
        ) from error
    if final_ref.page_url != requested_ref.page_url:
        raise MayoClinicPageUnavailableError(f"Mayo Clinic redirected {url!r} to a different article")
    return ARTICLE_SELECTOR


def _response_retry_after(response) -> str | None:  # noqa: ANN001
    """Read Retry-After without assuming a specific Playwright response stub."""
    header_value = getattr(response, "header_value", None)
    if callable(header_value):
        return header_value("retry-after")
    headers = getattr(response, "headers", {})
    return headers.get("retry-after") if isinstance(headers, dict) else None


def _retry_delay_seconds(*, attempt: int, retry_after: str | None = None) -> float:
    """Return a bounded Retry-After or exponential delay for one failed attempt."""
    _validate_retry_configuration()
    delay: float | None = None
    if retry_after:
        stripped = retry_after.strip()
        try:
            delay = float(stripped)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(stripped)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                delay = (parsed - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                delay = None
    if delay is None:
        delay = INITIAL_RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
    elif not math.isfinite(delay):
        delay = INITIAL_RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
    return min(MAX_RETRY_DELAY_SECONDS, max(0.0, delay))


def _validate_retry_configuration() -> None:
    """Reject invalid retry constants before browser navigation or sleeping."""
    if (
        isinstance(MAX_FETCH_ATTEMPTS, bool)
        or not isinstance(MAX_FETCH_ATTEMPTS, int)
        or not 1 <= MAX_FETCH_ATTEMPTS <= 10
    ):
        raise ValueError(f"MAX_FETCH_ATTEMPTS must be an integer between 1 and 10; got {MAX_FETCH_ATTEMPTS!r}")
    for name, value in (
        ("INITIAL_RETRY_DELAY_SECONDS", INITIAL_RETRY_DELAY_SECONDS),
        ("MAX_RETRY_DELAY_SECONDS", MAX_RETRY_DELAY_SECONDS),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and greater than zero; got {value!r}")


def build_article_text(
    html_text: str,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
    base_url: str = BASE_URL,
) -> tuple[str, int]:
    """Extract a Mayo condition article section as markdown."""
    doc = lxml_html.fromstring(html_text)
    roots = doc.xpath(
        "//*[@id='main-content']/*[contains(concat(' ', normalize-space(@class), ' '), ' row ')][1]"
        "/*[contains(concat(' ', normalize-space(@class), ' '), ' content ')][1]"
    )
    if not roots:
        roots = doc.xpath(
            "//article[contains(concat(' ', normalize-space(@class), ' '), ' cmp-article ')]"
            "/*[contains(concat(' ', normalize-space(@class), ' '), ' cmp-aside-container ')]"
            "/*[contains(concat(' ', normalize-space(@class), ' '), ' container-child ')]"
            "[.//*[contains(concat(' ', normalize-space(@class), ' '), ' cmp-dita-content ')]][1]"
        )
    if not roots:
        raise MayoClinicFetchError("No readable Mayo Clinic article content found")
    root = roots[0]
    _normalize_article(root)
    section_count = max(1, len(root.xpath(".//*[self::h2 or self::h3 or self::h4 or self::h5 or self::h6]")))
    content = html_to_markdown(
        lxml_html.tostring(root, encoding="unicode"),
        link_mode=link_mode,
        base_url=base_url,
        drop_numeric_citations=False,
    )
    if not content:
        raise MayoClinicFetchError("No readable Mayo Clinic article content found")
    return content, section_count


def _scrape_article(
    fetch: PageFetch,
    item: MayoClinicListingItem,
    *,
    permission_id: str,
    link_mode: LinkMode = LinkMode.KEEP,
    ingestion_mode: str,
) -> ScrapedDocument:
    """Scrape one explicitly configured Mayo article section."""
    ref = item.ref
    html_text, retrieval = fetch(ref.page_url)
    capture_artifact(
        html_text,
        media_type="text/html; charset=utf-8",
        filename=f"mayo-{ref.document_id}.html",
        url=ref.page_url,
        metadata={"representation": "rendered_dom_serialization", "content_scope": "article_section"},
    )
    doc = lxml_html.fromstring(html_text)
    title = _page_title(doc) or ref.title
    content, section_count = build_article_text(html_text, link_mode=link_mode, base_url=ref.page_url)
    metadata: dict[str, object] = {
        "slug": ref.slug,
        "section": ref.section,
        "document_id": ref.document_id,
        "publication": "Mayo Clinic",
        "content_scope": "article_section",
        "license": "All rights reserved",
        "permission_required": True,
        "permission_id": permission_id,
        "ingestion_mode": ingestion_mode,
        "terms_url": TERMS_URL,
    }
    published = _published_date(doc)
    if published:
        metadata["published"] = published
    authors = _authors(doc)
    if authors:
        metadata["authors"] = authors
    description = _meta_value(doc, "description")
    if description:
        metadata["description"] = description
    return ScrapedDocument(
        source="mayoclinic",
        external_id=f"mayo-{ref.document_id}",
        title=title,
        url=ref.page_url,
        content=content,
        section_count=section_count,
        metadata=metadata,
        provenance={
            "retrievals": ([retrieval] if item.discovery_retrieval is None else [item.discovery_retrieval, retrieval]),
            "access_method": ingestion_mode,
            "permission_id": permission_id,
        },
    )


def scrape_mayo_clinic(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
    authorized: bool = False,
    permission_id: str | None = None,
    manifest: Iterable[str] | None = None,
    headless: bool = True,
) -> ScrapeRun:
    """Configure permission-gated manifest, direct-URL, or A-Z ingestion.

    Authorization and permission checks precede URL fetching and browser
    startup. A manifest remains the deterministic production option; without
    one, a direct URL is accepted or the publisher A-Z index is traversed.
    """
    if documents is not None and documents < 1:
        raise ValueError(f"documents must be at least 1; got {documents}")
    if not authorized:
        raise MayoClinicPermissionError(
            "Mayo Clinic ingestion is disabled by default under the publisher's terms. "
            "After obtaining authorization, call scrape_mayo_clinic(..., authorized=True, permission_id=...)."
        )
    normalized_permission_id = clean_text(permission_id or "", drop_numeric_citations=False)
    if not normalized_permission_id:
        raise MayoClinicPermissionError("Mayo Clinic licensed ingestion requires a nonempty permission_id")

    def no_more_pages(_fetch: PageFetch, _page: int) -> Iterable[MayoClinicListingItem]:
        return ()

    if manifest is not None:
        refs = refs_from_manifest(manifest)
        if url is not None:
            target_url = mayo_clinic_ref_from_url(url).page_url
            refs = [ref for ref in refs if ref.page_url == target_url]
        if not refs:
            detail = " matching the requested URL" if url is not None else ""
            raise MayoClinicFetchError(f"The Mayo Clinic URL manifest contains no entries{detail}")
        items = [MayoClinicListingItem(ref) for ref in refs]
        total = len(items) if documents is None else (1 if url is not None else documents)
        ingestion_mode = "licensed_url_manifest"
        listing_page_fn = no_more_pages
    elif url is not None:
        items = [MayoClinicListingItem(mayo_clinic_ref_from_url(url))]
        total = 1
        ingestion_mode = "authorized_direct_url"
        listing_page_fn = no_more_pages
    else:
        items = None
        total = None
        ingestion_mode = "authorized_a_z_discovery"
        seen: set[str] = set()

        def list_discovery_page(fetch: PageFetch, page: int) -> Iterable[MayoClinicListingItem]:
            if page > len(INDEX_LETTERS):
                return ()
            refs, retrieval = list_mayo_clinic_index(fetch, letter=INDEX_LETTERS[page - 1])
            page_items: list[MayoClinicListingItem] = []
            for ref in refs:
                if ref.page_url in seen:
                    continue
                seen.add(ref.page_url)
                page_items.append(MayoClinicListingItem(ref, discovery_retrieval=retrieval))
            return page_items

        listing_page_fn = list_discovery_page

    pending_discovery_failures: list[dict[str, str]] = []
    consecutive_discovery_failures = 0
    emitted = 0

    def scrape_configured_item(fetch: PageFetch, item: MayoClinicListingItem) -> ScrapedDocument | None:
        nonlocal consecutive_discovery_failures, emitted
        try:
            document = _scrape_article(
                fetch,
                item,
                permission_id=normalized_permission_id,
                link_mode=link_mode,
                ingestion_mode=ingestion_mode,
            )
        except MayoClinicPageUnavailableError as error:
            if ingestion_mode != "authorized_a_z_discovery":
                raise
            consecutive_discovery_failures += 1
            pending_discovery_failures.append(
                {
                    "document_id": item.ref.document_id,
                    "url": item.ref.page_url,
                    "error_type": type(error).__name__,
                    "message": " ".join(str(error).split()),
                }
            )
            if consecutive_discovery_failures >= MAX_CONSECUTIVE_DISCOVERY_FAILURES:
                failed_ids = ", ".join(failure["document_id"] for failure in pending_discovery_failures)
                raise MayoClinicFetchError(
                    "Mayo Clinic A-Z discovery stopped after "
                    f"{consecutive_discovery_failures} consecutive unavailable articles ({failed_ids})"
                ) from error
            return None

        consecutive_discovery_failures = 0
        emitted += 1
        if pending_discovery_failures:
            failures = list(pending_discovery_failures)
            document = replace(
                document,
                metadata={**document.metadata, "skipped_unavailable_candidates": failures},
                provenance={**document.provenance, "skipped_unavailable_candidates": failures},
            )
            pending_discovery_failures.clear()
        return document

    discovered_documents = scrape_listing_documents(
        documents=documents,
        client_factory=lambda: _playwright_client(headless=headless),
        first_page_items=items,
        list_page=listing_page_fn,
        scrape_item=scrape_configured_item,
        document_delay_seconds=DOCUMENT_DELAY_SECONDS,
    )

    def iter_documents() -> Iterator[ScrapedDocument]:
        yield from discovered_documents
        expected_documents = 1 if url is not None else documents
        if expected_documents is not None and emitted < expected_documents:
            failure_detail = ""
            if pending_discovery_failures:
                failed_ids = ", ".join(failure["document_id"] for failure in pending_discovery_failures)
                failure_detail = f"; trailing unavailable articles: {failed_ids}"
            raise MayoClinicFetchError(
                f"Mayo Clinic {ingestion_mode} produced {emitted} of {expected_documents} requested documents"
                f"{failure_detail}"
            )

    return ScrapeRun(
        total=total,
        documents=iter_documents(),
    )


def _normalize_article(root: lxml_html.HtmlElement) -> None:
    for references in root.xpath(".//*[contains(concat(' ', normalize-space(@class), ' '), ' references ')]"):
        if not references.xpath("./*[self::h2 or self::h3]"):
            references.insert(0, etree.Element("h2"))
            references[0].text = "References"

    noise = root.xpath(
        ".//script | .//style | .//noscript | .//form | .//button | .//input | .//label | "
        ".//*[@data-nosnippet='true'] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' thin-content-bar ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' requestappt ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' sectionnav ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' pubdate ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' print ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' acces-list-container ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' tableofcontents ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' myc-subscription-form ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' cmp-news-letter-signup-from-model ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' cmp-related-content ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' contentbox ') and "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' myc-subscription-form ')]] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' dialog-backdrop ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' access-modal ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' social-share ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' thin-content-by ')] | "
        ".//a[contains(@href, '/appointments')]"
    )
    for element in noise:
        if element.getparent() is not None:
            element.drop_tree()

    for heading in root.xpath(".//*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6]"):
        if not clean_text(heading.text_content(), drop_numeric_citations=False):
            heading.drop_tree()


def _page_title(doc: lxml_html.HtmlElement) -> str | None:
    values = [clean_text(value, drop_numeric_citations=False) for value in doc.xpath("//h1[1]//text()")]
    return " ".join(value for value in values if value) or None


def _published_date(doc: lxml_html.HtmlElement) -> str | None:
    value = _meta_value(doc, "publishdate")
    if value is None:
        dates = doc.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' pubdate ')][1]/text()")
        value = clean_text(dates[0], drop_numeric_citations=False) if dates else None
    if value is None:
        return None
    for date_format in ("%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    return value


def _authors(doc: lxml_html.HtmlElement) -> list[str]:
    values = doc.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' thin-content-by ')]//text()")
    if not values:
        values = doc.xpath(
            "//*[contains(concat(' ', normalize-space(@class), ' '), ' cmp-meet-mc-staff-button ')]//text()"
        )
    author = clean_text(" ".join(values), drop_numeric_citations=False).removeprefix("By ")
    return [author] if author else []


def _meta_value(doc: lxml_html.HtmlElement, name: str) -> str | None:
    for meta in doc.xpath("//meta[@content]"):
        meta_name = (meta.get("name") or meta.get("property") or "").casefold()
        if meta_name != name.casefold():
            continue
        value = clean_text(meta.get("content"), drop_numeric_citations=False)
        if value:
            return value
    return None


__all__ = [
    "BASE_URL",
    "ARTICLE_SELECTOR",
    "DOCUMENT_DELAY_SECONDS",
    "INITIAL_RETRY_DELAY_SECONDS",
    "INDEX_LETTERS",
    "INDEX_RESULT_SELECTOR",
    "INDEX_URL",
    "MAX_CONSECUTIVE_DISCOVERY_FAILURES",
    "MAX_FETCH_ATTEMPTS",
    "MAX_RETRY_DELAY_SECONDS",
    "RETRIABLE_STATUS_CODES",
    "TERMS_URL",
    "MayoClinicArticleRef",
    "MayoClinicFetchError",
    "MayoClinicListingItem",
    "MayoClinicPageUnavailableError",
    "MayoClinicPermissionError",
    "PageFetch",
    "build_article_text",
    "list_mayo_clinic_index",
    "mayo_clinic_ref_from_url",
    "refs_from_manifest",
    "scrape_mayo_clinic",
]
