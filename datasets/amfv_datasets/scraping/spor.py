"""Read the historical SPOR guideline asset map through explicit manifests.

The SPOR Evidence Alliance asset map is a static inventory that is current only
through April 2018.  It is neither a current guideline crawler nor an endorsement
of the linked documents.  This adapter can read link annotations from the
official report PDF entirely in memory, or consume an operator-provided JSON/URL
manifest.  It downloads only direct PDF entries and never follows publisher HTML
pages to discover more material.

External guideline downloads require both an authorization assertion and a
nonempty permission reference because the asset map does not grant
redistribution or transformation rights for third-party publications.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from amfv_datasets.scraping.base import (
    DnsResolver,
    DownloadedContent,
    DownloadError,
    RequestStartPacer,
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    UrlPolicy,
    capture_artifact,
    default_client,
    download_content,
    redact_url,
)
from amfv_datasets.scraping.html import LinkMode, clean_text
from amfv_datasets.scraping.pdf import PdfConversionResult, convert_pdf, count_markdown_sections

try:
    from pypdf import PdfReader as _PdfReader
except ImportError:
    _PdfReader = None

BASE_URL = "https://sporevidencealliance.ca"
ASSET_MAP_PAGE_URL = f"{BASE_URL}/key-activities/cpg-asset-map/"
ASSET_MAP_PDF_URL = (
    f"{BASE_URL}/wp-content/uploads/2018/04/SPOR-Evidence-Alliance_Asset-Map-of-Canadian-CPGs_Reportv3.pdf"
)
ASSET_MAP_CURRENT_THROUGH = "2018-04"
DOCUMENT_DELAY_SECONDS = 5.0
PDF_TIMEOUT_SECONDS = 180.0
PUBLISHER_CONNECT_TIMEOUT_SECONDS = 15.0
MAX_DISCOVERY_FAILURES = 128
MAX_DOWNLOAD_RETRIES = 4
MAX_TRANSPORT_RETRIES = 1
MAX_RETRY_DELAY_SECONDS = 60.0
HOST_CIRCUIT_FAILURES = 2
_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

_ASSET_MAP_HOSTS = {"sporevidencealliance.ca", "www.sporevidencealliance.ca"}
_ASSET_MAP_PATH = "/wp-content/uploads/2018/04/spor-evidence-alliance_asset-map-of-canadian-cpgs_reportv3.pdf"
_ASSET_MAP_URL_POLICY = UrlPolicy.allow_hosts(*_ASSET_MAP_HOSTS)
_PUBLIC_PUBLISHER_URL_POLICY = UrlPolicy.public_web(allow_http=True)

type ClientFactory = Callable[[], AbstractContextManager[httpx.Client]]
type PdfMarkdownConverter = Callable[[bytes, str, str], str | PdfConversionResult]


class SporFetchError(ScrapeError):
    """Raised when a configured SPOR asset-map record cannot be read."""


class SporPermissionError(SporFetchError):
    """Raised before I/O when external guideline access is unauthorized."""


def _retry_delay(error: DownloadError, attempt: int) -> float | None:
    """Return a bounded retry delay for a transient publisher failure."""
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
    elif isinstance(cause, httpx.HTTPError):
        if attempt >= MAX_TRANSPORT_RETRIES:
            return None
    else:
        return None
    return min(2.0**attempt, MAX_RETRY_DELAY_SECONDS)


def _transport_failure(error: BaseException) -> bool:
    """Return whether a failure indicates an unavailable publisher host."""
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, httpx.TransportError):
            return True
        current = current.__cause__
    return False


def _download_with_backoff(client: httpx.Client, url: str, **kwargs: Any) -> DownloadedContent:
    """Download with bounded backoff while preserving attempt metadata."""
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


@dataclass(frozen=True)
class SporGuidelineRef:
    """A direct guideline PDF explicitly selected from the asset map."""

    guideline_id: str
    title: str
    pdf_url: str
    publisher: str | None = None
    document_license: str | None = None
    asset_map_page: int | None = None


type ManifestEntry = str | Mapping[str, Any] | SporGuidelineRef
type AnnotationParser = Callable[[bytes], Iterable[ManifestEntry]]


@dataclass(frozen=True)
class _AssetMapSource:
    """Resolved guideline references and optional report retrieval receipt."""

    refs: list[SporGuidelineRef]
    retrieval: dict[str, Any] | None
    adapter: str


def _normalize_asset_map_url(url: str) -> str:
    """Accept only the official, fixed 2018 SPOR report PDF."""
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").casefold() not in _ASSET_MAP_HOSTS:
        raise SporFetchError(f"Enter the official SPOR asset-map PDF URL under sporevidencealliance.ca; got {url!r}")
    try:
        port = parsed.port
    except ValueError as error:
        raise SporFetchError(f"The SPOR asset-map URL contains an invalid port; got {url!r}") from error
    default_port = 80 if parsed.scheme == "http" else 443
    if parsed.username is not None or parsed.password is not None or port not in {None, default_port}:
        raise SporFetchError("The SPOR asset-map URL cannot contain credentials or a non-default port")
    if parsed.path.casefold() != _ASSET_MAP_PATH:
        raise SporFetchError(
            f"Only the fixed April 2018 SPOR Evidence Alliance CPG Asset Map PDF is accepted; got {url!r}"
        )
    return urlunsplit(parsed._replace(query="", fragment=""))


def _normalize_external_url(url: str, *, require_pdf_path: bool) -> str:
    """Validate an external HTTP URL while blocking local-network targets."""
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SporFetchError(f"SPOR manifest entries must be direct HTTP(S) PDF URLs; got {url!r}")
    try:
        port = parsed.port
    except ValueError as error:
        raise SporFetchError(f"SPOR manifest URL contains an invalid port; got {url!r}") from error
    default_port = 80 if parsed.scheme == "http" else 443
    if parsed.username is not None or parsed.password is not None or port not in {None, default_port}:
        raise SporFetchError("SPOR manifest URLs cannot contain credentials or a non-default port")
    host = parsed.hostname.casefold().rstrip(".")
    if host == "localhost" or host.endswith(".localhost") or "." not in host:
        raise SporFetchError(f"SPOR manifest URLs cannot target a local hostname; got {url!r}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise SporFetchError(f"SPOR manifest URLs must use publisher hostnames, not IP addresses; got {url!r}")
    if require_pdf_path and not parsed.path.casefold().endswith(".pdf"):
        raise SporFetchError(
            f"SPOR ingestion accepts only explicit direct PDF URLs; publisher HTML crawling is disabled. Got {url!r}"
        )
    return urlunsplit(parsed._replace(fragment=""))


def _normalize_direct_pdf_url(url: str) -> str:
    """Validate an explicit direct-PDF manifest URL."""
    return _normalize_external_url(url, require_pdf_path=True)


def _manifest_dedup_key(url: str) -> tuple[str, str, str]:
    """Return a scheme-insensitive identity for an already validated PDF URL.

    The historical asset map contains HTTP and HTTPS spellings of the same
    publisher file. Both resolve to one payload, so treating them as separate
    candidates can silently reduce an exact-N sample's unique-document count.
    Paths and queries remain byte-sensitive because publishers may use either
    to distinguish documents.
    """
    parsed = urlsplit(url)
    return ((parsed.hostname or "").casefold().rstrip("."), parsed.path, parsed.query)


def _stable_guideline_id(url: str) -> str:
    """Build a stable compact identifier without trusting publisher filenames."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def _entry_value(entry: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in entry:
            return entry[key]
    return None


def _ref_from_entry(entry: ManifestEntry) -> SporGuidelineRef:
    """Normalize a string/object manifest entry into a direct-PDF reference."""
    if isinstance(entry, SporGuidelineRef):
        url = _normalize_direct_pdf_url(entry.pdf_url)
        return SporGuidelineRef(
            guideline_id=entry.guideline_id or _stable_guideline_id(url),
            title=entry.title,
            pdf_url=url,
            publisher=entry.publisher,
            document_license=entry.document_license,
            asset_map_page=entry.asset_map_page,
        )
    if isinstance(entry, str):
        url = _normalize_direct_pdf_url(entry)
        filename = PurePosixPath(urlsplit(url).path).stem
        return SporGuidelineRef(
            guideline_id=_stable_guideline_id(url),
            title=filename.replace("-", " ").replace("_", " ").strip().title() or "Guideline",
            pdf_url=url,
        )
    if not isinstance(entry, Mapping):
        raise SporFetchError("SPOR manifest entries must be URL strings or JSON objects")
    raw_url = _entry_value(entry, "url", "pdf_url", "download_url", "uri")
    if not isinstance(raw_url, str):
        raise SporFetchError("Each SPOR manifest object requires a direct PDF url")
    url = _normalize_direct_pdf_url(raw_url)
    raw_title = _entry_value(entry, "title", "name", "guideline_title")
    title = clean_text(str(raw_title), drop_numeric_citations=False) if raw_title else ""
    if not title:
        title = PurePosixPath(urlsplit(url).path).stem.replace("-", " ").replace("_", " ").title()
    raw_id = _entry_value(entry, "id", "guideline_id", "external_id")
    guideline_id = clean_text(str(raw_id), drop_numeric_citations=False) if raw_id else _stable_guideline_id(url)
    publisher = _entry_value(entry, "publisher", "developer", "organization")
    document_license = _entry_value(entry, "license", "document_license", "rights")
    page = _entry_value(entry, "asset_map_page", "page")
    return SporGuidelineRef(
        guideline_id=guideline_id,
        title=title,
        pdf_url=url,
        publisher=str(publisher).strip() if publisher else None,
        document_license=str(document_license).strip() if document_license else None,
        asset_map_page=page if isinstance(page, int) and page > 0 else None,
    )


def refs_from_manifest(entries: Iterable[ManifestEntry]) -> list[SporGuidelineRef]:
    """Validate and deduplicate an explicit direct-PDF manifest."""
    refs: list[SporGuidelineRef] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        ref = _ref_from_entry(entry)
        dedup_key = _manifest_dedup_key(ref.pdf_url)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        refs.append(ref)
    return refs


def refs_from_json_manifest(value: str | bytes | Mapping[str, Any] | list[Any]) -> list[SporGuidelineRef]:
    """Parse a portable JSON adapter for environments without annotation support.

    Accepted top-level shapes are an array, or an object containing one of
    ``documents``, ``guidelines``, ``items``, or ``urls``.
    """
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            payload: Any = json.loads(value)
        except json.JSONDecodeError as error:
            raise SporFetchError("Could not decode the SPOR JSON manifest") from error
    else:
        payload = value
    if isinstance(payload, Mapping):
        entries = next(
            (
                payload[key]
                for key in ("documents", "guidelines", "items", "urls")
                if key in payload and isinstance(payload[key], list)
            ),
            None,
        )
        if entries is None:
            raise SporFetchError("SPOR JSON manifest object requires a documents, guidelines, items, or urls array")
    elif isinstance(payload, list):
        entries = payload
    else:
        raise SporFetchError("SPOR JSON manifest must be an array or object")
    return refs_from_manifest(entries)


def _annotation_entries(data: bytes) -> list[dict[str, Any]]:
    """Extract URI link annotations from PDF bytes without temporary files."""
    if _PdfReader is None:
        raise SporFetchError(
            "PDF annotation parsing is unavailable in this environment. Provide manifest_json (or manifest) "
            "containing explicit direct-PDF URLs exported from the SPOR asset map."
        )
    try:
        reader = _PdfReader(BytesIO(data))
    except Exception as error:  # noqa: BLE001 - pypdf raises several parse-specific types
        raise SporFetchError(f"Could not parse the SPOR asset-map PDF: {error}") from error
    entries: list[dict[str, Any]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        for annotation_ref in page.get("/Annots", ()) or ():
            try:
                annotation = annotation_ref.get_object()
                action = annotation.get("/A")
                action = action.get_object() if hasattr(action, "get_object") else action
                uri = action.get("/URI") if action else None
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if isinstance(uri, str):
                entries.append({"url": uri, "asset_map_page": page_number})
    return entries


def _pdf_entries_from_asset_map(
    data: bytes,
    *,
    annotation_parser: AnnotationParser | None,
) -> list[SporGuidelineRef]:
    """Keep only direct PDFs among an asset-map PDF's many annotations."""
    parser = annotation_parser or _annotation_entries
    parsed = parser(data)
    refs: list[SporGuidelineRef] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in parsed:
        try:
            ref = _ref_from_entry(entry)
        except SporFetchError:
            # The report legitimately links publisher pages and references.
            # Those are inventory context, never implicit crawl targets.
            continue
        dedup_key = _manifest_dedup_key(ref.pdf_url)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        refs.append(ref)
    if not refs:
        raise SporFetchError(
            "The SPOR asset-map PDF contained no usable direct PDF annotations. Provide an explicit JSON manifest "
            "rather than crawling publisher HTML pages."
        )
    return refs


def _provided_asset_map_receipt(data: bytes) -> dict[str, Any]:
    """Record caller-provided bytes without implying an HTTP retrieval."""
    digest = hashlib.sha256(data).hexdigest()
    return {
        "requested_url": None,
        "final_url": None,
        "transport": "caller-provided-memory",
        "content_type": "application/pdf",
        "byte_count": len(data),
        "sha256": digest,
        "payload_bytes": len(data),
        "payload_sha256": digest,
        "payload_hash_algorithm": "sha256",
        "payload_representation": "caller_provided_bytes",
    }


def _is_pdf(downloaded: DownloadedContent) -> bool:
    return downloaded.data.lstrip()[:5] == b"%PDF-"


def _resolve_source(
    client: httpx.Client,
    *,
    url: str | None,
    asset_map_pdf: bytes | None,
    manifest: Iterable[ManifestEntry] | None,
    manifest_json: str | bytes | Mapping[str, Any] | list[Any] | None,
    annotation_parser: AnnotationParser | None,
    dns_resolver: DnsResolver | None,
) -> _AssetMapSource:
    """Resolve exactly one fixed-report or explicit-manifest input."""
    configured = sum(value is not None for value in (asset_map_pdf, manifest, manifest_json))
    if configured > 1 or (url is not None and configured):
        raise ValueError("Pass exactly one of url, asset_map_pdf, manifest, or manifest_json")
    if manifest is not None:
        return _AssetMapSource(refs=refs_from_manifest(manifest), retrieval=None, adapter="explicit_url_manifest")
    if manifest_json is not None:
        return _AssetMapSource(
            refs=refs_from_json_manifest(manifest_json),
            retrieval=None,
            adapter="explicit_json_manifest",
        )
    if asset_map_pdf is not None:
        if not asset_map_pdf.lstrip().startswith(b"%PDF-"):
            raise SporFetchError("The provided SPOR asset-map payload is not a PDF")
        capture_artifact(
            asset_map_pdf,
            media_type="application/pdf",
            filename="spor-asset-map-2018.pdf",
            url=ASSET_MAP_PDF_URL,
            role="inventory",
            metadata={"representation": "caller_provided_asset_map", "current_through": ASSET_MAP_CURRENT_THROUGH},
        )
        parse_started = time.perf_counter()
        refs = _pdf_entries_from_asset_map(asset_map_pdf, annotation_parser=annotation_parser)
        retrieval = {
            **_provided_asset_map_receipt(asset_map_pdf),
            "inventory_parse_duration_ms": max(0, round((time.perf_counter() - parse_started) * 1000)),
        }
        return _AssetMapSource(
            refs=refs,
            retrieval=retrieval,
            adapter="pdf_link_annotations",
        )

    report_url = _normalize_asset_map_url(url or ASSET_MAP_PDF_URL)
    downloaded = _download_with_backoff(
        client,
        report_url,
        timeout=PDF_TIMEOUT_SECONDS,
        url_policy=_ASSET_MAP_URL_POLICY,
        dns_resolver=dns_resolver,
    )
    _normalize_asset_map_url(str(downloaded.provenance["final_url"]))
    if not _is_pdf(downloaded):
        raise SporFetchError("The official SPOR asset-map URL did not return a PDF")
    capture_artifact(
        downloaded.data,
        media_type="application/pdf",
        filename="spor-asset-map-2018.pdf",
        url=str(downloaded.provenance.get("final_url") or report_url),
        role="inventory",
        metadata={"representation": "downloaded_asset_map", "current_through": ASSET_MAP_CURRENT_THROUGH},
    )
    parse_started = time.perf_counter()
    refs = _pdf_entries_from_asset_map(downloaded.data, annotation_parser=annotation_parser)
    retrieval = {
        **downloaded.provenance,
        "inventory_parse_duration_ms": max(0, round((time.perf_counter() - parse_started) * 1000)),
    }
    return _AssetMapSource(
        refs=refs,
        retrieval=retrieval,
        adapter="pdf_link_annotations",
    )


def _default_pdf_converter(data: bytes, title: str, guideline_id: str) -> PdfConversionResult:
    """Convert a guideline PDF through the shared in-memory backend."""
    return convert_pdf(data, running_header=title, name=f"spor-{guideline_id}.pdf")


def scrape_guideline(
    client: httpx.Client,
    ref: SporGuidelineRef,
    *,
    permission_id: str,
    asset_map_retrieval: dict[str, Any] | None = None,
    manifest_adapter: str,
    pdf_converter: PdfMarkdownConverter | None = None,
    dns_resolver: DnsResolver | None = None,
) -> ScrapedDocument:
    """Download and convert one explicitly selected direct guideline PDF."""
    normalized_permission_id = clean_text(permission_id, drop_numeric_citations=False)
    if not normalized_permission_id:
        raise SporPermissionError("SPOR ingestion requires a nonempty permission_id")
    downloaded = _download_with_backoff(
        client,
        ref.pdf_url,
        timeout=httpx.Timeout(PDF_TIMEOUT_SECONDS, connect=PUBLISHER_CONNECT_TIMEOUT_SECONDS),
        url_policy=_PUBLIC_PUBLISHER_URL_POLICY,
        dns_resolver=dns_resolver,
    )
    _normalize_external_url(str(downloaded.provenance["final_url"]), require_pdf_path=False)
    if not _is_pdf(downloaded):
        raise SporFetchError(
            "A SPOR manifest entry did not resolve directly to a PDF; publisher HTML crawling remains disabled "
            f"(final URL: {downloaded.provenance['final_url']!r})"
        )
    capture_arguments = {
        "media_type": "application/pdf",
        "filename": f"spor-{ref.guideline_id}.pdf",
        "url": str(downloaded.provenance.get("final_url") or ref.pdf_url),
        "metadata": {"representation": "downloaded_pdf", "publisher": ref.publisher},
    }
    # Explicit manifests fail the run on conversion errors, so retain their
    # source first for failure inspection. Fixed-report discovery skips stale
    # candidates; delay capture there so a failed candidate cannot be attached
    # to the next, unrelated document.
    if manifest_adapter != "pdf_link_annotations":
        capture_artifact(downloaded.data, **capture_arguments)
    converted = (pdf_converter or _default_pdf_converter)(downloaded.data, ref.title, ref.guideline_id)
    if isinstance(converted, PdfConversionResult):
        content = converted.markdown.strip()
        conversion = converted.provenance
    else:
        content = converted.strip()
        conversion = {
            "backend": "injected",
            "input_bytes": len(downloaded.data),
            "input_sha256": downloaded.provenance["sha256"],
        }
    if not content:
        raise SporFetchError(f"SPOR guideline PDF {redact_url(ref.pdf_url)!r} produced no readable Markdown")
    if manifest_adapter == "pdf_link_annotations":
        capture_artifact(downloaded.data, **capture_arguments)
    retrievals = [receipt for receipt in (asset_map_retrieval, downloaded.provenance) if receipt]
    phase_timings_ms: dict[str, int] = {}
    for phase, receipt, field in (
        ("inventory_retrieval", asset_map_retrieval, "retrieval_duration_ms"),
        ("inventory_parse", asset_map_retrieval, "inventory_parse_duration_ms"),
        ("pdf_retrieval", downloaded.provenance, "retrieval_duration_ms"),
    ):
        duration = receipt.get(field) if receipt else None
        if isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0:
            phase_timings_ms[phase] = duration
    conversion_duration = conversion.get("processing_time_ms")
    if isinstance(conversion_duration, int) and not isinstance(conversion_duration, bool) and conversion_duration >= 0:
        phase_timings_ms["pdf_conversion"] = conversion_duration
    return ScrapedDocument(
        source="spor",
        external_id=f"spor-{ref.guideline_id}",
        title=ref.title,
        url=redact_url(ref.pdf_url),
        content=content,
        section_count=count_markdown_sections(content),
        metadata={
            "guideline_id": ref.guideline_id,
            "publisher": ref.publisher,
            "document_license": ref.document_license or "unknown; review the originating publisher's terms",
            "permission_required": True,
            "authorization_asserted": True,
            "permission_id": normalized_permission_id,
            "content_scope": "full_pdf",
            "source_format_types": ["pdf"],
            "source_media_types": ["application/pdf"],
            "pdf_url": redact_url(ref.pdf_url),
            "pdf_retrieval": downloaded.provenance,
            "pdf_conversion": conversion,
            "asset_map_page": ref.asset_map_page,
            "asset_map_url": ASSET_MAP_PAGE_URL,
            "asset_map_report_url": ASSET_MAP_PDF_URL,
            "asset_map_current_through": ASSET_MAP_CURRENT_THROUGH,
            "asset_map_status": "historical_static_inventory",
            "staleness_warning": "The SPOR asset map was last updated in April 2018; verify guideline currency.",
            "asset_map_endorsement": "SPOR Evidence Alliance does not recommend or endorse listed guidelines.",
            "asset_map_retrieval": asset_map_retrieval,
            "manifest_adapter": manifest_adapter,
        },
        provenance={
            "retrievals": retrievals,
            "conversions": [conversion],
            "access_basis": "explicit_operator_authorization",
            "authorization_asserted": True,
            "permission_id": normalized_permission_id,
            "manifest_adapter": manifest_adapter,
            "phase_timings_ms": phase_timings_ms,
        },
    )


def scrape_spor(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
    authorized: bool = False,
    permission_id: str | None = None,
    asset_map_pdf: bytes | None = None,
    manifest: Iterable[ManifestEntry] | None = None,
    manifest_json: str | bytes | Mapping[str, Any] | list[Any] | None = None,
    annotation_parser: AnnotationParser | None = None,
    client_factory: ClientFactory = default_client,
    pdf_converter: PdfMarkdownConverter | None = None,
    dns_resolver: DnsResolver | None = None,
) -> ScrapeRun:
    """Configure permission-gated SPOR historical-manifest ingestion.

    ``url`` denotes only the fixed official asset-map report PDF.  For curated
    publisher files, use ``manifest`` or ``manifest_json`` with direct PDF URLs.
    ``link_mode`` is accepted for CLI consistency; PDF conversion controls link
    rendering in the produced Markdown.
    """
    del link_mode
    if documents is not None and documents < 1:
        raise ValueError(f"documents must be at least 1; got {documents}")
    if not authorized:
        raise SporPermissionError(
            "SPOR asset-map ingestion is disabled by default because the historical inventory does not grant "
            "permission to download or transform third-party guidelines. Pass authorized=True only with an "
            "appropriate legal basis and an explicit source configuration."
        )
    normalized_permission_id = clean_text(permission_id or "", drop_numeric_citations=False)
    if not normalized_permission_id:
        raise SporPermissionError("SPOR ingestion requires a nonempty permission_id")
    configured = sum(value is not None for value in (asset_map_pdf, manifest, manifest_json))
    if configured > 1 or (url is not None and configured):
        raise ValueError("Pass exactly one of url, asset_map_pdf, manifest, or manifest_json")
    if url is not None:
        url = _normalize_asset_map_url(url)

    def configured_documents() -> Iterator[ScrapedDocument]:
        with client_factory() as client:
            source = _resolve_source(
                client,
                url=url,
                asset_map_pdf=asset_map_pdf,
                manifest=manifest,
                manifest_json=manifest_json,
                annotation_parser=annotation_parser,
                dns_resolver=dns_resolver,
            )
            if not source.refs:
                raise SporFetchError("The configured SPOR manifest is empty")
            attempted = 0
            emitted = 0
            pending_failures: list[dict[str, Any]] = []
            last_error: ScrapeError | None = None
            host_transport_failures: dict[str, int] = {}
            blocked_hosts: set[str] = set()
            pacer = RequestStartPacer(DOCUMENT_DELAY_SECONDS)
            for ref in source.refs:
                if documents is not None and emitted >= documents:
                    return
                host = (urlsplit(ref.pdf_url).hostname or "").casefold().rstrip(".")
                if source.adapter == "pdf_link_annotations" and host in blocked_hosts:
                    attempted += 1
                    pending_failures.append(
                        {
                            "guideline_id": ref.guideline_id,
                            "pdf_url": redact_url(ref.pdf_url),
                            "error_type": "HostCircuitOpen",
                            "message": (
                                f"Skipped without a request after {HOST_CIRCUIT_FAILURES} transport failures "
                                f"from publisher host {host!r}"
                            ),
                        }
                    )
                    if len(pending_failures) >= MAX_DISCOVERY_FAILURES:
                        break
                    continue
                pacing_delay_seconds = pacer.wait()
                attempted += 1
                try:
                    document = scrape_guideline(
                        client,
                        ref,
                        permission_id=normalized_permission_id,
                        asset_map_retrieval=source.retrieval,
                        manifest_adapter=source.adapter,
                        pdf_converter=pdf_converter,
                        dns_resolver=dns_resolver,
                    )
                except ScrapeError as error:
                    if source.adapter != "pdf_link_annotations":
                        raise
                    last_error = error
                    if _transport_failure(error):
                        failures = host_transport_failures.get(host, 0) + 1
                        host_transport_failures[host] = failures
                        if failures >= HOST_CIRCUIT_FAILURES:
                            blocked_hosts.add(host)
                    pending_failures.append(
                        {
                            "guideline_id": ref.guideline_id,
                            "pdf_url": redact_url(ref.pdf_url),
                            "error_type": type(error).__name__,
                            "message": " ".join(str(error).split()),
                        }
                    )
                    if len(pending_failures) >= MAX_DISCOVERY_FAILURES:
                        break
                    continue
                if pending_failures:
                    document = replace(
                        document,
                        metadata={
                            **document.metadata,
                            "skipped_stale_candidates": list(pending_failures),
                        },
                        provenance={
                            **document.provenance,
                            "skipped_stale_candidates": list(pending_failures),
                        },
                    )
                    pending_failures.clear()
                document = replace(
                    document,
                    provenance={
                        **document.provenance,
                        "request_start_pacing_delay_ms": max(0, round(pacing_delay_seconds * 1000)),
                    },
                )
                host_transport_failures.pop(host, None)
                yield document
                emitted += 1
            if documents is not None and emitted >= documents:
                return
            if documents is not None and emitted < documents:
                detail = f"; last failure: {last_error}" if last_error is not None else ""
                raise SporFetchError(
                    f"SPOR configured source produced {emitted} of {documents} requested documents "
                    f"after {attempted} candidates{detail}"
                ) from last_error
            if last_error is not None:
                raise SporFetchError(
                    "SPOR fixed-report discovery could not produce all configured documents "
                    f"after {attempted} candidates; emitted {emitted}; last failure: {last_error}"
                ) from last_error

    # The report must be parsed lazily to preserve the zero-network construction
    # guarantee. A manifest's exact total is nevertheless cheap and useful.
    if manifest is not None:
        manifest = tuple(manifest)
        total_refs = refs_from_manifest(manifest)
        total = len(total_refs) if documents is None else min(documents, len(total_refs))
    elif manifest_json is not None:
        total_refs = refs_from_json_manifest(manifest_json)
        total = len(total_refs) if documents is None else min(documents, len(total_refs))
    else:
        total = documents
    return ScrapeRun(documents=configured_documents(), total=total)


__all__ = [
    "ASSET_MAP_CURRENT_THROUGH",
    "ASSET_MAP_PAGE_URL",
    "ASSET_MAP_PDF_URL",
    "BASE_URL",
    "DOCUMENT_DELAY_SECONDS",
    "PDF_TIMEOUT_SECONDS",
    "PUBLISHER_CONNECT_TIMEOUT_SECONDS",
    "HOST_CIRCUIT_FAILURES",
    "MAX_DOWNLOAD_RETRIES",
    "MAX_RETRY_DELAY_SECONDS",
    "MAX_TRANSPORT_RETRIES",
    "AnnotationParser",
    "ClientFactory",
    "ManifestEntry",
    "PdfMarkdownConverter",
    "SporFetchError",
    "SporGuidelineRef",
    "SporPermissionError",
    "refs_from_json_manifest",
    "refs_from_manifest",
    "scrape_guideline",
    "scrape_spor",
]
