"""Command-line interface for AMFV dataset scrapers."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from dataclasses import asdict
from enum import StrEnum
from itertools import chain
from pathlib import Path
from typing import Annotated, Protocol, TextIO

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from amfv_datasets.scraping.base import ScrapedDocument, ScrapeRun
from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.idsa import scrape_idsa
from amfv_datasets.scraping.nice import scrape_nice


class Scraper(Protocol):
    """Entry point a source module exposes to run one scrape."""

    def __call__(self, *, documents: int | None, link_mode: LinkMode, url: str | None) -> ScrapeRun:
        """Scrape a source into a run of documents."""
        ...


ALL_SOURCES = "all"

SCRAPERS: dict[str, Scraper] = {
    "nice": scrape_nice,
    "idsa": scrape_idsa,
}
"""Scraper entry point by source name. Adding a source is an import and an entry here."""


class OutputFormat(StrEnum):
    """Supported scraper output formats."""

    JSONL = "jsonl"
    HUGGINGFACE = "huggingface"
    MARKDOWN = "markdown"


app = typer.Typer(no_args_is_help=False, help="Run AMFV dataset web scrapers.")


def scrape_documents(
    source: str,
    *,
    documents: int | None,
    link_mode: LinkMode,
    url: str | None = None,
) -> ScrapeRun:
    """Configure a scrape for a source.

    Args:
        source: Scraper source to run. Use `ALL_SOURCES` to run every registered
            source.
        documents: Number of documents to scrape. When unset, each source runs
            until it is exhausted (default: None).
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text.
        url: Source URL to scrape as a single document (default: None).
    """
    if documents is not None and documents < 1:
        raise ValueError(f"documents must be at least 1; got {documents}")

    scrape_runs = [SCRAPERS[name](documents=documents, link_mode=link_mode, url=url) for name in _expand_source(source)]
    if len(scrape_runs) == 1:
        return scrape_runs[0]
    total = None if any(run.total is None for run in scrape_runs) else sum(run.total or 0 for run in scrape_runs)
    return ScrapeRun(documents=chain.from_iterable(run.documents for run in scrape_runs), total=total)


def write_jsonl(documents: Iterable[ScrapedDocument], output: TextIO) -> int:
    """Write scraped documents as JSON Lines.

    Args:
        documents: Documents to serialize.
        output: Writable text stream.
    """
    count = 0
    for document in documents:
        output.write(json.dumps(asdict(document), sort_keys=True))
        output.write("\n")
        count += 1
    return count


def write_huggingface_dataset(documents: Iterable[ScrapedDocument], output_path: Path) -> int:
    """Write scraped documents as a local Hugging Face dataset.

    Args:
        documents: Documents to serialize.
        output_path: Directory where `Dataset.save_to_disk` writes the dataset.
    """
    from datasets import Dataset

    rows = [asdict(document) for document in documents]
    dataset = Dataset.from_list(rows)
    dataset.save_to_disk(output_path)
    return len(rows)


def write_markdown_files(documents: Iterable[ScrapedDocument], output_path: Path) -> int:
    """Write scraped documents as one markdown file per document.

    Args:
        documents: Documents to serialize.
        output_path: Directory where markdown files are written.
    """
    output_path.mkdir(parents=True, exist_ok=True)
    count = 0
    for document in documents:
        path = output_path / f"{_markdown_filename(document)}.md"
        path.write_text(_markdown_document(document), encoding="utf-8")
        count += 1
    return count


def _expand_source(source: str) -> tuple[str, ...]:
    if source == ALL_SOURCES:
        return tuple(SCRAPERS)
    if source not in SCRAPERS:
        raise typer.BadParameter(f"unknown source {source!r}; choose from {', '.join([ALL_SOURCES, *SCRAPERS])}")
    return (source,)


@app.command(help="Run a scraper and write the scraped documents.")
def run(
    source: Annotated[str, typer.Option("--source", help=f"Scraper source to run: {', '.join([ALL_SOURCES, *SCRAPERS])}.")],  # noqa: E501
    url: Annotated[str | None, typer.Option("--url", help="Source URL to scrape as a single document.")] = None,
    documents: Annotated[str, typer.Option("--documents", help="Number of documents to scrape, or 'all'.")] = "1",
    link_mode: Annotated[LinkMode, typer.Option("--links", help="Whether to keep markdown links or strip links to text.")] = LinkMode.KEEP,  # noqa: E501
    output_format: Annotated[OutputFormat, typer.Option("--format", "-f", help="Output format.")] = OutputFormat.JSONL,
    output_path: Annotated[Path | None, typer.Option("--output", "-o", help="Output JSONL file, markdown directory, or Hugging Face dataset directory. JSONL defaults to stdout.")] = None,  # noqa: E501
    progress: Annotated[bool, typer.Option("--progress/--no-progress", help="Show a Rich progress bar.")] = True,
) -> None:  # fmt: skip
    """Run a scraper and write the scraped documents.

    Args:
        source: Scraper source to run.
        url: Source URL to scrape as a single document (default: None).
        documents: Number of documents to scrape, or "all" (default: "1").
        link_mode: Whether links are kept as markdown links or stripped to their
            visible text (default: LinkMode.KEEP).
        output_format: Output format to write (default: OutputFormat.JSONL).
        output_path: Output JSONL file, markdown directory, or Hugging Face
            dataset directory. When unset, JSONL is written to stdout (default:
            None).
        progress: Whether to show a Rich progress bar (default: True).
    """
    parsed_documents = _parse_documents(documents)
    scrape_run = scrape_documents(source, documents=parsed_documents, link_mode=link_mode, url=url)
    scraped_documents = scrape_run.documents
    if progress:
        scraped_documents = _progress_documents(scraped_documents, total=scrape_run.total)
    if output_format is OutputFormat.JSONL:
        count = _write_jsonl_output(scraped_documents, output_path)
    elif output_format is OutputFormat.HUGGINGFACE:
        if output_path is None:
            raise typer.BadParameter("--output is required when --format huggingface")
        count = write_huggingface_dataset(scraped_documents, output_path)
    else:
        if output_path is None:
            raise typer.BadParameter("--output is required when --format markdown")
        count = write_markdown_files(scraped_documents, output_path)
    target = url or source
    typer.echo(f"scraped {count} documents from {target}", err=True)


def _progress_columns(*, total: int | None) -> tuple[ProgressColumn, ...]:
    columns: list[ProgressColumn] = [
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
    ]
    if total is not None:
        columns.append(TaskProgressColumn())
    columns.extend(
        [
            TextColumn("{task.completed} documents"),
            TimeElapsedColumn(),
        ]
    )
    if total is not None:
        columns.append(TimeRemainingColumn())
    return tuple(columns)


def _progress_documents(
    documents: Iterable[ScrapedDocument],
    *,
    total: int | None,
) -> Iterable[ScrapedDocument]:
    console = Console(stderr=True)
    with Progress(*_progress_columns(total=total), console=console, transient=True) as progress:
        task = progress.add_task("Scraping", total=total)
        for document in documents:
            progress.advance(task)
            yield document


def _write_jsonl_output(documents: Iterable[ScrapedDocument], output_path: Path | None) -> int:
    if output_path is None:
        return write_jsonl(documents, sys.stdout)
    with output_path.open("w", encoding="utf-8") as output:
        return write_jsonl(documents, output)


def _parse_documents(value: str) -> int | None:
    normalized = value.strip().lower()
    if normalized == "all":
        return None
    try:
        documents = int(normalized)
    except ValueError as exc:
        raise typer.BadParameter("--documents must be a positive integer or 'all'") from exc
    if documents < 1:
        raise typer.BadParameter("--documents must be a positive integer or 'all'")
    return documents


def _markdown_filename(document: ScrapedDocument) -> str:
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", document.external_id).strip(".-")
    return filename or "document"


def _markdown_document(document: ScrapedDocument) -> str:
    metadata = [
        f"# {document.title}",
        "",
        f"Source: <{document.url}>",
        f"External ID: `{document.external_id}`",
        "",
        document.content.strip(),
        "",
    ]
    return "\n".join(metadata)


def main() -> None:
    """Run the Typer application."""
    app()


__all__ = [
    "ALL_SOURCES",
    "SCRAPERS",
    "OutputFormat",
    "Scraper",
    "app",
    "LinkMode",
    "main",
    "run",
    "scrape_documents",
    "write_huggingface_dataset",
    "write_jsonl",
    "write_markdown_files",
]
