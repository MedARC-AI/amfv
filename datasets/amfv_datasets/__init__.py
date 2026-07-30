"""Web scraping helpers and source-specific scrapers."""

from amfv_datasets.scraping.base import (
    USER_AGENT,
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    default_client,
    scrape_listing_documents,
)
from amfv_datasets.scraping.cco import (
    CcoFetchError,
    GuidelineRef,
    PageFetch,
    guideline_ref_from_url,
    list_cco_guidelines,
    playwright_client,
    playwright_fetch,
    scrape_cco,
    scrape_cco_guideline,
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

__all__ = [
    "CcoFetchError",
    "GuidanceRef",
    "GuidanceListingPage",
    "GuidelineRef",
    "LinkMode",
    "NiceFetchError",
    "OutputFormat",
    "PageFetch",
    "ScrapeError",
    "ScrapeRun",
    "ScrapedDocument",
    "ScraperSource",
    "USER_AGENT",
    "absolute_unique_urls",
    "build_guideline_text",
    "clean_text",
    "document_title",
    "default_client",
    "first_matching_urls",
    "guidance_ref_from_url",
    "guideline_ref_from_url",
    "html_to_markdown",
    "list_cco_guidelines",
    "list_published_guidance",
    "playwright_client",
    "playwright_fetch",
    "scrape_cco",
    "scrape_cco_guideline",
    "scrape_guideline",
    "scrape_listing_documents",
    "scrape_nice",
]