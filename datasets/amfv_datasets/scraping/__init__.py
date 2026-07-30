"""Web scraping helpers and source-specific scrapers."""

from amfv_datasets.scraping.base import (
    USER_AGENT,
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    default_client,
    scrape_listing_documents,
)
from amfv_datasets.scraping.cli import OutputFormat, ScraperSource
from amfv_datasets.scraping.html import (
    LinkMode,
    absolute_unique_urls,
    clean_text,
    document_title,
    first_matching_urls,
    html_to_markdown,
)
from amfv_datasets.scraping.nice import (
    GuidanceListingPage,
    GuidanceRef,
    NiceFetchError,
    build_guideline_text,
    guidance_ref_from_url,
    list_published_guidance,
    scrape_guideline,
    scrape_nice,
)
from amfv_datasets.scraping.wikidoc import (
    WikiDocFetchError,
    WikiDocPageRef,
    build_wikidoc_article_text,
    list_wikidoc_articles,
    scrape_wikidoc,
    scrape_wikidoc_article,
    wikidoc_ref_from_url,
)

__all__ = [
    "GuidanceRef",
    "GuidanceListingPage",
    "LinkMode",
    "NiceFetchError",
    "OutputFormat",
    "ScrapeError",
    "ScrapeRun",
    "ScrapedDocument",
    "ScraperSource",
    "USER_AGENT",
    "WikiDocFetchError",
    "WikiDocPageRef",
    "absolute_unique_urls",
    "build_guideline_text",
    "build_wikidoc_article_text",
    "clean_text",
    "document_title",
    "default_client",
    "first_matching_urls",
    "guidance_ref_from_url",
    "html_to_markdown",
    "list_published_guidance",
    "list_wikidoc_articles",
    "scrape_guideline",
    "scrape_listing_documents",
    "scrape_nice",
    "scrape_wikidoc",
    "scrape_wikidoc_article",
    "wikidoc_ref_from_url",
]
