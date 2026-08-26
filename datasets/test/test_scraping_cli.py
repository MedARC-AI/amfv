"""Tests for the scraping CLI."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amfv_datasets.scraping.base import ScrapedDocument, ScrapeRun
from amfv_datasets.scraping.cli import (
    ALL_SOURCES,
    SCRAPERS,
    _expand_source,
    app,
    scrape_documents,
    write_huggingface_dataset,
    write_jsonl,
    write_markdown_files,
)
from amfv_datasets.scraping.html import LinkMode


def test_write_jsonl_serializes_documents() -> None:
    """Scraped documents are written as one JSON object per line."""
    document = _document()
    output = _TextSink()

    count = write_jsonl([document], output)

    assert count == 1
    assert json.loads(output.value) == {
        "content": "content",
        "external_id": "nice-ng1",
        "metadata": {"ref": "NG1"},
        "section_count": 1,
        "source": "nice",
        "title": "Guideline 1",
        "url": "https://www.nice.org.uk/guidance/ng1",
    }


def test_write_huggingface_dataset_saves_to_disk(tmp_path: Path) -> None:
    """Scraped documents can be saved as a Hugging Face dataset."""
    output_path = tmp_path / "dataset"

    count = write_huggingface_dataset([_document()], output_path)

    assert count == 1
    assert (output_path / "dataset_info.json").exists()
    assert (output_path / "state.json").exists()


def test_write_markdown_files_saves_documents_to_directory(tmp_path: Path) -> None:
    """Scraped documents can be saved as markdown files."""
    output_path = tmp_path / "markdown"

    count = write_markdown_files([_document()], output_path)

    assert count == 1
    assert (output_path / "nice-ng1.md").read_text(encoding="utf-8") == (
        "# Guideline 1\n\nSource: <https://www.nice.org.uk/guidance/ng1>\nExternal ID: `nice-ng1`\n\ncontent\n"
    )


def test_cli_run_writes_jsonl_to_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI run command emits scraped documents as JSONL."""
    runner = CliRunner()

    def fake_scrape_documents(
        source: str,
        *,
        documents: int | None,
        link_mode: LinkMode,
        url: str | None = None,
    ) -> ScrapeRun:
        assert source == "nice"
        assert documents == 3
        assert link_mode is LinkMode.STRIP
        assert url is None
        return ScrapeRun([_document()], total=3)

    monkeypatch.setattr("amfv_datasets.scraping.cli.scrape_documents", fake_scrape_documents)

    def fake_progress(
        documents: Iterator[ScrapedDocument],
        *,
        total: int | None,
    ) -> Iterator[ScrapedDocument]:
        yield from documents

    monkeypatch.setattr("amfv_datasets.scraping.cli._progress_documents", fake_progress)

    result = runner.invoke(app, ["--source", "nice", "--documents", "3", "--links", "strip"])

    assert result.exit_code == 0
    assert json.loads(result.stdout.splitlines()[0])["external_id"] == "nice-ng1"
    assert "scraped 1 documents from nice" in result.stderr


def test_cli_run_can_disable_progress_for_file_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Progress can be disabled explicitly for automation-friendly runs."""
    runner = CliRunner()

    def fake_scrape_documents(
        source: str,
        *,
        documents: int | None,
        link_mode: LinkMode,
        url: str | None = None,
    ) -> ScrapeRun:
        return ScrapeRun([_document()], total=None)

    def fail_progress(
        documents: Iterator[ScrapedDocument],
        *,
        total: int | None,
    ) -> Iterator[ScrapedDocument]:
        raise AssertionError("progress must be disabled")

    monkeypatch.setattr("amfv_datasets.scraping.cli.scrape_documents", fake_scrape_documents)
    monkeypatch.setattr("amfv_datasets.scraping.cli._progress_documents", fail_progress)

    result = runner.invoke(
        app,
        ["--source", "nice", "--output", str(tmp_path / "out.jsonl"), "--no-progress"],
    )

    assert result.exit_code == 0


def test_cli_run_uses_progress_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Progress is enabled by default."""
    runner = CliRunner()
    progress_calls = 0

    def fake_scrape_documents(
        source: str,
        *,
        documents: int | None,
        link_mode: LinkMode,
        url: str | None = None,
    ) -> ScrapeRun:
        return ScrapeRun([_document()], total=7)

    def fake_progress(
        documents: Iterator[ScrapedDocument],
        *,
        total: int | None,
    ) -> Iterator[ScrapedDocument]:
        nonlocal progress_calls
        progress_calls += 1
        assert total == 7
        yield from documents

    monkeypatch.setattr("amfv_datasets.scraping.cli.scrape_documents", fake_scrape_documents)
    monkeypatch.setattr("amfv_datasets.scraping.cli._progress_documents", fake_progress)

    result = runner.invoke(app, ["--source", "nice", "--output", str(tmp_path / "out.jsonl")])

    assert result.exit_code == 0
    assert progress_calls == 1


def test_cli_run_accepts_source_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI accepts a source URL as the scrape target."""
    runner = CliRunner()

    def fake_scrape_nice(
        *,
        documents: int | None,
        link_mode: LinkMode,
        url: str | None,
    ) -> ScrapeRun:
        assert documents == 1
        assert url == "https://www.nice.org.uk/guidance/ta1138/chapter/4-Implementation"
        assert link_mode is LinkMode.KEEP
        return ScrapeRun([_document()], total=1)

    def fake_progress(
        documents: Iterator[ScrapedDocument],
        *,
        total: int | None,
    ) -> Iterator[ScrapedDocument]:
        assert total == 1
        yield from documents

    monkeypatch.setitem(SCRAPERS, "nice", fake_scrape_nice)
    monkeypatch.setattr("amfv_datasets.scraping.cli._progress_documents", fake_progress)

    result = runner.invoke(
        app,
        ["--source", "nice", "--url", "https://www.nice.org.uk/guidance/ta1138/chapter/4-Implementation"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout.splitlines()[0])["external_id"] == "nice-ng1"


def test_cli_run_accepts_all_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI accepts --documents all."""
    runner = CliRunner()

    def fake_scrape_documents(
        source: str,
        *,
        documents: int | None,
        link_mode: LinkMode,
        url: str | None = None,
    ) -> ScrapeRun:
        assert source == "all"
        assert documents is None
        assert link_mode is LinkMode.KEEP
        assert url is None
        return ScrapeRun([_document()], total=12)

    monkeypatch.setattr("amfv_datasets.scraping.cli.scrape_documents", fake_scrape_documents)

    result = runner.invoke(app, ["--source", "all", "--documents", "all"])

    assert result.exit_code == 0
    assert json.loads(result.stdout.splitlines()[0])["external_id"] == "nice-ng1"


def test_cli_run_rejects_an_unregistered_source() -> None:
    """An unknown --source names the sources that are registered."""
    runner = CliRunner()

    result = runner.invoke(app, ["--source", "nhs"])

    assert result.exit_code != 0
    assert "'nhs'" in result.stderr
    assert ", ".join([ALL_SOURCES, *SCRAPERS]) in result.stderr


def test_expand_source_runs_every_registered_scraper() -> None:
    """The all source expands to the registry rather than a hand-written list."""
    assert _expand_source(ALL_SOURCES) == tuple(SCRAPERS)


def test_scrape_documents_dispatches_idsa(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scrape dispatcher calls IDSA through the shared registry."""

    def fake_scrape_idsa(
        *,
        documents: int | None,
        link_mode: LinkMode,
        url: str | None,
    ) -> ScrapeRun:
        assert documents == 1
        assert link_mode is LinkMode.STRIP
        assert url == "https://www.idsociety.org/practice-guideline/current-guideline/"
        return ScrapeRun([_idsa_document()], total=1)

    monkeypatch.setitem(SCRAPERS, "idsa", fake_scrape_idsa)

    scrape_run = scrape_documents(
        "idsa",
        documents=1,
        link_mode=LinkMode.STRIP,
        url="https://www.idsociety.org/practice-guideline/current-guideline/",
    )

    assert list(scrape_run.documents) == [_idsa_document()]


def test_scrape_documents_all_includes_idsa(monkeypatch: pytest.MonkeyPatch) -> None:
    """The all source combines NICE and IDSA scrape runs."""

    def fake_scrape_nice(
        *,
        documents: int | None,
        link_mode: LinkMode,
        url: str | None,
    ) -> ScrapeRun:
        assert documents == 1
        assert link_mode is LinkMode.KEEP
        assert url is None
        return ScrapeRun([_document()], total=1)

    def fake_scrape_idsa(
        *,
        documents: int | None,
        link_mode: LinkMode,
        url: str | None,
    ) -> ScrapeRun:
        assert documents == 1
        assert link_mode is LinkMode.KEEP
        assert url is None
        return ScrapeRun([_idsa_document()], total=1)

    monkeypatch.setitem(SCRAPERS, "nice", fake_scrape_nice)
    monkeypatch.setitem(SCRAPERS, "idsa", fake_scrape_idsa)

    scrape_run = scrape_documents(ALL_SOURCES, documents=1, link_mode=LinkMode.KEEP)

    assert scrape_run.total == 2
    assert [document.external_id for document in scrape_run.documents] == [
        "nice-ng1",
        "idsa-current-guideline",
    ]


def _document() -> ScrapedDocument:
    return ScrapedDocument(
        source="nice",
        external_id="nice-ng1",
        title="Guideline 1",
        url="https://www.nice.org.uk/guidance/ng1",
        content="content",
        metadata={"ref": "NG1"},
    )


def _idsa_document() -> ScrapedDocument:
    return ScrapedDocument(
        source="idsa",
        external_id="idsa-current-guideline",
        title="Current Guideline",
        url="https://www.idsociety.org/practice-guideline/current-guideline/",
        content="content",
        metadata={"slug": "current-guideline"},
    )


class _TextSink:
    def __init__(self) -> None:
        self.value = ""

    def write(self, text: str) -> int:
        self.value += text
        return len(text)
