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
from amfv_datasets.scraping.pdf import (
    PdfBackend,
    PdfConversionError,
    count_markdown_sections,
    pdf_to_markdown,
)
from amfv_datasets.scraping.who import (
    WhoFetchError,
    WhoListingPage,
    WhoPublicationRef,
    build_publication_text,
    list_publications,
    publication_ref_from_url,
    scrape_publication,
    scrape_who,
)

__all__ = [
    "GuidanceRef",
    "GuidanceListingPage",
    "LinkMode",
    "NiceFetchError",
    "OutputFormat",
    "PdfBackend",
    "PdfConversionError",
    "ScrapeError",
    "ScrapeRun",
    "ScrapedDocument",
    "ScraperSource",
    "USER_AGENT",
    "WhoFetchError",
    "WhoListingPage",
    "WhoPublicationRef",
    "absolute_unique_urls",
    "build_guideline_text",
    "build_publication_text",
    "clean_text",
    "count_markdown_sections",
    "document_title",
    "default_client",
    "first_matching_urls",
    "guidance_ref_from_url",
    "html_to_markdown",
    "list_published_guidance",
    "list_publications",
    "pdf_to_markdown",
    "publication_ref_from_url",
    "scrape_guideline",
    "scrape_listing_documents",
    "scrape_nice",
    "scrape_publication",
    "scrape_who",
]
