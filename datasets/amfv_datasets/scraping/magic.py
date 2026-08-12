"""Scrape MAGICapp guidelines into normalized markdown documents.

MAGICapp (https://app.magicapp.org) is a publishing platform for clinical
practice guidelines written with the GRADE methodology, so one source covers
many publishing organizations. Unlike the other Meditron guideline sources, it
exposes a public, unauthenticated JSON API: the catalogue at
``/api/v1/guidelines`` returns every published guideline in one response, and
each entry carries a ``jsonPath`` pointing at the full guideline as structured
JSON, with nested sections and first-class recommendation objects.

Laid out as the pipeline runs:

* Catalogue access and document selection - refs, languages, placeholder and draft filters
* HTML repair, before conversion - track changes, emphasis surgery, citations, tables, scripts
* Conversion and markdown cleanup passes - ``_markdown`` and the paperwork/pointer drops
* Section keep/drop policy - the skip lists and the content guards behind them
* Recommendation and PICO rendering - labels, strengths, certainty wording, outcome estimates
* Document assembly - the section walk, moved PICOs, per-guideline blocks, deduplication
* Scrape API - ``scrape_magic`` and the per-guideline entry points

Attribution:
Meditron's guideline collection lists MAGIC as a source and ships a scraper for
it (epfLLM/meditron, gap-replay/guidelines/scrapers/scrapers.py, Apache License
2.0). That implementation drives the single-page application with Selenium and
is marked ``UNTESTED`` in the source; this module replaces the approach with
direct API access rather than porting it.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
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
from amfv_datasets.scraping.html import LinkMode, html_to_markdown
from amfv_datasets.scraping.magic_rules import (
    _GUIDELINE_DROP_BLOCKS,
    _GUIDELINE_INLINE_SKIP_HEADINGS,
    _GUIDELINE_KEEP_HEADINGS,
    _GUIDELINE_PAPERWORK_DROP_BLOCKS,
    _GUIDELINE_SKIP_HEADINGS,
    _INSTITUTION_SKIP_HEADINGS,
)

BASE_URL = "https://app.magicapp.org"
API_BASE_URL = "https://api.magicapp.org"
MAGIC_DATASET_NAME = "magic-webscrape"
MAGIC_DATASET_DISPLAY_NAME = "MAGICapp Webscrape"
DOCUMENT_DELAY_SECONDS = 5.0

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Catalogue access and document selection
# ---------------------------------------------------------------------------

# The endpoint ignores paging parameters; only `limit` narrows the response.
_CATALOGUE_LIMIT = 5000

DEFAULT_LANGUAGES = ("en",)

# Verified as all-training; title matching is unsafe ("test" appears inside real titles).
_TRAINING_INSTITUTIONS = frozenset(
    {
        "gela workshop malawi",
        "ges 2024 workshop guidelines",
        "magicapp tutorials",
        "magicapp workshops mapp",
    }
)

# Below this a guideline is a placeholder, not guidance. Applied to catalogue runs only.
MIN_CONTENT_CHARS = 400

# Keyed on the publisher's own words; matches exactly one document in the catalogue.
_DEMONSTRATION_RE = re.compile(
    r"demonstration guideline|not intended for (?:the )?(?:actual )?treatment of patients",
    re.IGNORECASE,
)

_DRAFT_TITLE_RE = re.compile(r"\bdrafts?\b", re.IGNORECASE)

_SHORT_CODE_RE = re.compile(r"^[A-Za-z0-9]{4,12}$")

_GUIDELINE_PATH_RE = re.compile(r"/guideline/(?P<short_code>[A-Za-z0-9]{4,12})")


class MagicFetchError(ScrapeError):
    """Raised when a MAGICapp catalogue or guideline document cannot be read."""


@dataclass(frozen=True)
class GuidelineRef:
    """A published MAGICapp guideline listed in the catalogue."""

    short_code: str
    guideline_id: int
    title: str
    json_path: str
    published_id: int = 0
    language: str = ""
    institution: str = ""
    publish_date: str = ""
    recommendation_count: int = 0
    disclaimer: str = ""
    status: str = ""
    description: str = ""
    last_search_date: str = ""

    @property
    def is_archived(self) -> bool:
        """Report whether the publisher has marked this guideline archived.

        The catalogue `description` is the only field that says so; `status`, `isLatestPublished`
        and `publishDate` are all useless here.
        """
        return "archiv" in self.description.lower()

    @property
    def is_draft(self) -> bool:
        """Report whether the publisher's own title marks this guideline a draft."""
        return _DRAFT_TITLE_RE.search(self.title) is not None

    @property
    def page_url(self) -> str:
        """Return the human-readable MAGICapp URL for this guideline."""
        return f"{BASE_URL}/#/guideline/{self.short_code}"


@dataclass(frozen=True)
class GuidelineListingPage:
    """One page of catalogue results.

    MAGICapp returns the whole catalogue in a single response, so only page 1
    is ever populated; later pages are empty and stop the listing loop.
    """

    refs: list[GuidelineRef]
    total: int | None = None


def _force_https(url: str) -> str:
    """Upgrade a catalogue content URL to HTTPS; the file host answers plain HTTP with 403."""
    return f"https://{url[len('http://') :]}" if url.startswith("http://") else url


def _catalogue_url() -> str:
    return f"{API_BASE_URL}/api/v1/guidelines?limit={_CATALOGUE_LIMIT}"


def _guideline_json_url(short_code: str) -> str:
    return f"{BASE_URL}/#/guideline/{short_code}"


def magic_ref_from_url(client: httpx.Client, url: str) -> GuidelineRef:
    """Resolve a MAGICapp guideline URL to its catalogue entry.

    Args:
        client: HTTP client used to fetch the catalogue.
        url: MAGICapp guideline URL, for example
            https://app.magicapp.org/#/guideline/nyxpZL.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise MagicFetchError(f"Enter a URL like {_guideline_json_url('nyxpZL')}; got {url!r}")
    if parsed.hostname not in {"app.magicapp.org", "magicapp.org"}:
        raise MagicFetchError(f"Enter a URL like {_guideline_json_url('nyxpZL')}; got {url!r}")

    # MAGICapp uses hash routing, so the short code lives in the fragment.
    match = _GUIDELINE_PATH_RE.search(parsed.fragment or parsed.path)
    if match is None:
        raise MagicFetchError(f"Enter a URL like {_guideline_json_url('nyxpZL')}; got {url!r}")

    short_code = match.group("short_code")
    # No language filter here: an explicit URL is an explicit request.
    for ref in list_published_guidelines(client, languages=None).refs:
        if ref.short_code == short_code:
            return ref
    raise MagicFetchError(f"No published MAGICapp guideline found for short code '{short_code}'")


def _catalogue_int(value: Any) -> int:
    """Coerce a numeric catalogue field, treating junk as 0 rather than aborting the run."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_catalogue(payload: Any, *, languages: tuple[str, ...] | None) -> GuidelineListingPage:
    """Build listing refs from a catalogue response; `languages=None` keeps every language."""
    if not isinstance(payload, list):
        raise MagicFetchError("Could not parse MAGICapp catalogue: expected a JSON array")

    refs: list[GuidelineRef] = []
    skipped_languages: Counter[str] = Counter()
    skipped_training = 0
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        short_code = str(entry.get("shortCode") or "")
        json_path = _force_https(str(entry.get("jsonPath") or ""))
        language = str(entry.get("language") or "")
        institution = str(entry.get("institutionName") or "").strip()
        if not _SHORT_CODE_RE.match(short_code) or not json_path:
            logger.debug("Skipping catalogue entry without a short code or content path: %r", entry.get("name"))
            continue
        if institution.lower() in _TRAINING_INSTITUTIONS:
            skipped_training += 1
            continue
        if languages is not None and not language.lower().startswith(languages):
            skipped_languages[language or "unknown"] += 1
            continue
        refs.append(
            GuidelineRef(
                short_code=short_code,
                guideline_id=_catalogue_int(entry.get("guidelineId")),
                title=str(entry.get("name") or short_code).strip(),
                json_path=json_path,
                published_id=_catalogue_int(entry.get("publishedId")),
                language=language,
                institution=institution,
                publish_date=str(entry.get("publishDate") or ""),
                recommendation_count=_catalogue_int(entry.get("publishedRecommendationCount")),
                disclaimer=str(entry.get("disclaimer") or "").strip(),
                status=str(entry.get("status") or "").strip(),
                description=str(entry.get("description") or "").strip(),
                last_search_date=str(entry.get("publishLastSearchDate") or "")[:10],
            )
        )
    if skipped_training:
        logger.info("Skipped %d guideline(s) from tutorial and workshop organisations", skipped_training)
    if skipped_languages:
        logger.info(
            "Kept %d guideline(s); skipped %d in other languages (%s)",
            len(refs),
            sum(skipped_languages.values()),
            ", ".join(f"{lang}={count}" for lang, count in sorted(skipped_languages.items())),
        )
    return GuidelineListingPage(refs=refs, total=len(refs))


def list_published_guidelines(
    client: httpx.Client,
    page: int = 1,
    *,
    languages: tuple[str, ...] | None = DEFAULT_LANGUAGES,
) -> GuidelineListingPage:
    """Return published guideline refs from the MAGICapp catalogue.

    Args:
        client: HTTP client used to fetch the catalogue.
        page: Listing page number. MAGICapp returns the whole catalogue at once,
            so any page after the first is empty (default: 1).
        languages: Language prefixes to keep. Pass None to collect every
            language (default: DEFAULT_LANGUAGES).
    """
    if page > 1:
        return GuidelineListingPage(refs=[], total=None)
    response = client.get(_catalogue_url())
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
        raise MagicFetchError("Could not parse MAGICapp catalogue: response was not JSON") from error
    return _parse_catalogue(payload, languages=languages)


# ---------------------------------------------------------------------------
# HTML repair, before conversion
# ---------------------------------------------------------------------------

# CKEditor track-changes marker for text an editor has struck out.
_DELETION_CLASS = "ck-suggestion-marker-deletion"

# html.py strips `[3]` markers only after conversion, too late to join the text; remove the element
# first.
_CITATION_CLASS = "magic-cite"

# Stripped downstream, this leaves an empty emphasis pair - a stray `**`.
_MANUAL_CITATION_RE = re.compile(r"\A(?:\[\d+\])+\Z")

_CITATION_TAGS = frozenset({"i", "em", "b", "strong", "sup", "span"})

# The wrapper goes too, climbed only while it holds nothing but the marker.
_CITATION_WRAPPER_TAGS = _CITATION_TAGS | {"a"}

# Opening brackets absent on purpose: nothing should be inserted after "(".
_CITATION_SEPARATORS = frozenset({",", ";", "-", "\u2013", "\u2014"})

# Non-greedy so it cannot run across two tags; the label is captured so the digits survive.
_NUMERIC_LINK_RE = re.compile(r"<a\b[^>]*>(\s*\d+\s*)</a>", re.IGNORECASE)

_CITATION_CLAUSE_ENDINGS = frozenset(".,;:)]}%\u2019\u201d")

_CITATION_EDGE_WHITESPACE = " \t\xa0"

# Sentence punctuation, not citation punctuation - no space may survive in front of it.
_CITATION_TRAILING_PUNCTUATION = ".,;:)]}!?"

# Separators whose items were all reference markers. At least one required, so empty cells are
# untouched.
_ORPHANED_SEPARATORS_RE = re.compile(r"[\s\xa0]*[,;/&·•][\s\xa0,;/&·•]*")

# Labels that can be nothing but paperwork - deliberately not the section skip list. See
# `_drop_inline_paperwork`.
_INLINE_PAPERWORK_LABELS = frozenset(
    {
        "about these guidelines",
        "about this guideline",
        "about this living guideline",
        "conflict of interest register",
        "conflicts of interest",
        "contributors",
        "authors",
        "author",
        "guideline panel",
        "panel members",
        "panel member",
        "copyright",
        "declaration of interests",
        "declaration of interests by external contributors",
        "disclaimer",
        "dissemination",
        "feedback",
        "link to who prequalification",
        "updating evidence-based guidance",
        "who guidelines, recommendations and good practice statements",
        "external review group",
        "funding",
        "guideline development group",
        "guideline development group (gdg)",
        "guideline development team",
        "guideline monitoring and auditing",
        "how to cite",
        "how to cite this guideline",
        "observers",
        "public consultation and feedback",
        "publication approval",
        "references",
        "updating",
        "who steering group",
    }
)

# Must stay in step with `_INLINE_PAPERWORK_LABELS` - an unreachable label is silently dead; a test
# asserts reachability.
_INLINE_PAPERWORK_HINT_RE = re.compile(
    r"authorship|participated in the development|about th[ie]s|copyright|how to cite|disclaimer"
    r"|funding|steering group|external review group|observers|publication approval"
    r"|public consultation|guideline development (group|team)|conflicts? of interest"
    r"|declaration of interests|monitoring and auditing|updating"
    r"|dissemination|feedback|prequalification|recommendations and good practice|references"
    r"|contributors|authors?|guideline panel|panel members?",
    re.IGNORECASE,
)

_AUTHORSHIP_MARKER_RE = re.compile(
    r"\A(authorship\b|the following[^.]{0,80}(participated|contributed|were involved))",
    re.IGNORECASE,
)

# markdownify drops `<sup>`, welding `10<sup>9</sup>/L` into "109/L"; Unicode superscripts instead,
# caret fallback.
_SUPERSCRIPTS = str.maketrans(
    "0123456789+-=()abdeghijklmnoprstuvwxyz",
    "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ᵃᵇᵈᵉᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ",
)

_SUBSCRIPTS = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")

_SCRIPT_TAGS = {"sup": _SUPERSCRIPTS, "sub": _SUBSCRIPTS}

# img is void, so the tag runs to its own `>`; DOTALL for alt texts with newlines.
_IMG_OPEN_RE = re.compile(r"<img", re.IGNORECASE)

_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.DOTALL)

_SCRIPT_PASSTHROUGH = frozenset({",", "."})

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

# Guard on publisher typos like `colspan="200"`.
_MAX_TABLE_SPAN = 40

_EMPHASIS_TAGS = frozenset({"strong", "b", "u"})

# `u` absent: markdown has no underline, so it never emits an unpairable marker.
_SPLITTABLE_EMPHASIS = frozenset({"strong", "b", "em", "i"})

# Bold-italic is two layers; three would be unusual and four has not been seen.
_MAX_EMPHASIS_SPLIT_PASSES = 4

_TABLE_TAGS = frozenset({"table", "figure"})

# A heading is a label, not a sentence; a bolded sentence is emphasis.
_MAX_INLINE_HEADING_CHARS = 80

# Longer than a heading (labels run to full sentences) but bounded, or any paragraph mentioning
# authorship matches.
_MAX_AUTHORSHIP_LABEL_CHARS = 160

# MUST stay a character class: as an alternation `\s` also matches `\xa0`, giving a run of n
# non-breaking spaces 2**n parses - one real guideline never returned.
_BLANK_UNIT = r"(?:[\s\xa0]|&nbsp;)"

# U+200B is a format character, not whitespace - `.strip()` keeps it. Deleted, and deliberately NOT
# folded into `_BLANK_UNIT`: moving one outside an emphasis run breaks the run.
_ZERO_WIDTH_CHAR_RE = re.compile("[\u200b\u200c\u200d\ufeff\u2060]")

_ZERO_WIDTH_INLINE_TAG_RE = re.compile(
    r"<(strong|em|span|b|i|u|sup|sub)\b[^>]*>​++</\1>",
    re.IGNORECASE,
)

# Conversion discards these, welding the words either side together, so replaced by a space first.
# Possessive: the required closing `<` is nothing `_BLANK_UNIT` matches, so backtracking cannot
# help.
_BLANK_INLINE_TAG_RE = re.compile(
    rf"<(strong|em|span|b|i|u|sup|sub)\b[^>]*>{_BLANK_UNIT}*+</\1>",
    re.IGNORECASE,
)

# Moves the space outside the tag first. Bounded chunks: an unbounded `+` was quadratic and hung two
# guidelines.
_SPACE_AFTER_OPEN_RE = re.compile(rf"(<(?:strong|em|b|i|u)\b[^>]*>)({_BLANK_UNIT}{{1,16}})", re.IGNORECASE)

_SPACE_BEFORE_CLOSE_RE = re.compile(rf"({_BLANK_UNIT}{{1,16}})(</(?:strong|em|b|i|u)>)", re.IGNORECASE)

# Edge punctuation moves out (CommonMark refuses it beside a word character). Square brackets stay:
# `[*299*]` would re-expose citation numbers.
_TRIMMABLE_EMPHASIS = frozenset({"strong", "em", "b", "i", "u"})

_EMPHASIS_EDGE = re.compile(r"[^\w\s\[\]]", re.UNICODE)

# Colour spans hide emphasis neighbours from each other; read through, never unwrapped.
_STYLING_WRAPPERS = frozenset({"span", "font"})

# Runs merge only when the whole emphasis stack matches - underlined never merges into plain.
_EMPHASIS_SEMANTICS = {"strong": "strong", "b": "strong", "em": "em", "i": "em", "u": "u"}

_MAX_EMPHASIS_MERGE_PASSES = 64


def _drop_tracked_deletions(html_text: str) -> str:
    """Remove text an editor has marked for deletion."""
    if _DELETION_CLASS not in html_text:
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    for element in root.find_class(_DELETION_CLASS):
        parent = element.getparent()
        if parent is None:
            continue
        # The tail still belongs to the sentence; reattach before removing.
        if element.tail:
            previous = element.getprevious()
            if previous is not None:
                previous.tail = (previous.tail or "") + element.tail
            else:
                parent.text = (parent.text or "") + element.tail
        parent.remove(element)
    # fragment_fromstring hangs leading text on the parent; serializing only children drops it.
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _split_emphasis_across_breaks(html_text: str) -> str:
    """Close and reopen emphasis at every line break it spans."""
    if "<br" not in html_text or not any(f"<{tag}" in html_text for tag in _SPLITTABLE_EMPHASIS):
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    # Repeated: nesting unlocks one layer at a time - `<em>` holds no `<br>` until `<strong>`
    # splits.
    for _pass in range(_MAX_EMPHASIS_SPLIT_PASSES):
        if not _split_emphasis_once(root):
            break
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _split_emphasis_once(root: Any) -> bool:
    """Split every emphasis element holding a direct line break. Reports whether any did."""
    split_any = False
    for element in list(root.iter()):
        tag = element.tag if isinstance(element.tag, str) else ""
        if tag not in _SPLITTABLE_EMPHASIS or element.find("br") is None:
            continue
        parent = element.getparent()
        if parent is None:
            continue
        # Rebuilt as emphasis, break, emphasis - each line's share carries its own tags.
        segments: list[list[Any]] = [[]]
        pending_text = element.text or ""
        for child in list(element):
            if isinstance(child.tag, str) and child.tag == "br":
                segments.append([])
                if child.tail:
                    segments[-1].append(child.tail)
                continue
            segments[-1].append(child)
        if pending_text:
            segments[0].insert(0, pending_text)
        if len(segments) < 2:
            continue
        index = parent.index(element)
        parent.remove(element)
        # Inserts go in reversed, so the FIRST inserted is LAST in document order and owns the tail.
        tail_target = None
        for offset, segment in enumerate(reversed(segments)):
            if offset:
                separator = lxml_html.Element("br")
                parent.insert(index, separator)
                tail_target = tail_target if tail_target is not None else separator
            wrapper = lxml_html.Element(tag)
            for piece in segment:
                if isinstance(piece, str):
                    wrapper.text = (wrapper.text or "") + piece
                else:
                    wrapper.append(piece)
            if wrapper.text or len(wrapper):
                parent.insert(index, wrapper)
                tail_target = tail_target if tail_target is not None else wrapper
        if element.tail and tail_target is not None:
            tail_target.tail = element.tail
        split_any = True
    return split_any


def _drop_orphaned_captions(html_text: str) -> str:
    """Remove a caption whose only job was to name a picture that is not kept."""
    if "<figure" not in html_text:
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    doomed = []
    for element in root.iter("p"):
        following = element.getnext()
        if following is None or following.tag != "figure":
            continue
        if "image" not in (following.get("class") or "").split():
            continue
        # The whole line must be emphasised, the way a caption is.
        text = _label_text(element)
        marked = "".join(
            part.text_content() or ""
            for part in element.iter()
            if isinstance(part.tag, str) and part.tag in _EMPHASIS_TAGS
        )
        if text and len(" ".join(marked.split())) >= len(text) - 2:
            doomed.append(element)
    for element in doomed:
        element.getparent().remove(element)
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _render_super_subscripts(html_text: str) -> str:
    """Keep superscripts and subscripts as characters, since markdown has no markup for them."""
    if "<sup" not in html_text and "<sub" not in html_text:
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    for element in list(root.iter()):
        table = _SCRIPT_TAGS.get(element.tag if isinstance(element.tag, str) else "")
        if table is None or len(element):
            continue
        text = element.text or ""
        if not text.strip():
            continue
        core = text.strip()
        # Capitals have no superscript characters, so the TM pair maps to the real sign.
        if element.tag == "sup" and core == "TM":
            leading = text[: len(text) - len(text.lstrip())]
            trailing = text[len(text.rstrip()) :]
            element.text = f"{leading}™{trailing}"
            element.tag = "span"
            continue
        converted = text.translate(table)
        # All or nothing: "¹**" is worse than "^1**". Separators excepted so `<sub>2,3</sub>` reads
        # "₂,₃".
        fully = any(character.translate(table) != character for character in core) and all(
            character.translate(table) != character or character in _SCRIPT_PASSTHROUGH for character in core
        )
        if fully:
            element.text = converted
        else:
            # In a link, `[^` starts a footnote reference - a superscript that is the whole link
            # text gets no caret.
            link = next((ancestor for ancestor in element.iterancestors() if ancestor.tag == "a"), None)
            inside_link = link is not None and link.text_content().strip() == text.strip()
            # Edge whitespace lived inside the tag; stripping it welds words together.
            leading = text[: len(text) - len(text.lstrip())]
            trailing = text[len(text.rstrip()) :]
            core = text.strip()
            element.text = f"{leading}{core}{trailing}" if inside_link else f"{leading}^{core}{trailing}"
        element.tag = "span"
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _rejoin_across_citation(before: str, after: str, lookahead: str) -> str:
    """Rejoin the text either side of a removed citation marker.

    `lookahead` is the first character a reader meets after the citation, even when it sits in a
    sibling element rather than the tail.
    """
    if not lookahead:
        return before + after
    if lookahead in _CITATION_TRAILING_PUNCTUATION:
        # The separating space goes with the citation; the punctuation ends the sentence.
        return before.rstrip(_CITATION_EDGE_WHITESPACE) + after
    if before[-1:] in _CITATION_EDGE_WHITESPACE and lookahead in _CITATION_EDGE_WHITESPACE:
        # Spaces on both sides would otherwise become a double space.
        return before.rstrip(_CITATION_EDGE_WHITESPACE) + " " + after.lstrip(_CITATION_EDGE_WHITESPACE)
    if (before[-1:].isalnum() or before[-1:] in _CITATION_CLAUSE_ENDINGS) and lookahead.isalnum():
        # The marker leaves a space behind, else the words weld. Clause endings count; opening
        # brackets do not.
        return before + " " + after
    return before + after


def _leading_text(element: Any) -> str:
    """Return the first characters a reader would see from an element, or ""."""
    if element is None or not isinstance(element.tag, str):
        return ""
    return (element.text_content() or "")[:1]


def _is_manual_citation(element: Any) -> bool:
    """Report whether an element is a bracketed reference number the publisher formatted by hand."""
    return (
        isinstance(element.tag, str)
        and element.tag in _CITATION_TAGS
        and len(element) == 0
        and bool(_MANUAL_CITATION_RE.match((element.text or "").strip()))
    )


def _citation_root(element: Any) -> Any:
    """Return the outermost element that holds nothing but this citation marker."""
    node = element
    while True:
        parent = node.getparent()
        if (
            parent is None
            or parent.tag not in _CITATION_WRAPPER_TAGS
            or len(parent) != 1
            or (parent.text or "").strip()
            or (node.tail or "").strip()
        ):
            return node
        node = parent


def _tag_name(element: Any) -> str:
    """The element's tag name, or "" for comments and processing instructions."""
    return element.tag if isinstance(element.tag, str) else ""


def _is_styling_wrapper(element: Any) -> bool:
    """Report whether an element is a span or font that conversion emits nothing for.

    A citation carrier is never transparent: `_drop_citations` runs later and keys on those
    elements.
    """
    if _tag_name(element) not in _STYLING_WRAPPERS:
        return False
    if _CITATION_CLASS in (element.get("class") or ""):
        return False
    content = (element.text_content() or "").strip()
    return not (content and _MANUAL_CITATION_RE.fullmatch(content))


def _emphasis_at_end(element: Any) -> Any | None:
    """The emphasis element whose closing marker would land against whatever follows."""
    if _tag_name(element) in _TRIMMABLE_EMPHASIS:
        return element
    if not _is_styling_wrapper(element) or len(element) == 0 or (element[-1].tail or "").strip():
        return None
    return _emphasis_at_end(element[-1])


def _emphasis_at_start(element: Any) -> Any | None:
    """The emphasis element whose opening marker would land against whatever precedes it."""
    if _tag_name(element) in _TRIMMABLE_EMPHASIS:
        return element
    if not _is_styling_wrapper(element) or len(element) == 0 or (element.text or "").strip():
        return None
    return _emphasis_at_start(element[0])


def _neighbour_before(element: Any) -> tuple[str, Any | None]:
    """What sits against an emphasis element's opening marker, seen through wrappers.

    Only styling wrappers are transparent; a citation or superscript genuinely separates two runs.
    """
    node = element
    while node is not None:
        previous = node.getprevious()
        while previous is not None:
            tail = previous.tail or ""
            if tail:
                return tail[-1], None
            emphasis = _emphasis_at_end(previous)
            if emphasis is not None:
                return "", emphasis
            if not _is_styling_wrapper(previous):
                return "", None
            content = previous.text_content() or ""
            if content:
                return content[-1], None
            previous = previous.getprevious()
        parent = node.getparent()
        if parent is None:
            return "", None
        if parent.text:
            return parent.text[-1], None
        if not _is_styling_wrapper(parent):
            return "", None
        node = parent
    return "", None


def _neighbour_after(element: Any) -> tuple[str, Any | None]:
    """What sits against an emphasis element's closing marker. See `_neighbour_before`."""
    node = element
    while node is not None:
        tail = node.tail or ""
        if tail:
            return tail[0], None
        following = node.getnext()
        if following is not None:
            emphasis = _emphasis_at_start(following)
            if emphasis is not None:
                return "", emphasis
            if not _is_styling_wrapper(following):
                return "", None
            content = following.text_content() or ""
            if content:
                return content[0], None
            node = following
            continue
        parent = node.getparent()
        if parent is None or not _is_styling_wrapper(parent):
            return "", None
        node = parent
    return "", None


def _emphasis_stack(element: Any) -> tuple[frozenset[str], Any] | None:
    """What a run of nested emphasis tags means, and the element holding its text."""
    if _tag_name(element) not in _EMPHASIS_SEMANTICS:
        return None
    meanings = [_EMPHASIS_SEMANTICS[_tag_name(element)]]
    innermost = element
    while (
        len(innermost) == 1
        and not (innermost.text or "").strip()
        and not (innermost[0].tail or "").strip()
        and _tag_name(innermost[0]) in _EMPHASIS_SEMANTICS
    ):
        innermost = innermost[0]
        meanings.append(_EMPHASIS_SEMANTICS[_tag_name(innermost)])
    return frozenset(meanings), innermost


def _remove_and_prune(element: Any) -> None:
    """Remove an element, then any wrapper or emphasis ancestor it leaves empty.

    Bottom-up, stopping at the first ancestor still holding something; an emptied inline tag cannot
    be left behind either.
    """
    while element is not None:
        parent = element.getparent()
        if parent is None:
            return
        tail = element.tail or ""
        if tail:
            previous = element.getprevious()
            if previous is not None:
                previous.tail = (previous.tail or "") + tail
            else:
                parent.text = (parent.text or "") + tail
        parent.remove(element)
        if not (_is_styling_wrapper(parent) or _tag_name(parent) in _EMPHASIS_SEMANTICS):
            return
        if len(parent) or (parent.text or ""):
            return
        element = parent


def _merge_adjacent_emphasis(html_text: str) -> str:
    """Join emphasis runs the editor split, so their markers stop colliding."""
    lowered = html_text.lower()
    if not any(f"<{tag}" in lowered for tag in _TRIMMABLE_EMPHASIS):
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    merged_any = False
    for element in list(root.iter()):
        if element.getparent() is None:
            continue
        run = _emphasis_stack(element)
        if run is None:
            continue
        meanings, target = run
        # Every merge removes an element, so the cap is a hang guard only.
        for _pass in range(_MAX_EMPHASIS_MERGE_PASSES):
            character, neighbour = _neighbour_after(element)
            if character or neighbour is None:
                break
            following = _emphasis_stack(neighbour)
            if following is None or following[0] != meanings:
                break
            source = following[1]
            if source is target or source.getparent() is None:
                break
            # Never reparent a subtree into itself.
            if target in source.iterancestors() or source in target.iterancestors():
                break
            if len(target):
                target[-1].tail = (target[-1].tail or "") + (source.text or "")
            else:
                target.text = (target.text or "") + (source.text or "")
            for moved in list(source):
                target.append(moved)
            # `_remove_and_prune` already carries the tail to the merged run; re-attaching
            # duplicated a clause.
            _remove_and_prune(source)
            merged_any = True
    if not merged_any:
        return html_text
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _trim_emphasis_edges(html_text: str) -> str:
    """Move punctuation out of an emphasis element's edge when it stops the run parsing.

    Moved only when punctuation sits inside against the marker AND a word character sits outside;
    edges that already parse are untouched.
    """
    lowered = html_text.lower()
    if not any(f"<{tag}" in lowered for tag in _TRIMMABLE_EMPHASIS):
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")

    for element in list(root.iter()):
        if _tag_name(element) not in _TRIMMABLE_EMPHASIS:
            continue
        parent = element.getparent()
        if parent is None:
            continue
        content = element.text_content() or ""
        if not content.strip():
            continue
        # Word characters and neighbouring emphasis markers both break the parse; sides are read
        # through styling wrappers.
        before, emphasis_before = _neighbour_before(element)
        after, emphasis_after = _neighbour_after(element)
        opens_badly = before.isalnum() or emphasis_before is not None
        closes_badly = after.isalnum() or emphasis_after is not None

        if not any(character.isalnum() for character in content):
            if not (opens_badly or closes_badly):
                continue
            # Unwrap by promoting children, never flattening to text - a nested `<sup>` has not been
            # rendered yet.
            index = parent.index(element)
            tail = element.tail or ""
            children = list(element)
            if children:
                for offset, child in enumerate(children):
                    parent.insert(index + offset, child)
                last = children[-1]
                last.tail = (last.tail or "") + tail
                if element.text:
                    previous = children[0].getprevious()
                    if previous is not None:
                        previous.tail = (previous.tail or "") + element.text
                    else:
                        parent.text = (parent.text or "") + element.text
            else:
                previous = element.getprevious()
                if previous is not None:
                    previous.tail = (previous.tail or "") + content + tail
                else:
                    parent.text = (parent.text or "") + content + tail
            parent.remove(element)
            continue

        while opens_badly and element.text and _EMPHASIS_EDGE.fullmatch(element.text[0]):
            moved, element.text = element.text[0], element.text[1:]
            previous = element.getprevious()
            if previous is not None:
                previous.tail = (previous.tail or "") + moved
            else:
                parent.text = (parent.text or "") + moved
        # Only when the last node is the element's own text; a nested tag waits its turn.
        while closes_badly and len(element) == 0 and element.text and _EMPHASIS_EDGE.fullmatch(element.text[-1]):
            moved, element.text = element.text[-1], element.text[:-1]
            element.tail = moved + (element.tail or "")
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _unwrap_numeric_links(html_text: str) -> str:
    """Unwrap a link whose whole visible label is a number, keeping the number.

    The address is deliberately lost with the anchor - the number is the content, and in this corpus
    the address resolves to nothing a reader can follow.
    """
    if "<a" not in html_text:
        return html_text
    return _NUMERIC_LINK_RE.sub(r"\1", html_text)


def _drop_citations(html_text: str) -> str:
    """Remove inline reference markers, taking the whitespace that was holding them."""
    if _CITATION_CLASS not in html_text and "[" not in html_text:
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")

    doomed = [
        _citation_root(element)
        for element in root.iter()
        if isinstance(element.tag, str)
        and (_CITATION_CLASS in (element.get("class") or "").split() or _is_manual_citation(element))
    ]
    emptied: list[Any] = []
    doomed_set = {id(element) for element in doomed}
    for element in doomed:
        parent = element.getparent()
        if parent is None:
            continue
        # The separator strictly between two markers is citation apparatus; only that position is
        # touched.
        separator = element.tail or ""
        following = element.getnext()
        if separator.strip() in _CITATION_SEPARATORS and following is not None and id(following) in doomed_set:
            element.tail = ""
        # The full stop may sit in a styled sibling, not the tail - the first following character
        # decides.
        tail = element.tail or ""
        lookahead = tail[:1] or _leading_text(element.getnext())
        previous = element.getprevious()
        rejoined = _rejoin_across_citation(
            (previous.tail if previous is not None else parent.text) or "", tail, lookahead
        )
        if previous is not None:
            previous.tail = rejoined
        else:
            parent.text = rejoined
        parent.remove(element)
        emptied.append(parent)

    # A cell of bare reference markers keeps its commas once they go; clear the run.
    for parent in emptied:
        if _ORPHANED_SEPARATORS_RE.fullmatch(parent.text_content() or ""):
            for child in list(parent):
                parent.remove(child)
            parent.text = None

    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _drop_embedded_images(html_text: str) -> str:
    """Remove `<img>` tags before conversion, so no image markup reaches the corpus.

    Textual removal, immediately before conversion: a parse-and-serialize round trip re-encodes
    unrelated attributes, and earlier removal exposes newly blank tags to other passes.
    """
    if not _IMG_OPEN_RE.search(html_text):
        return html_text
    return _IMG_TAG_RE.sub("", html_text)


def _rebase_headings(html_text: str, base_level: int) -> str:
    """Move a publisher's own headings underneath the heading that contains them.

    The publisher's relative nesting is kept: the shallowest heading lands one level below
    `base_level` and the rest keep their offsets.
    """
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    headings = [element for element in root.iter() if isinstance(element.tag, str) and element.tag in _HEADING_TAGS]
    if not headings:
        return html_text
    shallowest = min(int(element.tag[1]) for element in headings)
    for element in headings:
        offset = int(element.tag[1]) - shallowest
        # Markdown stops at six; deep nesting flattens rather than overflowing.
        element.tag = f"h{min(base_level + 1 + offset, _MAX_HEADING_LEVEL)}"
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _expand_table_spans(html_text: str) -> str:
    """Give every table row its full column count, by filling in spanned cells.

    Both span kinds are resolved together, column by column - resolved separately, a row-span filler
    lands at the wrong position whenever a column span precedes it.
    """
    if "span=" not in html_text:
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    for table in root.iter("table"):
        # Columns a cell from an earlier row still occupies: {column: rows remaining}.
        carried: dict[int, int] = {}
        for row in table.iter("tr"):
            cells = [cell for cell in row if isinstance(cell.tag, str) and cell.tag in {"td", "th"}]
            if not cells:
                continue
            # Consumed by this row and, if the span reaches further, kept for the next.
            covered = carried
            carried = {column: remaining - 1 for column, remaining in covered.items() if remaining > 1}
            filler_tag = cells[0].tag

            rebuilt: list[Any] = []
            column = 0
            for cell in cells:
                # Step over columns an earlier row still occupies.
                while column in covered:
                    rebuilt.append(lxml_html.Element(filler_tag))
                    column += 1
                colspan = _span_value(cell, "colspan")
                rowspan = _span_value(cell, "rowspan")
                rebuilt.append(cell)
                rebuilt.extend(lxml_html.Element(cell.tag) for _ in range(colspan - 1))
                if rowspan > 1:
                    for offset in range(colspan):
                        carried[column + offset] = rowspan - 1
                column += colspan

            # Columns still covered past the row's last cell, and any gap before them.
            while covered and column <= max(covered):
                rebuilt.append(lxml_html.Element(filler_tag))
                column += 1

            for child in cells:
                row.remove(child)
            for index, cell in enumerate(rebuilt):
                row.insert(index, cell)
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _span_value(cell: Any, attribute: str) -> int:
    """Read a colspan or rowspan attribute, treating anything unusable as 1."""
    try:
        span = int(cell.get(attribute) or 1)
    except ValueError:
        return 1
    cell.attrib.pop(attribute, None)
    # A publisher typo of 200 would otherwise produce a 200-column row.
    return span if 1 <= span <= _MAX_TABLE_SPAN else 1


def _label_text(element: Any) -> str:
    """Return an element's text as one line, or "" for comments and processing nodes."""
    if not isinstance(element.tag, str):
        return ""
    return " ".join((element.text_content() or "").split())


def _paperwork_label(text: str) -> bool:
    """Report whether a line is one of the labels publishers put over their paperwork."""
    return _skip_lookup_key(text) in _INLINE_PAPERWORK_LABELS


def _opens_with_paperwork_label(element: Any) -> bool:
    """Report whether a paragraph is a paperwork label and its value on one line."""
    if element.tag != "p" or len(element) == 0 or (element.text or "").strip():
        return False
    opener = element[0]
    if not isinstance(opener.tag, str) or opener.tag not in _EMPHASIS_TAGS:
        return False
    if not (opener.tail or "").lstrip().startswith(":"):
        return False
    label = " ".join((opener.text_content() or "").split()).rstrip(":")
    return bool(label) and _paperwork_label(label)


def _is_inline_heading(element: Any) -> bool:
    """Report whether an element is a line the publisher is using as a heading."""
    text = _label_text(element)
    if not text or len(text) > _MAX_INLINE_HEADING_CHARS:
        return False
    if element.tag in _HEADING_TAGS:
        return True
    if element.tag != "p":
        return False
    marked = " ".join(
        "".join(
            part.text_content() or ""
            for part in element.iter()
            if isinstance(part.tag, str) and part.tag in _EMPHASIS_TAGS
        ).split()
    )
    return bool(marked) and len(marked) >= len(text) - 2


def _identity(html_text: str) -> str:
    """Return the fragment untouched - the heading path's stand-in for a removal pass."""
    return html_text


def _drop_inline_paperwork(html_text: str) -> str:
    """Remove paperwork the publisher wrote inside a section it also uses for guidance.

    Only labels in `_INLINE_PAPERWORK_LABELS` match, and removal stops at the next label, so a
    mistake costs one block.
    """
    if not _INLINE_PAPERWORK_HINT_RE.search(html_text):
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")

    doomed: list[Any] = []
    children = [child for child in root if isinstance(child.tag, str)]
    for index, child in enumerate(children):
        text = _label_text(child)
        if _AUTHORSHIP_MARKER_RE.match(text) and len(text) <= _MAX_AUTHORSHIP_LABEL_CHARS:
            following = next(
                (later for later in children[index + 1 :] if _label_text(later) or later.tag in _TABLE_TAGS),
                None,
            )
            if following is not None and following.tag in _TABLE_TAGS:
                doomed.extend((child, following))
            continue
        if _opens_with_paperwork_label(child):
            doomed.append(child)
            continue
        # For publishers who don't mark the label up. Safe only because `_plain_heading` no longer
        # runs removal passes.
        if not _is_inline_heading(child):
            if not _paperwork_label(text):
                continue
            doomed.append(child)
            following = next((later for later in children[index + 1 :] if _label_text(later)), None)
            if following is not None and not _is_inline_heading(following):
                doomed.append(following)
            continue
        if not _paperwork_label(text):
            continue
        doomed.append(child)
        for later in children[index + 1 :]:
            if _is_inline_heading(later):
                break
            doomed.append(later)

    for element in doomed:
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)

    for paragraph in root.iter("p"):
        _drop_paperwork_runs(paragraph)

    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _drop_paperwork_runs(paragraph: Any) -> None:
    """Remove <br>-separated runs inside one paragraph that a paperwork label opens."""
    if not any(isinstance(child.tag, str) and child.tag == "br" for child in paragraph):
        return

    # A run's prose lives in the `<br>`'s tail, so a run is described by where its text is stored.
    runs: list[dict[str, Any]] = [{"owner": paragraph, "attribute": "text", "nodes": []}]
    for child in paragraph:
        if isinstance(child.tag, str) and child.tag == "br":
            runs.append({"owner": child, "attribute": "tail", "nodes": [child]})
        else:
            runs[-1]["nodes"].append(child)

    dropping = False
    authorship_doomed = False
    doomed_runs: list[dict[str, Any]] = []
    trailing_text: list[str] = []
    for run in runs:
        own_text = getattr(run["owner"], run["attribute"]) or ""
        text = " ".join(
            (
                own_text + "".join(_label_text(node) + (node.tail or "") for node in run["nodes"] if node.tag != "br")
            ).split()
        )
        opener = next((node for node in run["nodes"] if isinstance(node.tag, str) and node.tag in _EMPHASIS_TAGS), None)
        label = _label_text(opener) if opener is not None else ""
        if _AUTHORSHIP_MARKER_RE.match(text) and len(text) <= _MAX_AUTHORSHIP_LABEL_CHARS:
            dropping = True
            authorship_doomed = True
        elif label and len(label) <= _MAX_INLINE_HEADING_CHARS:
            # Only a label decides; an unlabelled run inherits the verdict of the label before it.
            dropping = _paperwork_label(label)
        if dropping:
            doomed_runs.append(run)
            trailing_text.clear()
        elif text:
            trailing_text.append(text)

    if not doomed_runs:
        return

    # Nothing but whitespace between an authorship label and the paragraph's end takes the following
    # table too.
    if authorship_doomed and not trailing_text:
        following = paragraph.getnext()
        if following is not None and isinstance(following.tag, str) and following.tag in _TABLE_TAGS:
            following.getparent().remove(following)

    for run in doomed_runs:
        setattr(run["owner"], run["attribute"], None)
        for node in run["nodes"]:
            node.tail = None
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)


def _label_table_recommendations(html_text: str) -> str:
    """Give a recommendation written as a table a heading made from its own label."""
    if "<table" not in html_text:
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    for table in list(root.iter("table")):
        rows = table.findall(".//tr")
        if not rows:
            continue
        cells = rows[0].xpath(".//td | .//th")
        if not cells:
            continue
        label = " ".join(cells[0].text_content().split())
        if not label or len(label) >= 60 or not _LABEL_OPENERS.match(label):
            continue
        # A [label, Grade] header row carries the grade in the next row's last cell.
        grade = ""
        if len(cells) >= 2 and " ".join(cells[1].text_content().split()).lower() == "grade" and len(rows) >= 2:
            tail_cells = rows[1].xpath(".//td | .//th")
            if tail_cells:
                grade_text = " ".join(tail_cells[-1].text_content().split())
                if 0 < len(grade_text) <= 8:
                    grade = grade_text
        heading = table.makeelement("h4", {})
        heading.text = f"{label} (Grade {grade})" if grade else label
        table.addprevious(heading)
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


# ---------------------------------------------------------------------------
# Conversion and markdown cleanup passes
# ---------------------------------------------------------------------------

_EMPTY_HEADING_RE = re.compile(r"^#{1,6}[ \t]*$\n?", re.MULTILINE)

_BLANK_RUN_RE = re.compile(r"\n{3,}")

# Four forms; the trailing one is removed only after sentence-ending punctuation.
_WIKI_CHROME_RE = re.compile(
    r"[ \t\xa0]*\\?\[edit source\\?\]"
    r"|^[ \t\xa0]*[*_]{0,2}\[?Back to top\]?(?:\([^)]*\))?[*_]{0,2}[ \t\xa0]*$\n?"
    r"|(?<=[.!?\)])[ \t\xa0]*\[?Back to top\]?(?:\([^)]*\))?[ \t\xa0]*$",
    re.MULTILINE,
)

# A dash or equals run directly under text turns the paragraph into a setext heading; escaped.
_ACCIDENTAL_SETEXT_RE = re.compile(r"^(?P<text>\S.*)\n(?P<rule>[-=]{2,})[ \t]*$", re.MULTILINE)


def _markdown(raw_html: Any, *, link_mode: LinkMode, base_level: int | None = None, as_heading: bool = False) -> str:
    """Convert a MAGICapp HTML fragment to markdown, dropping embedded images.

    `as_heading` skips every removal pass: a heading is being reduced to text, and a rule that
    erases one un-skips the whole section it named.
    """
    source = _label_table_recommendations(
        _expand_table_spans(
            _drop_orphaned_captions(
                (_drop_inline_paperwork if not as_heading else _identity)(
                    _render_super_subscripts(
                        _split_emphasis_across_breaks(
                            _drop_citations(
                                _unwrap_numeric_links(
                                    _trim_emphasis_edges(
                                        _merge_adjacent_emphasis(_drop_tracked_deletions(str(raw_html or "")))
                                    )
                                )
                            )
                        )
                    )
                )
            )
        )
    )
    # Runs to a fixpoint (blank tags nest, moves expose new blanks); the cap is a hang guard.
    for _ in range(64):
        replaced = _ZERO_WIDTH_INLINE_TAG_RE.sub("", source)
        replaced = _BLANK_INLINE_TAG_RE.sub(" ", replaced)
        replaced = _SPACE_AFTER_OPEN_RE.sub(r"\2\1", replaced)
        replaced = _SPACE_BEFORE_CLOSE_RE.sub(r"\2\1", replaced)
        if replaced == source:
            break
        source = replaced
    else:
        logger.debug("Inline-tag normalization did not stabilize within 64 passes")
    if base_level is not None:
        source = _rebase_headings(source, base_level)
    markdown = html_to_markdown(_drop_embedded_images(source), link_mode=link_mode)
    # Publishers leave headings whose text is empty, which convert to a bare "##".
    markdown = _WIKI_CHROME_RE.sub("", _EMPTY_HEADING_RE.sub("", markdown))
    # Replaced with a space, not deleted - publishers use them as separators, and deletion glues
    # words.
    markdown = _ZERO_WIDTH_CHAR_RE.sub(" ", markdown)
    markdown = _strip_question_numbers_from_headings(markdown)
    if not as_heading:
        markdown = _drop_pointer_blocks(markdown)
        # Order matters: this rule also takes the bold label above the list; the other would strand
        # it.
        markdown = _drop_resource_directories(markdown)
        markdown = _drop_link_directories(markdown)
        markdown = _drop_resource_lines(markdown)
        markdown = _drop_cross_reference_sentences(markdown)
        markdown = _drop_approval_stamps(markdown)
        markdown = _drop_source_credits(markdown)
        markdown = _drop_number_only_captions(markdown)
        markdown = _drop_search_method_blocks(markdown)
        markdown = _drop_review_citations(markdown)
        markdown = _drop_keyword_blocks(markdown)
        markdown = _drop_inline_appendices(markdown)
        markdown = _drop_citation_blocks(markdown)
        markdown = _drop_publication_announcements(markdown)
        markdown = _drop_bibliography_blocks(markdown)
        markdown = _drop_dateline_blocks(markdown)
        markdown = _drop_document_link_lists(markdown)
    markdown = _drop_grade_circles(markdown)
    # Removing a line leaves the blank line that was under it, so paragraphs drift apart.
    markdown = _BLANK_RUN_RE.sub("\\n\\n", markdown)
    return _ACCIDENTAL_SETEXT_RE.sub(r"\g<text>\n\\\g<rule>", markdown).strip()


_LINK_ONLY_ITEM_RE = re.compile(r"\A\s*[-*]\s+(?:<https?://\S+>|\[[^\]]*\]\(\S+\)|https?://\S+)\s*\Z")
_LIST_ITEM_RE = re.compile(r"\A\s*[-*]\s+")
# Two, because the atomic unit is one pair: a resource named on one line and its address beneath.
_MIN_LINK_DIRECTORY_ITEMS = 2
# Directory entries never advise; this keeps the rule off recommendations carrying a source link.
# Untripped today - insurance.
_DIRECTORY_LABEL_EXCLUSION_RE = re.compile(
    r"\b(?:should|recommend|we suggest|offer|must|advise|first-line)\b", re.IGNORECASE
)
# Of the LEAF items: real directories sit well above, clinical lists well below.
_MIN_LINK_DIRECTORY_SHARE = 0.5
# A reference entry minus its address is a short title; requiring it to BE a link counted almost
# none.
_MAX_REFERENCE_LABEL_CHARS = 140


def _is_reference_item(item: str) -> bool:
    """Report whether one list item is a citation: a title, an address, and nothing else."""
    if "http" not in item and "www." not in item:
        return False
    remainder = _LINK_TARGET_RE.sub(" ", item)
    remainder = " ".join(re.sub(r"\A\s*[-*]\s+", "", remainder).replace("[", " ").replace("]", " ").split())
    if len(remainder) > _MAX_REFERENCE_LABEL_CHARS:
        return False
    return not (_SENTENCE_CLAIM_RE.search(remainder) or _MEASUREMENT_RE.search(remainder))


_RESOURCE_LINE_RE = re.compile(
    r"\A\s*(?:[-*]\s+)?(?:websites?|web\s+address(?:es)?|online\s+resources?|useful\s+links?|"
    r"further\s+reading|resources?)\s*:",
    re.IGNORECASE,
)
_AVAILABLE_AT_RE = re.compile(r"\bavailable\s+(?:at|from|online\s+at)\b", re.IGNORECASE)


def _drop_resource_lines(markdown: str) -> str:
    """Remove a line that is a signpost to somewhere else on the web."""
    lines = markdown.split("\n")

    def indent(line: str) -> int:
        return len(line) - len(line.lstrip())

    def visible_text(line: str) -> str:
        return " ".join(re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line).split())

    doomed: set[int] = set()
    for number, line in enumerate(lines):
        if not line.strip():
            continue
        following = next((lines[later] for later in range(number + 1, len(lines)) if lines[later].strip()), "")
        if _LIST_ITEM_RE.match(line) and _LIST_ITEM_RE.match(following) and indent(following) > indent(line):
            continue
        visible = visible_text(line)
        labelled = _RESOURCE_LINE_RE.match(visible)
        cited = _AVAILABLE_AT_RE.search(visible) and ("http" in line or "www." in line)
        if not labelled and not cited:
            continue
        if _MEASUREMENT_RE.search(visible) and not labelled:
            continue
        doomed.add(number)
        if not labelled:
            continue
        # Walk back to the bolded name opening the entry; a heading means we left the block.
        span: list[int] = []
        for earlier in range(number - 1, max(-1, number - 9), -1):
            text = lines[earlier].strip()
            if not text:
                continue
            if text.startswith("#") or _LIST_ITEM_RE.match(lines[earlier]):
                span = []
                break
            span.append(earlier)
            if _WHOLE_LINE_BOLD_RE.fullmatch(text):
                break
        else:
            span = []
        doomed.update(span)
    if not doomed:
        return markdown
    return "\n".join(line for number, line in enumerate(lines) if number not in doomed)


def _drop_link_directories(markdown: str) -> str:
    """Remove a list that is a directory of other organisations' resources."""
    if "http" not in markdown:
        return markdown
    lines = markdown.split("\n")
    doomed: set[int] = set()
    index = 0
    while index < len(lines):
        if not _LIST_ITEM_RE.match(lines[index]):
            index += 1
            continue
        end = index
        # An indented non-bullet continues the item above.
        while end < len(lines) and (
            _LIST_ITEM_RE.match(lines[end])
            or not lines[end].strip()
            or (lines[end][:1].isspace() and lines[end].strip())
        ):
            end += 1
        items = [number for number in range(index, end) if _LIST_ITEM_RE.match(lines[number])]
        if not items:
            index = end if end > index else index + 1
            continue
        # An item is its line plus its continuation lines.
        spans = {
            number: "\n".join(lines[number : (items[position + 1] if position + 1 < len(items) else end)])
            for position, number in enumerate(items)
        }
        # A leaf is an item nothing is nested under: the next item is no deeper than it.
        leaves = [
            number
            for position, number in enumerate(items)
            if position + 1 >= len(items)
            or len(lines[items[position + 1]]) - len(lines[items[position + 1]].lstrip())
            <= len(lines[number]) - len(lines[number].lstrip())
        ]
        if len(items) >= _MIN_LINK_DIRECTORY_ITEMS and leaves:
            links = sum(1 for number in leaves if _is_reference_item(spans[number]))
            # A run is removed whole, so every item counts, not just parent labels.
            labels = [spans[number] for number in items]
            # Advice only, not measurements: `_is_reference_item` already refuses entries carrying a
            # number.
            advises = any(_DIRECTORY_LABEL_EXCLUSION_RE.search(label) for label in labels)
            if not advises and links / len(leaves) > _MIN_LINK_DIRECTORY_SHARE:
                doomed.update(range(index, end))
        index = end
    if not doomed:
        return markdown
    # No empty-fragment guard: these are often a subsection's whole body, and emptied bodies are
    # handled a level up.
    return "\n".join(line for number, line in enumerate(lines) if number not in doomed)


def _drop_pointer_blocks(markdown: str) -> str:
    """Remove a rendered paragraph whose whole content is one sentence pointing elsewhere."""
    if "\n\n" not in markdown and not _POINTER_ONLY_RE.match(markdown.strip()):
        return markdown
    blocks = markdown.split("\n\n")
    kept, dropped_any = [], False
    for block in blocks:
        stripped = block.strip()
        visible = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", stripped)
        visible = " ".join(visible.split())
        if (
            visible
            and not stripped.startswith(("#", "|", "-", "*", ">", "    "))
            and len(visible) <= _MAX_POINTER_CHARS
            and _POINTER_ONLY_RE.match(visible)
            and not _MEASUREMENT_RE.search(visible)
        ):
            dropped_any = True
            continue
        kept.append(block)
    if not dropped_any:
        return markdown
    remainder = "\n\n".join(kept)
    if not remainder.strip():
        return markdown
    return remainder


# URLs are full of full stops; mask before splitting sentences. Longest form first.
_LINK_TARGET_RE = re.compile(r"\]\([^)]*\)|<https?://[^>]*>|https?://\S+")
_MASK_RE = re.compile("\x00(\\d+)\x00")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[(*‘“])")
# Only whole sentences drop; fragments from bad splits ("e.g.") are kept. The final sentence may
# lack punctuation.
_SENTENCE_SHAPE_RE = re.compile(r"\A[A-Z\[(*‘“].*[.!?]\Z", re.DOTALL)
_FINAL_SENTENCE_SHAPE_RE = re.compile(r"\A[A-Z\[(*‘“].*(?:[.!?]|\S)\Z", re.DOTALL)
# Arrives mid-paragraph, after a clause that asserts - `_POINTER_ONLY_RE` anchors at the start and
# cannot see it.
_CROSS_REFERENCE_RE = re.compile(
    r"\b(?:is|are|was|were)\s+(?:covered|addressed|described|discussed|dealt\s+with|based)\s+(?:by|in|on)\b"
    r"|\b(?:is|are)\s+contained\s+(?:within|in)\b"
    r"|\bcan\s+be\s+(?:found|accessed|downloaded|obtained|viewed)\b"
    r"|\bavailable\s+(?:here|at|from|online)\b"
    r"|\bis\s+located\s+(?:here|at)\b|\bcan\s+be\s+(?:reached|visited)\s+(?:here|at)\b"
    r"|\bis\s+available\s+as\s+(?:supplemental|supplementary|an?\s+appendix)\b"
    r"|\b(?:please\s+)?refer\s+to\b"
    r"|\bfor\s+(?:further|more|additional)\s+(?:information|details?|guidance)\b"
    r"|\bfurther\s+(?:information|details?)\b"
    r"|\bprovides?\s+(?:further\s+|more\s+|detailed\s+)?information\s+(?:on|about)\b",
    re.IGNORECASE,
)
# Only beside a dropped cross-reference - the pair shape; a scope statement standing alone is kept.
_SCOPE_DISCLAIMER_RE = re.compile(r"\b(?:not\s+within|outside)\s+(?:the\s+)?scope\s+of\b", re.IGNORECASE)
# A sentence that instructs or quantifies is kept even with an address. Verb forms only, never
# `\w*`.
_SENTENCE_CLAIM_RE = re.compile(
    r"\b(?:should|must|shall|recommend(?:s|ed|ing)?|suggest(?:s|ed|ing)?|advise(?:s|d)?|advising|"
    r"offer(?:s|ed|ing)?|consider(?:s|ed|ing)?|prescrib(?:e|es|ed|ing)|administer(?:s|ed|ing)?|"
    r"monitor(?:s|ed|ing)?|assess(?:es|ed|ing)?|ensure(?:s|d)?|ensuring|"
    r"avoid(?:s|ed|ing)?|contraindicat\w*)\b"
    r"|\b(?:RR|OR|HR)\s*[=:]|95%\s*CI|\bp\s*[<=]\s*0",
    re.IGNORECASE,
)

# Names a document without an address. All three conditions below required - each phrase alone
# appears in keepers.
_DOCUMENT_REFERENCE_PHRASE_RE = re.compile(
    r"\bfurther\s+(?:guidance|resources?|discussion|reading|detail)\b"
    r"|\Aadvice\s+on\b|\A(?:further|more)\s+advice\b"
    r"|\A(?:see|refer to)\s+the\s+(?:flow\s?chart|diagram|algorithm)\b",
    re.IGNORECASE,
)
# Must say where the thing lives, or "there is further guidance on what to do" matches.
_LOCATES_A_DOCUMENT_RE = re.compile(
    r"\b(?:is|are)\s+(?:available|provided|detailed|described|given|contained|included|set\s+out)\b"
    r"|\bcan\s+be\s+found\b|\b(?:please\s+)?refer\s+to\b|\A(?:for|see)\b"
    r"|\bprovides?\s+(?:some\s+)?further\s+(?:guidance|resources?|discussion|reading|detail)\b",
    re.IGNORECASE,
)
# "Further guidance needed" is often a CERQual finding, not a signpost.
_FUTURE_WORK_RE = re.compile(
    r"\bneed(?:s|ed|ing)?\b|\baims?\s+to\s+develop\b|\bin\s+development\b|\bwill\s+provide\b"
    r"|\bwish(?:es|ed)?\s+for\b|\badvocate\s+for\b|\bto\s+justify\b",
    re.IGNORECASE,
)
# Anchored: publishers glue one onto the end of a real finding.
_CLICK_INSTRUCTION_RE = re.compile(
    r"\A\**\s*(?:please\s+)?click\b|\A(?:to|for)\b[^.]{0,70}\bclick\s+(?:here|on|to)\b",
    re.IGNORECASE,
)
# The verb only says the figure exists; the claim guard keeps the ones that assert.
_FIGURE_REFERENCE_RE = re.compile(
    r"\A(?:fig(?:ure)?\.?|table|box|chart|annex|appendix)\s*[A-Z]?\d+[a-z]?\.?\s+"
    r"(?:summari[sz]es|shows|illustrates|presents|depicts|displays|outlines|lists|describes|"
    r"provides|gives)\b",
    re.IGNORECASE,
)


def _mask_link_targets(text: str) -> tuple[str, list[str]]:
    """Replace every link target with a placeholder holding no sentence punctuation."""
    targets: list[str] = []

    def take(match: re.Match[str]) -> str:
        targets.append(match.group(0))
        return f"\x00{len(targets) - 1}\x00"

    return _LINK_TARGET_RE.sub(take, text), targets


def _unmask_link_targets(text: str, targets: list[str]) -> str:
    return _MASK_RE.sub(lambda match: targets[int(match.group(1))], text)


def _is_cross_reference_sentence(sentence: str, *, final: bool = False) -> bool:
    """Report whether one sentence only says that the subject lives in another document."""
    shape = _FINAL_SENTENCE_SHAPE_RE if final else _SENTENCE_SHAPE_RE
    if not shape.match(sentence.strip()):
        return False
    # Publishers put emphasis markers anywhere; they break wording tests wherever they land.
    visible = " ".join(re.sub(r"[*_]+", " ", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", sentence)).split())
    if _SENTENCE_CLAIM_RE.search(visible) or _MEASUREMENT_RE.search(visible):
        return False
    # A click instruction needs no address to be useless - the link it refers to is the address.
    if _CLICK_INSTRUCTION_RE.match(visible) or _FIGURE_REFERENCE_RE.match(visible):
        return True
    # Naming a document and its location is the same act as linking to it.
    if (
        _DOCUMENT_REFERENCE_PHRASE_RE.search(visible)
        and _LOCATES_A_DOCUMENT_RE.search(visible)
        and not _FUTURE_WORK_RE.search(visible)
    ):
        return True
    if "http" not in sentence and "www." not in sentence:
        return False
    return bool(_CROSS_REFERENCE_RE.search(visible))


def _drop_cross_reference_sentences(markdown: str) -> str:
    """Remove a sentence whose whole job is to name another document."""
    blocks, changed = markdown.split("\n\n"), False
    for index, block in enumerate(blocks):
        stripped = block.strip()
        # Bold passes; list markers belong to `_drop_link_directories`, italics to
        # `_drop_approval_stamps`.
        if (
            not stripped
            or stripped.startswith(("#", "|", ">", "    "))
            or _LIST_ITEM_RE.match(stripped)
            or (stripped.startswith("*") and not stripped.startswith("**"))
        ):
            continue
        masked, targets = _mask_link_targets(stripped)
        restored = [_unmask_link_targets(sentence, targets) for sentence in _SENTENCE_SPLIT_RE.split(masked)]
        keep = [
            not _is_cross_reference_sentence(sentence, final=position == len(restored) - 1)
            for position, sentence in enumerate(restored)
        ]
        if all(keep):
            continue
        keep = [
            kept and not (_SCOPE_DISCLAIMER_RE.search(sentence) and not _SENTENCE_CLAIM_RE.search(sentence))
            for kept, sentence in zip(keep, restored, strict=True)
        ]
        blocks[index] = " ".join(sentence for sentence, kept in zip(restored, keep, strict=True) if kept)
        changed = True
    if not changed:
        return markdown
    # May empty completely; callers handle it.
    return "\n\n".join(block for block in blocks if block.strip())


# Anchored at the sentence start - that is the whole safety argument.
_APPROVAL_STAMP_RE = re.compile(
    r"\A(?:update\s+)?approved\s+(?:by|in|on)\b"
    r"|\Aevidence\s+surveillance\s*:"
    r"|\Athis\s+(?:is\s+a\s+draft\s+recommendation|recommendation\s+is\s+currently\s+considered\s+stable)\b"
    r"|\A(?:date\s+of\s+(?:approval|publication|issue)|next\s+review|review\s+date)\b"
    r"|\Aexpires?\s",
    re.IGNORECASE,
)
# The stamp arrives wrapped in italics; see through them, re-wrap what survives.
_WHOLE_BLOCK_ITALIC_RE = re.compile(r"\A\*(?!\*)(?P<body>.*?)\*\Z", re.DOTALL)


_BOLD_LABEL_LINE_RE = re.compile(r"\A\*\*(?!\*)(?P<label>[^*].*?)\*\*[:.]?\Z")
_DIRECTORY_HEADER_RE = re.compile(r"\b(?:resources?|providing|support|services?)\b", re.IGNORECASE)
# A directory header names a category and stops; each exclusion below was paid for by a measured
# loss.
_MAX_DIRECTORY_HEADER_CHARS = 80
# Advice written as a label, detail bulleted underneath.
_DIRECTORY_HEADER_ADVISES_RE = re.compile(
    r"\b(?:we\s+recommend|we\s+suggest|should|must|shall|recommend(?:s|ed)?)\b", re.IGNORECASE
)
# "Resources" mostly names GRADE cost analysis, not directories.
_GRADE_RESOURCE_DOMAIN_RE = re.compile(
    r"\bcertainty\s+of\s+(?:the\s+)?evidence\b"
    r"|\bresources?\s+(?:consideration|requirement|required|utili[sz]ation)"
    r"|\bmain\s+resource\b|\bevidence\s+on\s+resources\b"
    r"|\bimpact\s+on\s+the\s+organi[sz]ation\s+of\s+care\b",
    re.IGNORECASE,
)


# The cap separates one citation or service from a topic section labelled "resources".
_MAX_PROSE_DIRECTORY_ENTRY_CHARS = 400


def _is_prose_directory_entry(entry: str) -> bool:
    """Report whether a block under a directory label is one entry rather than a section."""
    if len(entry) > _MAX_PROSE_DIRECTORY_ENTRY_CHARS:
        return False
    if "http" not in entry and "www." not in entry:
        return False
    visible = " ".join(re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", entry).split())
    return not (_SENTENCE_CLAIM_RE.search(visible) or _MEASUREMENT_RE.search(visible))


def _drop_resource_directories(markdown: str) -> str:
    """Remove a bold label announcing a list of services, together with that list."""
    if "**" not in markdown:
        return markdown
    blocks = markdown.split("\n\n")
    drop: set[int] = set()
    for index, block in enumerate(blocks):
        match = _BOLD_LABEL_LINE_RE.match(block.strip())
        if not match:
            continue
        label = match.group("label").strip().rstrip(":.").strip()
        if (
            not _DIRECTORY_HEADER_RE.search(label)
            or len(label) > _MAX_DIRECTORY_HEADER_CHARS
            or label.endswith("?")
            or _DIRECTORY_HEADER_ADVISES_RE.search(label)
            or _GRADE_RESOURCE_DOMAIN_RE.search(label)
        ):
            continue
        following = next((later for later in range(index + 1, len(blocks)) if blocks[later].strip()), None)
        if following is None:
            continue
        entry = blocks[following].strip()
        if not _LIST_ITEM_RE.match(entry.splitlines()[0]) and not _is_prose_directory_entry(entry):
            continue
        drop.update({index, following})
    if not drop:
        return markdown
    remainder = "\n\n".join(block for index, block in enumerate(blocks) if index not in drop)
    return remainder if remainder.strip() else markdown


_FOOTNOTE_MARKER_RE = re.compile(r"\A(?:\\\*|\*(?!\*)|†|‡|§|¶|[¹²³⁴⁵⁶⁷⁸⁹])\s*")
# Keyed on the anchor - "Department" and "hospital" are clinical vocabulary here.
_AFFILIATION_FOOTNOTE_RE = re.compile(r"\A\[\]\(#_ftnref\d+\)")


_CITATION_LABEL_RE = re.compile(
    r"\A(?:suggested|recommended|preferred)\s+citation\b|\A(?:how\s+to\s+)?cite\s+(?:this|as)\b",
    re.IGNORECASE,
)
_SOURCE_CREDIT_RE = re.compile(
    r"\A(?:adapted|reproduced|reprinted|modified|derived|taken)\s+(?:from|with|by)\b"
    r"|\A(?:source|credit|attribution)\s*:"
    r"|\Awith\s+permission\s+from\b"
    r"|\A(?:copyright|©)\b",
    re.IGNORECASE,
)
# The dangerous footnote defines a term or a threshold; it must never read as a credit line.
_FOOTNOTE_CONTENT_RE = re.compile(
    r"\b(?:should|must|shall|recommend(?:s|ed|ing)?|suggest(?:s|ed|ing)?|defined?\s+as|"
    r"downgrad\w*|upgrad\w*|imprecision|inconsistency|indirectness|risk\s+of\s+bias)\b"
    r"|\b(?:RR|OR|HR)\s*[=:]|95%\s*CI|\bp\s*[<=]\s*0"
    r"|\b\d+(?:\.\d+)?\s*(?:%|mg|mcg|µg|mmol|mL|IU|mmHg|hours?|days?|weeks?|months?|years?)\b",
    re.IGNORECASE,
)


# Only the bare number qualifies; a caption that describes its table stays.
_NUMBER_ONLY_CAPTION_RE = re.compile(
    r"\A\*\*(?:table|figure|box|chart|appendix)\s*[A-Z]?\d+[a-z]?\.?\s*\*\*[:.]?\Z", re.IGNORECASE
)


_RENDERED_HEADING_RE = re.compile(r"\A(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*\Z")
# These introduce database names and query strings, never what the search returned.
_SEARCH_METHOD_HEADING_RE = re.compile(
    r"\A(?:search\s+strateg(?:y|ies)|search\s+methods?|literature\s+search(?:es)?|"
    r"databases?\s+searched|search\s+terms?|search\s+and\s+selection|"
    r"limitations?\s+of\s+searches?|three\s+month\s+bridging\s+search|"
    r"search\s+for\s+existing\s+relevant\s+guidelines?[\w \-]*)\Z",
    re.IGNORECASE,
)
# An in-body rule carries its own content guard: effects, doses, recommendations and findings stay.
_SEARCH_BLOCK_CONTENT_RE = re.compile(
    r"\b(?:we\s+recommend|we\s+suggest|should|must|contraindicat\w*)\b"
    r"|\b(?:RR|OR|HR)\s*[=:]|95%\s*CI|\bp\s*[<=]\s*0"
    r"|\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|mmol|mL|IU|mmHg)\b"
    r"|\bcertainty\s+(?:of\s+the\s+evidence|was\s+(?:high|moderate|low))\b"
    r"|\bno\s+(?:studies|trials|evidence)\s+(?:were\s+)?(?:found|identified)"
    r"|\byielded\s+no\s+evidence|\bshowed\s+no\s+difference",
    re.IGNORECASE,
)
# Clinical questions get filed under the search heading that answered them; they stay.
_QUESTION_ITEM_RE = re.compile(r"\A\s*[-*]\s+.*\?\s*\Z")
_MIN_CLINICAL_QUESTIONS = 2


def _states_clinical_questions(body: str) -> bool:
    """Report whether a block carries a list of the guideline's own clinical questions."""
    return sum(1 for line in body.splitlines() if _QUESTION_ITEM_RE.match(line)) >= _MIN_CLINICAL_QUESTIONS


# The certainty is always also in words; a run of identical symbols is index noise.
_GRADE_CIRCLE_RE = re.compile(r"[⊕⊝]+")
# A label over nothing: the renderer writes one per MAGICapp field, filled or not.
_ORPHAN_LABEL_RE = re.compile(
    r"\A(?:\*{1,2})?\s*(?:certainty of the evidence|quality of evidence|summary of findings|"
    r"effect estimates?|benefits and harms|rationale|practical (?:advice|info)|"
    r"resources and cost|patient values and preferences|applies to)\s*:?\s*(?:\*{1,2})?\Z",
    re.IGNORECASE,
)


# Journal indexing terms, beside, under, or after the label.
_KEYWORD_LABEL_RE = re.compile(
    r"\A(?:\*{1,2})?\s*(?:key\s*words?|mesh\s+terms?|index\s+terms?)\s*:?\s*(?:\*{1,2})?\s*:?\s*(?P<terms>.*)\Z",
    re.IGNORECASE | re.DOTALL,
)
# A term list: separated, short, asserting nothing.
_MAX_KEYWORD_CHARS = 500


def _is_keyword_list(text: str) -> bool:
    """Report whether a run of text is a list of index terms rather than a sentence."""
    visible = " ".join(re.sub(r"[*_]+", "", text).split())
    if not visible or len(visible) > _MAX_KEYWORD_CHARS:
        return False
    if "," not in visible and ";" not in visible and " OR " not in visible:
        return False
    return not (_SENTENCE_CLAIM_RE.search(visible) or _MEASUREMENT_RE.search(visible))


_REVIEW_CITATION_LEAD_RE = re.compile(
    r"\A(?:this|the)\s+recommendation\s+(?:was|is)\s+informed\s+by\s+the\s+following\s+"
    r"(?:systematic\s+)?reviews?\b",
    re.IGNORECASE,
)
_REVIEW_CITATION_LABEL_RE = re.compile(r"\A\**\s*(?:systematic\s+reviews?|references?)\s*\**\s*:?\Z", re.IGNORECASE)
# A DOI, registry id, "Surname AB," author list, or an unpublished-review note.
_REVIEW_CITATION_RE = re.compile(
    r"\bdoi:\s*10\.|\bPROSPERO\s+\d{4}|\bCRD\d{6,}|\([Uu]npublished\)|\b[A-Z][a-z]+\s+[A-Z]{1,3},",
)


def _drop_review_citations(markdown: str) -> str:
    """Remove the list of systematic reviews a recommendation was built from."""
    if "informed by the following" not in markdown.lower():
        return markdown
    blocks = markdown.split("\n\n")
    doomed: set[int] = set()
    for index, block in enumerate(blocks):
        if not _REVIEW_CITATION_LEAD_RE.match(" ".join(block.split())):
            continue
        doomed.add(index)
        previous = next((earlier for earlier in range(index - 1, -1, -1) if blocks[earlier].strip()), None)
        if previous is not None and _REVIEW_CITATION_LABEL_RE.match(blocks[previous].strip()):
            doomed.add(previous)
        later = index + 1
        while later < len(blocks):
            flat = " ".join(blocks[later].split())
            if not flat:
                later += 1
                continue
            if flat.startswith("#") or not _REVIEW_CITATION_RE.search(flat):
                break
            doomed.add(later)
            later += 1
    if not doomed:
        return markdown
    remainder = "\n\n".join(block for index, block in enumerate(blocks) if index not in doomed)
    return remainder if remainder.strip() else markdown


def _drop_keyword_blocks(markdown: str) -> str:
    """Remove a keywords label and the index terms belonging to it."""
    if "eyword" not in markdown and "EYWORD" not in markdown and "esh term" not in markdown.lower():
        return markdown
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        match = _KEYWORD_LABEL_RE.match(block.strip())
        if not match:
            continue
        terms = match.group("terms").strip()
        if terms:
            if _is_keyword_list(terms):
                keep[index] = False
            continue
        following = next((later for later in range(index + 1, len(blocks)) if blocks[later].strip()), None)
        if following is not None and _is_keyword_list(blocks[following]):
            keep[index] = keep[following] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


# Keyed on the appendix's own title, exactly; `_matches_skip_list` substrings would take content.
_PAPERWORK_APPENDIX_TITLES = frozenset(
    {
        "description of existing guidance on opioids for acute dental pain",
        "additional description of the methods",
        "list of stakeholder organizations contacted and responses",
    }
)
_INLINE_APPENDIX_LABEL_RE = re.compile(r"\A(?:\\?[*_])+\s*(?:appendix|annex)\s+\d+\b.*(?:\\?[*_])+\s*\Z", re.IGNORECASE)


def _drop_inline_appendices(markdown: str) -> str:
    """Remove an appendix written as a bold line in a body, when it is paperwork."""
    if "ppendix" not in markdown and "PPENDIX" not in markdown and "nnex" not in markdown.lower():
        return markdown
    lines = markdown.split("\n")
    keep = [True] * len(lines)
    dropping = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if _RENDERED_HEADING_LINE_RE.match(stripped):
            dropping = False
        elif _INLINE_APPENDIX_LABEL_RE.match(stripped):
            dropping = _skip_lookup_key(stripped.replace("\\", "")) in _PAPERWORK_APPENDIX_TITLES
        keep[index] = not dropping
    if all(keep):
        return markdown
    return "\n".join(line for line, kept in zip(lines, keep, strict=True) if kept)


# Numbering AND journal signature together - numbered items are also how evidence narrative is
# written.
_CITATION_ENTRY_RE = re.compile(r"\A\s*e?\d+\.\s+\S")
_JOURNAL_SIGNATURE_RE = re.compile(r"\b(?:19|20)\d{2};\s*\d+|\bdoi:\s*\S|\bPMID:|\bPMCID:", re.IGNORECASE)
_MIN_CITATION_BLOCK_ENTRIES = 2


def _drop_citation_blocks(markdown: str) -> str:
    """Remove a block that is nothing but numbered journal citations."""
    if not _JOURNAL_SIGNATURE_RE.search(markdown):
        return markdown
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        entries = [line for line in block.split("\n") if line.strip()]
        if len(entries) < _MIN_CITATION_BLOCK_ENTRIES:
            continue
        if not all(_CITATION_ENTRY_RE.match(entry) for entry in entries):
            continue
        if all(_JOURNAL_SIGNATURE_RE.search(entry) for entry in entries):
            keep[index] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


# Safe because the next block must carry a DOI, PMID or journal volume; a directive disqualifies.
_PUBLICATION_ANNOUNCEMENT_RE = re.compile(r"\bpublish(?:ed|ation)\b", re.IGNORECASE)
_CITATION_SIGNATURE_RE = re.compile(r"\bdoi\.org/|\bdoi:\s*\S|\bPMID:|\bPMCID:|\b(?:19|20)\d{2};\s*\w", re.IGNORECASE)
_ANNOUNCEMENT_DIRECTIVE_RE = re.compile(
    r"\bwe (?:recommend|suggest)\b|\b(?:is|are) recommended\b|\bshould be\b", re.IGNORECASE
)
_MAX_ANNOUNCEMENT_CHARS = 250


def _drop_publication_announcements(markdown: str) -> str:
    """Remove a "this was published in ... :" line and the citation under it."""
    if not _PUBLICATION_ANNOUNCEMENT_RE.search(markdown):
        return markdown
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        text = _EMPHASIS_RE.sub("", " ".join(block.split())).strip()
        if not text.endswith(":") or len(text) > _MAX_ANNOUNCEMENT_CHARS:
            continue
        if not _PUBLICATION_ANNOUNCEMENT_RE.search(text) or _ANNOUNCEMENT_DIRECTIVE_RE.search(text):
            continue
        following = next((later for later in range(index + 1, len(blocks)) if blocks[later].strip()), None)
        if following is None or not _CITATION_SIGNATURE_RE.search(blocks[following]):
            continue
        keep[index] = keep[following] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


# Three conditions together; each alone is common in evidence narrative.
_BIBLIOGRAPHY_OPENING_RE = re.compile(r"\A[A-Z][A-Za-z.,&'\- ]{0,70}\((?:19|20)\d{2}[a-z]?\)")
_BIBLIOGRAPHY_CLOSING_RE = re.compile(
    r"(?:[A-Z][\w\u2019'\- ]+:\s*[^.]{2,70}\.?|\b\d+\s*\([^)]{1,12}\)\s*:\s*\d+[-\u2013]?\d*\.?"
    r"|\bLondon\b|\bGeneva\b|\bCanberra\b)"
    r"(?:\s*\[[^\]]{0,60}\]|\s*Accessed:?[^.]{0,40}\.?)*\s*\Z"
)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_REPORTING_VERB_RE = re.compile(
    r"\b(?:suggest|show|found|find|includ|report|investigat|conduct|randomis|randomiz|trial"
    r"|demonstrat|assess|compar|evaluat|examin|concluded|observ|analys)\w*\b",
    re.IGNORECASE,
)


def _drop_bibliography_blocks(markdown: str) -> str:
    """Remove a block whose every line is a citation of another publication."""
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        stripped = block.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("|"):
            continue
        # Judged on visible text - addresses and emphasis markers hide the line's shape.
        plain = _EMPHASIS_RE.sub("", _MARKDOWN_LINK_RE.sub(r"\1", stripped))
        lines = [line.strip() for line in plain.split("\n") if line.strip()]
        if not lines or _REPORTING_VERB_RE.search(plain):
            continue
        if all(_BIBLIOGRAPHY_OPENING_RE.match(line) and _BIBLIOGRAPHY_CLOSING_RE.search(line) for line in lines):
            keep[index] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


# The whole block must be the date; superscript ordinals fold down first.
_SUPERSCRIPT_LETTERS = str.maketrans("ᵗʰˢⁿᵈʳᵉ", "thsndre")
_MONTH_NAMES = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_DATELINE_RE = re.compile(
    rf"\A(?:\d{{1,2}}(?:st|nd|rd|th)?\s+)?{_MONTH_NAMES}\.?,?\s*(?:\d{{1,2}},?\s*)?(?:19|20)\d{{2}}\.?\Z",
    re.IGNORECASE,
)
_MAX_DATELINE_CHARS = 40


def _drop_dateline_blocks(markdown: str) -> str:
    """Remove a block whose whole text is a date."""
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        text = _EMPHASIS_RE.sub("", " ".join(block.split())).strip().translate(_SUPERSCRIPT_LETTERS)
        if text and len(text) <= _MAX_DATELINE_CHARS and _DATELINE_RE.match(text):
            keep[index] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


# The whole block must be links, so prose merely containing one is out of reach.
_DOCUMENT_LINK_LINE_RE = re.compile(r"\A.{1,240}?:\s*\[[^\]]+\]\(https?://[^)]+\)[.\w]{0,8}\s*\Z")
_DOCUMENT_LIST_LABEL_RE = re.compile(r"\A(?:\\?[*_]){2}\s*[A-Z][A-Za-z ]{2,60}\s*(?:\\?[*_]){2}\Z")


def _drop_document_link_lists(markdown: str) -> str:
    """Remove a block of "name: link" lines, and the bold label above it."""
    if "](http" not in markdown:
        return markdown
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        lines = [line.strip() for line in block.strip().split("\n") if line.strip()]
        if not lines or not all(_DOCUMENT_LINK_LINE_RE.match(line) for line in lines):
            continue
        keep[index] = False
        previous = next((earlier for earlier in range(index - 1, -1, -1) if blocks[earlier].strip()), None)
        if previous is not None and _DOCUMENT_LIST_LABEL_RE.match(blocks[previous].strip()):
            keep[previous] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


def _drop_grade_circles(markdown: str) -> str:
    """Remove GRADE's certainty circles, leaving the certainty written in words."""
    if "⊕" not in markdown and "⊝" not in markdown:
        return markdown
    # The circle usually trails a word with a space in front of it, so the space goes too.
    return re.sub(r"[  ]*[⊕⊝]+", "", markdown)


def _drop_orphan_labels(markdown: str) -> str:
    """Remove a field label that has nothing under it."""
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        if not _ORPHAN_LABEL_RE.match(block.strip()):
            continue
        following = next((blocks[later] for later in range(index + 1, len(blocks)) if blocks[later].strip()), None)
        # Publishers write their own headings inside these fields; content after a heading is not
        # orphaned.
        if following is None or _ORPHAN_LABEL_RE.match(following.strip()):
            keep[index] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


def _drop_search_method_blocks(markdown: str) -> str:
    """Remove a heading describing a database search, and everything under it."""
    if "#" not in markdown:
        return markdown
    lines = markdown.split("\n")
    doomed: set[int] = set()
    index = 0
    while index < len(lines):
        match = _RENDERED_HEADING_RE.match(lines[index])
        if not match:
            index += 1
            continue
        heading = _skip_lookup_key(_plain_heading(match.group("text"), link_mode=LinkMode.KEEP))
        if not _SEARCH_METHOD_HEADING_RE.match(heading):
            index += 1
            continue
        depth = len(match.group("hashes"))
        end = index + 1
        while end < len(lines):
            later = _RENDERED_HEADING_RE.match(lines[end])
            if later and len(later.group("hashes")) <= depth:
                break
            end += 1
        body = "\n".join(lines[index + 1 : end])
        visible = " ".join(re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body).split())
        # A bare heading whose content is in the next fragment stays - taking it strands the
        # content.
        if visible and not _SEARCH_BLOCK_CONTENT_RE.search(visible) and not _states_clinical_questions(body):
            doomed.update(range(index, end))
        index = end
    if not doomed:
        return markdown
    return "\n".join(line for number, line in enumerate(lines) if number not in doomed)


def _drop_number_only_captions(markdown: str) -> str:
    """Remove a bold caption that is a table number with no title after it."""
    if "**" not in markdown:
        return markdown
    blocks = markdown.split("\n\n")
    kept = [block for block in blocks if not _NUMBER_ONLY_CAPTION_RE.match(block.strip())]
    return "\n\n".join(kept) if len(kept) != len(blocks) else markdown


def _strip_question_numbers_from_headings(markdown: str) -> str:
    """Strip a question number and Executive Summary label from a heading inside a body."""
    if "PICO" not in markdown and "xecutive" not in markdown:
        return markdown
    lines = markdown.split("\n")
    for index, line in enumerate(lines):
        match = _RENDERED_HEADING_RE.match(line)
        if not match:
            continue
        remainder = _QUESTION_NUMBER_LABEL_RE.sub("", match.group("text")).strip(" *_:.\\").strip()
        if remainder and remainder != match.group("text"):
            lines[index] = f"{match.group('hashes')} {remainder}"
    return "\n".join(lines)


def _drop_source_credits(markdown: str) -> str:
    """Remove a table footnote whose whole content is where the table came from."""
    blocks = markdown.split("\n\n")
    keep = []
    dropped = False
    for block in blocks:
        stripped = block.strip()
        body = _FOOTNOTE_MARKER_RE.sub("", stripped, count=1).strip() if stripped else ""
        visible = " ".join(re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body).split())
        # A citation instruction needs no footnote marker to be a credit line.
        if _AFFILIATION_FOOTNOTE_RE.match(stripped):
            dropped = True
            continue
        plain = " ".join(re.sub(r"[*_]+", " ", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", stripped)).split())
        label = _CITATION_LABEL_RE.match(plain) if plain else None
        # Applied to what follows the label only: "Suggested" contains "suggest".
        if label and not _FOOTNOTE_CONTENT_RE.search(plain[label.end() :]):
            dropped = True
            continue
        if (
            visible
            and _FOOTNOTE_MARKER_RE.match(stripped)
            and _SOURCE_CREDIT_RE.match(visible)
            and not _FOOTNOTE_CONTENT_RE.search(visible)
        ):
            dropped = True
            continue
        keep.append(block)
    if not dropped:
        return markdown
    return "\n\n".join(block for block in keep if block.strip())


def _drop_approval_stamps(markdown: str) -> str:
    """Remove a sentence recording who approved a recommendation and when."""
    blocks, changed = markdown.split("\n\n"), False
    for index, block in enumerate(blocks):
        stripped = block.strip()
        if not stripped or stripped.startswith(("#", "|", ">", "    ", "**")):
            continue
        italic = _WHOLE_BLOCK_ITALIC_RE.match(stripped)
        inner = italic.group("body") if italic else stripped
        if inner.startswith(("-", "*")):
            continue
        masked, targets = _mask_link_targets(inner)
        restored = [_unmask_link_targets(sentence, targets) for sentence in _SENTENCE_SPLIT_RE.split(masked)]
        keep = [
            not (
                _APPROVAL_STAMP_RE.match(sentence.strip())
                and not _SENTENCE_CLAIM_RE.search(re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", sentence))
            )
            for sentence in restored
        ]
        if all(keep):
            continue
        remainder = " ".join(sentence for sentence, kept in zip(restored, keep, strict=True) if kept).strip()
        blocks[index] = f"*{remainder}*" if remainder and italic else remainder
        changed = True
    if not changed:
        return markdown
    return "\n\n".join(block for block in blocks if block.strip())


# ---------------------------------------------------------------------------
# Section keep/drop policy
# ---------------------------------------------------------------------------

# Container words ("appendix", "annex") are deliberately absent: location, not content.
_SKIP_SECTION_HEADINGS = frozenset(
    {
        "about this guideline",
        "applicability issues",
        "development of the guidelines",
        "glossary",
        "glossary and abbreviations",
        "how the guideline was made",
        "how this guideline was created",
        "how this guideline was made",
        "how to use these recommendations/understanding the recommendations",
        "methodology",
        "methods",
        "methods: how this guideline was created",
        "patient version",
        "research implication",
        "research implications",
        "what's new?",
        "abbreviations",
        "abbreviations and acronyms",
        "about the guidelines",
        "acknowledgements",
        "acknowledgments",
        "acronyms and abbreviations",
        "author group",
        "authors",
        "authors and disclosures",
        "authorship",
        "authorship and contributions",
        "authorship, contributions and acknowledgments",
        "additional resources",
        "companion resources",
        "other resources",
        "bmj rapid recommendations methods and process",
        "conflicts of interest",
        "contact information",
        "contact the guideline team",
        "copyright and disclaimer",
        "declarations of interest",
        "ethical approval",
        "funding",
        "funding statement",
        "guatantor",
        "informed consent",
        "guarantor",
        "guarantors",
        "guideline amendments",
        "guideline translations",
        "how to access and use the guideline",
        "how to access and use this guideline",
        "how to cite",
        "how to use these guidelines",
        "how to use this guideline",
        "ongoing feedback",
        "process report",
        "public consultation",
        "guideline development methods",
        "evidence retrieval, synthesis, and assessment",
        "evidence retrieval, synthesis and assessment",
        "formulating questions and selecting outcomes",
        "evidence for the guideline",
        "evidence for the guidelines",
        "timeline of guideline development activities",
        "declarations and management of interests",
        "declarations of interest for each guideline question",
        "managing declarations of interest",
        "declarations of interest register",
        "declaration of interest register",
        "interest-holders",
        "interest holders",
        "magicapp",
        "magicapp tabs",
        "using magicapp",
        "suggested citation",
        "dissemination",
        "how the guidelines were made",
        "how the guidelines were developed",
        "how the guideline was developed",
        "methods: how this guideline was made",
        "other information",
        "additional information",
        "further information",
        "administrative report",
        "working committee",
        "working committees",
        "future developments",
        "future development",
        "key areas for future development",
        "resources",
        "amendments to the guidelines",
        "amendments to the guideline",
        "define section headings and amendments to 2010 guidelines",
        "summary table of relevant infection prevention and control resources",
        "bmj rapid recommendations: background and methods",
        "background and methods: bmj rapid recommendations",
        "background and methods for bmj-rapidrecs",
        "primary care rapid recommendations: background and methods",
        "background and methods of the wikirecs project",
        "figures, tables and supplementary information",
        "pdf of the guideline",
        "pdf of the guidelines",
        "keyword",
        "keywords",
        "research in context",
        "applicability issue",
        "dissemination and implementation of the guideline",
        "dissemination and implementation of the guidelines",
        "disclosure",
        "disclosures",
        "disclosure of interest",
        "disclosure of interests",
        "disclaimer",
        "disclaimers",
        "search for existing relevant guidelines and systematic reviews",
        "limitations of searches",
        "guideline development methodology",
        "public consultation feedback",
        "evidence reports",
        "topics under development",
        "reading guide",
        "reference list",
        "references",
        "what's new",
        "version history",
        "scope and audience",
        "scope and purpose",
        "scope and purpose of the guidelines",
        "objective of the guidelines",
        "search strategies",
        "supporting guideline reports",
        "update and further research",
        "updating of the guideline",
        "updating of the guidelines",
        "updating the guideline",
        "updating the guidelines",
        "updating the recommendation",
        "updating the recommendations",
    }
)

_EMPHASIS_RE = re.compile(r"[*_]+")

_HEADING_LABEL_RE = re.compile(
    r"\A(?:(?:appendix|appendices|annex(?:es)?|app|attachment|supplement(?:ary)?)\b\.?\s*)?"
    r"(?:"
    r"[a-z]\s*[.\-–—:)]"  # a letter only when punctuation follows it: "A.", "E -"
    r"|\d+(?:\.\d+)*\s*[.\-–—:)]?"  # a number, where the punctuation is optional: "7.2", "6."
    r")?"
    r"\s*"
)

# Kept narrow: nothing that could plausibly appear in a clinical heading. `_is_skipped_section`'s
# guards still apply.
_SKIP_SECTION_TOPICS = frozenset(
    {
        "acknowledg",
        "involved in the preparation",
        "guideline development group",
        "technical report",
        "methods and processes",
        "advisory panel",
        "technical team",
        "panel member",
        "working group",
        "steering committee",
        "meeting attendance",
        "contributor",
        "guidelines development group",
        "steering group",
        "review group",
        "working party",
        "project team",
        "terms of reference",
        "search strategy",
        "living evidence update",
        "forest plot",
        "conflict of interest",
        "conflicts of interest",
        "conflicting interest",
        "competing interest",
        "declaration of interest",
        "declared interest",
    }
)

# A ceiling on what a heading alone may discard, measured as readable text, not raw HTML.
MAX_SKIPPED_SECTION_CHARS = 150_000

# Both required, more than once, so a roster mentioning GRADE in passing is not a table of
# judgements.
_GRADE_CERTAINTY_RE = re.compile(r"certainty of (?:the )?evidence", re.IGNORECASE)

_GRADE_EFFECT_RE = re.compile(r"favours (?:this|other) option|favours the (?:intervention|comparator)", re.IGNORECASE)

# The key to a guideline's own recommendation labels; it appears under five skip-listed names, so
# the body is asked.
_RECOMMENDATION_KEY_RE = re.compile(
    r"indicates a (?:strong|conditional|weak) recommendation|"
    r"symbol denotes an? [\w -]*recommendation|"
    r"level\s*\d\s*[\"“'‘]?\s*we\s+(?:recommend|suggest)",
    re.IGNORECASE,
)

# The subject must be the guideline or its recommendations, not prose that merely contains the
# words.
_GUIDELINE_SCOPE_RE = re.compile(
    r"(?:these|this|the)\s+(?:\w+\s+){0,3}(?:guidelines?|recommendations?|document)\s+"
    r"(?:only\s+)?(?:refers?\s+only\s+to|refers?\s+to|applies\s+to|apply\s+to|"
    r"does\s+not\s+(?:apply|cover|address)|do\s+not\s+(?:apply|cover|address)|covers?)"
    r"|scope\s+of\s+(?:this|these)\s+(?:\w+\s+){0,2}guidelines?\s+"
    r"(?:is|was|focuses|focused|covers?|includes?)"
    r"|no\s+(?:guidance|recommendations?|advice)\s+(?:is|are)\s+(?:given|provided|made|included)\b",
    re.IGNORECASE,
)

# A review's conclusion, not somebody's role in one - disclosure tables mention trials without
# reporting results.
_EVIDENCE_FINDING_RE = re.compile(
    r"yielded no evidence|no evidence was (?:found|identified)|did not show|"
    r"no (?:studies|trials|RCTs) (?:were )?(?:found|identified)|showed no difference",
    re.IGNORECASE,
)

# PICO frame headings filed under skipped parents - the only place the corpus scopes
# recommendations. Matched via `_skip_lookup_key`.
_PICO_FRAME_HEADINGS = frozenset(
    {
        "population",
        "populations",
        "intervention",
        "interventions",
        "comparator",
        "comparators",
        "outcome",
        "outcomes",
        "outcomes considered for guideline questions",
        "guideline scope",
        "guideline recommendation questions",
        "recommendation questions",
        "clinical questions",
        "values and preferences",
        "patient values and preferences",
    }
)

# Keyed on the section holding nothing but the pointer, so it cannot reach one that also carries
# guidance.
_POINTER_ONLY_RE = re.compile(
    r"\A(?:please\s+)?(?:also\s+)?(?:click|see|refer\s+to|go\s+to)\b"
    r"|\A(?:further|more|additional)\s+(?:information|detail|guidance|resources?)\b"
    r"|\A(?:to|for)\s+(?:access|view|download|read|obtain|find)\b",
    re.IGNORECASE,
)

# A pointer only directs, never asserts: a sentence carrying a measurement is prose.
_MAX_POINTER_CHARS = 200

_MEASUREMENT_RE = re.compile(
    r"\d+\s*(?:%|mg|kg|ml|mL|g\b|mcg|µg|mmol|mmHg|IU\b|hours?|days?|weeks?|months?|years?)",
    re.IGNORECASE,
)


def _has_recommendation(section: dict[str, Any]) -> bool:
    """Report whether a section or any descendant carries a real recommendation.

    INFO and NO_STRENGTH do not count (an editorial box and a navigation blurb both carry them);
    NOTSET does - real ungraded recommendations use it.
    """
    for recommendation in section.get("recommendations") or []:
        if isinstance(recommendation, dict):
            strength = str(recommendation.get("strength") or "").strip().upper()
            if strength not in _NON_GUIDANCE_STRENGTHS:
                return True
    return any(isinstance(child, dict) and _has_recommendation(child) for child in section.get("subSections") or [])


def _fragment_text_chars(html_text: str) -> int:
    """Return the readable-text length of an HTML fragment."""
    if not html_text.strip():
        return 0
    return len(lxml_html.fragment_fromstring(html_text, create_parent="div").text_content())


def _subtree_text_chars(section: dict[str, Any]) -> int:
    """Return the readable-text size of a section's own body plus every descendant's."""
    total = _fragment_text_chars(str(section.get("text") or ""))
    for child in section.get("subSections") or []:
        if isinstance(child, dict):
            total += _subtree_text_chars(child)
    return total


def _visible_subtree_text(section: dict[str, Any]) -> str:
    """Return a section's readable text plus every descendant's, as one string."""
    parts = [str(section.get("text") or "")]
    for child in section.get("subSections") or []:
        if isinstance(child, dict):
            parts.append(_visible_subtree_text(child))
    joined = " ".join(part for part in parts if part.strip())
    if not joined.strip():
        return ""
    # Joined with spaces - `text_content()` welds across cell boundaries and phrase search fails.
    root = lxml_html.fragment_fromstring(joined, create_parent="div")
    return " ".join(" ".join(root.itertext()).split())


def _is_pointer_only(section: dict[str, Any], body: str) -> bool:
    """Report whether a section's whole content is one sentence pointing somewhere else.

    A body that rendered away to nothing counts too; a heading that never carried text is an outline
    level and stays.
    """
    if section.get("subSections") or section.get("picos") or (section.get("recommendations") or []):
        return False
    if not body:
        # No body, no children, no recommendations - a dead heading, not an outline level.
        return True
    if len(body) > _MAX_POINTER_CHARS:
        return False
    # A link's address is not part of the sentence a reader sees.
    visible = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body).strip()
    return bool(_POINTER_ONLY_RE.match(visible)) and not _MEASUREMENT_RE.search(visible)


def _is_draft_recommendation(recommendation: dict[str, Any]) -> bool:
    """Report whether the publisher marks this recommendation as a draft."""
    visible = _visible_subtree_text({"text": str(recommendation.get("text") or "")})
    if _DRAFT_RECOMMENDATION_RE.match(visible):
        return True
    remarks = _visible_subtree_text({"text": str(recommendation.get("remarks") or "")})
    return bool(_UNAPPROVED_DRAFT_RE.search(remarks))


def _holds_evidence_table(section: dict[str, Any]) -> bool:
    """Report whether a section carries a GRADE evidence-to-decision table."""
    text = _visible_subtree_text(section)
    return len(_GRADE_CERTAINTY_RE.findall(text)) >= 2 and len(_GRADE_EFFECT_RE.findall(text)) >= 2


def _defines_recommendation_strength(section: dict[str, Any]) -> bool:
    """Report whether a section explains what this guideline's recommendation labels mean."""
    return bool(_RECOMMENDATION_KEY_RE.search(_visible_subtree_text(section)))


def _states_guideline_scope(section: dict[str, Any]) -> bool:
    """Report whether a section says who or what the guideline applies to."""
    own_text = str(section.get("text") or "")
    if not own_text.strip():
        return False
    return bool(_GUIDELINE_SCOPE_RE.search(_visible_subtree_text({"text": own_text})))


def _reports_evidence_findings(section: dict[str, Any]) -> bool:
    """Report whether a section states what a search or a study actually found."""
    return bool(_EVIDENCE_FINDING_RE.search(_visible_subtree_text(section)))


# "Protocol" is a review plan for some publishers, imaging protocols for others; the opening
# decides.
_PROTOCOL_HEADINGS = frozenset({"protocol", "protocols"})
_STUDY_PROTOCOL_OPENING_RE = re.compile(r"\A\s*(?:abstract|introduction|guideline protocol)\b", re.IGNORECASE)


def _is_study_protocol(section: dict[str, Any], heading: str) -> bool:
    """Report whether a Protocol section is the review's own plan rather than clinical steps."""
    if _skip_lookup_key(heading) not in _PROTOCOL_HEADINGS:
        return False
    return bool(_STUDY_PROTOCOL_OPENING_RE.match(_visible_subtree_text(section).lstrip()))


def _is_skipped_section(section: dict[str, Any], heading: str, institution: str = "", short_code: str = "") -> bool:
    """Report whether a section is front/back matter that can be dropped.

    Anything holding a recommendation is kept whatever it is called, which is what makes the heading
    lists safe to extend.
    """
    # Publisher rules beat the content guards: within one publisher a heading IS reliable.
    key = _skip_lookup_key(heading)
    # Wins over everything: a reader saw clinical content in this exact section.
    if key in _GUIDELINE_KEEP_HEADINGS.get(short_code, frozenset()):
        return False
    if key in _INSTITUTION_SKIP_HEADINGS.get(institution, frozenset()):
        return True
    # The narrowest rule, and like the publisher rule it takes precedence over the guards.
    if key in _GUIDELINE_SKIP_HEADINGS.get(short_code, frozenset()):
        return True
    if not _matches_skip_list(heading) and not _is_study_protocol(section, heading):
        return False
    if _has_recommendation(section):
        return False
    if _holds_evidence_table(section):
        return False
    if _defines_recommendation_strength(section):
        return False
    if _states_guideline_scope(section):
        return False
    if _reports_evidence_findings(section):
        return False
    # Front matter is small; prose recommendations are invisible to the object check, so size guards
    # them.
    return _subtree_text_chars(section) <= MAX_SKIPPED_SECTION_CHARS


def _skip_lookup_key(heading: str) -> str:
    """Reduce a heading to the form the skip list is written in."""
    key = _EMPHASIS_RE.sub("", heading).strip().lower()
    key = _HEADING_LABEL_RE.sub("", key)
    return " ".join(key.strip(" .-–—:").split())


def _matches_skip_list(heading: str) -> bool:
    """Report whether a heading names front or back matter."""
    key = _skip_lookup_key(heading)
    if not key:
        return False
    return key in _SKIP_SECTION_HEADINGS or any(topic in key for topic in _SKIP_SECTION_TOPICS)


# The markers arrive escaped - `_plain_heading` renders through `_markdown` first.
_EMPHASIS_MARKER = r"(?:\\?[*_])*"
_QUESTION_NUMBER_LABEL_RE = re.compile(
    rf"\A{_EMPHASIS_MARKER}\s*PICO\s*\d+[a-z]?{_EMPHASIS_MARKER}\s*[:.]?\s*"
    rf"(?:{_EMPHASIS_MARKER}\s*executive\s+summary\s*{_EMPHASIS_MARKER}\s*[:.]?)?\s*"
    rf"|\A{_EMPHASIS_MARKER}\s*executive\s+summary\s*{_EMPHASIS_MARKER}\s*[:.]\s*",
    re.IGNORECASE,
)


def _plain_heading(raw_heading: str, *, link_mode: LinkMode) -> str:
    """Reduce a section heading to a single line of text."""
    heading = _markdown(raw_heading, link_mode=link_mode, as_heading=True)
    heading = " ".join(heading.replace("#", " ").split())
    remainder = _QUESTION_NUMBER_LABEL_RE.sub("", heading).strip(" *_:.\\").strip()
    return remainder or heading


# ---------------------------------------------------------------------------
# Recommendation and PICO rendering
# ---------------------------------------------------------------------------

_MAX_HEADING_LEVEL = 6

# Strength values meaning "the panel assigned no GRADE strength".
_UNRATED_STRENGTHS = frozenset({"NOTSET", "NO_STRENGTH"})

# Strength value marking an editorial callout box rather than a recommendation.
_INFO_STRENGTH = "INFO"

# Used only to decide whether a skip-listed heading is rescued - see `_has_recommendation`.
_NON_GUIDANCE_STRENGTHS = frozenset({_INFO_STRENGTH, "NO_STRENGTH"})

# States the publisher no longer stands behind the text; UNDER_REVIEW and NEW_EVIDENCE are still
# current.
_DROPPED_STATUSES = frozenset({"POSSIBLY_OUTDATED"})

# Anchored at the start, so a recommendation merely mentioning a draft is untouched.
_DRAFT_RECOMMENDATION_RE = re.compile(r"\A\s*draft\b", re.IGNORECASE)

# Requires the draft word AND the not-yet-approved clause.
_UNAPPROVED_DRAFT_RE = re.compile(
    r"\bis\s+a\s+draft\s+recommendation\b[^.]*\bnot\s+yet\s+been\s+approved\b", re.IGNORECASE
)

# `WEAK` occupies GRADE's Low slot in this field; emitted raw it would mean two things in one label.
_GRADE_CERTAINTY = {
    "HIGH": "High",
    "MODERATE": "Moderate",
    "WEAK": "Low",
    "VERY_LOW": "Very low",
}

# Unlike `keyInfo.evidenceStrength`, this field uses LOW and never WEAK - a shared table would drop
# every LOW.
_OUTCOME_CERTAINTY = {
    "HIGH": "high",
    "MODERATE": "moderate",
    "LOW": "low",
    "VERY_LOW": "very low",
}

# NOTSET and an absent type mean a number of unstated kind - not an estimate to present.
_EFFECT_MEASURES = {"RR": "RR", "OR": "OR", "HR": "HR"}

# All three carry the same identifying and certainty fields, so one renderer handles them.
_OUTCOME_LISTS = ("dichotomousOutcomes", "continuousOutcomes", "nonPoolableOutcomes")

# The publisher's own label carries meaning a generic "Recommendation" heading would overwrite.
_LABEL_OPENERS = re.compile(
    r"\A(?:\d+(?:\.\d+)*\.?\s+)?"
    r"(?:"
    r"adapted\s+evidence[-\s]based\s+recommendation"
    r"|consensus[-\s]based\s+recommendation"
    r"|evidence[-\s]based\s+recommendation"
    r"|good[-\s]practice\s+statement"
    r"|expert\s+consensus\s+statement"
    r"|expert\s+opinion"
    r"|practice\s+point"
    r"|key\s+consideration"
    r"|key\s+statement"
    r"|research\s+recommendation"
    r"|statement"
    r"|recommendation"
    r")",
    re.IGNORECASE,
)

_LABEL_DECORATION_RE = re.compile(r"\A[#*\s]+|[*\s]+\Z")

# A first line that is one bold span end to end, with no "**" anywhere inside it.
_WHOLE_LINE_BOLD_RE = re.compile(r"\*\*((?:[^*]|\*(?!\*))+)\*\*")

_MAX_LABEL_CHARS = 120

# Prose carried on the recommendation itself, beyond `text` and `remarks`.
_RECOMMENDATION_PROSE = (
    ("rational", "Rationale"),
    ("advice", "Practical advice"),
    ("implementation", "Implementation"),
    ("evaluation", "Evaluation"),
    ("research", "Research needed"),
)

# Seven Evidence-to-Decision domains, emitted under labelled headings so consumers can drop what
# they don't want.
_KEY_INFO_DOMAINS = (
    ("evidence", "Certainty of the evidence"),
    ("benefits", "Benefits and harms"),
    ("preferences", "Patient values and preferences"),
    ("resources", "Resources and cost"),
    ("acceptability", "Acceptability"),
    ("feasibility", "Feasibility"),
    ("equity", "Health equity"),
)


def _intervention_arms(key_info: dict[str, Any]) -> str:
    """Summarize what a recommendation compared, as "intervention versus comparator"."""
    # Grouped by `picoId`: eight arms in one flat list would read as a comparison no trial
    # performed.
    groups: dict[Any, tuple[list[str], list[str]]] = {}
    for arm in key_info.get("interventions") or []:
        if not isinstance(arm, dict):
            continue
        name = str(arm.get("intervention") or "").strip()
        if not name:
            continue
        interventions, comparators = groups.setdefault(arm.get("picoId"), ([], []))
        element = str(arm.get("picoElement") or "").strip().upper()
        if element == "C" or arm.get("isComparator"):
            comparators.append(name)
        elif element == "I":
            interventions.append(name)
    pairs: list[str] = []
    for interventions, comparators in groups.values():
        if not interventions:
            continue
        arms = ", ".join(dict.fromkeys(interventions))
        if comparators:
            arms += f" versus {', '.join(dict.fromkeys(comparators))}"
        if arms not in pairs:
            pairs.append(arms)
    if not pairs:
        return ""
    if len(pairs) == 1:
        return pairs[0]
    return "\n".join(f"- {pair}" for pair in pairs)


def _pico_arms(recommendation: dict[str, Any], *, link_mode: LinkMode) -> str:
    """Recover what a recommendation compared from its PICOs when `keyInfo` is silent."""
    interventions: list[str] = []
    comparators: list[str] = []
    for pico in recommendation.get("picos") or []:
        if not isinstance(pico, dict):
            continue
        # Only for suppressed PICOs; rendered ones already print Intervention and Comparator below.
        if _markdown(pico.get("summary"), link_mode=link_mode).strip():
            continue
        for field, names in (("intervention", interventions), ("comparator", comparators)):
            name = " ".join(_markdown(pico.get(field), link_mode=link_mode).replace("#", " ").split())
            if name:
                names.append(name)
    if not interventions:
        return ""
    if not comparators:
        return ", ".join(dict.fromkeys(interventions))
    return f"{', '.join(dict.fromkeys(interventions))} versus {', '.join(dict.fromkeys(comparators))}"


def _split_leading_label(text: str) -> tuple[str, str]:
    """Split a publisher's own opening label off the recommendation body.

    Returns `(label, remaining_text)`, falling back to `("Recommendation", text)`; anything that
    would consume the whole body is left alone.
    """
    original = text
    first_line, newline, rest = text.partition("\n")
    whole_bold = _WHOLE_LINE_BOLD_RE.fullmatch(first_line.strip())
    if whole_bold:
        first_line = whole_bold.group(1).strip()
        text = first_line + newline + rest
    candidate = " ".join(_LABEL_DECORATION_RE.sub("", first_line).split())
    if not _LABEL_OPENERS.match(candidate):
        return "Recommendation", original

    # Cut by position - labels carry non-breaking spaces, so the normalized candidate is not
    # findable verbatim. The colon splits label from statement before the length check.
    colon = first_line.find(":")
    if 0 <= colon < len(first_line) - 1:
        cut, label = colon + 1, candidate.split(":", 1)[0].strip()
    else:
        cut, label = len(first_line), candidate.rstrip(":")
    if len(label) > _MAX_LABEL_CHARS:
        return "Recommendation", original

    consumed = text[:cut]
    remainder = text[cut:].lstrip(": \t\xa0").lstrip()
    # Emphasis pairs do not interleave: the prefix's unpaired `**` closes at the body's first `**`.
    if consumed.count("**") % 2 and remainder.count("**") % 2:
        first_marker = remainder.find("**")
        remainder = (remainder[:first_marker] + remainder[first_marker + 2 :]).lstrip()
    elif not remainder.startswith("**") and remainder.startswith("*") and consumed.count("*") % 2:
        remainder = remainder[1:].lstrip()
    if not remainder.strip():
        return "Recommendation", original
    label = " ".join(label.replace("**", " ").split())
    return label.strip("*").strip(":.-–— *\xa0"), remainder


def _recommendation_markdown(
    recommendation: dict[str, Any], *, level: int, link_mode: LinkMode, moved_picos: frozenset[int] = frozenset()
) -> str:
    """Render one recommendation as markdown, with its GRADE strength if set."""
    review_status = str(recommendation.get("status") or "").strip().upper()
    if review_status in _DROPPED_STATUSES:
        logger.debug("Dropping recommendation with review status %s", review_status)
        return ""
    if _is_draft_recommendation(recommendation):
        logger.debug("Dropping recommendation the publisher marks a draft")
        return ""
    text = _markdown(recommendation.get("text"), link_mode=link_mode, base_level=level)
    if not text:
        return ""
    strength = str(recommendation.get("strength") or "").strip().upper()

    # INFO marks editorial callout boxes; as recommendations they would read as clinical advice.
    if strength == _INFO_STRENGTH:
        return text

    # The publisher's label is kept - often the only place the record says the item is ungraded.
    base_label, text = _split_leading_label(text)
    if not text:
        return ""

    # NOTSET and NO_STRENGTH mean no strength assigned; omitted, not reported.
    label = base_label if not strength or strength in _UNRATED_STRENGTHS else f"{base_label} ({strength})"
    key_info = recommendation.get("keyInfo")
    key_info = key_info if isinstance(key_info, dict) else {}
    certainty = _GRADE_CERTAINTY.get(str(key_info.get("evidenceStrength") or "").strip().upper())
    if certainty:
        label = f"{label} — certainty of evidence: {certainty}"

    # A heading, not bold text: navigable, chunkable, and leading body markup still renders.
    parts = [f"{'#' * min(level, _MAX_HEADING_LEVEL)} {label}"]

    # The clinical situation may live only in this heading, not restated in the body.
    scope = _markdown(recommendation.get("heading"), link_mode=link_mode)
    scope = " ".join(scope.replace("#", " ").split())
    if scope and scope not in text:
        parts.append(f"*Applies to:*\n\n{scope}")

    parts.append(text)

    arms = _intervention_arms(key_info) or _pico_arms(recommendation, link_mode=link_mode)
    if arms:
        parts.append(f"*Compared:*\n\n{arms}")

    # On its own line: a leading markdown table must start at line start.
    remarks = _markdown(recommendation.get("remarks"), link_mode=link_mode, base_level=level)
    if remarks:
        parts.append(f"*Remarks:*\n\n{remarks}")

    for field, heading in _RECOMMENDATION_PROSE:
        body = _markdown(recommendation.get(field), link_mode=link_mode, base_level=level)
        if body:
            parts.append(f"*{heading}:*\n\n{body}")

    for field, heading in _KEY_INFO_DOMAINS:
        body = _markdown(key_info.get(field), link_mode=link_mode, base_level=level)
        research = _markdown(key_info.get(f"{field}ResearchEvidence"), link_mode=link_mode, base_level=level)
        # Hand-added local applicability, not a duplicate of the domain judgment.
        extra = _markdown(key_info.get(f"{field}AdditionalConsideration"), link_mode=link_mode, base_level=level)
        if body:
            parts.append(f"*{heading}:*\n\n{body}")
        if research:
            parts.append(f"*{heading} — research evidence:*\n\n{research}")
        if extra:
            parts.append(f"*{heading} — additional considerations:*\n\n{extra}")

    rendered_picos: set[int] = set()
    for pico in recommendation.get("picos") or []:
        if not isinstance(pico, dict):
            continue
        pico_id = pico.get("picoId")
        if pico_id not in moved_picos or pico_id in rendered_picos:
            continue
        rendered = _pico_markdown(pico, link_mode=link_mode, base_level=min(level, _MAX_HEADING_LEVEL))
        if rendered:
            rendered_picos.add(pico_id)
            parts.append(rendered)

    return _drop_orphan_labels("\n\n".join(parts))


def _plain_number(value: Any) -> str:
    """Render a stored number the way a person would write it: 75.0 as 75, 2.37 as 2.37."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return ""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _outcome_estimates(pico: dict[str, Any]) -> str:
    """One line per outcome: the numbers the panel weighed, which the prose often omits."""
    outcomes = pico.get("outcomes")
    if not isinstance(outcomes, dict):
        return ""
    lines: list[str] = []
    for name in _OUTCOME_LISTS:
        for row in outcomes.get(name) or []:
            if not isinstance(row, dict) or row.get("isHidden"):
                continue
            title = " ".join(str(row.get("outcome") or "").split())
            if not title:
                continue
            # Publishers use bare asterisks as footnote markers; unescaped they open unclosed
            # emphasis.
            title = title.replace("*", "\\*")
            parts: list[str] = []
            measure = _EFFECT_MEASURES.get(str(row.get("relativeEffectType") or "").strip().upper())
            effect = _plain_number(row.get("relativeEffect"))
            if measure and effect:
                low, high = (
                    _plain_number(row.get("relativeEffectConfidenceLow")),
                    _plain_number(row.get("relativeEffectConfidenceHigh")),
                )
                # The confidence type is spelled CI95 in the data and 95% CI in prose.
                interval = f" (95% CI {low} to {high})" if low and high else ""
                parts.append(f"{measure} {effect}{interval}")
            participants = _plain_number(row.get("interventionTotalParticipants"))
            if participants and participants != "0":
                parts.append(f"{participants} participants")
            studies = " ".join(str(row.get("interventionStudies") or "").split())
            if studies and studies != "0":
                parts.append(f"{studies} study" if studies == "1" else f"{studies} studies")
            certainty = _OUTCOME_CERTAINTY.get(str(row.get("qualityOfEvidenceLevel") or "").strip().upper())
            if certainty:
                parts.append(f"certainty {certainty}")
            if not parts:
                continue
            lines.append(f"- {title}: {', '.join(parts)}")
    return "\n".join(lines)


def _pico_markdown(pico: dict[str, Any], *, link_mode: LinkMode, base_level: int) -> str:
    """Render the readable head of a PICO: its question and its findings."""
    question = [
        (label, _markdown(pico.get(field), link_mode=link_mode, base_level=base_level))
        for field, label in (
            ("population", "Population"),
            ("intervention", "Intervention"),
            ("comparator", "Comparator"),
        )
    ]
    question = [(label, value) for label, value in question if value]
    summary = _markdown(pico.get("summary"), link_mode=link_mode, base_level=base_level)
    estimates = _outcome_estimates(pico)
    # A question with no answer is a topical match delivering nothing; a summary alone has lost its
    # population.
    if not question or not (summary or estimates):
        return ""
    # The summary goes on its own line below the label: a markdown table must begin at line start.
    lines = "\n".join(f"- {label}: {value}" for label, value in question)
    block = f"**Evidence question**\n\n{lines}"
    if summary:
        block = f"{block}\n\n*Summary of findings:*\n\n{summary}"
    return f"{block}\n\n*Effect estimates:*\n\n{estimates}" if estimates else block


def _is_labelled_recommendation(block: str) -> bool:
    """Report whether a rendered block was emitted as a recommendation.

    A heading means a recommendation; bare text is an INFO box; "" was dropped.
    """
    return block.startswith("#")


def _moved_pico_ids(payload: dict[str, Any], *, link_mode: LinkMode) -> frozenset[int]:
    """PICOs that render under their owning recommendation instead of their section.

    A PICO moves only when exactly one rendering recommendation owns it, both copies render, and the
    owner sits outside the listing section's subtree; shared PICOs stay put.
    """
    listed: dict[int, tuple[str, dict[str, Any]]] = {}
    owners: dict[int, list[tuple[str, dict[str, Any], dict[str, Any]]]] = {}

    def visit(sections: Any, path: str) -> None:
        for index, section in enumerate(sections or []):
            if not isinstance(section, dict):
                continue
            child_path = f"{path}.{index}"
            for pico in section.get("picos") or []:
                if isinstance(pico, dict) and pico.get("picoId") is not None:
                    listed.setdefault(pico["picoId"], (child_path, pico))
            for recommendation in section.get("recommendations") or []:
                if not isinstance(recommendation, dict):
                    continue
                for pico in recommendation.get("picos") or []:
                    if isinstance(pico, dict) and pico.get("picoId") is not None:
                        owners.setdefault(pico["picoId"], []).append((child_path, recommendation, pico))
            visit(section.get("subSections"), child_path)

    visit(payload.get("sections"), "s")
    for recommendation in payload.get("recommendations") or []:
        if not isinstance(recommendation, dict):
            continue
        for pico in recommendation.get("picos") or []:
            if isinstance(pico, dict) and pico.get("picoId") is not None:
                owners.setdefault(pico["picoId"], []).append(("root", recommendation, pico))

    moved: set[int] = set()
    for pico_id, (where, section_copy) in listed.items():
        owning = owners.get(pico_id) or []
        if len(owning) != 1:
            continue
        owner_path, recommendation, recommendation_copy = owning[0]
        if owner_path == where or owner_path.startswith(f"{where}."):
            continue
        # Both copies must render, or the move adds or loses content.
        if not _pico_markdown(section_copy, link_mode=link_mode, base_level=2):
            continue
        if not _pico_markdown(recommendation_copy, link_mode=link_mode, base_level=2):
            continue
        # Mirrors `_recommendation_markdown`'s early exits: no labelled block, no PICO.
        if str(recommendation.get("status") or "").strip().upper() in _DROPPED_STATUSES:
            continue
        if _is_draft_recommendation(recommendation):
            continue
        if str(recommendation.get("strength") or "").strip().upper() == _INFO_STRENGTH:
            continue
        text = _markdown(recommendation.get("text"), link_mode=link_mode, base_level=2)
        if not text or not _split_leading_label(text)[1]:
            continue
        moved.add(pico_id)
    return frozenset(moved)


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------

# Bold headings have no level, so they are deeper than any real one.
_INLINE_BOLD_HEADING_LEVEL = 7
# Anchored: bold end to end is a heading, merely containing bold is not.
_INLINE_BOLD_LINE_RE = re.compile(r"\A(?:\\?\*){2}\s*(.+?)\s*(?:\\?\*){2}\Z")


def _drop_inline_sections(markdown: str, short_code: str) -> str:
    """Remove a heading written inside a section body, and the block it opens."""
    headings = _GUIDELINE_INLINE_SKIP_HEADINGS.get(short_code, frozenset())
    if not headings:
        return markdown
    lines = markdown.split("\n")
    keep = [True] * len(lines)
    dropping = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        match = _RENDERED_HEADING_RE.match(stripped)
        if match:
            level, text = len(match.group("hashes")), match.group("text")
        else:
            bold = _INLINE_BOLD_LINE_RE.match(stripped)
            level, text = (_INLINE_BOLD_HEADING_LEVEL, bold.group(1)) if bold else (0, "")
        if level:
            if dropping and level <= dropping:
                dropping = 0
            if not dropping and _skip_lookup_key(text) in headings:
                dropping = level
        keep[index] = not dropping
    if all(keep):
        return markdown
    return "\n".join(line for line, kept in zip(lines, keep, strict=True) if kept)


def _section_markdown(
    section: dict[str, Any],
    *,
    depth: int,
    link_mode: LinkMode,
    moved_picos: frozenset[int] = frozenset(),
    institution: str = "",
    short_code: str = "",
) -> tuple[list[str], int]:
    """Render one section and its descendants into markdown blocks.

    Returns the blocks and the number of labelled recommendations emitted, which is checked against
    the publisher's own count.
    """
    blocks: list[str] = []
    emitted = 0
    heading = _plain_heading(str(section.get("heading") or ""), link_mode=link_mode)
    if heading and _is_skipped_section(section, heading, institution, short_code):
        logger.debug("Skipping boilerplate section %r", heading)
        for child in section.get("subSections") or []:
            if not isinstance(child, dict):
                continue
            child_heading = _plain_heading(str(child.get("heading") or ""), link_mode=link_mode)
            if _skip_lookup_key(child_heading) not in _PICO_FRAME_HEADINGS:
                continue
            # Promoted to the skipped parent's own level, leaving no gap.
            child_blocks, child_emitted = _section_markdown(
                child,
                depth=depth,
                link_mode=link_mode,
                moved_picos=moved_picos,
                institution=institution,
                short_code=short_code,
            )
            blocks.extend(child_blocks)
            emitted += child_emitted
        return blocks, emitted

    body = _markdown(section.get("text"), link_mode=link_mode, base_level=min(depth + 1, _MAX_HEADING_LEVEL))
    # Judged BEFORE the inline rules: emptying the body first would drop the guidance beside it.
    if _is_pointer_only(section, body):
        logger.debug("Skipping pointer-only section %r", heading)
        return [], emitted
    body = _drop_inline_sections(body, short_code)
    # A wrapper: no text, one child repeating the heading exactly.
    children = [child for child in (section.get("subSections") or []) if isinstance(child, dict)]
    duplicates_child = (
        not body
        and len(children) == 1
        and _skip_lookup_key(heading)
        and _skip_lookup_key(_plain_heading(str(children[0].get("heading") or ""), link_mode=link_mode))
        == _skip_lookup_key(heading)
    )
    if heading and not duplicates_child:
        blocks.append(f"{'#' * min(depth + 1, _MAX_HEADING_LEVEL)} {heading}")
    if body:
        blocks.append(body)

    for pico in section.get("picos") or []:
        if not isinstance(pico, dict) or pico.get("picoId") in moved_picos:
            continue
        rendered = _pico_markdown(pico, link_mode=link_mode, base_level=min(depth + 1, _MAX_HEADING_LEVEL))
        if rendered:
            blocks.append(rendered)

    for recommendation in section.get("recommendations") or []:
        if not isinstance(recommendation, dict):
            continue
        rendered = _recommendation_markdown(
            recommendation, level=depth + 2, link_mode=link_mode, moved_picos=moved_picos
        )
        if rendered:
            blocks.append(rendered)
            emitted += _is_labelled_recommendation(rendered)

    for child in section.get("subSections") or []:
        if isinstance(child, dict):
            child_blocks, child_emitted = _section_markdown(
                child,
                depth=depth + 1,
                link_mode=link_mode,
                moved_picos=moved_picos,
                institution=institution,
                short_code=short_code,
            )
            blocks.extend(child_blocks)
            emitted += child_emitted
    return blocks, emitted


_SHINGLE_WORDS = 8
_WORD_RE = re.compile(r"[a-z0-9]+")
_RENDERED_HEADING_LINE_RE = re.compile(r"\A#{1,6}\s")
_RECOMMENDATION_HEADING_RE = re.compile(r"\A#{1,6}\s+.*\brecommendation\b", re.IGNORECASE)
# A repeated effect estimate is that recommendation's evidence; it stays in both places.
_EVIDENCE_IN_BLOCK_RE = re.compile(
    r"\b(?:RR|OR|HR|aOR|aRR|MD|SMD|NNT|NNH)\s*[=:]?\s*\d|95%\s*(?:CI|confidence)"
    r"|\bp\s*[<=>]\s*0?\.\d|\bcertainty\s+(?:high|moderate|low|very\s+low)"
    r"|\b\d+\s+participants?\b|\b\d+\s+(?:studies|trials|RCTs)\b"
    r"|\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|μg|mmol|mL|IU|mmHg)\b"
    r"|\b(?:hazard|odds|risk)\s+ratio|\brelative\s+risk\b|\bmean\s+difference\b",
    re.IGNORECASE,
)


def _block_shingles(block: str) -> set[str]:
    """Return every run of eight consecutive words in a block."""
    words = _WORD_RE.findall(block.lower())
    return {" ".join(words[index : index + _SHINGLE_WORDS]) for index in range(len(words) - _SHINGLE_WORDS + 1)}


# Merged, not replaced - the hand entries carry their own reasoning. A new mapping, so importing
# never mutates `magic_rules`.
_GUIDELINE_DROP_BLOCKS = _GUIDELINE_DROP_BLOCKS | {
    code: _GUIDELINE_DROP_BLOCKS.get(code, ()) + rules for code, rules in _GUIDELINE_PAPERWORK_DROP_BLOCKS.items()
}


def _block_opening(block: str) -> str:
    """Reduce a block to the form `_GUIDELINE_DROP_BLOCKS` is written in."""
    return _EMPHASIS_RE.sub("", " ".join(block.split())).strip().lstrip("#").strip().lower()


def _drop_guideline_blocks(markdown: str, short_code: str) -> str:
    """Remove blocks named for one guideline, from the assembled document."""
    rules = _GUIDELINE_DROP_BLOCKS.get(short_code, ())
    if not rules:
        return markdown
    blocks = markdown.split("\n\n")
    openings = [_block_opening(block) for block in blocks]
    keep = [True] * len(blocks)
    for opening, stop in rules:
        for index, text in enumerate(openings):
            if not text.startswith(opening):
                continue
            if stop is None:
                # To the end of the document - only ever written in the table, never a fallback.
                for position in range(index, len(blocks)):
                    keep[position] = False
                continue
            if not stop:
                keep[index] = False
                continue
            end = next((later for later in range(index + 1, len(blocks)) if openings[later].startswith(stop)), None)
            # Fail closed: a run that cannot find its end takes one block.
            for position in range(index, end if end is not None else index + 1):
                keep[position] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


# Removed only when nothing sits before the next heading of the same or shallower level; to a
# fixpoint. `#` headings only - a bold line is a guess.
_MAX_EMPTY_HEADING_PASSES = 8


def _drop_empty_headings(markdown: str) -> str:
    """Remove a heading with no text and no subheading under it."""
    for _ in range(_MAX_EMPTY_HEADING_PASSES):
        lines = markdown.split("\n")
        marks = []
        for position, line in enumerate(lines):
            match = _RENDERED_HEADING_RE.match(line.strip())
            if match:
                marks.append((position, len(match.group("hashes"))))
        doomed = set()
        for index, (line_no, level) in enumerate(marks):
            end = marks[index + 1][0] if index + 1 < len(marks) else len(lines)
            if "".join(lines[line_no + 1 : end]).strip():
                continue
            if index + 1 < len(marks) and marks[index + 1][1] > level:
                continue
            doomed.add(line_no)
        if not doomed:
            return markdown
        markdown = "\n".join(line for position, line in enumerate(lines) if position not in doomed)
    logger.debug("Empty-heading removal did not stabilize")
    return markdown


def _drop_repeated_blocks(markdown: str) -> str:
    """Remove a block whose every phrase already appears elsewhere in the same document.

    A block goes only when every eight-word run of it appears in a kept block, walking backwards so
    the last copy wins; headings, recommendation blocks, and blocks carrying an estimate, certainty,
    count or dose never go.
    """
    blocks = markdown.split("\n\n")
    under_recommendation, flagged = False, []
    for block in blocks:
        stripped = block.strip()
        if _RENDERED_HEADING_LINE_RE.match(stripped):
            under_recommendation = bool(_RECOMMENDATION_HEADING_RE.match(stripped))
        flagged.append(under_recommendation)

    seen: set[str] = set()
    keep = [True] * len(blocks)
    for index in range(len(blocks) - 1, -1, -1):
        stripped = blocks[index].strip()
        if not stripped or _RENDERED_HEADING_LINE_RE.match(stripped) or flagged[index]:
            continue
        if _EVIDENCE_IN_BLOCK_RE.search(stripped):
            seen |= _block_shingles(stripped)
            continue
        shingles = _block_shingles(stripped)
        if not shingles:
            continue
        if shingles <= seen:
            keep[index] = False
        else:
            seen |= shingles
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


# ---------------------------------------------------------------------------
# Scrape API
# ---------------------------------------------------------------------------


def build_magic_guideline_text(
    client: httpx.Client,
    ref: GuidelineRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> tuple[str, int, str, int]:
    """Fetch a guideline's structured JSON and render it as markdown.

    Returns the markdown, the count of top-level sections that produced content, the
    title, and the count of recommendations that reached the markdown.

    Args:
        client: HTTP client used to fetch the guideline document.
        ref: Catalogue entry identifying the guideline to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    response = client.get(ref.json_path)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
        raise MagicFetchError(f"Could not parse guideline JSON for '{ref.short_code}'") from error
    if not isinstance(payload, dict):
        raise MagicFetchError(f"Could not parse guideline JSON for '{ref.short_code}': expected an object")

    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        raise MagicFetchError(f"No sections found for guideline '{ref.short_code}'")

    moved_picos = _moved_pico_ids(payload, link_mode=link_mode)
    blocks: list[str] = []
    section_count = 0
    emitted = 0
    # A version-banner wrapper: the title printed twice with nothing new under it.
    document_title = _skip_lookup_key(_plain_heading(str(payload.get("name") or ""), link_mode=link_mode))
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading_key = _skip_lookup_key(_plain_heading(str(section.get("heading") or ""), link_mode=link_mode))
        # Never when it is the only section, or nothing is left to emit.
        if document_title and len(sections) > 1 and heading_key == document_title and not _has_recommendation(section):
            logger.debug("Skipping a section that repeats the guideline title")
            continue
        rendered, section_emitted = _section_markdown(
            section,
            depth=1,
            link_mode=link_mode,
            moved_picos=moved_picos,
            institution=ref.institution,
            short_code=ref.short_code,
        )
        emitted += section_emitted
        if rendered:
            blocks.extend(rendered)
            section_count += 1

    # Some recommendations sit on the document root; walking only sections drops them.
    for recommendation in payload.get("recommendations") or []:
        if not isinstance(recommendation, dict):
            continue
        # Level 2: beside the sections, not inside one.
        rendered_recommendation = _recommendation_markdown(
            recommendation, level=2, link_mode=link_mode, moved_picos=moved_picos
        )
        if rendered_recommendation:
            blocks.append(rendered_recommendation)
            emitted += _is_labelled_recommendation(rendered_recommendation)

    # Empty headings BEFORE deduplication: a dedup-emptied heading still marks its topic.
    content = _drop_repeated_blocks(
        _drop_empty_headings(_drop_guideline_blocks("\n\n".join(blocks), ref.short_code))
    ).strip()
    if not content:
        raise MagicFetchError(f"No readable content for guideline '{ref.short_code}'")
    title = str(payload.get("name") or ref.title).strip() or ref.short_code
    return content, section_count, title, emitted


def scrape_magic_guideline(
    client: httpx.Client,
    ref: GuidelineRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> ScrapedDocument:
    """Scrape one MAGICapp guideline into a normalized document.

    Args:
        client: HTTP client used to fetch the guideline document.
        ref: Catalogue entry identifying the guideline to scrape.
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
    """
    content, section_count, title, recommendations_in_content = build_magic_guideline_text(
        client, ref, link_mode=link_mode
    )
    return ScrapedDocument(
        source="magic",
        external_id=f"magic-{ref.short_code}",
        title=title,
        url=ref.page_url,
        content=content,
        section_count=section_count,
        metadata={
            "short_code": ref.short_code,
            "guideline_id": ref.guideline_id,
            # `publishedId`, not `guidelineId`, builds the version-pinned viewer URL.
            "published_id": ref.published_id,
            "institution": ref.institution,
            "language": ref.language,
            "publish_date": ref.publish_date,
            "recommendation_count": ref.recommendation_count,
            # The publisher's tally versus what survived rendering; both carried so silent loss
            # shows.
            "recommendations_in_content": recommendations_in_content,
            # Terms of use - kept out of `content` so it never reads as clinical text.
            "disclaimer": _markdown(ref.disclaimer, link_mode=link_mode),
            # Weak signal: most entries are NOTSET, so absence proves nothing.
            "status": ref.status,
            # The only catalogue field that tracks currency; recorded, not filtered on.
            "last_search_date": ref.last_search_date,
        },
    )


def _scrape_guideline_or_empty(
    client: httpx.Client,
    ref: GuidelineRef,
    *,
    link_mode: LinkMode,
    include_drafts: bool = False,
) -> ScrapedDocument:
    """Scrape one catalogue guideline, returning empty content instead of raising.

    Returns a document with empty content for anything unreadable or shorter than MIN_CONTENT_CHARS;
    callers drop those.
    """

    def empty(reason: str) -> ScrapedDocument:
        logger.warning("Skipping MAGICapp guideline '%s' (%s): %s", ref.short_code, ref.title, reason)
        return ScrapedDocument(
            source="magic",
            external_id=f"magic-{ref.short_code}",
            title=ref.title,
            url=ref.page_url,
            content="",
            section_count=0,
        )

    if ref.is_archived:
        return empty("the publisher has archived this guideline")
    if ref.is_draft and not include_drafts:
        return empty("the publisher's own title marks it a draft, not final guidance")

    try:
        document = scrape_magic_guideline(client, ref, link_mode=link_mode)
    except (MagicFetchError, httpx.HTTPError) as error:
        return empty(str(error))
    if len(document.content) < MIN_CONTENT_CHARS:
        return empty(f"{len(document.content)} characters, below the {MIN_CONTENT_CHARS} minimum")
    if _DEMONSTRATION_RE.search(document.content):
        return empty("the guideline's own text says it is a demonstration, not guidance")
    return document


def scrape_magic(
    *,
    documents: int | None,
    link_mode: LinkMode = LinkMode.KEEP,
    url: str | None = None,
    languages: tuple[str, ...] | None = DEFAULT_LANGUAGES,
    include_drafts: bool = False,
) -> ScrapeRun:
    """Scrape MAGICapp documents from a URL or the published catalogue.

    Unreadable guidelines are logged and skipped, so `documents` is an upper bound.

    Args:
        documents: Number of documents to scrape; None for the whole catalogue.
            Ignored when `url` is set (default: None).
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        url: Scrape this one guideline URL instead of the catalogue (default: None).
        languages: Language prefixes to collect; None for every language
            (default: DEFAULT_LANGUAGES).
        include_drafts: Keep guidelines whose own title marks them drafts. Ignored
            when `url` is set (default: False).
    """
    if url is not None:
        # An explicit URL is an explicit request: unreadable is an error, not a skip.
        def scrape_url() -> Iterable[ScrapedDocument]:
            with default_client() as client:
                yield scrape_magic_guideline(client, magic_ref_from_url(client, url), link_mode=link_mode)

        return ScrapeRun(documents=scrape_url(), total=1)

    with default_client() as client:
        first_page = list_published_guidelines(client, page=1, languages=languages)
    total = first_page.total if documents is None or first_page.total is None else min(documents, first_page.total)
    listed = scrape_listing_documents(
        documents=documents,
        client_factory=default_client,
        first_page_items=first_page.refs,
        list_page=lambda client, page: list_published_guidelines(client, page, languages=languages).refs,
        scrape_item=lambda client, ref: _scrape_guideline_or_empty(
            client, ref, link_mode=link_mode, include_drafts=include_drafts
        ),
        document_delay_seconds=DOCUMENT_DELAY_SECONDS,
    )
    return ScrapeRun(
        total=total,
        documents=(document for document in listed if document.content),
    )


__all__ = [
    "API_BASE_URL",
    "BASE_URL",
    "DEFAULT_LANGUAGES",
    "DOCUMENT_DELAY_SECONDS",
    "MAGIC_DATASET_DISPLAY_NAME",
    "MAGIC_DATASET_NAME",
    "GuidelineListingPage",
    "GuidelineRef",
    "MagicFetchError",
    "build_magic_guideline_text",
    "list_published_guidelines",
    "magic_ref_from_url",
    "scrape_magic",
    "scrape_magic_guideline",
]
