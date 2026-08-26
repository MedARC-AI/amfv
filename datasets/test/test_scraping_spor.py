"""Offline tests for the historical SPOR asset-map adapter."""

from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest

import amfv_datasets.scraping.spor as spor_module
from amfv_datasets.scraping.base import ScrapeError, artifact_capture_context
from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.pdf import PdfConversionResult
from amfv_datasets.scraping.spor import (
    ASSET_MAP_CURRENT_THROUGH,
    ASSET_MAP_PDF_URL,
    SITEMAP_URL,
    SporFetchError,
    SporGuidelineRef,
    SporPermissionError,
    refs_from_json_manifest,
    refs_from_manifest,
    relevant_sitemap_entries,
    scrape_spor,
    sitemap_page_urls,
)

_PDF_BYTES = b"%PDF-1.7 fake publisher guideline"
_ASSET_MAP_BYTES = b"%PDF-1.7 fake SPOR asset map"
_GUIDELINE_URLS = [
    "https://guidelines.example/alpha.pdf",
    "https://guidelines.example/middle.pdf",
    "https://guidelines.example/zulu.pdf",
]
_MARKDOWN = "# Recommendation\n\n## Population\n\nOffer the intervention when appropriate."
_PERMISSION_ID = "spor-publisher-permission-2026-11"


class _CaptureSink:
    def __init__(self) -> None:
        self.artifacts: list[dict[str, object]] = []

    def capture_artifact(self, data: bytes | str, **metadata: object) -> None:
        self.artifacts.append({"data": data, **metadata})


def _public_dns(_host: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


def _fake_converter(data: bytes, title: str, guideline_id: str) -> PdfConversionResult:
    assert data == _PDF_BYTES
    assert title
    assert guideline_id
    return PdfConversionResult(
        markdown=_MARKDOWN,
        provenance={"backend": "pdf-inspector-test-double", "input_bytes": len(data), "page_count": 4},
    )


@contextmanager
def _pdf_client_factory(requests: list[str] | None = None) -> Iterator[httpx.Client]:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(str(request.url))
        return httpx.Response(
            200,
            content=_PDF_BYTES,
            headers={"Content-Type": "application/pdf", "ETag": '"guideline-v1"'},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        yield client


def test_json_manifest_adapter_preserves_metadata_and_deduplicates() -> None:
    """The dependency-free JSON adapter retains publisher/license context."""
    refs = refs_from_json_manifest(
        {
            "documents": [
                {
                    "id": "guideline-alpha",
                    "title": "Alpha Guideline",
                    "url": _GUIDELINE_URLS[0],
                    "developer": "Example Health",
                    "license": "CC BY 4.0",
                    "page": 7,
                },
                _GUIDELINE_URLS[0],
            ]
        }
    )

    assert refs == [
        SporGuidelineRef(
            guideline_id="guideline-alpha",
            title="Alpha Guideline",
            pdf_url=_GUIDELINE_URLS[0],
            publisher="Example Health",
            document_license="CC BY 4.0",
            asset_map_page=7,
        )
    ]


def test_manifest_deduplicates_http_and_https_spellings_of_same_pdf() -> None:
    """Scheme-only aliases cannot consume two slots in an exact-N validation sample."""
    refs = refs_from_manifest(
        [
            "http://Publisher.Example/guideline.pdf?edition=2026",
            "https://publisher.example/guideline.pdf?edition=2026",
            "https://publisher.example/guideline.pdf?edition=2025",
        ]
    )

    assert [ref.pdf_url for ref in refs] == [
        "http://Publisher.Example/guideline.pdf?edition=2026",
        "https://publisher.example/guideline.pdf?edition=2025",
    ]


@pytest.mark.parametrize(
    "entry",
    [
        "https://publisher.example/guideline",
        "https://publisher.example/guideline.html",
        "http://localhost/guideline.pdf",
        "http://127.0.0.1/guideline.pdf",
        "https://user@publisher.example/guideline.pdf",
        "https://publisher.example:444/guideline.pdf",
    ],
    ids=["extensionless", "html", "localhost", "ip-address", "credentials", "port"],
)
def test_manifest_rejects_non_pdf_and_unsafe_urls(entry: str) -> None:
    """Explicit manifests are direct-PDF adapters, not arbitrary publisher crawlers."""
    with pytest.raises(SporFetchError):
        refs_from_manifest([entry])


def test_asset_map_keeps_dynamic_pdf_downloads_and_deduplicates_scheme_aliases() -> None:
    """Official report links may identify PDFs in queries instead of URL paths."""

    def parse_annotations(_data: bytes) -> list[object]:
        return [
            {"url": "http://publisher.example/download.php?url=guideline.pdf", "page": 2},
            {"url": "https://publisher.example/download.php?url=guideline.pdf", "page": 2},
            {"url": "https://publisher.example/get?Filename=Clinical_Guideline.pdf", "page": 3},
            {"url": "https://publisher.example/catalogue.html", "page": 4},
        ]

    refs = spor_module._pdf_entries_from_asset_map(_ASSET_MAP_BYTES, annotation_parser=parse_annotations)

    assert [ref.pdf_url for ref in refs] == [
        "http://publisher.example/download.php?url=guideline.pdf",
        "https://publisher.example/get?Filename=Clinical_Guideline.pdf",
    ]
    assert [ref.title for ref in refs] == ["Guideline", "Clinical Guideline"]


def test_spor_sitemap_discovery_keeps_only_current_cpg_inventory_surfaces() -> None:
    """The WordPress sitemap walk is bounded to English CPG inventory pages."""
    index = b"""<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://sporevidencealliance.ca/wp-sitemap-posts-page-1.xml</loc></sitemap>
      <sitemap><loc>https://sporevidencealliance.ca/wp-sitemap-taxonomies-category-1.xml</loc></sitemap>
      <sitemap><loc>https://sporevidencealliance.ca/fr/wp-sitemap-posts-page-1.xml</loc></sitemap>
    </sitemapindex>"""
    page_url = "https://sporevidencealliance.ca/wp-sitemap-posts-page-1.xml"
    page = b"""<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://sporevidencealliance.ca/key-activities/cpg-asset-map/</loc><lastmod>2024-01-03</lastmod></url>
      <url><loc>https://sporevidencealliance.ca/key-activities/cpg-asset-map/cpg-database/</loc></url>
      <url><loc>https://sporevidencealliance.ca/resources/</loc></url>
    </urlset>"""

    assert sitemap_page_urls(index) == [
        page_url,
        "https://sporevidencealliance.ca/wp-sitemap-taxonomies-category-1.xml",
    ]
    assert relevant_sitemap_entries(page, source_url=page_url) == [
        {
            "url": "https://sporevidencealliance.ca/key-activities/cpg-asset-map/",
            "last_modified": "2024-01-03",
        },
        {
            "url": "https://sporevidencealliance.ca/key-activities/cpg-asset-map/cpg-database/",
            "last_modified": None,
        },
    ]
    assert SITEMAP_URL == "https://sporevidencealliance.ca/wp-sitemap.xml"


def test_default_spor_run_records_sitemap_inventory_before_fixed_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default collection audits current CPG surfaces without crawling their HTML."""
    sitemap_page_url = "https://sporevidencealliance.ca/wp-sitemap-posts-page-1.xml"
    cpg_page_url = "https://sporevidencealliance.ca/key-activities/cpg-asset-map/cpg-database/"
    requests: list[str] = []

    @contextmanager
    def client_factory() -> Iterator[httpx.Client]:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            requests.append(url)
            if url == SITEMAP_URL:
                return httpx.Response(
                    200,
                    content=(f"<sitemapindex><sitemap><loc>{sitemap_page_url}</loc></sitemap></sitemapindex>").encode(),
                )
            if url == sitemap_page_url:
                return httpx.Response(
                    200,
                    content=(
                        f"<urlset><url><loc>{cpg_page_url}</loc><lastmod>2026-08-20</lastmod></url></urlset>"
                    ).encode(),
                )
            if url == ASSET_MAP_PDF_URL:
                return httpx.Response(200, content=_ASSET_MAP_BYTES, headers={"Content-Type": "application/pdf"})
            assert url == _GUIDELINE_URLS[0]
            return httpx.Response(200, content=_PDF_BYTES, headers={"Content-Type": "application/pdf"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    monkeypatch.setattr(spor_module, "SITEMAP_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(spor_module, "DOCUMENT_DELAY_SECONDS", 0.0)
    documents = list(
        scrape_spor(
            documents=1,
            authorized=True,
            permission_id=_PERMISSION_ID,
            annotation_parser=lambda _data: [{"url": _GUIDELINE_URLS[0]}],
            client_factory=client_factory,
            pdf_converter=_fake_converter,
            dns_resolver=_public_dns,
        )
    )

    assert requests == [SITEMAP_URL, sitemap_page_url, ASSET_MAP_PDF_URL, _GUIDELINE_URLS[0]]
    assert documents[0].metadata["sitemap_inventory"]["relevant_pages"] == [
        {"url": cpg_page_url, "last_modified": "2026-08-20"}
    ]
    assert cpg_page_url not in requests


def test_unauthorized_spor_call_is_zero_network() -> None:
    """The permission gate runs before asset-map or publisher retrieval."""
    with pytest.raises(SporPermissionError, match="disabled by default"):
        scrape_spor(
            documents=1,
            link_mode=LinkMode.KEEP,
            url=ASSET_MAP_PDF_URL,
            client_factory=lambda: pytest.fail("network client must not be created"),
        )


def test_asset_map_annotation_adapter_converts_bounded_pdf_with_provenance() -> None:
    """Injected in-memory annotations route only direct PDFs through conversion."""
    parsed_payloads: list[bytes] = []

    def parse_annotations(data: bytes) -> list[object]:
        parsed_payloads.append(data)
        return [
            {"url": "https://publisher.example/catalogue.html", "page": 6},
            {
                "id": "asset-alpha",
                "title": "Asset Alpha",
                "url": _GUIDELINE_URLS[0],
                "publisher": "Example Health",
                "license": "CC BY 4.0",
                "asset_map_page": 7,
            },
            {"id": "asset-middle", "title": "Asset Middle", "url": _GUIDELINE_URLS[1], "page": 118},
        ]

    run = scrape_spor(
        documents=1,
        authorized=True,
        permission_id=_PERMISSION_ID,
        asset_map_pdf=_ASSET_MAP_BYTES,
        annotation_parser=parse_annotations,
        client_factory=_pdf_client_factory,
        pdf_converter=_fake_converter,
        dns_resolver=_public_dns,
    )
    documents = list(run)

    assert run.total == 1
    assert parsed_payloads == [_ASSET_MAP_BYTES]
    assert len(documents) == 1
    document = documents[0]
    assert document.external_id == "spor-asset-alpha"
    assert document.metadata["content_scope"] == "full_pdf"
    assert document.metadata["source_format_types"] == ["pdf"]
    assert document.metadata["source_media_types"] == ["application/pdf"]
    assert document.metadata["publisher"] == "Example Health"
    assert document.metadata["document_license"] == "CC BY 4.0"
    assert document.metadata["asset_map_page"] == 7
    assert document.metadata["asset_map_current_through"] == ASSET_MAP_CURRENT_THROUGH
    assert document.metadata["asset_map_status"] == "historical_static_inventory"
    assert "last updated in April 2018" in document.metadata["staleness_warning"]
    assert document.metadata["manifest_adapter"] == "pdf_link_annotations"
    assert document.metadata["asset_map_retrieval"]["transport"] == "caller-provided-memory"
    assert document.metadata["pdf_retrieval"]["etag"] == '"guideline-v1"'
    assert document.metadata["pdf_conversion"]["backend"] == "pdf-inspector-test-double"
    assert document.metadata["permission_id"] == _PERMISSION_ID
    assert document.provenance["permission_id"] == _PERMISSION_ID
    assert len(document.provenance["retrievals"]) == 2
    assert document.provenance["conversions"] == [document.metadata["pdf_conversion"]]
    assert document.provenance["phase_timings_ms"]["inventory_parse"] >= 0
    assert document.provenance["phase_timings_ms"]["pdf_retrieval"] >= 0
    assert "Offer the intervention" in document.content


def test_fixed_report_mode_skips_bounded_stale_links_and_records_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead 2018 annotation does not stop a later live PDF from being emitted."""
    requests: list[str] = []
    monkeypatch.setattr(spor_module, "DOCUMENT_DELAY_SECONDS", 0.0)

    def parse_annotations(_data: bytes) -> list[object]:
        return [
            {"id": "dead", "title": "Dead", "url": _GUIDELINE_URLS[0]},
            {"id": "live", "title": "Live", "url": _GUIDELINE_URLS[1]},
        ]

    @contextmanager
    def mixed_client_factory() -> Iterator[httpx.Client]:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if str(request.url) == _GUIDELINE_URLS[0]:
                return httpx.Response(404, text="gone")
            return httpx.Response(200, content=_PDF_BYTES, headers={"Content-Type": "application/pdf"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    document = next(
        iter(
            scrape_spor(
                documents=1,
                authorized=True,
                permission_id=_PERMISSION_ID,
                asset_map_pdf=_ASSET_MAP_BYTES,
                annotation_parser=parse_annotations,
                client_factory=mixed_client_factory,
                pdf_converter=_fake_converter,
                dns_resolver=_public_dns,
            )
        )
    )

    assert requests == _GUIDELINE_URLS[:2]
    assert document.external_id == "spor-live"
    assert document.metadata["skipped_stale_candidates"] == [
        {
            "guideline_id": "dead",
            "pdf_url": _GUIDELINE_URLS[0],
            "error_type": "DownloadError",
            "message": f"HTTP 404 while fetching '{_GUIDELINE_URLS[0]}'",
        }
    ]
    assert document.provenance["skipped_stale_candidates"] == document.metadata["skipped_stale_candidates"]


def test_fixed_report_opens_host_circuit_after_repeated_transport_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead publisher host is not contacted once for every historical URL."""
    dead_urls = [f"https://dead.example/guideline-{number}.pdf" for number in range(3)]
    live_url = "https://live.example/guideline.pdf"
    requests: list[str] = []
    monkeypatch.setattr(spor_module, "DOCUMENT_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(spor_module, "MAX_TRANSPORT_RETRIES", 0)

    def parse_annotations(_data: bytes) -> list[object]:
        return [
            *[{"id": f"dead-{index}", "title": "Dead", "url": url} for index, url in enumerate(dead_urls)],
            {"id": "live", "title": "Live", "url": live_url},
        ]

    @contextmanager
    def client_factory() -> Iterator[httpx.Client]:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url.host == "dead.example":
                raise httpx.ConnectTimeout("publisher unavailable", request=request)
            return httpx.Response(200, content=_PDF_BYTES, headers={"Content-Type": "application/pdf"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    document = next(
        iter(
            scrape_spor(
                documents=1,
                authorized=True,
                permission_id=_PERMISSION_ID,
                asset_map_pdf=_ASSET_MAP_BYTES,
                annotation_parser=parse_annotations,
                client_factory=client_factory,
                pdf_converter=_fake_converter,
                dns_resolver=_public_dns,
            )
        )
    )

    assert requests == [*dead_urls[:2], live_url]
    assert [failure["error_type"] for failure in document.metadata["skipped_stale_candidates"]] == [
        "DownloadError",
        "DownloadError",
        "HostCircuitOpen",
    ]


@pytest.mark.parametrize(
    "error_type",
    [httpx.ReadTimeout, httpx.RemoteProtocolError],
    ids=["read-timeout", "remote-protocol"],
)
def test_host_circuit_classifies_all_httpx_transport_failures(
    error_type: type[httpx.TransportError],
) -> None:
    """Slow reads and broken protocols trip the same host circuit as connect failures."""
    request = httpx.Request("GET", _GUIDELINE_URLS[0])
    error = SporFetchError("wrapped")
    error.__cause__ = error_type("publisher unavailable", request=request)

    assert spor_module._transport_failure(error) is True


def test_explicit_manifest_honors_bound_without_touching_end_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Beginning/middle/end manifest entries can be sampled without bulk downloads."""
    requests: list[str] = []
    monkeypatch.setattr(spor_module, "DOCUMENT_DELAY_SECONDS", 0.0)
    run = scrape_spor(
        documents=2,
        authorized=True,
        permission_id=_PERMISSION_ID,
        manifest=_GUIDELINE_URLS,
        client_factory=lambda: _pdf_client_factory(requests),
        pdf_converter=lambda data, title, guideline_id: _MARKDOWN,
        dns_resolver=_public_dns,
    )
    documents = list(run)

    assert run.total == 2
    assert [document.url for document in documents] == _GUIDELINE_URLS[:2]
    assert requests == _GUIDELINE_URLS[:2]
    assert all(document.metadata["manifest_adapter"] == "explicit_url_manifest" for document in documents)
    assert all("unknown; review" in document.metadata["document_license"] for document in documents)


def test_explicit_manifest_exhaustion_does_not_claim_requested_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """A healthy short manifest cannot silently satisfy a larger requested count."""
    monkeypatch.setattr(spor_module, "DOCUMENT_DELAY_SECONDS", 0.0)
    run = scrape_spor(
        documents=2,
        authorized=True,
        permission_id=_PERMISSION_ID,
        manifest=[_GUIDELINE_URLS[0]],
        client_factory=_pdf_client_factory,
        pdf_converter=_fake_converter,
        dns_resolver=_public_dns,
    )

    with pytest.raises(SporFetchError, match="produced 1 of 2 requested documents"):
        list(run)


def test_transient_publisher_response_uses_retry_after_and_records_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 is retried without turning a live guideline into a stale-link skip."""
    requests: list[str] = []
    delays: list[float] = []

    @contextmanager
    def client_factory() -> Iterator[httpx.Client]:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if len(requests) == 1:
                return httpx.Response(429, headers={"Retry-After": "4"})
            return httpx.Response(200, content=_PDF_BYTES, headers={"Content-Type": "application/pdf"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    monkeypatch.setattr(spor_module.time, "sleep", delays.append)
    document = next(
        iter(
            scrape_spor(
                documents=1,
                authorized=True,
                permission_id=_PERMISSION_ID,
                manifest=[_GUIDELINE_URLS[0]],
                client_factory=client_factory,
                pdf_converter=_fake_converter,
                dns_resolver=_public_dns,
            )
        )
    )

    assert requests == [_GUIDELINE_URLS[0], _GUIDELINE_URLS[0]]
    assert delays == [4.0]
    assert document.metadata["pdf_retrieval"]["attempt_count"] == 2
    assert document.metadata["pdf_retrieval"]["retry_delays_seconds"] == [4.0]


def test_asset_map_url_scope_is_checked_before_network() -> None:
    """An attacker cannot substitute an arbitrary PDF for the fixed historical report."""
    with pytest.raises(SporFetchError, match="Only the fixed April 2018"):
        scrape_spor(
            documents=1,
            authorized=True,
            permission_id=_PERMISSION_ID,
            url="https://sporevidencealliance.ca/wp-content/uploads/2026/other.pdf",
            client_factory=lambda: pytest.fail("network client must not be created for an invalid report URL"),
        )


def test_missing_annotation_dependency_points_to_json_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without pypdf, the failure explains the supported dependency-free route."""
    monkeypatch.setattr(spor_module, "_PdfReader", None)
    run = scrape_spor(
        documents=1,
        authorized=True,
        permission_id=_PERMISSION_ID,
        asset_map_pdf=_ASSET_MAP_BYTES,
        client_factory=_pdf_client_factory,
    )

    with pytest.raises(SporFetchError, match="Provide manifest_json"):
        list(run)


def test_manifest_entry_that_returns_html_is_not_followed() -> None:
    """A publisher redirect to HTML fails instead of triggering site crawling."""
    requests: list[str] = []

    @contextmanager
    def html_client_factory() -> Iterator[httpx.Client]:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, text="<html><body>Publisher landing page</body></html>")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    run = scrape_spor(
        documents=1,
        authorized=True,
        permission_id=_PERMISSION_ID,
        manifest=[_GUIDELINE_URLS[0]],
        client_factory=html_client_factory,
        pdf_converter=_fake_converter,
        dns_resolver=_public_dns,
    )

    with pytest.raises(SporFetchError, match="did not resolve directly to a PDF"):
        list(run)
    assert requests == [_GUIDELINE_URLS[0]]


def test_publisher_redirect_to_private_resolution_is_blocked_before_following() -> None:
    """A public manifest URL cannot redirect the downloader into a private network."""
    requests: list[str] = []

    @contextmanager
    def redirect_client_factory() -> Iterator[httpx.Client]:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(302, headers={"Location": "https://metadata.internal/guideline.pdf"})

        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            yield client

    def resolver(host: str, _port: int) -> list[str]:
        return ["169.254.169.254"] if host == "metadata.internal" else ["93.184.216.34"]

    run = scrape_spor(
        documents=1,
        authorized=True,
        permission_id=_PERMISSION_ID,
        manifest=[_GUIDELINE_URLS[0]],
        client_factory=redirect_client_factory,
        pdf_converter=_fake_converter,
        dns_resolver=resolver,
    )

    with pytest.raises(ScrapeError, match="public network boundary"):
        list(run)
    assert requests == [_GUIDELINE_URLS[0]]


def test_authorized_spor_call_requires_permission_reference_before_network() -> None:
    """Permission evidence is required even when the report URL is public."""
    with pytest.raises(SporPermissionError, match="permission_id"):
        scrape_spor(
            documents=1,
            authorized=True,
            url=ASSET_MAP_PDF_URL,
            client_factory=lambda: pytest.fail("network client must not be created"),
        )


def test_review_capture_retains_guideline_pdf_before_conversion() -> None:
    """Review mode stores the exact publisher PDF alongside converted Markdown."""
    sink = _CaptureSink()
    with artifact_capture_context(sink):
        documents = list(
            scrape_spor(
                documents=1,
                authorized=True,
                permission_id=_PERMISSION_ID,
                manifest=[
                    SporGuidelineRef(
                        guideline_id="capture",
                        title="Captured Guideline",
                        pdf_url=_GUIDELINE_URLS[0],
                        publisher="Example Health",
                    )
                ],
                client_factory=_pdf_client_factory,
                pdf_converter=_fake_converter,
                dns_resolver=_public_dns,
            )
        )

    assert len(documents) == 1
    assert len(sink.artifacts) == 1
    artifact = sink.artifacts[0]
    assert artifact["data"] == _PDF_BYTES
    assert artifact["media_type"] == "application/pdf"
    assert artifact["filename"] == "spor-capture.pdf"
    assert artifact["metadata"] == {
        "representation": "downloaded_pdf",
        "publisher": "Example Health",
    }


def test_fixed_report_capture_does_not_leak_failed_candidate_into_next_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skipped conversion cannot misattribute its PDF to a later record."""

    def parse_annotations(_data: bytes) -> list[object]:
        return [
            {"id": "dead", "title": "Dead", "url": _GUIDELINE_URLS[0]},
            {"id": "live", "title": "Live", "url": _GUIDELINE_URLS[1]},
        ]

    def converter(data: bytes, title: str, guideline_id: str) -> PdfConversionResult:
        if guideline_id == "dead":
            raise SporFetchError("conversion failed")
        return _fake_converter(data, title, guideline_id)

    monkeypatch.setattr(spor_module, "DOCUMENT_DELAY_SECONDS", 0.0)
    sink = _CaptureSink()
    with artifact_capture_context(sink):
        documents = list(
            scrape_spor(
                documents=1,
                authorized=True,
                permission_id=_PERMISSION_ID,
                asset_map_pdf=_ASSET_MAP_BYTES,
                annotation_parser=parse_annotations,
                client_factory=_pdf_client_factory,
                pdf_converter=converter,
                dns_resolver=_public_dns,
            )
        )

    assert [document.external_id for document in documents] == ["spor-live"]
    assert [artifact["role"] for artifact in sink.artifacts] == ["inventory", "source"]
    assert sink.artifacts[1]["filename"] == "spor-live.pdf"
