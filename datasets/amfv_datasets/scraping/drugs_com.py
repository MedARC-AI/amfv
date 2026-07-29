from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from lxml import html as lxml_html

from amfv_datasets.scraping.base import (
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    default_client,
    scrape_listing_documents,
)
from amfv_datasets.scraping.html import LinkMode, html_to_markdown

logger = logging.getLogger(__name__)

BASE_URL = "https://www.drugs.com"
ALPHA_PATH = "/alpha"

DRUGSCOM_DATASET_NAME = "drugscom-webscrape"
DRUGSCOM_DATASET_DISPLAY_NAME = "Drugs.com Webscrape"

DOCUMENT_DELAY_SECONDS = 5.0

APPROXIMATE_ARTICLE_COUNT = 24_000

LETTERS: tuple[str, ...] = tuple("abcdefghijklmnopqrstuvwxyz") + ("0-9",)

_DRUG_NAMESPACES = (
    "mtm",
    "cons",
    "pro",
    "monograph",
    "cdi",
)

_DRUG_LINK_RE = re.compile(r"^/(?:(?P<ns>" + "|".join(_DRUG_NAMESPACES) + r")/)?(?P<slug>[a-z0-9][a-z0-9-]*)\.html$")

_TWO_LETTER_LINK_RE = re.compile(r"^/alpha/(?P<pair>[a-z]{2})\.html$")

_EXCLUDED_ROOT_SLUGS = frozenset(
    {
        "drug_information",
        "pill_identification",
        "drug_interactions",
        "search_advanced",
        "sitemap",
        "news",
        "professionals",
    }
)

_TRUNCATE_FROM_HEADING_RE = re.compile(
    r"^#{1,3}\s*more about\b",
    re.IGNORECASE | re.MULTILINE,
)

_MIDPAGE_DROP_HEADING_RE = re.compile(
    r"^#{2,3}\s*(?:related/similar drugs|does .+ interact with my other drugs?\??)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_ANY_HEADING_RE = re.compile(
    r"^#{1,3}\s",
    re.MULTILINE,
)

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")

_REVIEWED_BYLINE_RE = re.compile(
    r"Medically reviewed by (?P<reviewer>.+?)\.\s*"
    r"Last updated on (?P<updated>[^.\n]+)\.?",
    re.DOTALL,
)

_COPYRIGHT_LINE_RE = re.compile(
    r"Copyright \d{4}-\d{4} (?P<holder>[^.\n]+?)\.?"
    r"(?:\s*Version:[^\n]*)?$",
    re.MULTILINE,
)

_PRONUNCIATION_LINE_RE = re.compile(
    r"^Play pronunciation\.$",
    re.MULTILINE,
)

_BOILERPLATE_LINES = frozenset(
    {
        "Print page",
        "My Meds",
    }
)


def _plain_text(value: str) -> str:
    """Collapse markdown links into visible text."""
    return _MARKDOWN_LINK_RE.sub(r"\1", value).strip()


class DrugsComFetchError(ScrapeError):
    """Raised when a Drugs.com page cannot be fetched or parsed."""


@dataclass(frozen=True)
class DrugsComPageRef:
    """Reference to a Drugs.com detail page."""

    slug: str
    name: str

    @property
    def path(self) -> str:
        """Return the page's absolute path."""
        return f"/{self.slug}.html"

    @property
    def page_url(self) -> str:
        """Return the canonical page URL."""
        return f"{BASE_URL}{self.path}"

    @property
    def external_id(self) -> str:
        """Return the unique external identifier."""
        return f"drugscom-{self.slug.replace('/', '-')}"


def drugscom_ref_from_url(url: str) -> DrugsComPageRef:
    """Convert a Drugs.com URL into a page reference."""
    parsed = urlparse(url.strip())

    if parsed.scheme not in {"http", "https"}:
        raise DrugsComFetchError(f"Invalid Drugs.com URL: {url!r}")

    if parsed.netloc.lower() not in {
        "www.drugs.com",
        "drugs.com",
    }:
        raise DrugsComFetchError(f"Not a Drugs.com URL: {url!r}")

    match = _DRUG_LINK_RE.match(unquote(parsed.path))

    if not match:
        raise DrugsComFetchError(f"Invalid drug page URL: {url!r}")

    if match.group("ns") is None and match.group("slug") in _EXCLUDED_ROOT_SLUGS:
        raise DrugsComFetchError(f"Not a drug page: {url!r}")

    namespace = match.group("ns")

    slug = match.group("slug") if namespace is None else f"{namespace}/{match.group('slug')}"

    return DrugsComPageRef(
        slug=slug,
        name=match.group("slug").replace("-", " "),
    )


def _get(
    client: httpx.Client,
    path: str,
) -> lxml_html.HtmlElement:
    """Fetch a Drugs.com path and parse HTML."""
    response = client.get(f"{BASE_URL}{path}")

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise DrugsComFetchError(f"Drugs.com returned {response.status_code} for {path!r}") from error

    return lxml_html.fromstring(response.text)


def list_two_letter_pages(
    client: httpx.Client,
    letter: str,
) -> list[str]:
    """Return two-letter alpha pages linked from a letter page."""
    root = _get(
        client,
        f"{ALPHA_PATH}/{letter}.html",
    )

    paths: list[str] = []
    seen: set[str] = set()

    for href in root.xpath("//a/@href"):
        path = urlparse(href).path

        if _TWO_LETTER_LINK_RE.match(path) and path not in seen:
            seen.add(path)
            paths.append(path)

    return paths


def list_drug_refs(
    client: httpx.Client,
    listing_path: str,
) -> list[DrugsComPageRef]:
    """Return drug page references from an alpha listing page."""
    root = _get(
        client,
        listing_path,
    )

    refs: list[DrugsComPageRef] = []
    seen: set[str] = set()

    for anchor in root.xpath("//a[@href]"):
        href = anchor.get("href")

        if not href:
            continue

        path = urlparse(href).path

        if href != path:
            continue

        match = _DRUG_LINK_RE.match(path)

        if not match:
            continue

        namespace = match.group("ns")
        slug_name = match.group("slug")

        if namespace is None and slug_name in _EXCLUDED_ROOT_SLUGS:
            continue

        slug = slug_name if namespace is None else f"{namespace}/{slug_name}"

        if slug in seen:
            continue

        seen.add(slug)

        name = " ".join((anchor.text_content() or slug_name).split())

        refs.append(
            DrugsComPageRef(
                slug=slug,
                name=name,
            )
        )

    return refs


def _strip_chrome_html(
    page_root: lxml_html.HtmlElement,
) -> lxml_html.HtmlElement:
    """Remove site navigation and unrelated HTML."""
    content = page_root.xpath("//*[@id='content']")

    root = content[0] if content else page_root

    for tag in (
        "header",
        "nav",
        "footer",
        "script",
        "style",
        "form",
        "aside",
        "noscript",
    ):
        for element in root.xpath(f".//{tag}"):
            parent = element.getparent()

            if parent is not None:
                parent.remove(element)

    return root


def _clean_markdown(
    content: str,
) -> tuple[str, dict[str, str]]:
    """Remove boilerplate and extract metadata."""
    metadata: dict[str, str] = {}

    reviewed = _REVIEWED_BYLINE_RE.search(content)

    if reviewed:
        metadata["reviewed_by"] = _plain_text(reviewed.group("reviewer"))
        metadata["last_updated"] = reviewed.group("updated").strip()

        content = _REVIEWED_BYLINE_RE.sub(
            "",
            content,
            count=1,
        )

    copyright_match = _COPYRIGHT_LINE_RE.search(content)

    if copyright_match:
        metadata["data_source"] = copyright_match.group("holder").strip()

        content = _COPYRIGHT_LINE_RE.sub(
            "",
            content,
            count=1,
        )

    content = _PRONUNCIATION_LINE_RE.sub(
        "",
        content,
    )

    content = "\n".join(line for line in content.splitlines() if line.strip() not in _BOILERPLATE_LINES)

    truncate_match = _TRUNCATE_FROM_HEADING_RE.search(content)

    if truncate_match:
        content = content[: truncate_match.start()]

    while True:
        drop_match = _MIDPAGE_DROP_HEADING_RE.search(content)

        if not drop_match:
            break

        next_heading = _ANY_HEADING_RE.search(
            content,
            drop_match.end(),
        )

        end = next_heading.start() if next_heading else len(content)

        content = content[: drop_match.start()] + content[end:]

    content = re.sub(
        r"\n{3,}",
        "\n\n",
        content,
    ).strip()

    return content, metadata


def build_drugs_com_article_text(
    client: httpx.Client,
    ref: DrugsComPageRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> tuple[str, str, dict[str, Any]]:
    """Scrape one page into markdown."""
    page_root = _get(
        client,
        ref.path,
    )

    title_nodes = page_root.xpath("//h1")

    title = " ".join((title_nodes[0].text_content() or ref.name).split()) if title_nodes else ref.name

    article_root = _strip_chrome_html(page_root)

    raw_content = html_to_markdown(
        lxml_html.tostring(
            article_root,
            encoding="unicode",
        ),
        link_mode=link_mode,
        base_url=BASE_URL,
    )

    if not raw_content:
        raise DrugsComFetchError(f"No readable content for {ref.slug!r}")

    content, extra_metadata = _clean_markdown(raw_content)

    if not content:
        raise DrugsComFetchError(f"No content after cleanup for {ref.slug!r}")

    namespace = ref.slug.split("/", 1)[0] if "/" in ref.slug else "consumer"

    metadata: dict[str, Any] = {
        "namespace": namespace,
        "content_length_chars": len(content),
        "license": "proprietary",
        **extra_metadata,
    }

    return content, title, metadata


def scrape_drugs_com_page(
    client: httpx.Client,
    ref: DrugsComPageRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> ScrapedDocument:
    """Scrape one Drugs.com page into a normalized document."""
    content, title, metadata = build_drugs_com_article_text(
        client,
        ref,
        link_mode=link_mode,
    )

    return ScrapedDocument(
        source="drugscom",
        external_id=ref.external_id,
        title=title,
        url=ref.page_url,
        content=content,
        section_count=max(
            content.count("\n#"),
            1,
        ),
        metadata=metadata,
    )


def _scrape_or_skip(
    client: httpx.Client,
    ref: DrugsComPageRef,
    *,
    link_mode: LinkMode,
) -> ScrapedDocument | None:
    """Scrape one page, skipping failures."""
    try:
        return scrape_drugs_com_page(
            client,
            ref,
            link_mode=link_mode,
        )
    except DrugsComFetchError:
        logger.warning(
            "Skipping Drugs.com page %r",
            ref.slug,
            exc_info=True,
        )
        return None


def scrape_drugs_com(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
) -> ScrapeRun:
    """Scrape Drugs.com documents."""
    if url is not None:

        def scrape_url() -> Iterable[ScrapedDocument]:
            with default_client() as client:
                yield scrape_drugs_com_page(
                    client,
                    drugscom_ref_from_url(url),
                    link_mode=link_mode,
                )

        return ScrapeRun(
            documents=scrape_url(),
            total=1,
        )

    letter_queue: list[str] = list(LETTERS)
    subpage_queue: list[str] = []
    exhausted = False

    def list_page(
        client: httpx.Client,
        _page: int,
    ) -> list[DrugsComPageRef]:
        nonlocal exhausted

        if exhausted:
            return []

        while True:
            while not subpage_queue:
                if not letter_queue:
                    exhausted = True
                    return []

                letter = letter_queue.pop(0)

                two_letter_pages = list_two_letter_pages(
                    client,
                    letter,
                )

                subpage_queue.extend(two_letter_pages or [f"{ALPHA_PATH}/{letter}.html"])

            listing_path = subpage_queue.pop(0)

            refs = list_drug_refs(
                client,
                listing_path,
            )

            if refs:
                return refs

    return ScrapeRun(
        total=(documents if documents is not None else APPROXIMATE_ARTICLE_COUNT),
        documents=(
            document
            for document in scrape_listing_documents(
                documents=documents,
                client_factory=default_client,
                list_page=list_page,
                scrape_item=lambda client, ref: _scrape_or_skip(
                    client,
                    ref,
                    link_mode=link_mode,
                ),
                document_delay_seconds=DOCUMENT_DELAY_SECONDS,
            )
            if document is not None
        ),
    )


__all__ = [
    "APPROXIMATE_ARTICLE_COUNT",
    "BASE_URL",
    "DOCUMENT_DELAY_SECONDS",
    "DRUGSCOM_DATASET_DISPLAY_NAME",
    "DRUGSCOM_DATASET_NAME",
    "DrugsComFetchError",
    "DrugsComPageRef",
    "build_drugs_com_article_text",
    "drugscom_ref_from_url",
    "list_drug_refs",
    "list_two_letter_pages",
    "scrape_drugs_com",
    "scrape_drugs_com_page",
]
