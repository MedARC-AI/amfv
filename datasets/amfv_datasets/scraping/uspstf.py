"""Scrape USPSTF recommendation statements into normalized markdown documents.

The U.S. Preventive Services Task Force publishes final recommendation
statements for clinical preventive services, each carrying a letter grade (A,
B, C, D, or I) that reflects the certainty and magnitude of net benefit.

Discovery works around a quirk of the published-recommendations listing: it
reports a total in a "Hits: N" counter but renders only the first 20 rows,
with no working pager or page-size parameter. The same view filtered by one
of its twelve topic categories does return that category's full set, so
discovery walks all twelve up front and deduplicates by slug. A
recommendation that belongs to several categories is therefore listed
several times and scraped once. The categories are walked eagerly rather
than one per listing page because a category can contribute nothing new,
and the shared listing loop reads an empty page as the end of the source,
which would silently drop every later category.

Only recommendations the listing marks Published are kept. The listing is
requested with `topic_status=P`, but that filter stops being honoured once a
category filter is applied, so rows marked Inactive or Referred (guidance
USPSTF has retired or handed to another body, some dating to 1996) come back
too and are dropped here. Retired guidance in a verification corpus is worse
than a smaller corpus: it produces confident, wrong verdicts.

Content is assembled from the recommendation's own outline: the
population/recommendation/grade summary table, then each content panel.
Panels that are boilerplate identical across every recommendation (the
preamble or mission statement) or purely administrative (task force
membership, the copyright notice) are skipped, since 100+ verbatim copies of
the same text are noise in a corpus.

Licensing: USPSTF recommendations are not public domain despite being
federal work. AHRQ's copyright notice
(https://www.uspreventiveservicestaskforce.org/uspstf/recommendation-topics/copyright-notice)
permits reproduction and redistribution "provided that it is reproduced
without any changes to the work or portions thereof, except as permitted as
fair use", prohibits redistribution for a fee or incorporation into a
profit-making venture without written permission, and asks that the USPSTF
web page be cited when parts are quoted. Every document carries `LICENSE`,
`LICENSE_URL`, and `ATTRIBUTION` in its metadata so this travels downstream.
The no-changes term deserves attention before this corpus feeds claim
decomposition or model training, both of which are derivative uses; that
call belongs to whoever builds those pipelines, not to this scraper.

Documents are large: live samples run 15,000-95,000 characters, since a
recommendation statement bundles its rationale, evidence review, and
references.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from lxml import html as lxml_html

from amfv_datasets.scraping.base import (
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    default_client,
    scrape_listing_documents,
)
from amfv_datasets.scraping.html import LinkMode, clean_text, html_to_markdown

BASE_URL = "https://www.uspreventiveservicestaskforce.org"
USPSTF_DOMAIN = "uspreventiveservicestaskforce.org"
"""Registrable domain a `--url` argument has to sit on, matched exactly or as a
parent of one of its subdomains. Matching it as a bare suffix would also accept
lookalikes such as `notuspreventiveservicestaskforce.org`."""
LISTING_URL = f"{BASE_URL}/uspstf/topic_search_results"
USPSTF_DATASET_NAME = "uspstf-webscrape"
USPSTF_DATASET_DISPLAY_NAME = "USPSTF Webscrape"
DOCUMENT_DELAY_SECONDS = 5.0
"""Delay between recommendation pages, matching the site's robots.txt Crawl-delay."""
LISTING_DELAY_SECONDS = 5.0
"""Delay between the twelve category listing requests, for the same reason."""
CATEGORY_IDS = (15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26)
"""Topic category filter values, used to page past the listing's 20-row ceiling."""
LICENSE = "AHRQ USPSTF copyright notice (no changes, no redistribution for a fee)"
LICENSE_URL = f"{BASE_URL}/uspstf/recommendation-topics/copyright-notice"
ATTRIBUTION = "U.S. Preventive Services Task Force"

logger = logging.getLogger(__name__)

_RECOMMENDATION_PATH_RE = re.compile(r"^/uspstf/recommendation/(?P<slug>[A-Za-z0-9_-]+)/?$")
_LISTING_COLUMNS = ("status", "topic_type", "year", "title", "age_group", "grade", "category")
PUBLISHED_STATUS = "Published"
"""Only status kept. The listing still returns retired rows under a category filter."""
# Boilerplate repeated verbatim on every recommendation, and administrative
# sections that carry no clinical content. Compared case-insensitively against
# each panel's heading.
_SKIP_PANEL_HEADINGS = frozenset(
    {
        "preamble",
        "mission statement",
        "authors of the recommendation statement",
        "members of the us preventive services task force",
        "copyright and source information",
    }
)


class UspstfFetchError(ScrapeError):
    """Raised when a USPSTF recommendation cannot be fetched or parsed."""


@dataclass(frozen=True)
class RecommendationRef:
    """A published recommendation discovered from a category listing page."""

    slug: str
    title: str
    status: str = ""
    topic_type: str = ""
    year: str = ""
    age_group: str = ""
    grade: str = ""
    category: str = ""


def recommendation_url(slug: str) -> str:
    """Return the page URL for a recommendation slug.

    Args:
        slug: Recommendation slug, e.g. `breast-cancer-screening`.
    """
    return f"{BASE_URL}/uspstf/recommendation/{slug}"


def recommendation_slug_from_url(url: str) -> str:
    """Return the slug for a USPSTF recommendation URL.

    Args:
        url: Recommendation URL, e.g.
            `https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/breast-cancer-screening`.
    """
    parsed = urlparse(url.strip())
    # `hostname` drops any port and lowercases, unlike `netloc`.
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not (host == USPSTF_DOMAIN or host.endswith(f".{USPSTF_DOMAIN}")):
        raise UspstfFetchError(f"Enter a USPSTF URL from {USPSTF_DOMAIN}; got {url!r}")
    match = _RECOMMENDATION_PATH_RE.match(parsed.path)
    if not match:
        raise UspstfFetchError(
            f"Enter a recommendation URL like {recommendation_url('breast-cancer-screening')}; got {url!r}"
        )
    return match.group("slug")


def list_category_recommendations(client: httpx.Client, category_id: int) -> list[RecommendationRef]:
    """Return published recommendations listed under one topic category.

    Args:
        client: HTTP client used to fetch the listing page.
        category_id: Topic category filter value from `CATEGORY_IDS`.
    """
    try:
        response = client.get(LISTING_URL, params={"topic_status": "P", "category[]": category_id})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise UspstfFetchError(f"Could not fetch the USPSTF listing for category {category_id}") from exc

    doc = lxml_html.fromstring(response.text)
    refs: list[RecommendationRef] = []
    for row in doc.xpath("//table//tbody/tr"):
        hrefs = row.xpath('.//a[contains(@href, "/uspstf/recommendation/")]/@href')
        if not hrefs:
            # The site's own "Hits" counter can exceed the rows it renders; a
            # row without a link is not reachable, so there is nothing to scrape.
            continue
        match = _RECOMMENDATION_PATH_RE.match(urlparse(hrefs[0]).path)
        if not match:
            continue
        cells = [clean_text(cell.text_content()) for cell in row.xpath("./td")]
        fields = dict(zip(_LISTING_COLUMNS, cells, strict=False))
        status = fields.get("status", "")
        if status != PUBLISHED_STATUS:
            # `topic_status=P` is requested but not honoured once a category
            # filter is applied, so retired guidance is filtered here instead.
            logger.debug("Skipping %s USPSTF recommendation %s", status or "unlabelled", match.group("slug"))
            continue
        refs.append(
            RecommendationRef(
                slug=match.group("slug"),
                title=fields.get("title") or match.group("slug"),
                status=fields.get("status", ""),
                topic_type=fields.get("topic_type", ""),
                year=fields.get("year", ""),
                age_group=fields.get("age_group", ""),
                grade=fields.get("grade", ""),
                category=fields.get("category", ""),
            )
        )
    return refs


def list_recommendations(
    client: httpx.Client, *, delay_seconds: float = LISTING_DELAY_SECONDS
) -> list[RecommendationRef]:
    """Return every published recommendation across all topic categories.

    Categories are walked eagerly rather than lazily one per listing page: a
    category whose recommendations all appeared under an earlier category
    yields nothing new, and the shared listing loop treats an empty page as
    the end of the source, which would silently drop every later category.

    Args:
        client: HTTP client used to fetch the listing pages.
        delay_seconds: Delay between category listing requests (default:
            LISTING_DELAY_SECONDS).
    """
    seen: set[str] = set()
    refs: list[RecommendationRef] = []
    for index, category_id in enumerate(CATEGORY_IDS):
        if index and delay_seconds:
            time.sleep(delay_seconds)
        for ref in list_category_recommendations(client, category_id):
            if ref.slug in seen:
                continue
            seen.add(ref.slug)
            refs.append(ref)
    if not refs:
        raise UspstfFetchError(f"No published recommendations found across {len(CATEGORY_IDS)} categories")
    return refs


def scrape_recommendation(
    client: httpx.Client,
    ref: RecommendationRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> ScrapedDocument | None:
    """Scrape one recommendation into a normalized document.

    Returns None when the page cannot be fetched or carries no readable
    content, which is logged rather than raised so one bad page costs a
    document instead of the rest of the crawl.

    Args:
        client: HTTP client used to fetch the recommendation page.
        ref: Recommendation discovered from a category listing.
        link_mode: Whether links are kept as markdown links or stripped to
            their visible text (default: LinkMode.KEEP).
    """
    url = recommendation_url(ref.slug)
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Skipping USPSTF recommendation %s: %s", ref.slug, exc)
        return None

    doc = lxml_html.fromstring(response.text)
    content, section_count = _recommendation_content(doc, base_url=url, link_mode=link_mode)
    if not content:
        logger.warning("Skipping USPSTF recommendation %s: no readable content", ref.slug)
        return None

    return ScrapedDocument(
        source="uspstf",
        external_id=f"uspstf-{ref.slug}",
        title=ref.title or _page_title(doc, fallback=ref.slug),
        url=url,
        content=content,
        section_count=section_count,
        metadata=_metadata(doc, ref=ref),
    )


def scrape_recommendation_by_url(
    client: httpx.Client,
    url: str,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> ScrapedDocument:
    """Scrape a recommendation from its page URL.

    Unlike `scrape_recommendation`, this raises on failure rather than
    returning None: a single `--url` request has no other document to fall
    back on, and the listing metadata (grade, year, category) is unavailable.

    Args:
        client: HTTP client used to fetch the recommendation page.
        url: Recommendation URL to scrape.
        link_mode: Whether links are kept as markdown links or stripped to
            their visible text (default: LinkMode.KEEP).
    """
    slug = recommendation_slug_from_url(url)
    page_url = recommendation_url(slug)
    try:
        response = client.get(page_url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise UspstfFetchError(f"Could not fetch USPSTF recommendation '{slug}'") from exc

    doc = lxml_html.fromstring(response.text)
    content, section_count = _recommendation_content(doc, base_url=page_url, link_mode=link_mode)
    if not content:
        raise UspstfFetchError(f"No readable content for USPSTF recommendation '{slug}'")

    ref = RecommendationRef(slug=slug, title=_page_title(doc, fallback=slug))
    return ScrapedDocument(
        source="uspstf",
        external_id=f"uspstf-{slug}",
        title=ref.title,
        url=page_url,
        content=content,
        section_count=section_count,
        metadata=_metadata(doc, ref=ref),
    )


def scrape_uspstf(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
) -> ScrapeRun:
    """Scrape USPSTF recommendations discovered from the category listings.

    Args:
        documents: Number of recommendations to scrape. Ignored when `url` is
            set. When unset, every category is walked (default: None).
        link_mode: Whether links are kept as markdown links or stripped to
            their visible text (default: LinkMode.KEEP).
        url: Recommendation URL to scrape as a single document (default: None).
    """
    if url is not None:
        recommendation = recommendation_slug_from_url(url)

        def scrape_url() -> Iterable[ScrapedDocument]:
            with default_client() as client:
                yield scrape_recommendation_by_url(client, recommendation_url(recommendation), link_mode=link_mode)

        return ScrapeRun(documents=scrape_url(), total=1)

    with default_client() as client:
        refs = list_recommendations(client)

    return ScrapeRun(
        total=documents if documents is not None else len(refs),
        documents=scrape_listing_documents(
            documents=documents,
            client_factory=default_client,
            first_page_items=refs,
            list_page=lambda client, page: [],
            scrape_item=lambda client, ref: scrape_recommendation(client, ref, link_mode=link_mode),
            document_delay_seconds=DOCUMENT_DELAY_SECONDS,
        ),
    )


def _page_title(doc: lxml_html.HtmlElement, *, fallback: str) -> str:
    headings = [
        clean_text(value.text_content()) for value in doc.xpath('//section[@class="recommendation-statement-intro"]/h1')
    ]
    return next((value for value in headings if value), fallback)


def _panel_heading(panel: lxml_html.HtmlElement) -> str:
    headings = panel.xpath('.//*[contains(@class, "panel-title")]')
    return clean_text(headings[0].text_content()) if headings else ""


def _recommendation_content(doc: lxml_html.HtmlElement, *, base_url: str, link_mode: LinkMode) -> tuple[str, int]:
    """Return a recommendation as markdown and the number of sections kept.

    Args:
        doc: Parsed recommendation page.
        base_url: URL of the page `doc` came from, used to resolve the relative
            links it contains. A relative link means something only against the
            page that carries it, so passing the site root here would resolve
            `tools/` to `/tools/` rather than to a path under the
            recommendation.
        link_mode: Whether links are kept as markdown links or stripped to
            their visible text.
    """
    sections: list[str] = []

    summary_tables = doc.xpath('//div[contains(@class, "summary-table")]')
    if summary_tables:
        summary = html_to_markdown(
            lxml_html.tostring(summary_tables[0], encoding="unicode"),
            link_mode=link_mode,
            base_url=base_url,
        )
        if summary:
            sections.append(f"## Recommendation Summary\n\n{summary}")

    panels = doc.xpath(
        '//div[starts-with(@id, "bootstrap-panel")][contains(@class, "panel")][not(contains(@class, "panel-collapse"))]'
    )
    for panel in panels:
        heading = _panel_heading(panel)
        if heading.lower() in _SKIP_PANEL_HEADINGS:
            continue
        bodies = panel.xpath('.//div[contains(@class, "panel-body")]')
        if not bodies:
            continue
        body = html_to_markdown(
            lxml_html.tostring(bodies[0], encoding="unicode"),
            link_mode=link_mode,
            base_url=base_url,
        )
        if not body:
            continue
        sections.append(f"## {heading}\n\n{body}" if heading else body)

    return "\n\n".join(sections).strip(), len(sections)


def _metadata(doc: lxml_html.HtmlElement, *, ref: RecommendationRef) -> dict[str, str]:
    published = [clean_text(value.text_content()) for value in doc.xpath('//h4[contains(@class, "pubdate")]')]
    return {
        "slug": ref.slug,
        "grade": ref.grade,
        "year": ref.year,
        "status": ref.status,
        "topic_type": ref.topic_type,
        "age_group": ref.age_group,
        "category": ref.category,
        "published": next((value for value in published if value), ""),
        "attribution": ATTRIBUTION,
        "license": LICENSE,
        "license_url": LICENSE_URL,
    }


__all__ = [
    "ATTRIBUTION",
    "BASE_URL",
    "CATEGORY_IDS",
    "DOCUMENT_DELAY_SECONDS",
    "LICENSE",
    "LICENSE_URL",
    "LISTING_DELAY_SECONDS",
    "LISTING_URL",
    "PUBLISHED_STATUS",
    "USPSTF_DATASET_DISPLAY_NAME",
    "USPSTF_DATASET_NAME",
    "USPSTF_DOMAIN",
    "RecommendationRef",
    "UspstfFetchError",
    "list_category_recommendations",
    "list_recommendations",
    "recommendation_slug_from_url",
    "recommendation_url",
    "scrape_recommendation",
    "scrape_recommendation_by_url",
    "scrape_uspstf",
]
