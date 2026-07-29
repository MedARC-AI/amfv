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
from amfv_datasets.scraping.drugs_com import (
    DrugsComFetchError,
    DrugsComPageRef,
    build_drugs_com_article_text,
    drugscom_ref_from_url,
    list_drug_refs,
    list_two_letter_pages,
    scrape_drugs_com,
    scrape_drugs_com_page,
)
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
    "DRUGSCOM_BASE_URL",
    "DrugsComFetchError",
    "DrugsComPageRef",
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
    "absolute_unique_urls",
    "build_drugs_com_article_text",
    "build_guideline_text",
    "clean_text",
    "default_client",
    "document_title",
    "drugscom_ref_from_url",
    "first_matching_urls",
    "guidance_ref_from_url",
    "html_to_markdown",
    "list_drug_refs",
    "list_published_guidance",
    "list_two_letter_pages",
    "scrape_drugs_com",
    "scrape_drugs_com_page",
    "scrape_guideline",
    "scrape_listing_documents",
    "scrape_nice",
]
