"""Compare PDF text, Docling, raster OCR and Firecrawl on the same WHO excerpts.

Run from the workspace root with `uv run --group ocr python
datasets/benchmarks/pdf_ocr.py --output-dir /tmp/amfv-pdf-results`.
This is a small diagnostic, not a character-error-rate or model leaderboard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx

try:
    import pypdfium2 as pdfium

    HAS_PDFIUM = True
except ImportError:
    HAS_PDFIUM = False

try:
    from amfv_datasets.scraping.pdf import HAS_DOCLING as HAS_AMFV_DOCLING
    from amfv_datasets.scraping.pdf import pdf_to_markdown

    HAS_AMFV_PDF = True
except ImportError:
    HAS_AMFV_PDF = False
    HAS_AMFV_DOCLING = False

_FIXTURES = Path(__file__).resolve().parents[1] / "test" / "fixtures" / "pdf"
_MANIFEST = Path(__file__).with_name("pdf_ocr_manifest.json")
_BACKENDS = ("native", "tesseract", "docling", "firecrawl-auto", "firecrawl-ocr")


def _normalized(text: str) -> str:
    text = text.translate(str.maketrans({"–": "-", "−": "-", "‑": "-", "\u00ad": ""}))
    text = re.sub(r"-\s+", "-", text)
    return " ".join(text.casefold().split())


def _native_text(path: Path) -> str:
    with pdfium.PdfDocument(path) as document:
        pages: list[str] = []
        for page in document:
            text_page = page.get_textpage()
            try:
                pages.append(text_page.get_text_range())
            finally:
                text_page.close()
                page.close()
        return "\n\n".join(pages)


def _tesseract_text(path: Path, *, dpi: int, page_segmentation: int) -> str:
    with tempfile.TemporaryDirectory(prefix="amfv-ocr-") as directory, pdfium.PdfDocument(path) as document:
        pages: list[str] = []
        for index, page in enumerate(document, start=1):
            image_path = Path(directory) / f"page-{index}.png"
            bitmap = page.render(scale=dpi / 72)
            try:
                bitmap.to_pil().save(image_path)
            finally:
                bitmap.close()
                page.close()
            result = subprocess.run(
                ["tesseract", str(image_path), "stdout", "-l", "eng", "--psm", str(page_segmentation)],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
            pages.append(result.stdout)
        return "\n\n".join(pages)


def _firecrawl_text(document: dict[str, Any], *, mode: str, api_key: str) -> tuple[str, dict[str, Any]]:
    """Check remote input identity before sending a public fixture URL for parsing."""
    url = document["public_fixture_url"]
    with httpx.Client(timeout=240, follow_redirects=True) as client:
        source = client.get(url)
        source.raise_for_status()
        actual_hash = hashlib.sha256(source.content).hexdigest()
        if actual_hash != document["sha256"]:
            raise ValueError(f"Remote fixture differs from the local PDF: {document['filename']}")
        response = client.post(
            "https://api.firecrawl.dev/v2/scrape",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "url": url,
                "formats": ["markdown"],
                "parsers": [{"type": "pdf", "mode": mode, "maxPages": document["pages"], "pages": True}],
                "timeout": 180000,
                "maxAge": 0,
                "onlyMainContent": False,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError("Firecrawl returned an unsuccessful scrape; inspect the API dashboard")
        data = payload.get("data", {})
        markdown = data.get("markdown", "")
        if not markdown.strip():
            raise RuntimeError("Firecrawl returned no markdown; the PDF comparison is incomplete")
        return markdown, {"mode": mode, "metadata": data.get("metadata"), "pages": data.get("pages")}


def main() -> int:
    """Run selected backends and retain raw outputs, provenance and anchor checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=_BACKENDS, nargs="+", default=["native", "tesseract"])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--psm", type=int, choices=range(14), default=3)
    args = parser.parse_args()
    if args.dpi < 72:
        parser.error("--dpi must be at least 72")

    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "methodology": manifest["methodology"],
        "backends": args.backend,
        "raster_dpi": args.dpi,
        "tesseract_psm": args.psm,
        "results": results,
    }
    report_path = args.output_dir / "results.json"
    failed = False
    for document in manifest["documents"]:
        path = _FIXTURES / document["filename"]
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != document["sha256"]:
            raise ValueError(f"Local fixture changed; review and update the manifest: {path}")
        for backend in args.backend:
            result: dict[str, Any] = {
                "document": document["filename"],
                "sha256": actual_hash,
                "pages": document["pages"],
                "backend": backend,
            }
            results.append(result)
            api_key = os.environ.get("FIRECRAWL_API_KEY")
            if backend.startswith("firecrawl-") and not api_key:
                result.update(status="skipped", reason="FIRECRAWL_API_KEY is not configured")
            elif backend == "tesseract" and shutil.which("tesseract") is None:
                result.update(status="skipped", reason="Install the Tesseract executable and English language data")
            elif backend in {"native", "tesseract"} and not HAS_PDFIUM:
                result.update(status="skipped", reason="Install the raster dependencies with uv sync --group ocr")
            elif backend == "docling" and not HAS_AMFV_PDF:
                result.update(status="skipped", reason="The AMFV Docling exporter requires the WHO scraper PR")
            elif backend == "docling" and not HAS_AMFV_DOCLING:
                result.update(status="skipped", reason="Install WHO's PDF dependencies with uv sync --group pdf")
            else:
                start = time.perf_counter()
                try:
                    if backend == "native":
                        text = _native_text(path)
                        result["version"] = version("pypdfium2")
                    elif backend == "tesseract":
                        text = _tesseract_text(path, dpi=args.dpi, page_segmentation=args.psm)
                        result["version"] = subprocess.check_output(["tesseract", "--version"], text=True).splitlines()[
                            0
                        ]
                    elif backend == "docling":
                        text = pdf_to_markdown(path.read_bytes(), running_header=document["title"], name=path.name)
                        result["version"] = version("docling")
                        result["ocr"] = False
                        result["export"] = "AMFV markdown export, including footnotes"
                        result["accelerator"] = "Docling default auto"
                        result["table_structure"] = True
                    else:
                        text, details = _firecrawl_text(
                            document, mode=backend.removeprefix("firecrawl-"), api_key=api_key
                        )
                        details_path = args.output_dir / f"{path.stem}.{backend}.response.json"
                        details_path.write_text(json.dumps(details, indent=2) + "\n", encoding="utf-8")
                        result["version"] = "hosted API v2; model version not pinned"
                    if not text.strip():
                        raise ValueError("No readable output")
                    output_name = f"{path.stem}.{backend}.md"
                    (args.output_dir / output_name).write_text(text, encoding="utf-8")
                    normalized = _normalized(text)
                    checks = [
                        {
                            "name": anchor["name"],
                            "text": anchor["text"],
                            "present": _normalized(anchor["text"]) in normalized,
                        }
                        for anchor in document["anchors"]
                    ]
                    result.update(
                        status="ok",
                        seconds=round(time.perf_counter() - start, 3),
                        characters=len(text),
                        output=output_name,
                        anchors=checks,
                        anchors_found=sum(check["present"] for check in checks),
                        anchors_total=len(checks),
                    )
                except Exception as exc:  # noqa: BLE001 - keep completed backend evidence when another backend fails
                    failed = True
                    result.update(status="error", reason=str(exc), seconds=round(time.perf_counter() - start, 3))
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"{path.name}: {backend}: {result['status']}", flush=True)
    return int(failed)


__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
