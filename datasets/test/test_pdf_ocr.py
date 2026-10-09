"""Exercise the standalone PDF/OCR diagnostic's public command."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_DATASETS = Path(__file__).resolve().parents[1]
_RUNNER = _DATASETS / "benchmarks" / "pdf_ocr.py"
_PDF_FIXTURES = _DATASETS / "test" / "fixtures" / "pdf"


def test_firecrawl_without_credentials_records_skips(tmp_path: Path) -> None:
    """A missing API key records every requested skip without calling Firecrawl."""
    environment = os.environ.copy()
    environment.pop("FIRECRAWL_API_KEY", None)
    result = subprocess.run(
        [sys.executable, str(_RUNNER), "--output-dir", str(tmp_path), "--backend", "firecrawl-auto", "firecrawl-ocr"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "results.json").read_text())
    assert len(report["results"]) == 4
    assert {entry["backend"] for entry in report["results"]} == {"firecrawl-auto", "firecrawl-ocr"}
    assert all(entry["status"] == "skipped" for entry in report["results"])
    assert all("FIRECRAWL_API_KEY" in entry["reason"] for entry in report["results"])
    assert not list(tmp_path.glob("*.md"))


def test_native_backend_retains_pdf_provenance_and_raw_text(tmp_path: Path) -> None:
    """Native extraction retains the source hashes and clinically relevant wording."""
    pytest.importorskip("pypdfium2", reason="requires the root ocr dependency group")
    result = subprocess.run(
        [sys.executable, str(_RUNNER), "--output-dir", str(tmp_path), "--backend", "native"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "results.json").read_text())
    assert len(report["results"]) == 2
    for entry in report["results"]:
        assert entry["status"] == "ok"
        assert entry["sha256"] == hashlib.sha256((_PDF_FIXTURES / entry["document"]).read_bytes()).hexdigest()
        markdown = (tmp_path / entry["output"]).read_text()
        assert markdown.strip()
        assert "certainty" in markdown
    cvc = next(entry for entry in report["results"] if "1805" in entry["document"])
    assert cvc["anchors_found"] == 7


def test_invalid_raster_resolution_is_rejected_before_output(tmp_path: Path) -> None:
    """An invalid DPI fails before creating an experiment directory."""
    output = tmp_path / "invalid"
    result = subprocess.run(
        [sys.executable, str(_RUNNER), "--output-dir", str(output), "--dpi", "0"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 2
    assert "--dpi must be at least 72" in result.stderr
    assert not output.exists()
