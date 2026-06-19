from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

NICE_BASE = "https://www.nice.org.uk"

_SKIP_SLUG_PATTERNS = [
    r"update.information",
    r"finding.more.information",
    r"committee.details",
    r"about.this.guidance",
    r"using.this.guideline",
]

_REMOVE_SELECTORS = [
    "header", "footer", "nav", ".breadcrumb", ".pagination",
    ".page-header__tags", "#cookie-banner", ".nhsuk-back-link",
    ".nhsuk-contents-list", "script", "style", "noscript",
    ".side-panel", ".js-filters", ".action-banner",
    ".label--tag", ".panel--inverse",
    ".nhsuk-inpage-navigation",
]


@dataclass
class NiceChapter:
    title: str
    url: str
    slug: str
    text: str = ""


@dataclass
class NiceGuideline:
    code: str
    delay: float = 1.5
    session: requests.Session = field(default_factory=requests.Session)
    chapters: list[NiceChapter] = field(default_factory=list)
    title: str = ""
    failed_chapters: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.code = self.code.lower().strip()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (compatible; AMFV-research-bot/0.1; "
                "+https://github.com/MedARC-AI/amfv)"
            ),
            "Accept": "text/html,application/xhtml+xml",
        })

    @property
    def overview_url(self) -> str:
        return f"{NICE_BASE}/guidance/{self.code}"

    def fetch_all(self, *, verbose: bool = True, max_retries: int = 3) -> None:
        if verbose:
            print(f"Fetching overview: {self.overview_url}")
        overview_html = self._get(self.overview_url, max_retries=max_retries)
        self.title, chapter_links = self._parse_overview(overview_html)

        if verbose:
            print(f"Guideline: {self.title}")
            print(f"Found {len(chapter_links)} chapters to fetch")

        for url, slug, chapter_title in chapter_links:
            if self._should_skip(slug):
                if verbose:
                    print(f"  skip  {slug}")
                continue

            time.sleep(self.delay)
            try:
                html = self._get(url, max_retries=max_retries)
            except requests.exceptions.RequestException as exc:
                self.failed_chapters.append((slug, str(exc)))
                if verbose:
                    print(f"  FAIL  {slug}  ({exc})")
                continue

            text = self._extract_text(html)
            self.chapters.append(NiceChapter(title=chapter_title, url=url, slug=slug, text=text))
            if verbose:
                print(f"  fetch {slug}")

        if verbose:
            print(f"Done. Fetched {len(self.chapters)} chapters.")
            if self.failed_chapters:
                print(f"Failed ({len(self.failed_chapters)}): " + ", ".join(s for s, _ in self.failed_chapters))

    def combined_text(self) -> str:
        parts = [f"# {self.title}\n\nSource: {self.overview_url}\n\n"]
        for ch in self.chapters:
            parts.append(f"## {ch.title}\n\n{ch.text}\n\n")
        return "\n".join(parts)

    def save(self, out_dir: str | Path, *, verbose: bool = True) -> None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

        for i, ch in enumerate(self.chapters, 1):
            fname = out / f"{i:02d}_{ch.slug}.txt"
            fname.write_text(ch.text, encoding="utf-8")
            if verbose:
                print(f"  wrote {fname}  ({len(ch.text):,} chars)")

        combined = out / f"{self.code}_combined.txt"
        combined.write_text(self.combined_text(), encoding="utf-8")
        if verbose:
            print(f"  wrote {combined}  ({len(self.combined_text()):,} chars)")

    def _get(self, url: str, *, max_retries: int = 3) -> str:
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                return resp.text
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status is not None and 400 <= status < 500:
                    raise
                last_exc = exc
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                last_exc = exc

            if attempt < max_retries:
                time.sleep(self.delay * (2 ** (attempt - 1)))

        assert last_exc is not None
        raise last_exc

    def retry_failed(self, *, verbose: bool = True, max_retries: int = 3) -> None:
        still_failed: list[tuple[str, str]] = []
        for slug, _ in self.failed_chapters:
            url = f"{NICE_BASE}/guidance/{self.code}/chapter/{slug}"
            time.sleep(self.delay)
            try:
                html = self._get(url, max_retries=max_retries)
            except requests.exceptions.RequestException as exc:
                still_failed.append((slug, str(exc)))
                if verbose:
                    print(f"  still failing: {slug}  ({exc})")
                continue
            text = self._extract_text(html)
            self.chapters.append(NiceChapter(title=slug, url=url, slug=slug, text=text))
            if verbose:
                print(f"  recovered: {slug}")
        self.failed_chapters = still_failed

    def _parse_overview(self, html: str) -> tuple[str, list[tuple[str, str, str]]]:
        soup = BeautifulSoup(html, "html.parser")

        h1 = soup.find("h1")
        guideline_title = h1.get_text(strip=True) if h1 else self.code.upper()

        chapter_pattern = re.compile(rf"/guidance/{re.escape(self.code)}/chapter/", re.IGNORECASE)
        seen: set[str] = set()
        chapters: list[tuple[str, str, str]] = []

        for a in soup.find_all("a", href=chapter_pattern):
            href: str = a["href"]
            full_url = urljoin(NICE_BASE, href)
            slug = urlparse(full_url).path.rstrip("/").split("/")[-1]
            if slug in seen:
                continue
            seen.add(slug)
            chapter_title = a.get_text(strip=True) or slug
            chapters.append((full_url, slug, chapter_title))

        return guideline_title, chapters

    @staticmethod
    def _should_skip(slug: str) -> bool:
        slug_lower = slug.lower()
        return any(re.search(p, slug_lower) for p in _SKIP_SLUG_PATTERNS)

    @staticmethod
    def _clean_text(text: str) -> str:
        text = re.sub(r"[\u00A0\u2000-\u200A\u202F\u205F\u3000]", " ", text)
        text = text.replace("\u200B", "").replace("\u200C", "").replace("\u200D", "")
        text = text.replace("\u00AD", " ")
        text = re.sub(r" {2,}", " ", text)
        return text.strip()

    @staticmethod
    def _extract_text(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")

        for selector in _REMOVE_SELECTORS:
            for el in soup.select(selector):
                el.decompose()

        main = (
            soup.find("main")
            or soup.find("div", {"id": "content"})
            or soup.find("div", class_=re.compile(r"content"))
            or soup.body
        )
        if not main or not isinstance(main, Tag):
            return NiceGuideline._clean_text(soup.get_text(separator="\n", strip=True))

        lines: list[str] = []
        for el in main.descendants:
            if not isinstance(el, Tag):
                continue

            tag = el.name.lower()

            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                text = NiceGuideline._clean_text(el.get_text(separator=" ", strip=True))
                if text:
                    prefix = "#" * int(tag[1])
                    lines.append(f"\n{prefix} {text}\n")

            elif tag == "p":
                if el.find_parent("li") is not None:
                    continue
                text = NiceGuideline._clean_text(el.get_text(separator=" ", strip=True))
                if text:
                    lines.append(text)

            elif tag == "li":
                text = NiceGuideline._clean_text(el.get_text(separator=" ", strip=True))
                if text:
                    lines.append(f"- {text}")

        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()