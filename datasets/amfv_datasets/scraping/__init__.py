"""Web scraping helpers and source-specific scrapers.

Only the shared scraping contract is re-exported here. Import a source's own
symbols from its module, so registering a source touches one file.
"""

from amfv_datasets.scraping.base import (
    SCRAPED_DOCUMENT_SCHEMA_VERSION,
    USER_AGENT,
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    default_client,
    scrape_listing_documents,
)
from amfv_datasets.scraping.cli import ALL_SOURCES, SCRAPERS, OutputFormat, Scraper
from amfv_datasets.scraping.contract import (
    SCRAPED_DOCUMENT_V1_FIELDS,
    ScrapedDocumentContractError,
    serialize_scraped_document,
    validate_scraped_document_row,
)
from amfv_datasets.scraping.html import (
    LinkMode,
    absolute_unique_urls,
    clean_text,
    document_title,
    first_matching_urls,
    html_to_markdown,
)

__all__ = [
    "ALL_SOURCES",
    "LinkMode",
    "OutputFormat",
    "SCRAPERS",
    "SCRAPED_DOCUMENT_SCHEMA_VERSION",
    "SCRAPED_DOCUMENT_V1_FIELDS",
    "ScrapeError",
    "ScrapeRun",
    "ScrapedDocument",
    "ScrapedDocumentContractError",
    "Scraper",
    "USER_AGENT",
    "absolute_unique_urls",
    "clean_text",
    "default_client",
    "document_title",
    "first_matching_urls",
    "html_to_markdown",
    "scrape_listing_documents",
    "serialize_scraped_document",
    "validate_scraped_document_row",
]
