"""Common types for source web scrapers."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)


class ScrapeError(RuntimeError):
    """Raised when a source page cannot be fetched or parsed."""


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


@dataclass(frozen=True)
class ScrapeRun:
    """A configured scrape with an optional source-provided document total.

    Sources should set `total` only when it is cheap enough to know before
    iterating the documents.
    """

    documents: Iterable[ScrapedDocument]
    total: int | None = None

    def __iter__(self) -> Iterator[ScrapedDocument]:
        return iter(self.documents)


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


def scrape_listing_documents[ListingItemT](
    *,
    documents: int | None,
    client_factory: Callable[[], AbstractContextManager[httpx.Client]],
    list_page: Callable[[httpx.Client, int], Iterable[ListingItemT]],
    scrape_item: Callable[[httpx.Client, ListingItemT], ScrapedDocument | None],
    document_delay_seconds: float = 5.0,
    first_page_items: Iterable[ListingItemT] | None = None,
) -> Iterable[ScrapedDocument]:
    """Scrape a limited number of documents discovered from listing pages.

    Args:
        documents: Number of documents to scrape. When unset, listing pages are
            fetched until a page returns no items (default: None).
        client_factory: Factory returning a context-managed HTTP client.
        list_page: Function that lists source-specific items for a page.
        scrape_item: Function that scrapes one listed item into a document. It
            may return None to skip a discovered item that is not a document.
        document_delay_seconds: Delay before scraping each document after the
            first one (default: 5.0).
        first_page_items: Already-fetched first listing page items. When set,
            these are used before fetching page 2 (default: None).
    """
    if documents is not None and documents < 1:
        raise ValueError(f"documents must be at least 1; got {documents}")
    if document_delay_seconds < 0:
        raise ValueError(f"document_delay_seconds must be non-negative; got {document_delay_seconds}")

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
                if attempted and document_delay_seconds:
                    time.sleep(document_delay_seconds)
                document = scrape_item(client, item)
                attempted += 1
                if document is None:
                    continue
                yield document
                scraped += 1
            page += 1


__all__ = [
    "ScrapeError",
    "ScrapeRun",
    "ScrapedDocument",
    "USER_AGENT",
    "default_client",
    "scrape_listing_documents",
]
