"""Permission-gated ingestion of explicitly selected ICRC publications.

The legacy Meditron adapter downloaded an unofficial third-party archive.  This
module deliberately does not reproduce that workflow: it accepts only official
``icrc.org`` publication/document URLs supplied by the operator, and it makes
no request until the caller asserts that the planned conversion and use are
authorized.

ICRC's general website terms permit narrow, intact, unmodified, non-commercial
copying.  Converting a publication to Markdown is a transformation, so the
``authorized`` flag and nonempty ``permission_id`` must represent permission or
another legal basis that is appropriate for the caller's use; ordinary access
to a public page is not treated as that permission.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from lxml import html as lxml_html
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from amfv_datasets.scraping.base import (
    USER_AGENT,
    DownloadedContent,
    DownloadError,
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    UrlPolicy,
    capture_artifact,
    default_client,
    download_content,
    redact_url,
)
from amfv_datasets.scraping.html import LinkMode, clean_text, document_title, html_to_markdown
from amfv_datasets.scraping.pdf import PdfConversionResult, convert_pdf, count_markdown_sections

BASE_URL = "https://www.icrc.org"
TERMS_URL = f"{BASE_URL}/en/copyright-and-terms-use"
DOCUMENT_DELAY_SECONDS = 5.0
PDF_TIMEOUT_SECONDS = 180.0
MAX_HTML_BYTES = 16 * 1024 * 1024
MAX_DOWNLOAD_RETRIES = 4
MAX_RETRY_DELAY_SECONDS = 60.0
MAX_CONSECUTIVE_FAILURES = 20
_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
_ICRC_URL_POLICY = UrlPolicy.allow_hosts("icrc.org", allow_subdomains=True)

_LANDING_PATH_RE = re.compile(r"^/en/(?:publication|document)/(?P<slug>[^/]+)/?$", re.IGNORECASE)
_PDF_PATH_RE = re.compile(r"\.pdf$", re.IGNORECASE)
_SAFE_ID_RE = re.compile(r"[^a-z0-9]+")
_ICRC_COPY_CONSTRAINTS = (
    "non-commercial use",
    "copy kept intact and unmodified under general website terms",
    "source identified",
)

type ClientFactory = Callable[[], AbstractContextManager[httpx.Client]]
type PdfMarkdownConverter = Callable[[bytes, str, str], str | PdfConversionResult]


class IcrcFetchError(ScrapeError):
    """Raised when an explicitly configured ICRC publication cannot be read."""


class IcrcPermissionError(IcrcFetchError):
    """Raised before network I/O when authorization has not been asserted."""


class IcrcDuplicatePublicationError(IcrcFetchError):
    """Raised when two landing-page aliases resolve to the same retained PDF."""


@dataclass(frozen=True)
class IcrcPublicationRef:
    """An operator-supplied official ICRC landing page or PDF."""

    publication_id: str
    title: str
    page_url: str
    direct_pdf: bool = False


@dataclass(frozen=True)
class _PublicationBody:
    """Converted PDF content and its two receipts."""

    markdown: str
    retrieval: dict[str, Any]
    conversion: dict[str, Any]


@dataclass(frozen=True)
class ShopPdfResolution:
    """Official ebook URL exposed by one rendered ICRC shop product variant."""

    pdf_url: str
    retrieval: dict[str, Any]


type ShopPdfResolver = Callable[[str], ShopPdfResolution | None]


def _retry_delay(error: DownloadError, attempt: int) -> float | None:
    """Return a bounded retry delay for transient transport/status failures."""
    cause = error.__cause__
    if isinstance(cause, httpx.HTTPStatusError):
        if cause.response.status_code not in _RETRYABLE_STATUS_CODES:
            return None
        retry_after = cause.response.headers.get("Retry-After")
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    now = datetime.now(retry_at.tzinfo or UTC)
                    delay = max((retry_at - now).total_seconds(), 0.0)
                except (TypeError, ValueError, OverflowError):
                    delay = 0.0
            if delay > 0:
                return min(delay, MAX_RETRY_DELAY_SECONDS)
    elif not isinstance(cause, httpx.HTTPError):
        return None
    return min(2.0**attempt, MAX_RETRY_DELAY_SECONDS)


def _download_with_backoff(client: httpx.Client, url: str, **kwargs: Any) -> DownloadedContent:
    """Download with bounded exponential/Retry-After backoff and audit metadata."""
    retry_delays: list[float] = []
    for attempt in range(MAX_DOWNLOAD_RETRIES + 1):
        try:
            downloaded = download_content(client, url, **kwargs)
        except DownloadError as error:
            delay = _retry_delay(error, attempt)
            if delay is None or attempt >= MAX_DOWNLOAD_RETRIES:
                raise
            retry_delays.append(delay)
            time.sleep(delay)
            continue
        return DownloadedContent(
            data=downloaded.data,
            provenance={
                **downloaded.provenance,
                "attempt_count": attempt + 1,
                "retry_delays_seconds": retry_delays,
            },
        )
    raise AssertionError("bounded retry loop did not return or raise")


def _is_icrc_host(hostname: str | None) -> bool:
    """Return whether a hostname is ICRC-owned rather than a lookalike."""
    host = (hostname or "").lower().rstrip(".")
    return host == "icrc.org" or host.endswith(".icrc.org")


def _normalized_official_url(url: str) -> str:
    """Validate an official ICRC URL and remove query/fragment noise."""
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not _is_icrc_host(parsed.hostname):
        raise IcrcFetchError(f"Enter an official ICRC URL under icrc.org; got {url!r}")
    try:
        port = parsed.port
    except ValueError as error:
        raise IcrcFetchError(f"Enter an official ICRC URL with a valid port; got {url!r}") from error
    default_port = 80 if parsed.scheme == "http" else 443
    if parsed.username is not None or parsed.password is not None or port not in {None, default_port}:
        raise IcrcFetchError(f"ICRC URLs cannot contain credentials or a non-default port; got {url!r}")
    # Canonicalize an accepted HTTP spelling to HTTPS before any request. The
    # reconstructed netloc also drops credentials/default-port syntax already
    # rejected above.
    return urlunsplit(("https", (parsed.hostname or "").casefold().rstrip("."), parsed.path, "", ""))


def icrc_ref_from_url(url: str, *, title: str | None = None) -> IcrcPublicationRef:
    """Validate and normalize one official ICRC publication/document URL."""
    normalized = _normalized_official_url(url)
    parsed = urlsplit(normalized)
    landing_match = _LANDING_PATH_RE.match(parsed.path)
    direct_pdf = bool(_PDF_PATH_RE.search(parsed.path))
    if landing_match is None and not direct_pdf:
        raise IcrcFetchError(
            "Enter an official ICRC /en/publication/... or /en/document/... landing page, "
            f"or an official direct PDF; got {url!r}"
        )
    slug = landing_match.group("slug") if landing_match else PurePosixPath(parsed.path).stem
    publication_id = _safe_id(slug)
    if not publication_id:
        raise IcrcFetchError(f"Could not derive an ICRC publication identifier from {url!r}")
    return IcrcPublicationRef(
        publication_id=publication_id,
        title=title or slug.replace("-", " ").replace("_", " ").title(),
        page_url=normalized,
        direct_pdf=direct_pdf,
    )


def refs_from_manifest(urls: Iterable[str]) -> list[IcrcPublicationRef]:
    """Validate and deduplicate an explicit ICRC publication URL manifest."""
    refs: list[IcrcPublicationRef] = []
    seen: set[str] = set()
    for url in urls:
        ref = icrc_ref_from_url(url)
        if ref.page_url in seen:
            continue
        seen.add(ref.page_url)
        refs.append(ref)
    return refs


def _safe_id(value: str) -> str:
    return _SAFE_ID_RE.sub("-", value.casefold()).strip("-")


def _is_pdf(downloaded: DownloadedContent) -> bool:
    """Recognize an actual PDF rather than trusting a shop redirect or header."""
    return downloaded.data.lstrip()[:5] == b"%PDF-"


def _decode_html(downloaded: DownloadedContent) -> str:
    """Decode a landing page conservatively without persisting it."""
    content_type = str(downloaded.provenance.get("content_type") or "").casefold()
    if "application/pdf" in content_type or _is_pdf(downloaded):
        raise IcrcFetchError(
            f"Expected an ICRC landing page but received a PDF from {downloaded.provenance['final_url']!r}"
        )
    return downloaded.data.decode("utf-8", errors="replace")


def _meta_value(doc: lxml_html.HtmlElement, *names: str) -> str | None:
    wanted = {name.casefold() for name in names}
    for meta in doc.xpath("//meta[@content]"):
        key = (meta.get("name") or meta.get("property") or "").casefold()
        if key not in wanted:
            continue
        value = clean_text(meta.get("content"), drop_numeric_citations=False)
        if value:
            return value
    return None


def _publication_date(doc: lxml_html.HtmlElement) -> str | None:
    value = _meta_value(doc, "article:published_time", "date", "dcterms.date")
    if value:
        return value
    values = doc.xpath("//time[@datetime][1]/@datetime | //time[1]/text()")
    return clean_text(values[0], drop_numeric_citations=False) if values else None


def _landing_markdown(html_text: str, *, page_url: str, link_mode: LinkMode) -> str:
    """Extract the publication description while omitting navigation and commerce UI."""
    doc = lxml_html.fromstring(html_text)
    roots = doc.xpath("//main[1] | //article[1]")
    root = roots[0] if roots else doc
    for node in root.xpath(
        ".//script | .//style | .//noscript | .//form | .//button | .//nav | .//aside | "
        ".//*[@role='navigation'] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' social-sharing ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' share-widget ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' breadcrumb ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' related-content ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' related-articles ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' newsletter-box ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' promo ')] | "
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' promotional ')]"
    ):
        if node.getparent() is not None:
            node.drop_tree()
    content = html_to_markdown(
        lxml_html.tostring(root, encoding="unicode"),
        link_mode=link_mode,
        base_url=page_url,
        drop_numeric_citations=False,
    )
    if content:
        return content
    description = _meta_value(doc, "description", "og:description")
    return description or ""


def _pdf_candidates(html_text: str, *, page_url: str) -> list[str]:
    """Return official PDF/download candidates in document order."""
    doc = lxml_html.fromstring(html_text)
    candidates: list[str] = []
    seen: set[str] = set()
    for link in doc.xpath("//a[@href]"):
        raw_url = link.get("href", "")
        text = clean_text(link.text_content(), drop_numeric_citations=False).casefold()
        try:
            url = _normalized_official_url(urljoin(page_url, raw_url))
        except IcrcFetchError:
            continue
        parsed = urlsplit(url)
        path = parsed.path.casefold()
        shop_publication = (parsed.hostname or "").casefold().startswith("shop.") and any(
            marker in text for marker in ("get", "publication", "download")
        )
        looks_like_download = path.endswith(".pdf") or "pdf" in text or shop_publication
        if not looks_like_download or url in seen:
            continue
        seen.add(url)
        candidates.append(url)
    return candidates


def _is_shop_product_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.hostname or "").casefold() == "shop.icrc.org" and parsed.path.casefold().endswith(".html")


def _validate_shop_download_url(url: str) -> str:
    """Validate the SKU-bearing official ebook route without stripping its query."""
    parsed = urlsplit(url.strip())
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != "shop.icrc.org"
        or parsed.path != "/download/ebook"
        or not parsed.query.startswith("sku=")
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise IcrcFetchError("ICRC shop resolution did not expose a valid official ebook download URL")
    return url


def _shop_browser_receipt(
    requested_url: str,
    final_url: str,
    html_text: str,
    *,
    status_code: int | None,
) -> dict[str, Any]:
    data = html_text.encode("utf-8")
    completed_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "requested_url": requested_url,
        "final_url": final_url,
        "transport": "playwright-ephemeral-browser",
        "download_completed_at_utc": completed_at,
        "downloaded_at_utc": completed_at,
        "status_code": status_code,
        "content_type": "text/html; charset=utf-8",
        "byte_count": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "purpose": "resolve_official_shop_pdf_variant",
    }


@contextmanager
def _shop_playwright_page() -> Iterator[Any]:
    """Open one ephemeral browser page for the authorized product variant flow."""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as bundled_error:
            try:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
            except PlaywrightError as chrome_error:
                raise IcrcFetchError(
                    "Could not start Chromium to resolve the ICRC PDF variant. Run "
                    "`uv run --group pdf playwright install chromium` or install Google Chrome."
                ) from ExceptionGroup("browser launch failures", [bundled_error, chrome_error])
        context = browser.new_context(user_agent=USER_AGENT, locale="en-US")
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()


def resolve_icrc_shop_pdf(shop_url: str) -> ShopPdfResolution | None:
    """Render one official product page and select its English PDF variant."""
    normalized_shop_url = _normalized_official_url(shop_url)
    if not _is_shop_product_url(normalized_shop_url):
        return None
    with _shop_playwright_page() as page:
        response = page.goto(shop_url, wait_until="domcontentloaded", timeout=45_000)
        status_code = response.status if response is not None else None
        if status_code is not None and status_code >= 400:
            raise IcrcFetchError(f"ICRC shop returned HTTP {status_code} for the product page")
        final_url = str(page.url)
        if not _is_shop_product_url(_normalized_official_url(final_url)):
            raise IcrcFetchError("ICRC shop navigation left the official product route")
        try:
            document_type = page.get_by_role("combobox", name="Type de document *")
            document_type.select_option(label="PDF")
            language = page.get_by_role("combobox").nth(1)
            language.select_option(label="English")
            download = page.locator("a[href*='/download/ebook']").first
            download.wait_for(state="visible", timeout=20_000)
            raw_download_url = download.get_attribute("href")
        except PlaywrightError as error:
            raise IcrcFetchError("ICRC shop did not expose an English PDF variant") from error
        if not raw_download_url:
            raise IcrcFetchError("ICRC shop exposed a download control without a URL")
        pdf_url = _validate_shop_download_url(urljoin(final_url, raw_download_url))
        html_text = page.content()
        if len(html_text.encode("utf-8")) > MAX_HTML_BYTES:
            raise IcrcFetchError(f"ICRC shop HTML exceeds the {MAX_HTML_BYTES}-byte limit")
        return ShopPdfResolution(
            pdf_url=pdf_url,
            retrieval=_shop_browser_receipt(
                shop_url,
                final_url,
                html_text,
                status_code=status_code,
            ),
        )


def _default_pdf_converter(data: bytes, title: str, publication_id: str) -> PdfConversionResult:
    """Convert an ICRC PDF through the shared in-memory backend."""
    return convert_pdf(data, running_header=title, name=f"{publication_id}.pdf")


def _markdown_title(markdown: str) -> str | None:
    """Return the first level-one heading from converted PDF Markdown."""
    for line in markdown.splitlines():
        match = re.fullmatch(r"#\s+(.+?)\s*", line)
        if match:
            title = clean_text(match.group(1), drop_numeric_citations=False)
            return title or None
    return None


def _converted_body(
    client: httpx.Client,
    *,
    pdf_url: str,
    title: str,
    publication_id: str,
    pdf_converter: PdfMarkdownConverter,
    seen_pdf_sha256: set[str] | None = None,
) -> _PublicationBody:
    """Download and convert one official direct PDF entirely in memory."""
    downloaded = _download_with_backoff(
        client,
        pdf_url,
        timeout=PDF_TIMEOUT_SECONDS,
        url_policy=_ICRC_URL_POLICY,
    )
    _normalized_official_url(str(downloaded.provenance["final_url"]))
    if not _is_pdf(downloaded):
        raise IcrcFetchError(
            f"The ICRC download candidate did not resolve to a PDF (final URL: {downloaded.provenance['final_url']!r})"
        )
    payload_sha256 = str(downloaded.provenance["sha256"])
    if seen_pdf_sha256 is not None and payload_sha256 in seen_pdf_sha256:
        raise IcrcDuplicatePublicationError(
            "ICRC publication alias resolved to a PDF payload already emitted by this manifest run "
            f"(sha256: {payload_sha256})"
        )
    converted = pdf_converter(downloaded.data, title, publication_id)
    if isinstance(converted, PdfConversionResult):
        markdown = converted.markdown.strip()
        conversion = converted.provenance
    else:
        markdown = converted.strip()
        conversion = {
            "backend": "injected",
            "input_bytes": len(downloaded.data),
            "input_sha256": downloaded.provenance["sha256"],
        }
    if not markdown:
        raise IcrcFetchError(f"ICRC PDF {pdf_url!r} produced no readable Markdown")
    # Capture only after conversion succeeds. A skipped manifest candidate must
    # never leak its bytes into the next successful review bundle.
    capture_artifact(
        downloaded.data,
        media_type="application/pdf",
        filename=f"{publication_id}.pdf",
        url=str(downloaded.provenance.get("final_url") or pdf_url),
        metadata={"representation": "downloaded_pdf"},
    )
    if seen_pdf_sha256 is not None:
        seen_pdf_sha256.add(payload_sha256)
    return _PublicationBody(markdown=markdown, retrieval=downloaded.provenance, conversion=conversion)


def scrape_publication(
    client: httpx.Client,
    ref: IcrcPublicationRef,
    *,
    permission_id: str,
    link_mode: LinkMode = LinkMode.KEEP,
    pdf_converter: PdfMarkdownConverter | None = None,
    shop_pdf_resolver: ShopPdfResolver | None = None,
    seen_pdf_sha256: set[str] | None = None,
) -> ScrapedDocument:
    """Ingest one explicitly authorized official ICRC publication."""
    normalized_permission_id = clean_text(permission_id, drop_numeric_citations=False)
    if not normalized_permission_id:
        raise IcrcPermissionError("ICRC ingestion requires a nonempty permission_id")
    converter = pdf_converter or _default_pdf_converter
    if ref.direct_pdf:
        try:
            body = _converted_body(
                client,
                pdf_url=ref.page_url,
                title=ref.title,
                publication_id=ref.publication_id,
                pdf_converter=converter,
            )
        except ScrapeError as error:
            raise IcrcFetchError(str(error)) from error
        return _build_document(
            ref=ref,
            title=_markdown_title(body.markdown) or ref.title,
            content=body.markdown,
            content_scope="full_pdf",
            pdf_url=ref.page_url,
            pdf_status="converted",
            landing_retrieval=None,
            shop_retrieval=None,
            body=body,
            page_metadata={},
            permission_id=normalized_permission_id,
        )

    try:
        landing = _download_with_backoff(client, ref.page_url, url_policy=_ICRC_URL_POLICY)
    except ScrapeError as error:
        raise IcrcFetchError(str(error)) from error
    _normalized_official_url(str(landing.provenance["final_url"]))
    html_text = _decode_html(landing)
    doc = lxml_html.fromstring(html_text)
    title = _meta_value(doc, "og:title", "citation_title") or document_title(
        html_text,
        fallback=ref.title,
        suffixes=(" | International Committee of the Red Cross", " | ICRC"),
    )
    landing_content = _landing_markdown(html_text, page_url=ref.page_url, link_mode=link_mode)
    candidates = _pdf_candidates(html_text, page_url=ref.page_url)
    body: _PublicationBody | None = None
    pdf_error: str | None = None
    selected_pdf_url: str | None = None
    shop_retrieval: dict[str, Any] | None = None
    for candidate in candidates:
        try:
            resolved_candidate = candidate
            if shop_pdf_resolver is not None and _is_shop_product_url(candidate):
                resolution = shop_pdf_resolver(candidate)
                if resolution is None:
                    raise IcrcFetchError("ICRC shop product page exposed no authorized PDF variant")
                resolved_candidate = _validate_shop_download_url(resolution.pdf_url)
                shop_retrieval = resolution.retrieval
            selected_pdf_url = resolved_candidate
            body = _converted_body(
                client,
                pdf_url=resolved_candidate,
                title=title,
                publication_id=ref.publication_id,
                pdf_converter=converter,
                seen_pdf_sha256=seen_pdf_sha256,
            )
        except IcrcDuplicatePublicationError:
            raise
        except (httpx.HTTPError, ScrapeError) as error:
            pdf_error = str(error)
            continue
        break

    if body is not None:
        content = f"{landing_content}\n\n## Full publication\n\n{body.markdown}" if landing_content else body.markdown
        content_scope = "landing_page_and_full_pdf"
        pdf_status = "converted"
    else:
        if not landing_content:
            raise IcrcFetchError(f"No readable landing-page content or resolvable PDF for {ref.page_url!r}")
        content = landing_content
        content_scope = "landing_page_only"
        pdf_status = "unresolved" if candidates else "no_direct_pdf_link"

    page_metadata = {
        "publication_date": _publication_date(doc),
        "description": _meta_value(doc, "description", "og:description"),
        "language": doc.get("lang") or _meta_value(doc, "language", "og:locale"),
        "pdf_error": pdf_error,
        "pdf_candidates": candidates,
        "resolution_action": (
            None
            if body is not None
            else "Provide an official direct PDF URL in the authorized manifest or resolve access with ICRC."
        ),
    }
    document = _build_document(
        ref=ref,
        title=title,
        content=content,
        content_scope=content_scope,
        pdf_url=selected_pdf_url,
        pdf_status=pdf_status,
        landing_retrieval=landing.provenance,
        shop_retrieval=shop_retrieval,
        body=body,
        page_metadata=page_metadata,
        permission_id=normalized_permission_id,
    )
    # Manifest mode may skip a malformed or unavailable landing page and keep
    # consuming candidates with the same capture sink. Retain the landing bytes
    # only after this candidate has produced a document, otherwise its artifact
    # could be attached to the next unrelated success.
    capture_artifact(
        landing.data,
        media_type=str(landing.provenance.get("content_type") or "text/html; charset=utf-8"),
        filename=f"{ref.publication_id}-landing.html",
        url=str(landing.provenance.get("final_url") or ref.page_url),
        role="landing_page",
        metadata={"representation": "downloaded_http_body"},
    )
    return document


def _build_document(
    *,
    ref: IcrcPublicationRef,
    title: str,
    content: str,
    content_scope: str,
    pdf_url: str | None,
    pdf_status: str,
    landing_retrieval: dict[str, Any] | None,
    shop_retrieval: dict[str, Any] | None,
    body: _PublicationBody | None,
    page_metadata: dict[str, Any],
    permission_id: str,
) -> ScrapedDocument:
    """Assemble the stable metadata/provenance envelope for an ICRC record."""
    external_id = (
        ref.publication_id if ref.publication_id.casefold().startswith("icrc-") else f"icrc-{ref.publication_id}"
    )
    source_format_types = (["html"] if landing_retrieval is not None else []) + (["pdf"] if body else [])
    source_media_types = (["text/html"] if landing_retrieval is not None else []) + (
        ["application/pdf"] if body else []
    )
    retrievals = [
        receipt for receipt in (landing_retrieval, shop_retrieval, body.retrieval if body else None) if receipt
    ]
    conversions = [body.conversion] if body else []
    return ScrapedDocument(
        source="icrc",
        external_id=external_id,
        title=title,
        url=ref.page_url,
        content=content,
        section_count=count_markdown_sections(content),
        metadata={
            "publication_id": ref.publication_id,
            "publication": "International Committee of the Red Cross",
            "content_scope": content_scope,
            "source_format_types": source_format_types,
            "source_media_types": source_media_types,
            "pdf_url": redact_url(pdf_url) if pdf_url else None,
            "pdf_resolution_status": pdf_status,
            "pdf_retrieval": body.retrieval if body else None,
            "pdf_conversion": body.conversion if body else None,
            "landing_retrieval": landing_retrieval,
            "shop_resolution_retrieval": shop_retrieval,
            "license": "ICRC copyright; permission or an applicable legal basis required for transformed corpus use",
            "permission_required": True,
            "authorization_asserted": True,
            "permission_id": permission_id,
            "terms_url": TERMS_URL,
            "general_copy_constraints": list(_ICRC_COPY_CONSTRAINTS),
            **page_metadata,
        },
        provenance={
            "retrievals": retrievals,
            "conversions": conversions,
            "access_basis": "explicit_operator_authorization",
            "authorization_asserted": True,
            "permission_id": permission_id,
        },
    )


def scrape_icrc(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
    authorized: bool = False,
    permission_id: str | None = None,
    manifest: Iterable[str] | None = None,
    client_factory: ClientFactory = default_client,
    pdf_converter: PdfMarkdownConverter | None = None,
    shop_pdf_resolver: ShopPdfResolver | None = resolve_icrc_shop_pdf,
) -> ScrapeRun:
    """Configure permission-gated ICRC ingestion from one URL or a manifest.

    No catalogue discovery is implemented.  Listing mode requires a caller-
    supplied manifest so this adapter cannot expand its own collection scope.
    Authorization and its permission reference are checked before URL parsing
    or client construction.
    """
    if documents is not None and documents < 1:
        raise ValueError(f"documents must be at least 1; got {documents}")
    if not authorized:
        raise IcrcPermissionError(
            "ICRC Markdown ingestion is disabled by default because conversion is not an intact, unmodified copy. "
            "After obtaining permission or confirming another legal basis, pass authorized=True with one official "
            "publication URL or an explicit manifest."
        )
    normalized_permission_id = clean_text(permission_id or "", drop_numeric_citations=False)
    if not normalized_permission_id:
        raise IcrcPermissionError("ICRC ingestion requires a nonempty permission_id")
    if url is not None and manifest is not None:
        raise ValueError("Pass either url or manifest, not both")

    if url is not None:
        ref = icrc_ref_from_url(url)

        def scrape_url() -> Iterator[ScrapedDocument]:
            with client_factory() as client:
                yield scrape_publication(
                    client,
                    ref,
                    permission_id=normalized_permission_id,
                    link_mode=link_mode,
                    pdf_converter=pdf_converter,
                    shop_pdf_resolver=shop_pdf_resolver,
                )

        return ScrapeRun(documents=scrape_url(), total=1)

    if manifest is None:
        raise IcrcFetchError(
            "ICRC listing mode requires an explicit official publication URL manifest; automatic discovery and the "
            "legacy third-party archive are disabled."
        )
    refs = refs_from_manifest(manifest)
    if not refs:
        raise IcrcFetchError("The ICRC publication URL manifest is empty")
    total = len(refs) if documents is None else min(documents, len(refs))

    def scrape_manifest() -> Iterator[ScrapedDocument]:
        with client_factory() as client:
            attempted = 0
            emitted = 0
            consecutive_failures = 0
            pending_failures: list[dict[str, Any]] = []
            last_error: ScrapeError | None = None
            seen_pdf_sha256: set[str] = set()
            for ref in refs:
                if documents is not None and emitted >= documents:
                    return
                if attempted and DOCUMENT_DELAY_SECONDS:
                    time.sleep(DOCUMENT_DELAY_SECONDS)
                attempted += 1
                try:
                    document = scrape_publication(
                        client,
                        ref,
                        permission_id=normalized_permission_id,
                        link_mode=link_mode,
                        pdf_converter=pdf_converter,
                        shop_pdf_resolver=shop_pdf_resolver,
                        seen_pdf_sha256=seen_pdf_sha256,
                    )
                except ScrapeError as error:
                    last_error = error
                    consecutive_failures += 1
                    pending_failures.append(
                        {
                            "publication_id": ref.publication_id,
                            "url": redact_url(ref.page_url),
                            "error_type": type(error).__name__,
                            "message": " ".join(str(error).split()),
                        }
                    )
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        break
                    continue
                consecutive_failures = 0
                if pending_failures:
                    document = ScrapedDocument(
                        source=document.source,
                        external_id=document.external_id,
                        title=document.title,
                        url=document.url,
                        content=document.content,
                        section_count=document.section_count,
                        metadata={**document.metadata, "skipped_manifest_candidates": list(pending_failures)},
                        provenance={
                            **document.provenance,
                            "skipped_manifest_candidates": list(pending_failures),
                        },
                    )
                    pending_failures.clear()
                yield document
                emitted += 1
            if documents is not None and emitted >= documents:
                return
            if documents is not None and emitted < documents:
                detail = f"; last failure: {last_error}" if last_error is not None else ""
                raise IcrcFetchError(
                    f"ICRC manifest could not produce the requested {documents} documents after "
                    f"{attempted} candidates; emitted {emitted}{detail}"
                ) from last_error
            if last_error is not None:
                raise IcrcFetchError(
                    "ICRC manifest could not produce all configured documents after "
                    f"{attempted} candidates; emitted {emitted}; last failure: {last_error}"
                ) from last_error

    return ScrapeRun(total=total, documents=scrape_manifest())


__all__ = [
    "BASE_URL",
    "DOCUMENT_DELAY_SECONDS",
    "PDF_TIMEOUT_SECONDS",
    "MAX_HTML_BYTES",
    "MAX_CONSECUTIVE_FAILURES",
    "MAX_DOWNLOAD_RETRIES",
    "MAX_RETRY_DELAY_SECONDS",
    "TERMS_URL",
    "ClientFactory",
    "IcrcFetchError",
    "IcrcDuplicatePublicationError",
    "IcrcPermissionError",
    "IcrcPublicationRef",
    "PdfMarkdownConverter",
    "ShopPdfResolution",
    "ShopPdfResolver",
    "icrc_ref_from_url",
    "refs_from_manifest",
    "resolve_icrc_shop_pdf",
    "scrape_icrc",
    "scrape_publication",
]
