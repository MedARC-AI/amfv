"""Reusable HTML-to-markdown helpers for source scrapers."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from enum import StrEnum
from urllib.parse import urljoin, urlsplit, urlunsplit

from lxml import html as lxml_html
from markdownify import MarkdownConverter

_NUMERIC_CITATION_RE = re.compile(r"\[\d+\]")
_WHITESPACE_RE = re.compile(r"\s+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


class LinkMode(StrEnum):
    """How HTML links are rendered in markdown output."""

    KEEP = "keep"
    STRIP = "strip"


_MARKDOWN_CONVERTERS = {
    LinkMode.KEEP: MarkdownConverter(bullets="-", heading_style="ATX"),
    LinkMode.STRIP: MarkdownConverter(bullets="-", heading_style="ATX", strip=("a",)),
}


def clean_text(value: str, *, drop_numeric_citations: bool = True) -> str:
    """Normalize whitespace and optionally drop bracketed numeric citations.

    Args:
        value: Text to normalize.
        drop_numeric_citations: Whether to remove bracketed numeric citations
            before whitespace normalization (default: True).
    """
    text = _NUMERIC_CITATION_RE.sub("", value) if drop_numeric_citations else value
    return _WHITESPACE_RE.sub(" ", text).strip()


def absolute_unique_urls(
    urls: Iterable[str],
    *,
    base_url: str,
    allowed_schemes: Collection[str] = ("http", "https"),
    allowed_hosts: Collection[str] | None = None,
) -> list[str]:
    """Normalize URLs against a base URL and remove duplicates.

    Args:
        urls: Raw URL values to normalize.
        base_url: Base URL used for relative links.
        allowed_schemes: URL schemes that may be returned.
        allowed_hosts: Optional exact hostname allowlist. When set, URLs with
            credentials or non-default ports are rejected.
    """
    normalized_schemes = {scheme.lower() for scheme in allowed_schemes}
    normalized_hosts = {host.lower() for host in allowed_hosts} if allowed_hosts is not None else None
    seen: set[str] = set()
    normalized_urls: list[str] = []
    for raw_url in urls:
        parsed = urlsplit(urljoin(base_url, raw_url))
        scheme = parsed.scheme.lower()
        if scheme not in normalized_schemes:
            continue
        try:
            port = parsed.port
        except ValueError:
            continue
        if normalized_hosts is not None:
            default_port = 80 if scheme == "http" else 443 if scheme == "https" else None
            if (
                parsed.hostname is None
                or parsed.hostname.lower() not in normalized_hosts
                or parsed.username is not None
                or parsed.password is not None
                or port not in {None, default_port}
            ):
                continue
        url = urlunsplit(parsed._replace(query="", fragment=""))
        if url in seen:
            continue
        seen.add(url)
        normalized_urls.append(url)
    return normalized_urls


def first_matching_urls(
    html_text: str,
    *,
    xpaths: Iterable[str],
    base_url: str,
    allowed_schemes: Collection[str] = ("http", "https"),
    allowed_hosts: Collection[str] | None = None,
) -> list[str]:
    """Return normalized URLs from the first XPath with matches.

    Args:
        html_text: HTML page text to parse.
        xpaths: XPath expressions that return URL strings.
        base_url: Base URL used for relative links.
        allowed_schemes: URL schemes that may be returned.
        allowed_hosts: Optional exact hostname allowlist.
    """
    doc = lxml_html.fromstring(html_text)
    for xpath in xpaths:
        urls = doc.xpath(xpath)
        if urls:
            normalized_urls = absolute_unique_urls(
                urls,
                base_url=base_url,
                allowed_schemes=allowed_schemes,
                allowed_hosts=allowed_hosts,
            )
            if normalized_urls:
                return normalized_urls
    return []


def document_title(html_text: str, *, fallback: str, suffixes: Iterable[str] = ()) -> str:
    """Extract a document title from common HTML title locations.

    Args:
        html_text: HTML page text to parse.
        fallback: Title returned when no page title is found.
        suffixes: Title suffixes to strip from the extracted value (default: ()).
    """
    doc = lxml_html.fromstring(html_text)
    for xpath in ("//h1[1]/text()", "//title[1]/text()"):
        values = [clean_text(value) for value in doc.xpath(xpath)]
        title = next((value for value in values if value), "")
        if title:
            for suffix in suffixes:
                title = title.removesuffix(suffix)
            return title.strip() or fallback
    return fallback


def html_to_markdown(
    html_text: str,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
    base_url: str | None = None,
) -> str:
    """Convert HTML to markdown.

    Args:
        html_text: HTML fragment to convert.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        base_url: Base URL used to make kept relative links absolute (default:
            None).
    """
    source = _absolutize_links(html_text, base_url=base_url) if base_url and link_mode is LinkMode.KEEP else html_text
    markdown = _MARKDOWN_CONVERTERS[link_mode].convert(source)
    markdown = _NUMERIC_CITATION_RE.sub("", markdown)
    lines = [line.rstrip() for line in markdown.splitlines()]
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def _absolutize_links(html_text: str, *, base_url: str) -> str:
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    for link in root.xpath(".//a[@href]"):
        link.set("href", urljoin(base_url, link.get("href")))
    return "".join(lxml_html.tostring(child, encoding="unicode") for child in root)


__all__ = [
    "LinkMode",
    "absolute_unique_urls",
    "clean_text",
    "document_title",
    "first_matching_urls",
    "html_to_markdown",
]
