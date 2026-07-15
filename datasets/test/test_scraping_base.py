"""Tests for shared scraping primitives."""

import httpx
import pytest

from amfv_datasets.scraping import base
from amfv_datasets.scraping.base import ScrapedDocument, default_client, scrape_listing_documents


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
    """Listing scraping waits before each document after the first."""
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
