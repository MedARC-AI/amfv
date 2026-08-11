"""Compare PDF-to-markdown backends on WHO guideline PDFs.

Reproduces the measurements in `README.md` that led to Docling being the default
backend in `amfv_datasets.scraping.pdf`. Runs against the committed excerpt
fixtures by default, so it works offline:

    uv run --group pdf python datasets/benchmarks/pdf_backends.py

Point it at full guideline PDFs for a heavier comparison:

    uv run --group pdf python datasets/benchmarks/pdf_backends.py --pdf-dir /tmp/who-pdfs

Backends that are not installed are reported as skipped rather than failing the
run. Marker and MinerU are not project dependencies; install them separately to
include them.
"""

from __future__ import annotations

import argparse
import re
import time
from collections.abc import Callable
from pathlib import Path

from amfv_datasets.scraping.pdf import pdf_to_markdown

DEFAULT_PDF_DIR = Path(__file__).resolve().parents[1] / "test" / "fixtures" / "pdf"
_STRAY_PAGE_NUMBER_RE = re.compile(r"\s*\d{1,3}\s*")


def convert_docling(path: Path) -> str:
    """Convert with the backend the scrapers actually ship.

    Args:
        path: PDF to convert.
    """
    return pdf_to_markdown(path.read_bytes(), name=path.name)


def convert_pymupdf4llm(path: Path) -> str:
    """Convert with PyMuPDF4LLM.

    Args:
        path: PDF to convert.
    """
    import pymupdf4llm

    return pymupdf4llm.to_markdown(str(path))


def convert_marker(path: Path) -> str:
    """Convert with Marker.

    Args:
        path: PDF to convert.
    """
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered

    converter = PdfConverter(artifact_dict=create_model_dict(), config={"pdftext_workers": 1})
    text, _, _ = text_from_rendered(converter(str(path)))
    return text


BACKENDS: dict[str, Callable[[Path], str]] = {
    "docling": convert_docling,
    "pymupdf4llm": convert_pymupdf4llm,
    "marker": convert_marker,
}


def measure(markdown: str) -> dict[str, int]:
    """Score converted markdown on structure recovery and page furniture.

    Table rows matter most: a backend that silently drops rows from a GRADE
    evidence table produces text that reads fine but states the wrong thing.

    Args:
        markdown: Converted markdown to score.
    """
    lines = markdown.splitlines()
    return {
        "chars": len(markdown),
        "headings": sum(1 for line in lines if line.strip().startswith("#")),
        "table_rows": sum(1 for line in lines if line.strip().startswith("|")),
        "stray_page_numbers": sum(1 for line in lines if _STRAY_PAGE_NUMBER_RE.fullmatch(line)),
    }


def run(pdf_dir: Path, backend_names: list[str]) -> None:
    """Convert every PDF in a directory with each backend and print the scores.

    Args:
        pdf_dir: Directory of PDFs to convert.
        backend_names: Backends to run.
    """
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"No PDFs found in {pdf_dir}")

    header = f"{'backend':<13}{'document':<34}{'secs':>7}{'chars':>9}{'head':>6}{'rows':>6}{'stray':>7}"
    print(header)
    print("-" * len(header))
    for name in backend_names:
        for pdf in pdfs:
            started = time.perf_counter()
            try:
                scores = measure(BACKENDS[name](pdf))
            except ImportError:
                print(f"{name:<13}{'(not installed)':<34}")
                break
            except Exception as exc:  # noqa: BLE001 - backends raise library-specific errors
                print(f"{name:<13}{pdf.stem[:33]:<34} FAILED {str(exc)[:60]}")
                continue
            elapsed = time.perf_counter() - started
            print(
                f"{name:<13}{pdf.stem[:33]:<34}{elapsed:>7.1f}{scores['chars']:>9}"
                f"{scores['headings']:>6}{scores['table_rows']:>6}{scores['stray_page_numbers']:>7}"
            )


def main() -> None:
    """Parse arguments and run the comparison."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR, help="directory of PDFs to convert")
    parser.add_argument(
        "--backend",
        action="append",
        choices=sorted(BACKENDS),
        help="backend to run; repeatable, defaults to all",
    )
    args = parser.parse_args()
    run(args.pdf_dir, args.backend or sorted(BACKENDS))


if __name__ == "__main__":
    main()
