"""Common types for source web scrapers."""

from __future__ import annotations

import ipaddress
import math
import socket
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
PROVENANCE_SCHEMA_VERSION = 1
DEFAULT_MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_REDIRECTS = 8
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})

type DnsResolver = Callable[[str, int], Iterable[str]]


class ScrapeError(RuntimeError):
    """Raised when a source page cannot be fetched or parsed."""


class DownloadError(ScrapeError):
    """Raised for a sanitized HTTP transport, status, or body-read failure."""


@dataclass(frozen=True)
class UrlPolicy:
    """Network-boundary policy applied before every request in a redirect chain.

    An explicit host allowlist is appropriate for publisher-owned discovery
    URLs. ``require_public_address`` is intended for operator-provided URLs
    whose host cannot be known in advance. It rejects non-public literal and
    resolved addresses immediately before each request. The DNS check reduces
    SSRF exposure but does not pin the subsequent HTTP connection to the
    checked address, so it is not a complete defense against DNS rebinding.
    """

    allowed_hosts: frozenset[str] | None = None
    allow_subdomains: bool = False
    allowed_schemes: frozenset[str] = frozenset({"https"})
    allowed_ports: frozenset[int | None] = frozenset({None, 443})
    path_prefixes_by_host: tuple[tuple[str, str], ...] = ()
    require_public_address: bool = False

    @classmethod
    def allow_hosts(cls, *hosts: str, allow_subdomains: bool = False) -> UrlPolicy:
        """Build an HTTPS policy for a fixed set of source-owned hosts."""
        return cls(
            allowed_hosts=frozenset(host.casefold().rstrip(".") for host in hosts),
            allow_subdomains=allow_subdomains,
        )

    @classmethod
    def public_web(cls, *, allow_http: bool = False) -> UrlPolicy:
        """Build a policy for arbitrary public web hosts."""
        schemes = frozenset({"http", "https"}) if allow_http else frozenset({"https"})
        ports: frozenset[int | None] = frozenset({None, 80, 443}) if allow_http else frozenset({None, 443})
        return cls(allowed_schemes=schemes, allowed_ports=ports, require_public_address=True)


@dataclass(frozen=True)
class DownloadedContent:
    """In-memory download plus an auditable HTTP retrieval receipt."""

    data: bytes
    provenance: dict[str, Any]


class ArtifactCaptureSink(Protocol):
    """Optional destination for source bytes retained by review tooling."""

    def capture_artifact(
        self,
        data: bytes,
        *,
        media_type: str,
        filename: str | None = None,
        url: str | None = None,
        role: str = "source",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Retain one source artifact for the document being scraped."""


_ARTIFACT_CAPTURE_SINK: ContextVar[ArtifactCaptureSink | None] = ContextVar(
    "amfv_artifact_capture_sink",
    default=None,
)


@contextmanager
def artifact_capture_context(sink: ArtifactCaptureSink) -> Iterator[ArtifactCaptureSink]:
    """Install an opt-in artifact sink for the current execution context."""
    token = _ARTIFACT_CAPTURE_SINK.set(sink)
    try:
        yield sink
    finally:
        _ARTIFACT_CAPTURE_SINK.reset(token)


def capture_artifact(
    data: bytes | str,
    *,
    media_type: str,
    filename: str | None = None,
    url: str | None = None,
    role: str = "source",
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    """Offer source bytes to review tooling without coupling scrapers to it.

    Returns:
        True when an active sink accepted the artifact; False when capture is
        disabled. Text is encoded as UTF-8 before dispatch.
    """
    sink = _ARTIFACT_CAPTURE_SINK.get()
    if sink is None:
        return False
    payload = data.encode("utf-8") if isinstance(data, str) else data
    sink.capture_artifact(
        payload,
        media_type=media_type,
        filename=filename,
        url=url,
        role=role,
        metadata=dict(metadata or {}),
    )
    return True


@dataclass(frozen=True)
class ScrapedDocument:
    """Normalized source document produced by a scraper."""

    source: str
    external_id: str
    title: str
    url: str
    content: str
    section_count: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Attach a uniform, serializable provenance envelope to every record."""
        content_bytes = self.content.encode("utf-8")
        provenance = {
            **self.provenance,
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            "source_url": redact_url(self.url),
            "content_type": "text/markdown",
            "content_bytes": len(content_bytes),
            "content_sha256": sha256(content_bytes).hexdigest(),
        }
        provenance.setdefault("scraped_at_utc", _utc_now())
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "provenance", provenance)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def redact_url(url: str) -> str:
    """Return a canonical URL safe to persist in logs or dataset metadata.

    User information is removed and every query value is replaced. Parameter
    names remain visible because they are useful when auditing signed download
    flows, while the original query can be correlated by its separate hash in
    retrieval receipts.
    """
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if not hostname:
        return urlunsplit((parsed.scheme.casefold(), "", parsed.path, "", ""))
    try:
        port = parsed.port
    except ValueError:
        port = None
    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 80 if parsed.scheme.casefold() == "http" else 443 if parsed.scheme.casefold() == "https" else None
    netloc = f"{host_for_netloc}:{port}" if port is not None and port != default_port else host_for_netloc
    redacted_query = urlencode([(name, "REDACTED") for name, _value in parse_qsl(parsed.query, keep_blank_values=True)])
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or "/", redacted_query, ""))


def _query_sha256(url: str) -> str | None:
    query = urlsplit(url).query
    return sha256(query.encode("utf-8")).hexdigest() if query else None


def _default_dns_resolver(host: str, port: int) -> Iterable[str]:
    """Resolve a hostname to address strings for a public-network policy check."""
    return {
        str(sockaddr[0]).split("%", 1)[0]
        for _family, _socktype, _proto, _canonname, sockaddr in socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    }


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global


def validate_url(
    url: str,
    policy: UrlPolicy,
    *,
    dns_resolver: DnsResolver | None = None,
) -> str:
    """Validate one URL immediately before network access.

    Args:
        url: Absolute URL to validate.
        policy: Allowed schemes, ports, hosts, and address boundary.
        dns_resolver: Optional resolver injection used by deterministic tests.

    Returns:
        The unchanged absolute URL.

    Raises:
        ScrapeError: If the URL crosses the configured network boundary.
    """
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold().rstrip(".")
    if scheme not in policy.allowed_schemes or not host:
        raise ScrapeError(f"Refusing URL outside the permitted scheme/host boundary: {redact_url(url)!r}")
    try:
        port = parsed.port
    except ValueError as error:
        raise ScrapeError(f"Refusing URL with an invalid port: {redact_url(url)!r}") from error
    if parsed.username is not None or parsed.password is not None:
        raise ScrapeError(f"Refusing URL containing user information: {redact_url(url)!r}")
    if port not in policy.allowed_ports:
        raise ScrapeError(f"Refusing URL outside the permitted port boundary: {redact_url(url)!r}")

    if policy.allowed_hosts is not None:
        exact_match = host in policy.allowed_hosts
        subdomain_match = policy.allow_subdomains and any(
            host.endswith(f".{allowed}") for allowed in policy.allowed_hosts
        )
        if not exact_match and not subdomain_match:
            raise ScrapeError(f"Refusing URL outside the permitted host boundary: {redact_url(url)!r}")
    restricted_prefixes = tuple(
        prefix for expected_host, prefix in policy.path_prefixes_by_host if host == expected_host
    )
    if restricted_prefixes and not parsed.path.startswith(restricted_prefixes):
        raise ScrapeError(f"Refusing URL outside the permitted path boundary: {redact_url(url)!r}")

    if not policy.require_public_address:
        return url
    if host == "localhost" or host.endswith(".localhost") or "." not in host:
        raise ScrapeError(f"Refusing URL whose host is not public: {redact_url(url)!r}")
    try:
        literal_address = ipaddress.ip_address(host)
    except ValueError:
        resolver = dns_resolver or _default_dns_resolver
        effective_port = port or (443 if scheme == "https" else 80)
        try:
            resolved = tuple(dict.fromkeys(resolver(host, effective_port)))
        except OSError as error:
            raise ScrapeError(f"Could not verify the public addresses for {host!r}: {error}") from error
        if not resolved:
            raise ScrapeError(f"Refusing URL whose host resolved to no addresses: {host!r}")
        unsafe = [address for address in resolved if not _is_public_ip(address)]
        if unsafe:
            raise ScrapeError(f"Refusing URL whose host resolved outside the public network boundary: {host!r}")
    else:
        if not literal_address.is_global:
            raise ScrapeError(f"Refusing URL outside the public network boundary: {redact_url(url)!r}")
    return url


def _url_receipt_fields(prefix: str, url: str) -> dict[str, Any]:
    fields: dict[str, Any] = {f"{prefix}_url": redact_url(url)}
    if query_hash := _query_sha256(url):
        fields[f"{prefix}_url_query_sha256"] = query_hash
    return fields


def sanitize_retrieval_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a retrieval receipt while removing URL credentials and query values."""
    sanitized = dict(receipt)
    for prefix in ("requested", "final"):
        key = f"{prefix}_url"
        value = sanitized.get(key)
        if not isinstance(value, str):
            continue
        if query_hash := _query_sha256(value):
            sanitized.setdefault(f"{key}_query_sha256", query_hash)
        sanitized[key] = redact_url(value)
    redirect_chain = sanitized.get("redirect_chain")
    if isinstance(redirect_chain, list):
        raw_urls = [value for value in redirect_chain if isinstance(value, str)]
        sanitized["redirect_chain"] = [redact_url(value) for value in raw_urls]
        sanitized.setdefault(
            "redirect_chain_query_sha256",
            [_query_sha256(value) for value in raw_urls],
        )
    return sanitized


@dataclass
class ScrapeTiming:
    """Mutable wall-clock metrics collected while one scrape run is consumed."""

    started_at_utc: str | None = None
    completed_at_utc: str | None = None
    elapsed_ms: int | None = None
    document_durations_ms: list[int] = field(default_factory=list)
    _started_at_perf: float | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        """Start the run clock once."""
        if self._started_at_perf is None:
            self.started_at_utc = _utc_now()
            self._started_at_perf = time.perf_counter()

    def record_document(self, elapsed_ms: int) -> None:
        """Record one completed document duration."""
        self.document_durations_ms.append(max(0, elapsed_ms))

    def finish(self) -> None:
        """Stop the run clock once and retain aggregate wall time."""
        if self._started_at_perf is not None and self.elapsed_ms is None:
            self.elapsed_ms = max(0, round((time.perf_counter() - self._started_at_perf) * 1000))
            self.completed_at_utc = _utc_now()

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-serializable aggregate and per-document timing metrics."""
        durations = list(self.document_durations_ms)
        ordered_durations = sorted(durations)
        median_document_ms = None
        p90_document_ms = None
        if ordered_durations:
            midpoint = len(ordered_durations) // 2
            if len(ordered_durations) % 2:
                median_document_ms = ordered_durations[midpoint]
            else:
                median_document_ms = round((ordered_durations[midpoint - 1] + ordered_durations[midpoint]) / 2)
            p90_document_ms = ordered_durations[math.ceil(len(ordered_durations) * 0.9) - 1]
        return {
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "elapsed_ms": self.elapsed_ms,
            "document_count": len(durations),
            "document_durations_ms": durations,
            "average_document_ms": round(sum(durations) / len(durations)) if durations else None,
            "median_document_ms": median_document_ms,
            "p90_document_ms": p90_document_ms,
            "minimum_document_ms": min(durations, default=None),
            "maximum_document_ms": max(durations, default=None),
        }


@dataclass
class RequestStartPacer:
    """Enforce a minimum interval between scrape-attempt start times.

    Parser and conversion work count toward the interval. This preserves the
    configured maximum start rate without stacking an avoidable full sleep
    after a slow document has already occupied the worker.
    """

    interval_seconds: float
    _last_started_at: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.interval_seconds, bool)
            or not isinstance(self.interval_seconds, (int, float))
            or not math.isfinite(self.interval_seconds)
            or self.interval_seconds < 0
        ):
            raise ValueError(f"interval_seconds must be a finite non-negative number; got {self.interval_seconds!r}")

    def wait(self) -> float:
        """Wait only for the unelapsed interval and mark this attempt's start."""
        now = time.monotonic()
        slept_seconds = 0.0
        if self._last_started_at is not None:
            slept_seconds = max(0.0, self.interval_seconds - (now - self._last_started_at))
            if slept_seconds:
                time.sleep(slept_seconds)
                now = time.monotonic()
        self._last_started_at = now
        return slept_seconds


@dataclass(frozen=True)
class ScrapeRun:
    """A configured scrape with an optional source-provided document total.

    Sources should set `total` only when it is cheap enough to know before
    iterating the documents.
    """

    documents: Iterable[ScrapedDocument]
    total: int | None = None
    timing: ScrapeTiming = field(default_factory=ScrapeTiming, compare=False)

    def __iter__(self) -> Iterator[ScrapedDocument]:
        return self._timed_documents()

    def _timed_documents(self) -> Iterator[ScrapedDocument]:
        iterator = iter(self.documents)
        self.timing.start()
        try:
            while True:
                document_started = time.perf_counter()
                try:
                    document = next(iterator)
                except StopIteration:
                    return
                elapsed_ms = max(0, round((time.perf_counter() - document_started) * 1000))
                self.timing.record_document(elapsed_ms)
                provenance = dict(document.provenance)
                # These values describe this run, so stale timing copied from
                # an earlier run must not win over the measurement above.
                provenance["scrape_duration_ms"] = elapsed_ms
                provenance["scrape_sequence"] = len(self.timing.document_durations_ms)
                yield replace(document, provenance=provenance)
        finally:
            try:
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()
            finally:
                self.timing.finish()


def default_client(
    *,
    user_agent: str = USER_AGENT,
    headers: Mapping[str, str] | None = None,
    timeout: float = 60.0,
    follow_redirects: bool = True,
) -> httpx.Client:
    """Create a default HTTP client for scrapers.

    Args:
        user_agent: User-Agent header to send when `headers` does not already
            include one (default: USER_AGENT).
        headers: Additional HTTP headers to send (default: None).
        timeout: Request timeout in seconds (default: 60.0).
        follow_redirects: Whether to follow HTTP redirects (default: True).
    """
    client_headers = dict(headers or {})
    if user_agent is not None:
        client_headers.setdefault("User-Agent", user_agent)
    return httpx.Client(headers=client_headers, timeout=timeout, follow_redirects=follow_redirects)


def download_content(
    client: httpx.Client,
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    timeout: float | httpx.Timeout | None = None,
    url_policy: UrlPolicy | None = None,
    dns_resolver: DnsResolver | None = None,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> DownloadedContent:
    """Download one bounded response into memory and retain its HTTP metadata.

    Args:
        client: HTTP client used for the request.
        url: URL to download.
        max_bytes: Maximum response size accepted in bytes (default:
            DEFAULT_MAX_DOWNLOAD_BYTES).
        timeout: Optional request timeout override (default: None).
        url_policy: Optional boundary checked before the initial request and
            every redirect hop (default: None).
        dns_resolver: Optional resolver for public-address policy checks
            (default: None).
        max_redirects: Maximum HTTP redirects to follow (default:
            DEFAULT_MAX_REDIRECTS).
    """
    if max_bytes < 1:
        raise ValueError(f"max_bytes must be at least 1; got {max_bytes}")
    if max_redirects < 0:
        raise ValueError(f"max_redirects must be non-negative; got {max_redirects}")

    request_kwargs: dict[str, Any] = {}
    if timeout is not None:
        request_kwargs["timeout"] = timeout
    download_started_perf = time.perf_counter()
    request_started_at_utc: str | None = None
    current_url = url
    redirect_urls: list[str] = []
    response: httpx.Response | None = None
    for redirect_count in range(max_redirects + 1):
        if url_policy is not None:
            validate_url(current_url, url_policy, dns_resolver=dns_resolver)
        if request_started_at_utc is None:
            request_started_at_utc = _utc_now()
        try:
            request = client.build_request("GET", current_url, **request_kwargs)
            response = client.send(request, stream=True, follow_redirects=False)
        except httpx.HTTPError as error:
            raise DownloadError(f"Could not fetch {redact_url(current_url)!r} ({type(error).__name__})") from error
        if response.status_code not in _REDIRECT_STATUS_CODES:
            break
        location = response.headers.get("Location")
        if not location:
            response.close()
            raise ScrapeError(f"Redirect response from {redact_url(current_url)!r} omitted a Location header")
        next_url = urljoin(str(response.url), location)
        redirect_urls.append(str(response.url))
        response.close()
        if redirect_count >= max_redirects:
            raise ScrapeError(f"Refusing {redact_url(url)!r}: exceeded the {max_redirects}-redirect limit")
        # Validate here as well as at the top of the next iteration so an
        # unsafe Location is rejected immediately and can never be requested.
        if url_policy is not None:
            validate_url(next_url, url_policy, dns_resolver=dns_resolver)
        current_url = next_url
    if response is None:  # pragma: no cover - loop always executes once
        raise ScrapeError(f"Could not request {redact_url(url)!r}")

    try:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise DownloadError(
                f"HTTP {response.status_code} while fetching {redact_url(str(response.url))!r}"
            ) from error
        content_length = response.headers.get("Content-Length")
        if content_length and content_length.isdigit() and int(content_length) > max_bytes:
            raise ScrapeError(
                f"Refusing {redact_url(url)!r}: Content-Length {content_length} exceeds the "
                f"{max_bytes}-byte download limit"
            )
        chunks: list[bytes] = []
        byte_count = 0
        digest = sha256()
        try:
            for chunk in response.iter_bytes():
                byte_count += len(chunk)
                if byte_count > max_bytes:
                    raise ScrapeError(
                        f"Refusing {redact_url(url)!r}: response exceeds the {max_bytes}-byte download limit"
                    )
                chunks.append(chunk)
                digest.update(chunk)
        except httpx.HTTPError as error:
            raise DownloadError(
                f"Could not read the response body from {redact_url(str(response.url))!r} ({type(error).__name__})"
            ) from error
        data = b"".join(chunks)
        download_completed_at_utc = _utc_now()
        retrieval_duration_ms = max(0, round((time.perf_counter() - download_started_perf) * 1000))
        redirect_chain = [redact_url(item) for item in redirect_urls]
        provenance = {
            **_url_receipt_fields("requested", url),
            **_url_receipt_fields("final", str(response.url)),
            "request_started_at_utc": request_started_at_utc,
            "download_completed_at_utc": download_completed_at_utc,
            # Backward-compatible name; unlike the former implementation this
            # is the time the full payload finished, not the request start.
            "downloaded_at_utc": download_completed_at_utc,
            "retrieval_duration_ms": retrieval_duration_ms,
            "status_code": response.status_code,
            "content_type": response.headers.get("Content-Type"),
            "content_encoding": response.headers.get("Content-Encoding"),
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "content_disposition": response.headers.get("Content-Disposition"),
            "content_length_header": int(content_length) if content_length and content_length.isdigit() else None,
            "byte_count": byte_count,
            "sha256": digest.hexdigest(),
            "payload_bytes": byte_count,
            "payload_sha256": digest.hexdigest(),
            "payload_hash_algorithm": "sha256",
            "payload_representation": "decoded_response_body",
            "redirect_chain": redirect_chain,
            "redirect_chain_query_sha256": [_query_sha256(item) for item in redirect_urls],
        }
    finally:
        response.close()
    return DownloadedContent(data=data, provenance=provenance)


def response_provenance(
    response: httpx.Response,
    *,
    request_started_at_utc: str | None = None,
    downloaded_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a retrieval receipt for an already-buffered HTTP response."""
    data = response.content
    try:
        requested_url = str(response.history[0].request.url if response.history else response.request.url)
    except RuntimeError:
        requested_url = str(response.url)
    content_length = response.headers.get("Content-Length")
    completed_at_utc = downloaded_at_utc or _utc_now()
    # An already-buffered response does not expose its wall-clock start time.
    # Leave it unknown rather than mislabelling the observation/completion time.
    started_at_utc = request_started_at_utc
    redirect_urls = [str(item.url) for item in response.history]
    digest = sha256(data).hexdigest()
    return {
        **_url_receipt_fields("requested", requested_url),
        **_url_receipt_fields("final", str(response.url)),
        "request_started_at_utc": started_at_utc,
        "download_completed_at_utc": completed_at_utc,
        "downloaded_at_utc": completed_at_utc,
        "status_code": response.status_code,
        "content_type": response.headers.get("Content-Type"),
        "content_encoding": response.headers.get("Content-Encoding"),
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
        "content_disposition": response.headers.get("Content-Disposition"),
        "content_length_header": int(content_length) if content_length and content_length.isdigit() else None,
        "byte_count": len(data),
        "sha256": digest,
        "payload_bytes": len(data),
        "payload_sha256": digest,
        "payload_hash_algorithm": "sha256",
        "payload_representation": "decoded_response_body",
        "redirect_chain": [redact_url(item) for item in redirect_urls],
        "redirect_chain_query_sha256": [_query_sha256(item) for item in redirect_urls],
    }


def scrape_listing_documents[ClientT, ListingItemT](
    *,
    documents: int | None,
    client_factory: Callable[[], AbstractContextManager[ClientT]],
    list_page: Callable[[ClientT, int], Iterable[ListingItemT]],
    scrape_item: Callable[[ClientT, ListingItemT], ScrapedDocument | None],
    document_delay_seconds: float = 5.0,
    first_page_items: Iterable[ListingItemT] | None = None,
) -> Iterable[ScrapedDocument]:
    """Scrape a limited number of documents discovered from listing pages.

    Args:
        documents: Number of documents to scrape. When unset, listing pages are
            fetched until a page returns no items (default: None).
        client_factory: Factory returning a context-managed client, passed to
            `list_page` and `scrape_item` unchanged.
        list_page: Function that lists source-specific items for a page.
        scrape_item: Function that scrapes one listed item into a document. It
            may return None to skip a discovered item that is not a document.
        document_delay_seconds: Minimum interval between scrape-attempt start
            times (default: 5.0).
        first_page_items: Already-fetched first listing page items. When set,
            these are used before fetching page 2 (default: None).
    """
    if documents is not None and documents < 1:
        raise ValueError(f"documents must be at least 1; got {documents}")
    pacer = RequestStartPacer(document_delay_seconds)

    with client_factory() as client:
        page = 1
        scraped = 0
        attempted = 0
        page_items = list(first_page_items) if first_page_items is not None else None
        while documents is None or scraped < documents:
            if page_items is None:
                items = list(list_page(client, page))
            else:
                items = page_items
                page_items = None
            if not items:
                break
            for item in items:
                if documents is not None and scraped >= documents:
                    break
                pacing_delay_seconds = pacer.wait()
                document = scrape_item(client, item)
                attempted += 1
                if document is None:
                    continue
                provenance = dict(document.provenance)
                provenance["request_start_pacing_delay_ms"] = max(0, round(pacing_delay_seconds * 1000))
                document = replace(document, provenance=provenance)
                yield document
                scraped += 1
            page += 1


__all__ = [
    "ArtifactCaptureSink",
    "DEFAULT_MAX_DOWNLOAD_BYTES",
    "DEFAULT_MAX_REDIRECTS",
    "DnsResolver",
    "DownloadError",
    "DownloadedContent",
    "PROVENANCE_SCHEMA_VERSION",
    "RequestStartPacer",
    "ScrapeError",
    "ScrapeRun",
    "ScrapeTiming",
    "ScrapedDocument",
    "USER_AGENT",
    "UrlPolicy",
    "artifact_capture_context",
    "capture_artifact",
    "default_client",
    "download_content",
    "redact_url",
    "response_provenance",
    "sanitize_retrieval_receipt",
    "scrape_listing_documents",
    "validate_url",
]
