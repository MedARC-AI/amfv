"""Scrape MAGICapp guidelines into normalized markdown documents."""

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

BASE_URL = "https://app.magicapp.org"
API_BASE_URL = "https://api.magicapp.org"
MAGIC_DATASET_NAME = "magic-webscrape"
MAGIC_DATASET_DISPLAY_NAME = "MAGICapp Webscrape"
DOCUMENT_DELAY_SECONDS = 5.0

logger = logging.getLogger(__name__)


_CATALOGUE_LIMIT = 5000

DEFAULT_LANGUAGES = ("en",)

_TRAINING_INSTITUTIONS = frozenset(
    {
        "gela workshop malawi",
        "ges 2024 workshop guidelines",
        "magicapp tutorials",
        "magicapp workshops mapp",
    }
)

MIN_CONTENT_CHARS = 400

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
        """Report whether the publisher has marked this guideline archived."""
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
    """One page of catalogue results."""

    refs: list[GuidelineRef]
    total: int | None = None


def _force_https(url: str) -> str:
    return f"https://{url[len('http://') :]}" if url.startswith("http://") else url


def _catalogue_url() -> str:
    return f"{API_BASE_URL}/api/v1/guidelines?limit={_CATALOGUE_LIMIT}"


def _guideline_json_url(short_code: str) -> str:
    return f"{BASE_URL}/#/guideline/{short_code}"


def magic_ref_from_url(client: httpx.Client, url: str) -> GuidelineRef:
    """Resolve a MAGICapp guideline URL to its catalogue entry."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise MagicFetchError(f"Enter a URL like {_guideline_json_url('nyxpZL')}; got {url!r}")
    if parsed.hostname not in {"app.magicapp.org", "magicapp.org"}:
        raise MagicFetchError(f"Enter a URL like {_guideline_json_url('nyxpZL')}; got {url!r}")

    match = _GUIDELINE_PATH_RE.search(parsed.fragment or parsed.path)
    if match is None:
        raise MagicFetchError(f"Enter a URL like {_guideline_json_url('nyxpZL')}; got {url!r}")

    short_code = match.group("short_code")
    for ref in list_published_guidelines(client, languages=None).refs:
        if ref.short_code == short_code:
            return ref
    raise MagicFetchError(f"No published MAGICapp guideline found for short code '{short_code}'")


def _catalogue_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_catalogue(payload: Any, *, languages: tuple[str, ...] | None) -> GuidelineListingPage:
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
    """Return published guideline refs from the MAGICapp catalogue."""
    if page > 1:
        return GuidelineListingPage(refs=[], total=None)
    response = client.get(_catalogue_url())
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
        raise MagicFetchError("Could not parse MAGICapp catalogue: response was not JSON") from error
    return _parse_catalogue(payload, languages=languages)


_DELETION_CLASS = "ck-suggestion-marker-deletion"

_CITATION_CLASS = "magic-cite"

_MANUAL_CITATION_RE = re.compile(r"\A(?:\[\d+\])+\Z")

_CITATION_TAGS = frozenset({"i", "em", "b", "strong", "sup", "span"})

_CITATION_WRAPPER_TAGS = _CITATION_TAGS | {"a"}

_CITATION_SEPARATORS = frozenset({",", ";", "-", "\u2013", "\u2014"})


_CITATION_CLAUSE_ENDINGS = frozenset(".,;:)]}%\u2019\u201d")

_CITATION_EDGE_WHITESPACE = " \t\xa0"

_CITATION_TRAILING_PUNCTUATION = ".,;:)]}!?"

_ORPHANED_SEPARATORS_RE = re.compile(r"[\s\xa0]*[,;/&·•][\s\xa0,;/&·•]*")


_SUPERSCRIPTS = str.maketrans(
    "0123456789+-=()abdeghijklmnoprstuvwxyz",
    "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ᵃᵇᵈᵉᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ",
)

_SUBSCRIPTS = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")

_SCRIPT_TAGS = {"sup": _SUPERSCRIPTS, "sub": _SUBSCRIPTS}

_IMG_OPEN_RE = re.compile(r"<img", re.IGNORECASE)

_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.DOTALL)

_SCRIPT_PASSTHROUGH = frozenset({",", "."})

_MAX_TABLE_SPAN = 40

_EMPHASIS_TAGS = frozenset({"strong", "b", "u"})


_BLANK_UNIT = r"(?:[\s\xa0]|&nbsp;)"

_ZERO_WIDTH_CHAR_RE = re.compile("[\u200b\u200c\u200d\ufeff\u2060]")

_ZERO_WIDTH_INLINE_TAG_RE = re.compile(
    r"<(strong|em|span|b|i|u|sup|sub)\b[^>]*>​++</\1>",
    re.IGNORECASE,
)

_BLANK_INLINE_TAG_RE = re.compile(
    rf"<(strong|em|span|b|i|u|sup|sub)\b[^>]*>{_BLANK_UNIT}*+</\1>",
    re.IGNORECASE,
)

_SPACE_AFTER_OPEN_RE = re.compile(rf"(<(?:strong|em|b|i|u)\b[^>]*>)({_BLANK_UNIT}{{1,16}})", re.IGNORECASE)

_SPACE_BEFORE_CLOSE_RE = re.compile(rf"({_BLANK_UNIT}{{1,16}})(</(?:strong|em|b|i|u)>)", re.IGNORECASE)

_TRIMMABLE_EMPHASIS = frozenset({"strong", "em", "b", "i", "u"})

_EMPHASIS_EDGE = re.compile(r"[^\w\s\[\]]", re.UNICODE)

_STYLING_WRAPPERS = frozenset({"span", "font"})

_EMPHASIS_SEMANTICS = {"strong": "strong", "b": "strong", "em": "em", "i": "em", "u": "u"}

_MAX_EMPHASIS_MERGE_PASSES = 64


def _drop_tracked_deletions(html_text: str) -> str:
    if _DELETION_CLASS not in html_text:
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    for element in root.find_class(_DELETION_CLASS):
        parent = element.getparent()
        if parent is None:
            continue
        if element.tail:
            previous = element.getprevious()
            if previous is not None:
                previous.tail = (previous.tail or "") + element.tail
            else:
                parent.text = (parent.text or "") + element.tail
        parent.remove(element)
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _drop_orphaned_captions(html_text: str) -> str:
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
        if element.tag == "sup" and core == "TM":
            leading = text[: len(text) - len(text.lstrip())]
            trailing = text[len(text.rstrip()) :]
            element.text = f"{leading}™{trailing}"
            element.tag = "span"
            continue
        converted = text.translate(table)
        fully = any(character.translate(table) != character for character in core) and all(
            character.translate(table) != character or character in _SCRIPT_PASSTHROUGH for character in core
        )
        if fully:
            element.text = converted
        else:
            link = next((ancestor for ancestor in element.iterancestors() if ancestor.tag == "a"), None)
            inside_link = link is not None and link.text_content().strip() == text.strip()
            leading = text[: len(text) - len(text.lstrip())]
            trailing = text[len(text.rstrip()) :]
            core = text.strip()
            element.text = f"{leading}{core}{trailing}" if inside_link else f"{leading}^{core}{trailing}"
        element.tag = "span"
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _rejoin_across_citation(before: str, after: str, lookahead: str) -> str:
    if not lookahead:
        return before + after
    if lookahead in _CITATION_TRAILING_PUNCTUATION:
        return before.rstrip(_CITATION_EDGE_WHITESPACE) + after
    if before[-1:] in _CITATION_EDGE_WHITESPACE and lookahead in _CITATION_EDGE_WHITESPACE:
        return before.rstrip(_CITATION_EDGE_WHITESPACE) + " " + after.lstrip(_CITATION_EDGE_WHITESPACE)
    if (before[-1:].isalnum() or before[-1:] in _CITATION_CLAUSE_ENDINGS) and lookahead.isalnum():
        return before + " " + after
    return before + after


def _leading_text(element: Any) -> str:
    if element is None or not isinstance(element.tag, str):
        return ""
    return (element.text_content() or "")[:1]


def _is_manual_citation(element: Any) -> bool:
    return (
        isinstance(element.tag, str)
        and element.tag in _CITATION_TAGS
        and len(element) == 0
        and bool(_MANUAL_CITATION_RE.match((element.text or "").strip()))
    )


def _citation_root(element: Any) -> Any:
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
    return element.tag if isinstance(element.tag, str) else ""


def _is_styling_wrapper(element: Any) -> bool:
    if _tag_name(element) not in _STYLING_WRAPPERS:
        return False
    if _CITATION_CLASS in (element.get("class") or ""):
        return False
    content = (element.text_content() or "").strip()
    return not (content and _MANUAL_CITATION_RE.fullmatch(content))


def _emphasis_at_end(element: Any) -> Any | None:
    if _tag_name(element) in _TRIMMABLE_EMPHASIS:
        return element
    if not _is_styling_wrapper(element) or len(element) == 0 or (element[-1].tail or "").strip():
        return None
    return _emphasis_at_end(element[-1])


def _emphasis_at_start(element: Any) -> Any | None:
    if _tag_name(element) in _TRIMMABLE_EMPHASIS:
        return element
    if not _is_styling_wrapper(element) or len(element) == 0 or (element.text or "").strip():
        return None
    return _emphasis_at_start(element[0])


def _neighbour_before(element: Any) -> tuple[str, Any | None]:
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
            if target in source.iterancestors() or source in target.iterancestors():
                break
            if len(target):
                target[-1].tail = (target[-1].tail or "") + (source.text or "")
            else:
                target.text = (target.text or "") + (source.text or "")
            for moved in list(source):
                target.append(moved)
            _remove_and_prune(source)
            merged_any = True
    if not merged_any:
        return html_text
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _trim_emphasis_edges(html_text: str) -> str:
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
        before, emphasis_before = _neighbour_before(element)
        after, emphasis_after = _neighbour_after(element)
        opens_badly = before.isalnum() or emphasis_before is not None
        closes_badly = after.isalnum() or emphasis_after is not None

        if not any(character.isalnum() for character in content):
            if not (opens_badly or closes_badly):
                continue
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
        while closes_badly and len(element) == 0 and element.text and _EMPHASIS_EDGE.fullmatch(element.text[-1]):
            moved, element.text = element.text[-1], element.text[:-1]
            element.tail = moved + (element.tail or "")
    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _drop_citations(html_text: str) -> str:
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
        separator = element.tail or ""
        following = element.getnext()
        if separator.strip() in _CITATION_SEPARATORS and following is not None and id(following) in doomed_set:
            element.tail = ""
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

    for parent in emptied:
        if _ORPHANED_SEPARATORS_RE.fullmatch(parent.text_content() or ""):
            for child in list(parent):
                parent.remove(child)
            parent.text = None

    serialized = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
    return (root.text or "") + serialized


def _drop_embedded_images(html_text: str) -> str:
    if not _IMG_OPEN_RE.search(html_text):
        return html_text
    return _IMG_TAG_RE.sub("", html_text)


def _expand_table_spans(html_text: str) -> str:
    if "span=" not in html_text:
        return html_text
    root = lxml_html.fragment_fromstring(html_text, create_parent="div")
    for table in root.iter("table"):
        carried: dict[int, int] = {}
        for row in table.iter("tr"):
            cells = [cell for cell in row if isinstance(cell.tag, str) and cell.tag in {"td", "th"}]
            if not cells:
                continue
            covered = carried
            carried = {column: remaining - 1 for column, remaining in covered.items() if remaining > 1}
            filler_tag = cells[0].tag

            rebuilt: list[Any] = []
            column = 0
            for cell in cells:
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
    try:
        span = int(cell.get(attribute) or 1)
    except ValueError:
        return 1
    cell.attrib.pop(attribute, None)
    return span if 1 <= span <= _MAX_TABLE_SPAN else 1


def _label_text(element: Any) -> str:
    if not isinstance(element.tag, str):
        return ""
    return " ".join((element.text_content() or "").split())


_EMPTY_HEADING_RE = re.compile(r"^#{1,6}[ \t]*$\n?", re.MULTILINE)

_BLANK_RUN_RE = re.compile(r"\n{3,}")

_WIKI_CHROME_RE = re.compile(
    r"[ \t\xa0]*\\?\[edit source\\?\]"
    r"|^[ \t\xa0]*[*_]{0,2}\[?Back to top\]?(?:\([^)]*\))?[*_]{0,2}[ \t\xa0]*$\n?"
    r"|(?<=[.!?\)])[ \t\xa0]*\[?Back to top\]?(?:\([^)]*\))?[ \t\xa0]*$",
    re.MULTILINE,
)

_ACCIDENTAL_SETEXT_RE = re.compile(r"^(?P<text>\S.*)\n(?P<rule>[-=]{2,})[ \t]*$", re.MULTILINE)


def _markdown(raw_html: Any, *, link_mode: LinkMode, as_heading: bool = False) -> str:
    source = _expand_table_spans(
        _drop_orphaned_captions(
            _render_super_subscripts(
                _drop_citations(
                    _trim_emphasis_edges(_merge_adjacent_emphasis(_drop_tracked_deletions(str(raw_html or ""))))
                )
            )
        )
    )
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
    markdown = html_to_markdown(_drop_embedded_images(source), link_mode=link_mode)
    markdown = _WIKI_CHROME_RE.sub("", _EMPTY_HEADING_RE.sub("", markdown))
    markdown = _ZERO_WIDTH_CHAR_RE.sub(" ", markdown)
    markdown = _strip_question_numbers_from_headings(markdown)
    markdown = _drop_grade_circles(markdown)
    markdown = _drop_bibliography_blocks(markdown)
    markdown = _BLANK_RUN_RE.sub("\\n\\n", markdown)
    return _ACCIDENTAL_SETEXT_RE.sub(r"\g<text>\n\\\g<rule>", markdown).strip()


def _drop_grade_circles(markdown: str) -> str:
    if "⊕" not in markdown and "⊝" not in markdown:
        return markdown
    return re.sub(r"[  ]*[⊕⊝]+", "", markdown)


_RENDERED_HEADING_RE = re.compile(r"\A(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*\Z")
_ORPHAN_LABEL_RE = re.compile(
    r"\A(?:\*{1,2})?\s*(?:certainty of the evidence|quality of evidence|summary of findings|"
    r"effect estimates?|benefits and harms|rationale|practical (?:advice|info)|"
    r"resources and cost|patient values and preferences|applies to)\s*:?\s*(?:\*{1,2})?\Z",
    re.IGNORECASE,
)


def _drop_orphan_labels(markdown: str) -> str:
    blocks = markdown.split("\n\n")
    keep = [True] * len(blocks)
    for index, block in enumerate(blocks):
        if not _ORPHAN_LABEL_RE.match(block.strip()):
            continue
        following = next((blocks[later] for later in range(index + 1, len(blocks)) if blocks[later].strip()), None)
        if following is None or _ORPHAN_LABEL_RE.match(following.strip()):
            keep[index] = False
    if all(keep):
        return markdown
    return "\n\n".join(block for block, kept in zip(blocks, keep, strict=True) if kept)


def _strip_question_numbers_from_headings(markdown: str) -> str:
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


_EMPHASIS_RE = re.compile(r"[*_]+")

_HEADING_LABEL_RE = re.compile(
    r"\A(?:(?:appendix|appendices|annex(?:es)?|app|attachment|supplement(?:ary)?)\b\.?\s*)?"
    r"(?:"
    r"[a-z]\s*[.\-–—:)]"
    r"|\d+(?:\.\d+)*\s*[.\-–—:)]?"
    r")?"
    r"\s*"
)


_GRADE_CERTAINTY_RE = re.compile(r"certainty of (?:the )?evidence", re.IGNORECASE)

_GRADE_EFFECT_RE = re.compile(r"favours (?:this|other) option|favours the (?:intervention|comparator)", re.IGNORECASE)

_RECOMMENDATION_KEY_RE = re.compile(
    r"indicates a (?:strong|conditional|weak) recommendation|"
    r"symbol denotes an? [\w -]*recommendation|"
    r"level\s*\d\s*[\"“'‘]?\s*we\s+(?:recommend|suggest)",
    re.IGNORECASE,
)

_GUIDELINE_SCOPE_RE = re.compile(
    r"(?:these|this|the)\s+(?:\w+\s+){0,3}(?:guidelines?|recommendations?|document)\s+"
    r"(?:only\s+)?(?:refers?\s+only\s+to|refers?\s+to|applies\s+to|apply\s+to|"
    r"does\s+not\s+(?:apply|cover|address)|do\s+not\s+(?:apply|cover|address)|covers?)"
    r"|scope\s+of\s+(?:this|these)\s+(?:\w+\s+){0,2}guidelines?\s+"
    r"(?:is|was|focuses|focused|covers?|includes?)"
    r"|no\s+(?:guidance|recommendations?|advice)\s+(?:is|are)\s+(?:given|provided|made|included)\b",
    re.IGNORECASE,
)

_EVIDENCE_FINDING_RE = re.compile(
    r"yielded no evidence|no evidence was (?:found|identified)|did not show|"
    r"no (?:studies|trials|RCTs) (?:were )?(?:found|identified)|showed no difference",
    re.IGNORECASE,
)


def _has_recommendation(section: dict[str, Any]) -> bool:
    if any(isinstance(recommendation, dict) for recommendation in section.get("recommendations") or []):
        return True
    return any(isinstance(child, dict) and _has_recommendation(child) for child in section.get("subSections") or [])


def _visible_subtree_text(section: dict[str, Any]) -> str:
    parts = [str(section.get("text") or "")]
    for child in section.get("subSections") or []:
        if isinstance(child, dict):
            parts.append(_visible_subtree_text(child))
    joined = " ".join(part for part in parts if part.strip())
    if not joined.strip():
        return ""
    root = lxml_html.fragment_fromstring(joined, create_parent="div")
    return " ".join(" ".join(root.itertext()).split())


def _is_draft_recommendation(recommendation: dict[str, Any]) -> bool:
    visible = _visible_subtree_text({"text": str(recommendation.get("text") or "")})
    if _DRAFT_RECOMMENDATION_RE.match(visible):
        return True
    remarks = _visible_subtree_text({"text": str(recommendation.get("remarks") or "")})
    return bool(_UNAPPROVED_DRAFT_RE.search(remarks))


def _holds_evidence_table(section: dict[str, Any]) -> bool:
    text = _visible_subtree_text(section)
    return len(_GRADE_CERTAINTY_RE.findall(text)) >= 2 and len(_GRADE_EFFECT_RE.findall(text)) >= 2


def _defines_recommendation_strength(section: dict[str, Any]) -> bool:
    return bool(_RECOMMENDATION_KEY_RE.search(_visible_subtree_text(section)))


def _states_guideline_scope(section: dict[str, Any]) -> bool:
    own_text = str(section.get("text") or "")
    if not own_text.strip():
        return False
    return bool(_GUIDELINE_SCOPE_RE.search(_visible_subtree_text({"text": own_text})))


def _reports_evidence_findings(section: dict[str, Any]) -> bool:
    return bool(_EVIDENCE_FINDING_RE.search(_visible_subtree_text(section)))


_BIBLIOGRAPHY_HEADINGS = frozenset({"references", "reference list", "bibliography"})

_PROSE_GUIDANCE_RE = re.compile(
    r"\bwe (?:recommend|suggest)\b"
    r"|\b(?:is|are) (?:not )?recommended\b"
    r"|\b(?:strong|weak|conditional) recommendation\b"
    r"|\bpractice point\b",
    re.IGNORECASE,
)


def _subtree_clinical(section: dict[str, Any]) -> bool:
    if section.get("recommendations") or section.get("picos"):
        return True
    if _PROSE_GUIDANCE_RE.search(f"{section.get('heading') or ''} {section.get('text') or ''}"):
        return True
    return any(isinstance(child, dict) and _subtree_clinical(child) for child in section.get("subSections") or [])


def _is_skipped_section(section: dict[str, Any], heading: str) -> bool:
    if _skip_lookup_key(heading) in _BIBLIOGRAPHY_HEADINGS and not _has_recommendation(section):
        return True
    if _subtree_clinical(section):
        return False
    if _holds_evidence_table(section):
        return False
    if _defines_recommendation_strength(section):
        return False
    if _states_guideline_scope(section):
        return False
    return not _reports_evidence_findings(section)


def _skip_lookup_key(heading: str) -> str:
    key = _EMPHASIS_RE.sub("", heading).strip().lower()
    key = _HEADING_LABEL_RE.sub("", key)
    return " ".join(key.strip(" .-–—:").split())


def _drop_bibliography_blocks(markdown: str) -> str:
    lines = markdown.split("\n")
    keep = [True] * len(lines)
    dropping = 0
    for index, line in enumerate(lines):
        match = _RENDERED_HEADING_RE.match(line.strip())
        if match:
            level = len(match.group("hashes"))
            if dropping and level <= dropping:
                dropping = 0
            if not dropping and _skip_lookup_key(match.group("text")) in _BIBLIOGRAPHY_HEADINGS:
                dropping = level
        keep[index] = not dropping
    if all(keep):
        return markdown
    return "\n".join(line for line, kept in zip(lines, keep, strict=True) if kept)


_EMPHASIS_MARKER = r"(?:\\?[*_])*"
_QUESTION_NUMBER_LABEL_RE = re.compile(
    rf"\A{_EMPHASIS_MARKER}\s*PICO\s*\d+[a-z]?{_EMPHASIS_MARKER}\s*[:.]?\s*"
    rf"(?:{_EMPHASIS_MARKER}\s*executive\s+summary\s*{_EMPHASIS_MARKER}\s*[:.]?)?\s*"
    rf"|\A{_EMPHASIS_MARKER}\s*executive\s+summary\s*{_EMPHASIS_MARKER}\s*[:.]\s*",
    re.IGNORECASE,
)


def _plain_heading(raw_heading: str, *, link_mode: LinkMode) -> str:
    heading = _markdown(raw_heading, link_mode=link_mode, as_heading=True)
    heading = " ".join(heading.replace("#", " ").split())
    remainder = _QUESTION_NUMBER_LABEL_RE.sub("", heading).strip(" *_:.\\").strip()
    return remainder or heading


_MAX_HEADING_LEVEL = 6

_UNRATED_STRENGTHS = frozenset({"NOTSET", "NO_STRENGTH"})

_INFO_STRENGTH = "INFO"


_DROPPED_STATUSES = frozenset({"POSSIBLY_OUTDATED"})

_DRAFT_RECOMMENDATION_RE = re.compile(r"\A\s*draft\b", re.IGNORECASE)

_UNAPPROVED_DRAFT_RE = re.compile(
    r"\bis\s+a\s+draft\s+recommendation\b[^.]*\bnot\s+yet\s+been\s+approved\b", re.IGNORECASE
)

_GRADE_CERTAINTY = {
    "HIGH": "High",
    "MODERATE": "Moderate",
    "WEAK": "Low",
    "VERY_LOW": "Very low",
}

_OUTCOME_CERTAINTY = {
    "HIGH": "high",
    "MODERATE": "moderate",
    "LOW": "low",
    "VERY_LOW": "very low",
}

_EFFECT_MEASURES = {"RR": "RR", "OR": "OR", "HR": "HR"}

_OUTCOME_LISTS = ("dichotomousOutcomes", "continuousOutcomes", "nonPoolableOutcomes")

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

_WHOLE_LINE_BOLD_RE = re.compile(r"\*\*((?:[^*]|\*(?!\*))+)\*\*")

_MAX_LABEL_CHARS = 120

_RECOMMENDATION_PROSE = (
    ("rational", "Rationale"),
    ("advice", "Practical advice"),
    ("implementation", "Implementation"),
    ("evaluation", "Evaluation"),
    ("research", "Research needed"),
)

_KEY_INFO_DOMAINS = (
    ("evidence", "Certainty of the evidence"),
    ("benefits", "Benefits and harms"),
    ("preferences", "Patient values and preferences"),
    ("resources", "Resources and cost"),
    ("acceptability", "Acceptability"),
    ("feasibility", "Feasibility"),
    ("equity", "Health equity"),
)


def _split_leading_label(text: str) -> tuple[str, str]:
    original = text
    first_line, newline, rest = text.partition("\n")
    whole_bold = _WHOLE_LINE_BOLD_RE.fullmatch(first_line.strip())
    if whole_bold:
        first_line = whole_bold.group(1).strip()
        text = first_line + newline + rest
    candidate = " ".join(_LABEL_DECORATION_RE.sub("", first_line).split())
    if not _LABEL_OPENERS.match(candidate):
        return "Recommendation", original

    colon = first_line.find(":")
    if 0 <= colon < len(first_line) - 1:
        cut, label = colon + 1, candidate.split(":", 1)[0].strip()
    else:
        cut, label = len(first_line), candidate.rstrip(":")
    if len(label) > _MAX_LABEL_CHARS:
        return "Recommendation", original

    consumed = text[:cut]
    remainder = text[cut:].lstrip(": \t\xa0").lstrip()
    if consumed.count("**") % 2 and remainder.count("**") % 2:
        first_marker = remainder.find("**")
        remainder = (remainder[:first_marker] + remainder[first_marker + 2 :]).lstrip()
    elif not remainder.startswith("**") and remainder.startswith("*") and consumed.count("*") % 2:
        remainder = remainder[1:].lstrip()
    if not remainder.strip():
        return "Recommendation", original
    label = " ".join(label.replace("**", " ").split())
    return label.strip("*").strip(":.-–— *\xa0"), remainder


def _recommendation_markdown(recommendation: dict[str, Any], *, level: int, link_mode: LinkMode) -> str:
    review_status = str(recommendation.get("status") or "").strip().upper()
    if review_status in _DROPPED_STATUSES:
        logger.debug("Dropping recommendation with review status %s", review_status)
        return ""
    if _is_draft_recommendation(recommendation):
        logger.debug("Dropping recommendation the publisher marks a draft")
        return ""
    text = _markdown(recommendation.get("text"), link_mode=link_mode)
    if not text:
        return ""
    strength = str(recommendation.get("strength") or "").strip().upper()

    if strength == _INFO_STRENGTH:
        return text

    base_label, text = _split_leading_label(text)
    if not text:
        return ""

    label = base_label if not strength or strength in _UNRATED_STRENGTHS else f"{base_label} ({strength})"
    key_info = recommendation.get("keyInfo")
    key_info = key_info if isinstance(key_info, dict) else {}
    certainty = _GRADE_CERTAINTY.get(str(key_info.get("evidenceStrength") or "").strip().upper())
    if certainty:
        label = f"{label} — certainty of evidence: {certainty}"

    parts = [f"{'#' * min(level, _MAX_HEADING_LEVEL)} {label}"]

    scope = _markdown(recommendation.get("heading"), link_mode=link_mode)
    scope = " ".join(scope.replace("#", " ").split())
    if scope and scope not in text:
        parts.append(f"*Applies to:*\n\n{scope}")

    parts.append(text)

    remarks = _markdown(recommendation.get("remarks"), link_mode=link_mode)
    if remarks:
        parts.append(f"*Remarks:*\n\n{remarks}")

    for field, heading in _RECOMMENDATION_PROSE:
        body = _markdown(recommendation.get(field), link_mode=link_mode)
        if body:
            parts.append(f"*{heading}:*\n\n{body}")

    for field, heading in _KEY_INFO_DOMAINS:
        body = _markdown(key_info.get(field), link_mode=link_mode)
        research = _markdown(key_info.get(f"{field}ResearchEvidence"), link_mode=link_mode)
        extra = _markdown(key_info.get(f"{field}AdditionalConsideration"), link_mode=link_mode)
        if body:
            parts.append(f"*{heading}:*\n\n{body}")
        if research:
            parts.append(f"*{heading} — research evidence:*\n\n{research}")
        if extra:
            parts.append(f"*{heading} — additional considerations:*\n\n{extra}")

    return _drop_orphan_labels("\n\n".join(parts))


def _plain_number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return ""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _outcome_estimates(pico: dict[str, Any]) -> str:
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
            title = title.replace("*", "\\*")
            parts: list[str] = []
            measure = _EFFECT_MEASURES.get(str(row.get("relativeEffectType") or "").strip().upper())
            effect = _plain_number(row.get("relativeEffect"))
            if measure and effect:
                low, high = (
                    _plain_number(row.get("relativeEffectConfidenceLow")),
                    _plain_number(row.get("relativeEffectConfidenceHigh")),
                )
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


def _pico_markdown(pico: dict[str, Any], *, link_mode: LinkMode) -> str:
    question = [
        (label, _markdown(pico.get(field), link_mode=link_mode))
        for field, label in (
            ("population", "Population"),
            ("intervention", "Intervention"),
            ("comparator", "Comparator"),
        )
    ]
    question = [(label, value) for label, value in question if value]
    summary = _markdown(pico.get("summary"), link_mode=link_mode)
    estimates = _outcome_estimates(pico)
    if not question or not (summary or estimates):
        return ""
    lines = "\n".join(f"- {label}: {value}" for label, value in question)
    block = f"**Evidence question**\n\n{lines}"
    if summary:
        block = f"{block}\n\n*Summary of findings:*\n\n{summary}"
    return f"{block}\n\n*Effect estimates:*\n\n{estimates}" if estimates else block


def _is_labelled_recommendation(block: str) -> bool:
    return block.startswith("#")


def _section_markdown(
    section: dict[str, Any],
    *,
    depth: int,
    link_mode: LinkMode,
) -> tuple[list[str], int]:
    blocks: list[str] = []
    emitted = 0
    heading = _plain_heading(str(section.get("heading") or ""), link_mode=link_mode)
    if heading and _is_skipped_section(section, heading):
        logger.debug("Skipping non-clinical section %r", heading)
        return blocks, emitted

    body = _markdown(section.get("text"), link_mode=link_mode)
    if heading:
        blocks.append(f"{'#' * min(depth + 1, _MAX_HEADING_LEVEL)} {heading}")
    if body:
        blocks.append(body)

    for pico in section.get("picos") or []:
        if not isinstance(pico, dict):
            continue
        rendered = _pico_markdown(pico, link_mode=link_mode)
        if rendered:
            blocks.append(rendered)

    for recommendation in section.get("recommendations") or []:
        if not isinstance(recommendation, dict):
            continue
        rendered = _recommendation_markdown(recommendation, level=depth + 2, link_mode=link_mode)
        if rendered:
            blocks.append(rendered)
            emitted += _is_labelled_recommendation(rendered)

    for child in section.get("subSections") or []:
        if isinstance(child, dict):
            child_blocks, child_emitted = _section_markdown(
                child,
                depth=depth + 1,
                link_mode=link_mode,
            )
            blocks.extend(child_blocks)
            emitted += child_emitted
    return blocks, emitted


_SHINGLE_WORDS = 8
_WORD_RE = re.compile(r"[a-z0-9]+")
_RENDERED_HEADING_LINE_RE = re.compile(r"\A#{1,6}\s")
_RECOMMENDATION_HEADING_RE = re.compile(r"\A#{1,6}\s+.*\brecommendation\b", re.IGNORECASE)
_EVIDENCE_IN_BLOCK_RE = re.compile(
    r"\b(?:RR|OR|HR|aOR|aRR|MD|SMD|NNT|NNH)\s*[=:]?\s*\d|95%\s*(?:CI|confidence)"
    r"|\bp\s*[<=>]\s*0?\.\d|\bcertainty\s+(?:high|moderate|low|very\s+low)"
    r"|\b\d+\s+participants?\b|\b\d+\s+(?:studies|trials|RCTs)\b"
    r"|\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|μg|mmol|mL|IU|mmHg)\b"
    r"|\b(?:hazard|odds|risk)\s+ratio|\brelative\s+risk\b|\bmean\s+difference\b",
    re.IGNORECASE,
)


def _block_shingles(block: str) -> set[str]:
    words = _WORD_RE.findall(block.lower())
    return {" ".join(words[index : index + _SHINGLE_WORDS]) for index in range(len(words) - _SHINGLE_WORDS + 1)}


_MAX_EMPTY_HEADING_PASSES = 8


def _drop_empty_headings(markdown: str) -> str:
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


def build_magic_guideline_text(
    client: httpx.Client,
    ref: GuidelineRef,
    *,
    link_mode: LinkMode = LinkMode.KEEP,
) -> tuple[str, int, str, int]:
    """Fetch a guideline's structured JSON and render it as markdown."""
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

    blocks: list[str] = []
    section_count = 0
    emitted = 0
    document_title = _skip_lookup_key(_plain_heading(str(payload.get("name") or ""), link_mode=link_mode))
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading_key = _skip_lookup_key(_plain_heading(str(section.get("heading") or ""), link_mode=link_mode))
        if document_title and len(sections) > 1 and heading_key == document_title and not _has_recommendation(section):
            logger.debug("Skipping a section that repeats the guideline title")
            continue
        rendered, section_emitted = _section_markdown(
            section,
            depth=1,
            link_mode=link_mode,
        )
        emitted += section_emitted
        if rendered:
            blocks.extend(rendered)
            section_count += 1

    for recommendation in payload.get("recommendations") or []:
        if not isinstance(recommendation, dict):
            continue
        rendered_recommendation = _recommendation_markdown(recommendation, level=2, link_mode=link_mode)
        if rendered_recommendation:
            blocks.append(rendered_recommendation)
            emitted += _is_labelled_recommendation(rendered_recommendation)

    content = _drop_repeated_blocks(_drop_empty_headings("\n\n".join(blocks))).strip()
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
    """Scrape one MAGICapp guideline into a normalized document."""
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
            "published_id": ref.published_id,
            "institution": ref.institution,
            "language": ref.language,
            "publish_date": ref.publish_date,
            "recommendation_count": ref.recommendation_count,
            "recommendations_in_content": recommendations_in_content,
            "disclaimer": _markdown(ref.disclaimer, link_mode=link_mode),
            "status": ref.status,
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
    """Scrape MAGICapp documents from a URL or the published catalogue."""
    if url is not None:

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
