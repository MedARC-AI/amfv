"""Tests for shared scraping primitives."""

from hashlib import sha256

import httpx
import pytest

from amfv_datasets.scraping import base
from amfv_datasets.scraping.base import (
    ArtifactCaptureSink,
    RequestStartPacer,
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    ScrapeTiming,
    UrlPolicy,
    artifact_capture_context,
    capture_artifact,
    default_client,
    download_content,
    redact_url,
    response_provenance,
    scrape_listing_documents,
)


class _RecordingArtifactSink:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, dict[str, object]]] = []

    def capture_artifact(self, data: bytes, **kwargs: object) -> None:
        self.calls.append((data, kwargs))


def test_capture_artifact_is_a_noop_without_an_active_sink() -> None:
    """Normal scraper use does not retain source payloads."""
    assert capture_artifact("source", media_type="text/html") is False


def test_artifact_capture_context_encodes_text_and_dispatches_metadata() -> None:
    """An active review sink receives UTF-8 bytes and artifact metadata."""
    sink: ArtifactCaptureSink = _RecordingArtifactSink()

    with artifact_capture_context(sink):
        captured = capture_artifact(
            "Résumé",
            media_type="text/html; charset=utf-8",
            filename="article.html",
            url="https://example.org/article",
            role="source-page",
            metadata={"representation": "rendered-dom"},
        )

    assert captured is True
    assert isinstance(sink, _RecordingArtifactSink)
    assert sink.calls == [
        (
            "Résumé".encode(),
            {
                "media_type": "text/html; charset=utf-8",
                "filename": "article.html",
                "url": "https://example.org/article",
                "role": "source-page",
                "metadata": {"representation": "rendered-dom"},
            },
        )
    ]


def test_artifact_capture_context_resets_after_an_exception() -> None:
    """Capture state cannot leak into later scrapes after a failed run."""
    sink = _RecordingArtifactSink()

    with pytest.raises(RuntimeError, match="conversion failed"):
        with artifact_capture_context(sink):
            raise RuntimeError("conversion failed")

    assert capture_artifact(b"later", media_type="application/pdf") is False
    assert sink.calls == []


def test_scrape_run_records_per_document_and_total_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run iteration attaches file timing and exposes aggregate metrics."""
    clock = iter((10.0, 10.0, 10.025, 10.025, 10.040))
    monkeypatch.setattr("amfv_datasets.scraping.base.time.perf_counter", lambda: next(clock))
    document = ScrapedDocument(
        source="test",
        external_id="test-1",
        title="Test",
        url="https://example.org/1",
        content="body",
        provenance={"scrape_duration_ms": 999, "scrape_sequence": 999},
    )
    run = ScrapeRun(documents=[document], timing=ScrapeTiming())

    [timed_document] = list(run)

    assert timed_document.provenance["scrape_duration_ms"] == 25
    assert timed_document.provenance["scrape_sequence"] == 1
    assert run.timing.as_dict() == {
        "started_at_utc": run.timing.started_at_utc,
        "completed_at_utc": run.timing.completed_at_utc,
        "elapsed_ms": 40,
        "document_count": 1,
        "document_durations_ms": [25],
        "average_document_ms": 25,
        "median_document_ms": 25,
        "p90_document_ms": 25,
        "minimum_document_ms": 25,
        "maximum_document_ms": 25,
    }
    assert document.provenance["scrape_duration_ms"] == 999
    assert document.provenance["scrape_sequence"] == 999


def test_scrape_run_timing_finishes_for_an_empty_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exhausted source still records a completed zero-document run."""
    clock = iter((20.0, 20.0, 20.050))
    monkeypatch.setattr("amfv_datasets.scraping.base.time.perf_counter", lambda: next(clock))
    run = ScrapeRun(documents=[])

    assert list(run) == []
    assert run.timing.as_dict() == {
        "started_at_utc": run.timing.started_at_utc,
        "completed_at_utc": run.timing.completed_at_utc,
        "elapsed_ms": 50,
        "document_count": 0,
        "document_durations_ms": [],
        "average_document_ms": None,
        "median_document_ms": None,
        "p90_document_ms": None,
        "minimum_document_ms": None,
        "maximum_document_ms": None,
    }


def test_scrape_run_timing_finishes_when_source_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A source exception retains elapsed time for the failed partial run."""
    clock = iter((30.0, 30.0, 30.075))
    monkeypatch.setattr("amfv_datasets.scraping.base.time.perf_counter", lambda: next(clock))

    def failing_documents():
        raise ScrapeError("source failed")
        yield  # pragma: no cover

    run = ScrapeRun(documents=failing_documents())

    with pytest.raises(ScrapeError, match="source failed"):
        list(run)

    assert run.timing.elapsed_ms == 75
    assert run.timing.completed_at_utc is not None
    assert run.timing.document_durations_ms == []


def test_scrape_run_timing_finishes_when_iteration_is_closed_early(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing a partial iterator finalizes total time and keeps completed files."""
    clock = iter((40.0, 40.0, 40.010, 40.025))
    monkeypatch.setattr("amfv_datasets.scraping.base.time.perf_counter", lambda: next(clock))
    source_closed = False

    def documents():
        nonlocal source_closed
        try:
            yield ScrapedDocument("test", "one", "One", "https://example.org/one", "one")
            yield ScrapedDocument("test", "two", "Two", "https://example.org/two", "two")
        finally:
            source_closed = True

    run = ScrapeRun(documents=documents())
    iterator = iter(run)

    first = next(iterator)
    iterator.close()

    assert first.external_id == "one"
    assert run.timing.elapsed_ms == 25
    assert run.timing.completed_at_utc is not None
    assert run.timing.document_durations_ms == [10]
    assert source_closed is True


def test_scraped_document_attaches_integrity_provenance() -> None:
    """Every record carries a source, timestamp, size, and content hash."""
    content = "# Recommendation\n\nUse treatment A."
    document = ScrapedDocument(
        source="test",
        external_id="test-1",
        title="Recommendation",
        url="https://example.org/guideline/1",
        content=content,
        provenance={"scraped_at_utc": "2026-08-20T00:00:00Z", "retrievals": [{"status_code": 200}]},
    )

    assert document.provenance == {
        "schema_version": 1,
        "scraped_at_utc": "2026-08-20T00:00:00Z",
        "source_url": "https://example.org/guideline/1",
        "content_type": "text/markdown",
        "content_bytes": len(content.encode()),
        "content_sha256": sha256(content.encode()).hexdigest(),
        "retrievals": [{"status_code": 200}],
    }


def test_download_content_stays_in_memory_and_records_http_provenance() -> None:
    """Bounded downloads retain headers, redirects, size, and a raw hash."""
    payload = b"%PDF provenance"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=payload,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": str(len(payload)),
                "ETag": '"version-1"',
                "Last-Modified": "Wed, 19 Aug 2026 12:00:00 GMT",
            },
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        downloaded = download_content(client, "https://example.org/guideline.pdf")

    assert downloaded.data == payload
    assert downloaded.provenance["requested_url"] == "https://example.org/guideline.pdf"
    assert downloaded.provenance["final_url"] == "https://example.org/guideline.pdf"
    assert downloaded.provenance["content_type"] == "application/pdf"
    assert downloaded.provenance["etag"] == '"version-1"'
    assert downloaded.provenance["byte_count"] == len(payload)
    assert downloaded.provenance["sha256"] == sha256(payload).hexdigest()
    assert downloaded.provenance["payload_bytes"] == len(payload)
    assert downloaded.provenance["payload_sha256"] == sha256(payload).hexdigest()
    assert downloaded.provenance["payload_hash_algorithm"] == "sha256"
    assert downloaded.provenance["payload_representation"] == "decoded_response_body"
    assert downloaded.provenance["request_started_at_utc"].endswith("Z")
    assert downloaded.provenance["download_completed_at_utc"].endswith("Z")
    assert downloaded.provenance["downloaded_at_utc"].endswith("Z")
    assert isinstance(downloaded.provenance["retrieval_duration_ms"], int)


def test_download_content_redacts_credentials_and_every_query_value() -> None:
    """Retrieval receipts correlate signed URLs without persisting their secrets."""
    raw_url = "https://operator:password@example.org/guideline.pdf?token=secret&expires=123"

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"pdf"))) as client:
        downloaded = download_content(client, raw_url)

    receipt = downloaded.provenance
    assert receipt["requested_url"] == "https://example.org/guideline.pdf?token=REDACTED&expires=REDACTED"
    assert receipt["final_url"] == "https://example.org/guideline.pdf?token=REDACTED&expires=REDACTED"
    assert receipt["requested_url_query_sha256"] == sha256(b"token=secret&expires=123").hexdigest()
    assert "password" not in repr(receipt)
    assert "secret" not in repr(receipt)


def test_download_content_checks_redirect_boundary_before_the_next_request() -> None:
    """A discovered redirect cannot make a source-owned fetch cross to another host."""
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/private.pdf"})

    policy = UrlPolicy.allow_hosts("example.org")
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(ScrapeError, match="host boundary"):
            download_content(client, "https://example.org/start", url_policy=policy)

    assert requests == ["https://example.org/start"]


def test_download_content_rejects_private_dns_resolution_before_network() -> None:
    """Arbitrary publisher hosts must resolve exclusively to public addresses."""
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, content=b"should not be reached")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ScrapeError, match="public network boundary"):
            download_content(
                client,
                "https://publisher.example/guideline.pdf",
                url_policy=UrlPolicy.public_web(),
                dns_resolver=lambda _host, _port: ["169.254.169.254"],
            )

    assert requests == []


def test_download_content_records_start_and_completion_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """The legacy downloaded timestamp denotes completion, not request start."""
    timestamps = iter(["2026-08-20T00:00:00Z", "2026-08-20T00:00:02Z"])
    monkeypatch.setattr(base, "_utc_now", lambda: next(timestamps))

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"ok"))) as client:
        receipt = download_content(client, "https://example.org/file").provenance

    assert receipt["request_started_at_utc"] == "2026-08-20T00:00:00Z"
    assert receipt["download_completed_at_utc"] == "2026-08-20T00:00:02Z"
    assert receipt["downloaded_at_utc"] == "2026-08-20T00:00:02Z"


def test_download_error_does_not_echo_a_signed_query_value() -> None:
    """Persisted source error metadata can safely include shared download errors."""
    raw_url = "https://example.org/file.pdf?signature=top-secret"

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403))) as client:
        with pytest.raises(ScrapeError) as captured:
            download_content(client, raw_url)

    assert "top-secret" not in str(captured.value)
    assert "signature=REDACTED" in str(captured.value)


def test_redact_url_canonicalizes_default_ports_and_drops_fragments() -> None:
    """Persisted URLs contain neither authentication data nor client-only fragments."""
    assert redact_url("HTTPS://user:pass@EXAMPLE.org:443/file?q=value#fragment") == (
        "https://example.org/file?q=REDACTED"
    )


def test_download_content_rejects_large_payload_from_content_length() -> None:
    """A source cannot force an unexpectedly large download."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"too large", headers={"Content-Length": "9"}, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ScrapeError, match="download limit"):
            download_content(client, "https://example.org/large.pdf", max_bytes=8)


def test_response_provenance_records_already_buffered_html() -> None:
    """HTML/API callers can attach the same raw-response integrity receipt."""
    payload = b"<main>Guidance</main>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"Content-Type": "text/html"}, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = client.get("https://example.org/guidance")
    receipt = response_provenance(response, downloaded_at_utc="2026-08-20T00:00:00Z")

    assert receipt["requested_url"] == "https://example.org/guidance"
    assert receipt["byte_count"] == len(payload)
    assert receipt["sha256"] == sha256(payload).hexdigest()
    assert receipt["downloaded_at_utc"] == "2026-08-20T00:00:00Z"


def test_default_client_applies_user_agent_and_headers() -> None:
    """The shared HTTP client factory applies reusable client configuration."""
    client = default_client(
        user_agent="amfv-test",
        headers={"Accept": "text/html"},
        timeout=12.0,
        follow_redirects=False,
    )

    try:
        assert client.headers["User-Agent"] == "amfv-test"
        assert client.headers["Accept"] == "text/html"
        assert client.timeout.connect == 12.0
        assert not client.follow_redirects
    finally:
        client.close()


def test_scrape_listing_documents_limits_document_count() -> None:
    """Listing scraping limits returned documents, not source pages."""
    calls: list[int] = []

    class _FakeClient:
        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def client_factory():
        return _FakeClient()

    def list_page(client: httpx.Client, page: int) -> list[str]:
        calls.append(page)
        return [f"item-{page}-1", f"item-{page}-2"]

    def scrape_item(client: httpx.Client, item: str) -> ScrapedDocument:
        return ScrapedDocument(
            source="test",
            external_id=item,
            title=item,
            url=f"https://example.org/{item}",
            content="content",
        )

    documents = list(
        scrape_listing_documents(
            documents=3,
            client_factory=client_factory,
            list_page=list_page,
            scrape_item=scrape_item,
            document_delay_seconds=0,
        )
    )

    assert calls == [1, 2]
    assert [document.external_id for document in documents] == ["item-1-1", "item-1-2", "item-2-1"]


def test_scrape_listing_documents_supports_all_documents() -> None:
    """Unset document count keeps scraping until a listing page returns no items."""
    calls: list[int] = []

    class _FakeClient:
        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def client_factory():
        return _FakeClient()

    def list_page(client: httpx.Client, page: int) -> list[str]:
        calls.append(page)
        if page > 2:
            return []
        return [f"item-{page}"]

    def scrape_item(client: httpx.Client, item: str) -> ScrapedDocument:
        return ScrapedDocument(
            source="test",
            external_id=item,
            title=item,
            url=f"https://example.org/{item}",
            content="content",
        )

    documents = list(
        scrape_listing_documents(
            documents=None,
            client_factory=client_factory,
            list_page=list_page,
            scrape_item=scrape_item,
            document_delay_seconds=0,
        )
    )

    assert calls == [1, 2, 3]
    assert [document.external_id for document in documents] == ["item-1", "item-2"]


def test_scrape_listing_documents_delays_between_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fast listing items retain the full minimum start interval."""
    delays: list[float] = []

    class _FakeClient:
        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def client_factory():
        return _FakeClient()

    def list_page(client: httpx.Client, page: int) -> list[str]:
        return ["item-1", "item-2", "item-3"] if page == 1 else []

    def scrape_item(client: httpx.Client, item: str) -> ScrapedDocument:
        return ScrapedDocument(
            source="test",
            external_id=item,
            title=item,
            url=f"https://example.org/{item}",
            content="content",
        )

    monkeypatch.setattr(base.time, "sleep", delays.append)
    monkeypatch.setattr(base.time, "monotonic", lambda: 0.0)

    documents = list(
        scrape_listing_documents(
            documents=3,
            client_factory=client_factory,
            list_page=list_page,
            scrape_item=scrape_item,
            document_delay_seconds=5.0,
        )
    )

    assert [document.external_id for document in documents] == ["item-1", "item-2", "item-3"]
    assert delays == [5.0, 5.0]


def test_scrape_listing_documents_skips_non_documents_without_consuming_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skipped listing entries are delayed but do not count as returned documents."""
    delays: list[float] = []

    class _FakeClient:
        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def client_factory():
        return _FakeClient()

    def list_page(client: httpx.Client, page: int) -> list[str]:
        return ["skip", "item-1", "item-2"] if page == 1 else []

    def scrape_item(client: httpx.Client, item: str) -> ScrapedDocument | None:
        if item == "skip":
            return None
        return ScrapedDocument("test", item, item, f"https://example.org/{item}", "content")

    monkeypatch.setattr(base.time, "sleep", delays.append)
    monkeypatch.setattr(base.time, "monotonic", lambda: 0.0)

    documents = list(
        scrape_listing_documents(
            documents=2,
            client_factory=client_factory,
            list_page=list_page,
            scrape_item=scrape_item,
            document_delay_seconds=5.0,
        )
    )

    assert [document.external_id for document in documents] == ["item-1", "item-2"]
    assert delays == [5.0, 5.0]


def test_request_start_pacer_counts_processing_toward_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Slow parsing absorbs pacing time instead of stacking another full delay."""
    current = 0.0
    delays: list[float] = []

    def clock() -> float:
        return current

    def sleep(delay: float) -> None:
        nonlocal current
        delays.append(delay)
        current += delay

    monkeypatch.setattr(base.time, "monotonic", clock)
    monkeypatch.setattr(base.time, "sleep", sleep)
    pacer = RequestStartPacer(5.0)

    assert pacer.wait() == 0.0
    current += 2.0
    assert pacer.wait() == 3.0
    current += 7.0
    assert pacer.wait() == 0.0

    assert delays == [3.0]


@pytest.mark.parametrize("interval", [-1.0, float("nan"), float("inf"), True])
def test_request_start_pacer_rejects_invalid_intervals(interval: object) -> None:
    """Misconfigured pacing cannot silently disable source throttling."""
    with pytest.raises(ValueError, match="finite non-negative"):
        RequestStartPacer(interval)  # type: ignore[arg-type]


def test_scrape_timing_reports_median_and_nearest_rank_p90() -> None:
    """Retained timing summaries preserve distribution statistics after cleanup."""
    timing = ScrapeTiming(document_durations_ms=[10, 20, 30, 40, 100])

    summary = timing.as_dict()

    assert summary["average_document_ms"] == 40
    assert summary["median_document_ms"] == 30
    assert summary["p90_document_ms"] == 100
