"""Scrape PubMed clinical practice guidelines into normalized markdown documents.

PubMed indexes roughly 43k articles tagged with the `Guideline` publication
type, but most of them expose an abstract only and stay under publisher
copyright. This module deliberately scrapes the intersection with the PMC Open
Access subset instead:

    Guideline[pt] AND pubmed pmc open access[filter] AND English[la]
        NOT "Retracted Publication"[pt]

That is about 3,000 guidelines, growing by a handful a week, and it is the only
slice where the full text is both retrievable and openly licensed.

Licensing is recorded per document rather than claimed for the source, because
the Open Access Subset is not uniformly Creative Commons licensed. Censused
over all 2,999 records present on 2026-07-29: CC BY 44.5%, CC BY-NC 22.1%,
CC BY-NC-ND 18.4%, CC BY-NC-SA 2.1%, CC0 1.3%, and 11.7% carrying publisher
terms instead. By what that permits, 45.8% is unrestricted for derivative
works, 24.1% is non-commercial only, 18.4% asserts NoDerivatives, and 11.7%
has to be read case by case.

Discovery and extraction both go through NCBI's E-utilities rather than the
rendered pubmed.ncbi.nlm.nih.gov pages. `esearch` paginates by numeric offset and
reports the result total, `esummary` resolves a whole batch of PMIDs to PMCIDs
and citation metadata in one request, and `efetch` returns JATS XML carrying the
article body, section structure and license. All three are documented, versioned
contracts that do not break when the website is restyled.

Attribution:
Meditron's guideline scrapers (epfLLM/meditron, gap-replay/guidelines) cover a
dozen sources but have no PubMed scraper, so this module has no upstream port to
follow. It follows the conventions of `nice.py` in this package instead.
Source license: Apache License 2.0.
"""

from __future__ import annotations

import copy
import logging
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from lxml import etree

from amfv_datasets.scraping.base import (
    ScrapedDocument,
    ScrapeError,
    ScrapeRun,
    default_client,
    scrape_listing_documents,
)
from amfv_datasets.scraping.html import LinkMode, clean_text, html_to_markdown

BASE_URL = "https://pubmed.ncbi.nlm.nih.gov"
EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PMC_ARTICLE_URL = "https://www.ncbi.nlm.nih.gov/pmc/articles"
# `Guideline` is a strict superset of `Practice Guideline`: searching for either
# returns exactly the `Guideline` count. The 352 extra records are clinical, not
# administrative — the 2025 Korean CPR guidelines and similar — so scoping to
# `Practice Guideline` would drop 352 records (11%) for nothing.
#
# `English[la]` drops 185 records. Most other sources in this package are
# English-only as a side effect of the URL they start from (CPS is a bilingual
# site scraped through its `/en/` routes; WHO publishes in six languages and is
# scraped through its English listing). PubMed's API returns every language, so
# the filter is explicit here to match. The excluded records are largely French
# CMAJ translations of English guidelines already in the corpus.
#
# Retracted articles keep their `Guideline` type and stay in the open-access
# subset, so nothing else here excludes them: PMID 37026270, a retracted 2023
# rosacea practice pattern, is in the result set today. A retracted guideline is
# precisely the document a fact verifier must not retrieve as evidence, so it is
# dropped at search time rather than recorded like the licence fields are. That
# is the one place this scraper decides instead of records, because there is no
# reading under which retracted guidance is valid evidence.
SEARCH_TERM = 'Guideline[pt] AND pubmed pmc open access[filter] AND English[la] NOT "Retracted Publication"[pt]'
PUBMED_DATASET_NAME = "pubmed-webscrape"
PUBMED_DATASET_DISPLAY_NAME = "PubMed Webscrape"
NCBI_TOOL = "amfv"
# NCBI documents 3 requests/second without an API key and 10 with one, so unlike
# a scraped website this delay is a published allowance rather than a guess. Do
# not raise it to match the other scrapers "for consistency" — that turns a
# 20-minute run into a 4-hour one for no benefit.
DOCUMENT_DELAY_SECONDS = 0.4
LISTING_PAGE_SIZE = 200
# Enough of a non-CC license statement to tell "unrestricted re-use" apart from
# "all rights reserved" without carrying a wall of boilerplate per document.
_LICENSE_STATEMENT_CHARS = 400
_FIGURE_CAPTION_CHARS = 300
# NCBI answers a burst with 429 and is transiently unavailable often enough that
# a multi-thousand document run needs to ride both out.
_MAX_RETRIES = 4
_RETRY_BACKOFF_SECONDS = 1.0
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

logger = logging.getLogger(__name__)

_PUBMED_PATH_RE = re.compile(r"^/(?P<pmid>\d+)/?$")
_PMC_PATH_RE = re.compile(r"^/pmc/articles/(?P<pmcid>PMC\d+)/?$", re.IGNORECASE)
_CC_URL_RE = re.compile(r"https?://creativecommons\.org/\S+?(?=[\s\"<)]|$)")
# Unwrapping `xref` and dropping figures leaves the surrounding spacing behind,
# as in "(PRISMA flow diagram in Supplementary Fig. S1) ." — close it back up.
_ORPHAN_SPACE_RE = re.compile(r" +([,.;:)\]])")
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_ALI_LICENSE_REF = "{http://www.niso.org/schemas/ali/1.0/}license_ref"

# JATS elements whose entire subtree is dropped. Reference lists and footnote
# groups are citation apparatus rather than clinical content. Figure payloads
# cannot be linked from the API response at all (see `_article_figures`), so the
# `<graphic>` goes but its `<label>` and `<caption>` stay in the text, and the
# filename is recorded in `metadata.figures` for a later pass to resolve.
_DROPPED_TAGS = (
    "ref-list",
    "fn-group",
    "supplementary-material",
    "table-wrap-foot",
    "graphic",
    "inline-graphic",
    "media",
    "object-id",
    "alt-text",
)
# Elements unwrapped to their text. `xref` is a citation/figure pointer whose
# rendered text is noise like "[12]", which `html_to_markdown` then strips.
_UNWRAPPED_TAGS = ("xref", "styled-content", "named-content")
_RENAMED_TAGS = {
    "sec": "div",
    "italic": "em",
    "bold": "strong",
    "underline": "u",
    "monospace": "code",
    "list-item": "li",
    "table-wrap": "div",
    "disp-quote": "blockquote",
    "break": "br",
    "label": "strong",
}


class PubMedFetchError(ScrapeError):
    """Raised when a guideline cannot be sourced from PubMed or PMC."""


@dataclass(frozen=True)
class PubMedArticleRef:
    """A PubMed guideline reference resolved from the search listing."""

    pmid: str = ""
    pmcid: str = ""
    title: str = ""
    journal: str = ""
    pub_date: str = ""
    doi: str = ""

    @property
    def page_url(self) -> str:
        """Canonical PubMed record URL, falling back to the PMC article URL."""
        return f"{BASE_URL}/{self.pmid}/" if self.pmid else self.pmc_url

    @property
    def pmc_url(self) -> str:
        """PMC full-text article URL."""
        return f"{PMC_ARTICLE_URL}/{self.pmcid}/"

    @property
    def slug(self) -> str:
        """Stable identifier used for `external_id`."""
        return self.pmid or self.pmcid


@dataclass(frozen=True)
class PubMedListingPage:
    """One page of PubMed guideline search results."""

    refs: list[PubMedArticleRef]
    total: int | None


def pubmed_ref_from_url(url: str) -> PubMedArticleRef:
    """Parse a PubMed or PMC article URL into a guideline reference.

    Only one of the two identifiers is known from a URL; the other is resolved
    against E-utilities when the article is scraped.

    Args:
        url: PubMed or PMC article URL to parse.
    """
    parsed = urlparse(url.strip())
    netloc = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        raise PubMedFetchError(f"Enter an http(s) PubMed or PMC URL; got {url!r}")

    if netloc in {"pubmed.ncbi.nlm.nih.gov", "www.pubmed.ncbi.nlm.nih.gov"}:
        match = _PUBMED_PATH_RE.match(parsed.path)
        if match:
            return PubMedArticleRef(pmid=match.group("pmid"))
    elif netloc in {"www.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov"}:
        match = _PMC_PATH_RE.match(parsed.path)
        if match:
            return PubMedArticleRef(pmcid=match.group("pmcid").upper())

    raise PubMedFetchError(f"Enter a URL like {BASE_URL}/42476581/ or {PMC_ARTICLE_URL}/PMC13384732/; got {url!r}")


def _policy_params() -> dict[str, str]:
    """Identify the client per NCBI's E-utilities usage policy.

    NCBI asks every request to carry a `tool` name and contact `email`, and
    raises the rate limit from 3 to 10 requests/second when an `api_key` is
    supplied. Both optional values come from the environment so that no personal
    address is committed to the repository.
    """
    params = {"tool": NCBI_TOOL}
    for name, variable in (("email", "NCBI_EMAIL"), ("api_key", "NCBI_API_KEY")):
        value = os.environ.get(variable)
        if value:
            params[name] = value
    return params


def _eutils_get(client: httpx.Client, endpoint: str, params: dict[str, Any]) -> httpx.Response:
    """Call one E-utilities endpoint, retrying when NCBI asks us to slow down.

    `DOCUMENT_DELAY_SECONDS` paces one document against the next, but a single
    document still makes up to two calls back to back, and the published 3/s
    limit is per source address rather than per process. NCBI answers a burst
    with 429. Without a retry that propagates out of `_scrape_or_skip`, which
    only swallows `PubMedFetchError`, and kills a 3,000-document run partway
    through.
    """
    url = f"{EUTILS_URL}/{endpoint}"
    merged = {**params, **_policy_params()}
    for attempt in range(_MAX_RETRIES):
        response = client.get(url, params=merged)
        if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES - 1:
            delay = _RETRY_BACKOFF_SECONDS * 2**attempt
            logger.warning("NCBI returned %s for %s; retrying in %.1fs", response.status_code, endpoint, delay)
            time.sleep(delay)
            continue
        response.raise_for_status()
        return response
    raise PubMedFetchError(f"E-utilities {endpoint} did not succeed after {_MAX_RETRIES} attempts")


def _summarize(client: httpx.Client, pmids: list[str]) -> list[PubMedArticleRef]:
    """Resolve a batch of PMIDs to references carrying PMCIDs and citation data."""
    payload = _eutils_get(
        client,
        "esummary.fcgi",
        {"db": "pubmed", "id": ",".join(pmids), "retmode": "json"},
    ).json()
    result = payload.get("result", {})

    refs: list[PubMedArticleRef] = []
    for pmid in result.get("uids", []):
        record = result.get(pmid, {})
        article_ids = {entry.get("idtype"): entry.get("value") for entry in record.get("articleids", [])}
        pmcid = article_ids.get("pmc", "")
        if not pmcid:
            # The open-access filter should guarantee a PMC copy, so a missing
            # one means the record changed between search and summary.
            logger.warning("Skipping PubMed record %s with no PMC identifier", pmid)
            continue
        refs.append(
            PubMedArticleRef(
                pmid=pmid,
                pmcid=pmcid,
                title=clean_text(record.get("title", ""), drop_numeric_citations=False),
                journal=record.get("fulljournalname", ""),
                pub_date=record.get("pubdate", ""),
                doi=article_ids.get("doi", ""),
            )
        )
    return refs


def list_pubmed_guidelines(client: httpx.Client, page: int = 1) -> PubMedListingPage:
    """List one page of open-access PubMed practice guidelines.

    `esearch` paginates by numeric offset, so the page number maps directly onto
    `retstart` and a page past the end returns an empty list rather than an
    error. That is what terminates the scrape loop.

    Args:
        client: HTTP client used to call E-utilities.
        page: 1-based listing page number (default: 1).
    """
    payload = _eutils_get(
        client,
        "esearch.fcgi",
        {
            "db": "pubmed",
            "term": SEARCH_TERM,
            "retstart": (page - 1) * LISTING_PAGE_SIZE,
            "retmax": LISTING_PAGE_SIZE,
            "retmode": "json",
        },
    ).json()
    result = payload.get("esearchresult", {})
    if "ERROR" in result:
        raise PubMedFetchError(f"PubMed search error: {result['ERROR']}")

    raw_total = str(result.get("count", ""))
    total = int(raw_total) if raw_total.isdigit() else None
    pmids = [str(pmid) for pmid in result.get("idlist", [])]
    if not pmids:
        return PubMedListingPage(refs=[], total=total)
    return PubMedListingPage(refs=_summarize(client, pmids), total=total)


def _fetch_pmc_article(client: httpx.Client, pmcid: str) -> etree._Element:
    """Fetch one PMC record and return its `<article>` element."""
    response = _eutils_get(
        client,
        "efetch.fcgi",
        {"db": "pmc", "id": pmcid.upper().removeprefix("PMC"), "retmode": "xml"},
    )
    try:
        root = etree.fromstring(response.content)
    except etree.XMLSyntaxError as error:
        raise PubMedFetchError(f"Malformed PMC XML for {pmcid}: {error}") from error

    article = root.find(".//article")
    if article is None:
        raise PubMedFetchError(f"No PMC article returned for {pmcid}")
    return article


def _resolve_ref(client: httpx.Client, ref: PubMedArticleRef) -> PubMedArticleRef:
    """Fill in a PMCID for a reference that only carries a PMID."""
    if ref.pmcid or not ref.pmid:
        return ref
    resolved = _summarize(client, [ref.pmid])
    if not resolved:
        raise PubMedFetchError(f"No PMC full text for PubMed record {ref.pmid}")
    return resolved[0]


def _first_text(node: etree._Element, tag: str) -> str:
    """Return the normalized text of the first matching descendant."""
    element = node.find(f".//{tag}")
    return clean_text("".join(element.itertext()), drop_numeric_citations=False) if element is not None else ""


def _article_license(article: etree._Element) -> dict[str, str]:
    """Extract licensing terms from the article's front matter.

    The PMC Open Access Subset is not uniformly Creative Commons licensed. 88.3%
    of the guideline slice carries a CC license, found in one of three shapes:
    an `<ali:license_ref>` element, an `<ext-link>` pointing at
    creativecommons.org, or a bare URL in the license free text. The name is
    parsed from the URL path rather than from the `license-type` attribute,
    which is unusable: the corpus spells it 18 different ways, including
    `cc-by`, `creativeCommonsBy`, `CC BY-NC-ND`, `open-access` and `openaccess`.

    The remaining 11.7% carry publisher terms that are not equivalent to each
    other: most are Elsevier's COVID-19 resource centre grant, whose copyright
    line still reads "All rights reserved"; some are the PMC Open Access
    Subset's own "unrestricted re-use" statement; and 112 have no `<license>`
    element at all, only a plain copyright line such as "(c) Springer-Verlag
    Tokyo 2007".
    Being in the subset is therefore not by itself a grant to redistribute, so
    the raw terms are recorded rather than flattened to an empty license.
    """
    # The reservation of rights sits in `<copyright-statement>`, a sibling of
    # `<license>` rather than a child, so reading only the license element loses
    # the "All rights reserved" half of a COVID-era temporary grant.
    copyright_statement = _first_text(article, "copyright-statement")

    license_element = article.find(".//license")
    if license_element is None:
        return {
            "license": "",
            "license_url": "",
            "license_type": "",
            "license_statement": "",
            "copyright_statement": copyright_statement,
        }

    url = ""
    license_ref = license_element.find(f".//{_ALI_LICENSE_REF}")
    if license_ref is not None and license_ref.text:
        url = license_ref.text.strip()
    if not url:
        for element in license_element.iter():
            href = element.get(_XLINK_HREF) or ""
            if "creativecommons.org" in href:
                url = href.strip()
                break
    if not url:
        match = _CC_URL_RE.search("".join(license_element.itertext()))
        url = match.group(0) if match else ""

    name = ""
    match = re.search(r"creativecommons\.org/(?:licenses|publicdomain)/([a-z0-9-]+)/", url)
    if match:
        token = match.group(1)
        name = "CC0" if token == "zero" else f"CC {token.upper()}"

    statement = clean_text("".join(license_element.itertext()), drop_numeric_citations=False)
    return {
        "license": name,
        "license_url": url,
        "license_type": license_element.get("license-type", ""),
        "license_statement": statement[:_LICENSE_STATEMENT_CHARS],
        "copyright_statement": copyright_statement,
    }


def _article_figures(article: etree._Element) -> list[dict[str, str]]:
    """Record each figure's source filename, label and caption.

    The image itself cannot be linked from here. JATS carries only a bare
    filename (`keag320f1.jpg`), while the served URL inserts a CDN shard and a
    content hash — `.../pmc/blobs/1ab4/13384732/bd9a2d61ca1a/keag320f1.jpg` —
    that appear nowhere in the API response. Building a URL from the filename
    alone yields a 404, which is worse than no link because nothing downstream
    notices it is broken.

    Other scrapers here keep images as absolute remote references (see the
    `<img>` handling added in #9 and #10), which works because their sources
    serve a real path. Recording the filename keeps that door open: a later
    pass can resolve these against the rendered page or the OA package without
    re-scraping the corpus.

    Scans the whole article rather than just the body: a graphical abstract is
    declared in `<front>`, so a body-only scan silently misses it.

    Args:
        article: The parsed `<article>` element, before tag rewriting.
    """
    figures: list[dict[str, str]] = []
    for figure in article.iter("fig"):
        graphic = figure.find(".//graphic")
        href = graphic.get(_XLINK_HREF) if graphic is not None else None
        if not href:
            continue
        figures.append(
            {
                "id": figure.get("id", ""),
                "file": href,
                "label": _first_text(figure, "label"),
                "caption": _first_text(figure, "caption")[:_FIGURE_CAPTION_CHARS],
            }
        )
    return figures


def _article_supplements(article: etree._Element) -> list[dict[str, str]]:
    """Record supplementary file names, labels and captions.

    Same situation as `_article_figures`, and measured: across 40 sampled
    guidelines every supplementary block was a pointer to an external file
    rather than inline content, totalling 0.18% of body text. The evidence
    tables a verifier would want live inside those `.docx` and `.tif` payloads,
    which the API does not return, so the pointer is the only thing worth
    keeping.

    Args:
        article: The parsed `<article>` element, before tag rewriting.
    """
    supplements: list[dict[str, str]] = []
    for supplement in article.iter("supplementary-material"):
        href = supplement.get(_XLINK_HREF)
        if not href:
            media = supplement.find(f".//*[@{_XLINK_HREF}]")
            href = media.get(_XLINK_HREF) if media is not None else None
        if not href:
            continue
        supplements.append(
            {
                "id": supplement.get("id", ""),
                "file": href,
                "label": _first_text(supplement, "label"),
                "caption": _first_text(supplement, "caption")[:_FIGURE_CAPTION_CHARS],
            }
        )
    return supplements


def _heading_depth(section: etree._Element) -> int:
    """Return how deeply a `<sec>` is nested inside the article body."""
    depth = 1
    parent = section.getparent()
    while parent is not None:
        if parent.tag == "sec":
            depth += 1
        parent = parent.getparent()
    return depth


def _jats_to_html(source: etree._Element) -> str:
    """Rewrite a JATS subtree into HTML that `html_to_markdown` understands.

    JATS is close enough to HTML that renaming tags is cheaper and less
    error-prone than writing a second markdown serializer: `<table-wrap>`
    already contains genuine XHTML tables, and inline markup maps one-to-one.
    """
    root = copy.deepcopy(source)
    etree.strip_elements(root, *_DROPPED_TAGS, with_tail=False)
    etree.strip_tags(root, *_UNWRAPPED_TAGS)

    # An `<abstract>` labels itself "Abstract", which would duplicate the heading
    # this module already writes above it.
    if root.tag == "abstract":
        title = root.find("title")
        if title is not None:
            root.remove(title)

    # Headings depend on `<sec>` nesting, so resolve them before `sec` is renamed.
    for section in root.iter("sec"):
        title = section.find("title")
        if title is not None:
            title.tag = f"h{min(_heading_depth(section) + 1, 6)}"
    for title in root.iter("title"):
        title.tag = "strong"

    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if element.tag == "list":
            element.tag = "ol" if element.get("list-type") == "order" else "ul"
        elif element.tag == "ext-link":
            href = element.get(_XLINK_HREF)
            element.tag = "a"
            if href:
                element.set("href", href)
        else:
            element.tag = _RENAMED_TAGS.get(element.tag, element.tag)
        for name in list(element.attrib):
            if name.startswith("{") or name in {"list-type", "content-type", "position"}:
                del element.attrib[name]

    return etree.tostring(root, encoding="unicode", method="html")


def _jats_to_markdown(source: etree._Element, *, link_mode: LinkMode) -> str:
    """Convert a JATS subtree to markdown and close up gaps left by dropped nodes.

    The spacing pass has to run last: `html_to_markdown` is what removes the
    "[12]" text of an unwrapped `xref`, so the orphaned space only appears once
    the markdown exists.
    """
    markdown = html_to_markdown(_jats_to_html(source), link_mode=link_mode)
    return _ORPHAN_SPACE_RE.sub(r"\1", markdown)


def build_pubmed_article_text(
    client: httpx.Client,
    ref: PubMedArticleRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> tuple[str, int, str, dict[str, Any]]:
    """Scrape one guideline's full text into markdown, section count, title and metadata.

    Args:
        client: HTTP client used to call E-utilities.
        ref: PubMed guideline reference to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    ref = _resolve_ref(client, ref)
    article = _fetch_pmc_article(client, ref.pmcid)

    body = article.find(".//body")
    if body is None:
        # Roughly 2% of the subset is deposited as metadata only. That is a
        # property of the record, not a fetch failure.
        raise PubMedFetchError(f"No full text body in PMC record {ref.pmcid}")

    sections: list[str] = []
    abstract = article.find(".//abstract")
    if abstract is not None:
        abstract_markdown = _jats_to_markdown(abstract, link_mode=link_mode)
        if abstract_markdown:
            sections.append(f"## Abstract\n\n{abstract_markdown}")

    body_markdown = _jats_to_markdown(body, link_mode=link_mode)
    if body_markdown:
        sections.append(body_markdown)

    content = "\n\n".join(sections).strip()
    if not content:
        raise PubMedFetchError(f"No readable content in PMC record {ref.pmcid}")

    title = ref.title or _first_text(article, "article-title") or ref.slug
    section_count = max(len(body.findall("sec")), 1) + (1 if abstract is not None else 0)
    metadata = {
        "pmid": ref.pmid or _pmid_from_article(article),
        "pmcid": ref.pmcid,
        "doi": ref.doi or _article_id(article, "doi"),
        "journal": ref.journal or _first_text(article, "journal-title"),
        "pub_date": ref.pub_date,
        "article_type": article.get("article-type", ""),
        **_article_license(article),
        "figures": _article_figures(article),
        "supplements": _article_supplements(article),
        "content_length_chars": len(content),
    }
    return content, section_count, title, metadata


def _article_id(article: etree._Element, id_type: str) -> str:
    """Return an `<article-id>` value of the requested type."""
    for element in article.iter("article-id"):
        if element.get("pub-id-type") == id_type:
            return "".join(element.itertext()).strip()
    return ""


def _pmid_from_article(article: etree._Element) -> str:
    return _article_id(article, "pmid")


def scrape_pubmed_article(
    client: httpx.Client,
    ref: PubMedArticleRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> ScrapedDocument:
    """Scrape a PubMed guideline into a normalized document.

    Args:
        client: HTTP client used to call E-utilities.
        ref: PubMed guideline reference to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    content, section_count, title, metadata = build_pubmed_article_text(client, ref, link_mode=link_mode)
    # Prefer the PMID from the record itself so one article gets one identifier
    # whether it was reached from the listing or from a PMC URL, which carries
    # no PMID of its own.
    slug = metadata["pmid"] or ref.slug or metadata["pmcid"]
    return ScrapedDocument(
        source="pubmed",
        external_id=f"pubmed-{slug}",
        title=title,
        url=ref.page_url,
        content=content,
        section_count=section_count,
        metadata=metadata,
    )


def _scrape_or_skip(
    client: httpx.Client,
    ref: PubMedArticleRef,
    *,
    link_mode: LinkMode,
) -> ScrapedDocument | None:
    """Scrape one guideline, skipping records PMC holds without full text.

    About 2% of the open-access subset is deposited as metadata only, so a run
    over the whole corpus should not die when it reaches one.

    Args:
        client: HTTP client used to call E-utilities.
        ref: PubMed guideline reference to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text.
    """
    try:
        return scrape_pubmed_article(client, ref, link_mode=link_mode)
    except PubMedFetchError:
        logger.warning("Skipping PubMed guideline %r", ref.slug, exc_info=True)
        return None


def scrape_pubmed(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
) -> ScrapeRun:
    """Scrape PubMed guidelines from a URL or the open-access search listing.

    Args:
        documents: Number of documents to scrape. Ignored when `url` is set.
            When unset, listing pages are fetched until a page returns no items
            (default: None).
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        url: PubMed or PMC article URL to scrape as a single document
            (default: None).
    """
    if url is not None:

        def scrape_url() -> Iterable[ScrapedDocument]:
            with default_client() as client:
                yield scrape_pubmed_article(client, pubmed_ref_from_url(url), link_mode=link_mode)

        return ScrapeRun(documents=scrape_url(), total=1)

    with default_client() as client:
        first_page = list_pubmed_guidelines(client, page=1)
    total = first_page.total if documents is None or first_page.total is None else min(documents, first_page.total)
    return ScrapeRun(
        total=total,
        documents=scrape_listing_documents(
            documents=documents,
            client_factory=default_client,
            first_page_items=first_page.refs,
            list_page=lambda client, page: list_pubmed_guidelines(client, page).refs,
            scrape_item=lambda client, ref: _scrape_or_skip(client, ref, link_mode=link_mode),
            document_delay_seconds=DOCUMENT_DELAY_SECONDS,
        ),
    )


__all__ = [
    "BASE_URL",
    "DOCUMENT_DELAY_SECONDS",
    "EUTILS_URL",
    "PMC_ARTICLE_URL",
    "PUBMED_DATASET_DISPLAY_NAME",
    "PUBMED_DATASET_NAME",
    "SEARCH_TERM",
    "PubMedArticleRef",
    "PubMedFetchError",
    "PubMedListingPage",
    "build_pubmed_article_text",
    "list_pubmed_guidelines",
    "pubmed_ref_from_url",
    "scrape_pubmed",
    "scrape_pubmed_article",
]
