from __future__ import annotations

from pathlib import Path

import pytest
import requests

from amfv_datasets.nice.fetcher import NiceGuideline

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def overview_html() -> str:
    return (FIXTURES / "nice_overview.html").read_text(encoding="utf-8")


@pytest.fixture
def chapter_html() -> str:
    return (FIXTURES / "nice_chapter.html").read_text(encoding="utf-8")


@pytest.fixture
def guideline() -> NiceGuideline:
    return NiceGuideline("ng28")


class TestGuidelineInit:
    def test_code_is_lowercased(self):
        g = NiceGuideline("NG28")
        assert g.code == "ng28"

    def test_overview_url(self, guideline):
        assert guideline.overview_url == "https://www.nice.org.uk/guidance/ng28"

    def test_user_agent_set(self, guideline):
        assert "User-Agent" in guideline.session.headers


class TestParseOverview:
    def test_extracts_title(self, guideline, overview_html):
        title, _ = guideline._parse_overview(overview_html)
        assert title == "Type 2 diabetes in adults: management"

    def test_finds_all_chapter_links(self, guideline, overview_html):
        _, chapters = guideline._parse_overview(overview_html)
        slugs = {slug for _, slug, _ in chapters}
        assert "Individualised-care" in slugs
        assert "Blood-glucose-management" in slugs
        assert "Initial-medicines" in slugs

    def test_overview_link_itself_excluded(self, guideline, overview_html):
        _, chapters = guideline._parse_overview(overview_html)
        urls = {url for url, _, _ in chapters}
        assert "https://www.nice.org.uk/guidance/ng28" not in urls

    def test_no_duplicate_slugs(self, guideline, overview_html):
        _, chapters = guideline._parse_overview(overview_html)
        slugs = [slug for _, slug, _ in chapters]
        assert len(slugs) == len(set(slugs))

    def test_chapter_titles_populated(self, guideline, overview_html):
        _, chapters = guideline._parse_overview(overview_html)
        title_by_slug = {slug: title for _, slug, title in chapters}
        assert title_by_slug["Blood-glucose-management"] == "Blood glucose management"

    def test_urls_are_absolute(self, guideline, overview_html):
        _, chapters = guideline._parse_overview(overview_html)
        for url, _, _ in chapters:
            assert url.startswith("https://www.nice.org.uk/")


class TestShouldSkip:
    @pytest.mark.parametrize("slug", [
        "Update-information",
        "Finding-more-information-and-committee-details",
        "Using-this-guideline",
    ])
    def test_administrative_chapters_skipped(self, slug):
        assert NiceGuideline._should_skip(slug) is True

    @pytest.mark.parametrize("slug", [
        "Blood-glucose-management",
        "Individualised-care",
        "Recommendations-for-research",
        "Dietary-advice-and-interventions",
    ])
    def test_clinical_chapters_kept(self, slug):
        assert NiceGuideline._should_skip(slug) is False


class TestExtractText:
    def test_removes_nav_chrome(self, chapter_html):
        text = NiceGuideline._extract_text(chapter_html)
        assert "site nav junk" not in text

    def test_removes_cookie_banner(self, chapter_html):
        text = NiceGuideline._extract_text(chapter_html)
        assert "Accept cookies" not in text

    def test_removes_sidebar_and_action_banner(self, chapter_html):
        text = NiceGuideline._extract_text(chapter_html)
        assert "sidebar junk" not in text
        assert "banner junk" not in text

    def test_removes_footer_and_scripts(self, chapter_html):
        text = NiceGuideline._extract_text(chapter_html)
        assert "footer junk" not in text
        assert "tracking script" not in text

    def test_removes_inpage_navigation(self, chapter_html):
        text = NiceGuideline._extract_text(chapter_html)
        assert "On this page" not in text

    def test_preserves_numeric_clinical_values(self, chapter_html):
        text = NiceGuideline._extract_text(chapter_html)
        assert "48 mmol/mol (6.5%)" in text
        assert "30-44 mL/min/1.73 m2" in text
        assert "eGFR 30 mL/min/1.73 m2" in text

    def test_preserves_headings(self, chapter_html):
        text = NiceGuideline._extract_text(chapter_html)
        assert "HbA1c measurement and targets" in text
        assert "Metformin dosing" in text

    def test_preserves_list_items(self, chapter_html):
        text = NiceGuideline._extract_text(chapter_html)
        assert "Discontinue metformin below eGFR 30 mL/min/1.73 m2." in text
        assert "Review renal function annually." in text

    def test_no_triple_blank_lines(self, chapter_html):
        text = NiceGuideline._extract_text(chapter_html)
        assert "\n\n\n" not in text

    def test_nbsp_normalized_to_space(self, chapter_html):
        text = NiceGuideline._extract_text(chapter_html)
        assert "type 2 diabetes to monitor" in text
        assert "\u00a0" not in text

    def test_soft_hyphen_normalized_to_space(self, chapter_html):
        text = NiceGuideline._extract_text(chapter_html)
        assert "their device" in text
        assert "theirdevice" not in text
        assert "\u00ad" not in text

    def test_li_wrapping_p_does_not_duplicate(self):
        html = (
            "<html><body><main>"
            "<ul>"
            '<li class="listitem"><p>reinforce advice about diet</p></li>'
            '<li class="listitem"><p>intensify medicines</p></li>'
            "</ul>"
            "</main></body></html>"
        )
        text = NiceGuideline._extract_text(html)
        assert text.count("reinforce advice about diet") == 1
        assert text.count("intensify medicines") == 1
        assert "- reinforce advice about diet" in text
        assert "- intensify medicines" in text

    def test_li_without_nested_p_still_works(self):
        html = "<html><body><main><ul><li>option A</li><li>option B</li></ul></main></body></html>"
        text = NiceGuideline._extract_text(html)
        assert text == "- option A\n- option B"

    def test_standalone_p_after_list_unaffected(self):
        html = (
            "<html><body><main>"
            "<ul><li><p>bullet one</p></li></ul>"
            "<p>standalone paragraph, not a bullet</p>"
            "</main></body></html>"
        )
        text = NiceGuideline._extract_text(html)
        assert "- bullet one" in text
        assert "standalone paragraph, not a bullet" in text
        assert "- standalone paragraph" not in text

    def test_real_nice_markup_reproduces_correctly(self):
        html = (
            "<html><body><main>"
            '<article id="ng28-1_5_9" class="numbered-paragraph">'
            "<h5>1.5.9</h5>"
            "<div>"
            "<p>Consider relaxing the target HbA1c level on a case-by-case basis if:</p>"
            '<ul class="itemizedlist indented">'
            '<li class="listitem"><p>they are unlikely to achieve longer-term risk-reduction benefits, for example, people with a reduced life expectancy</p></li>'
            '<li class="listitem"><p>tight blood glucose control would put them at high risk if they developed hypoglycaemia</p></li>'
            '<li class="listitem"><p>intensive management would not be appropriate, for example if they have significant comorbidities. <strong>[2015, amended 2022]</strong></p></li>'
            "</ul>"
            "</div>"
            "</article>"
            "</main></body></html>"
        )
        text = NiceGuideline._extract_text(html)
        assert text.count("reduced life expectancy") == 1
        assert text.count("developed hypoglycaemia") == 1
        assert text.count("significant comorbidities") == 1
        assert "- they are unlikely to achieve longer-term risk-reduction benefits, for example, people with a reduced life expectancy" in text
        assert "- tight blood glucose control would put them at high risk if they developed hypoglycaemia" in text
        assert "- intensive management would not be appropriate, for example if they have significant comorbidities. [2015, amended 2022]" in text


class TestCleanText:
    def test_nbsp_to_space(self):
        assert NiceGuideline._clean_text("type\u00a02 diabetes") == "type 2 diabetes"

    def test_soft_hyphen_to_space_not_deletion(self):
        # Deleting (not spacing) would fuse "their" + "device" -> "theirdevice"
        result = NiceGuideline._clean_text("their\u00addevice")
        assert result == "their device"
        assert "theirdevice" not in result

    def test_zero_width_chars_removed(self):
        assert NiceGuideline._clean_text("foo\u200bbar") == "foobar"

    def test_multiple_spaces_collapsed(self):
        assert NiceGuideline._clean_text("foo   bar") == "foo bar"

    def test_normal_clinical_text_unchanged(self):
        text = "Metformin 500 mg twice daily for type 2 diabetes mellitus"
        assert NiceGuideline._clean_text(text) == text


class TestCombinedText:
    def test_combined_text_includes_all_chapters(self, guideline):
        from amfv_datasets.nice.fetcher import NiceChapter

        guideline.title = "Type 2 diabetes in adults: management"
        guideline.chapters = [
            NiceChapter(title="Chapter A", url="https://x/a", slug="a", text="Text A content."),
            NiceChapter(title="Chapter B", url="https://x/b", slug="b", text="Text B content."),
        ]
        combined = guideline.combined_text()
        assert "Chapter A" in combined
        assert "Text A content." in combined
        assert "Chapter B" in combined
        assert "Text B content." in combined
        assert guideline.overview_url in combined


class TestSaveToDisk:
    def test_save_writes_one_file_per_chapter_plus_combined(self, guideline, tmp_path):
        from amfv_datasets.nice.fetcher import NiceChapter

        guideline.title = "Type 2 diabetes in adults: management"
        guideline.chapters = [
            NiceChapter(title="Chapter A", url="https://x/a", slug="chapter-a", text="Content A"),
            NiceChapter(title="Chapter B", url="https://x/b", slug="chapter-b", text="Content B"),
        ]
        guideline.save(tmp_path, verbose=False)

        written = sorted(p.name for p in tmp_path.glob("*.txt"))
        assert written == ["01_chapter-a.txt", "02_chapter-b.txt", "ng28_combined.txt"]

        assert (tmp_path / "01_chapter-a.txt").read_text() == "Content A"
        assert (tmp_path / "02_chapter-b.txt").read_text() == "Content B"

    def test_save_creates_out_dir_if_missing(self, guideline, tmp_path):
        nested = tmp_path / "does" / "not" / "exist"
        guideline.title = "Test"
        guideline.chapters = []
        guideline.save(nested, verbose=False)
        assert nested.exists()


class TestRetryAndResilience:
    def test_get_retries_on_5xx_then_succeeds(self, guideline):
        from unittest.mock import MagicMock, patch

        call_count = {"n": 0}

        def fake_get(url, timeout=30):
            call_count["n"] += 1
            resp = MagicMock()
            if call_count["n"] < 3:
                resp.status_code = 502
                resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
            else:
                resp.status_code = 200
                resp.text = "<html>ok</html>"
                resp.raise_for_status.side_effect = None
            return resp

        guideline.delay = 0.01
        with patch.object(guideline.session, "get", side_effect=fake_get):
            result = guideline._get("https://x/y", max_retries=3)

        assert result == "<html>ok</html>"
        assert call_count["n"] == 3

    def test_get_does_not_retry_4xx(self, guideline):
        from unittest.mock import MagicMock, patch

        call_count = {"n": 0}

        def fake_get_404(url, timeout=30):
            call_count["n"] += 1
            resp = MagicMock()
            resp.status_code = 404
            resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
            return resp

        guideline.delay = 0.01
        with patch.object(guideline.session, "get", side_effect=fake_get_404):
            with pytest.raises(requests.exceptions.HTTPError):
                guideline._get("https://x/y", max_retries=3)

        assert call_count["n"] == 1

    def test_fetch_all_continues_past_failed_chapter(self, guideline, overview_html):
        from unittest.mock import MagicMock, patch

        guideline.delay = 0.01

        def fake_get(url, timeout=30):
            resp = MagicMock()
            if url == guideline.overview_url:
                resp.status_code = 200
                resp.text = overview_html
                resp.raise_for_status.side_effect = None
            elif "Blood-glucose-management" in url:
                resp.status_code = 502
                resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
            else:
                resp.status_code = 200
                resp.text = "<html><body><main><p>text</p></main></body></html>"
                resp.raise_for_status.side_effect = None
            return resp

        with patch.object(guideline.session, "get", side_effect=fake_get):
            guideline.fetch_all(verbose=False, max_retries=2)

        assert len(guideline.failed_chapters) == 1
        assert guideline.failed_chapters[0][0] == "Blood-glucose-management"
        assert len(guideline.chapters) > 0

    def test_retry_failed_recovers_chapter(self, guideline):
        from unittest.mock import MagicMock, patch

        guideline.delay = 0.01
        guideline.failed_chapters = [("Blood-glucose-management", "previous error")]

        def fake_get(url, timeout=30):
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<html><body><main><p>recovered text</p></main></body></html>"
            resp.raise_for_status.side_effect = None
            return resp

        with patch.object(guideline.session, "get", side_effect=fake_get):
            guideline.retry_failed(verbose=False)

        assert guideline.failed_chapters == []
        assert len(guideline.chapters) == 1
        assert "recovered text" in guideline.chapters[0].text