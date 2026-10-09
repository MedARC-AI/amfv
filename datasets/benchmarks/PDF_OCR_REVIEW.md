# PDF extraction and OCR review

Local diagnostic, October 6, 2026. The Firecrawl versus FinePDFs comparison is
**not complete**. Firecrawl was skipped because `FIRECRAWL_API_KEY` is unset.
FinePDFs was inspected but its GPU OCR pipeline was not executed. “finepdf” is
provisionally interpreted as Hugging Face FinePDFs, pending user confirmation.

## Measured sample and method

Two committed English, born-digital WHO excerpts, six pages each:

- Bloodstream infections, publication `9789240121805`, source PDF pages 30-35.
- HIV service delivery, publication `9789240124233`, source PDF pages 11-16.

The fixtures, original URLs and reuse notes are documented in
[fixture attribution](../test/fixtures/pdf/README.md). Raw outputs are
research derivatives of the WHO sources and carry the source reuse conditions;
the repository's code licence does not replace those conditions.

Hardware: Apple M4 MacBook Air, 16 GB RAM; macOS 26.2, Python 3.13.15. Docling
used its default automatic accelerator selection, not a forced CPU benchmark.
Its model weights were already cached by the integration tests. Each elapsed
time covers conversion/export; Tesseract times include 300-DPI rendering and
all six page OCR calls. These are single runs, not stable throughput estimates.

Native extraction used PDFium's text layer. Tesseract received page PNGs only,
with English language data and automatic page segmentation (`--psm 3`). Docling
used OCR off and table structure/cell matching on. Rasterizing born-digital PDFs
tests an OCR path; it does not substitute for naturally scanned/degraded PDFs.

Seven source-checked anchors per excerpt cover recommendation wording,
population, a negation, concentration/risk numbers, age-dependent certainty,
intervals, task sharing and a date-column footnote. Relevant source pages were
rendered and checked visually. The checks normalize whitespace and hyphenated
line breaks. Anchor presence is not a clinical accuracy, recall, CER or WER
score, and it does not establish table alignment or absence of hallucinations.

## Results

| Backend | Version/configuration | CVC anchors | HIV anchors | Seconds: CVC / HIV |
| --- | --- | --- | --- | --- |
| Native PDF text | pypdfium2 5.14.0 | 7/7 | 6/7 | 0.024 / 0.026 |
| Tesseract | 5.5.3, English, 300 DPI, PSM 3 | 7/7 | 7/7 | 17.029 / 13.848 |
| Docling before footnote fix | 2.134.0, default markdown export | 7/7 | 6/7 | 12.787 / 5.156 |
| AMFV Docling after footnote fix | 2.134.0, missing footnotes retained with page provenance | 7/7 | 7/7 | 11.282 / 5.035 |
| Firecrawl auto and forced OCR | Hosted API | skipped | skipped | not measured |
| FinePDFs | Published full pipeline | not run | not run | not measured |

The native HIV miss is a text-layer artefact: `high-certainty` contains U+FFFE
in place of the hyphen. Both certainty populations remain readable in the raw
output, but exact matching fails. This is not an omitted recommendation.

Docling preserved all 26 dated recommendation rows in the two-page HIV table,
including their date/status columns. Their order and the 2025 Updated/New
statuses were checked against the source rendering. Native output separates
status values into a block after the recommendation text, which weakens their
row association. Tesseract's anchor success alone does not prove a usable
three-column table: its output is unstructured text, groups statuses separately,
and omits the `New` status for the 2025 mental-health recommendation on page 2.

The default Docling export omitted the footnote defining the date column.
The structured conversion still contained that footnote. AMFV now appends
detected footnotes missing from markdown under `PDF footnotes`, with PDF page
numbers. The HIV footnote retains its source wording and page 2 provenance.
The real-PDF test and refreshed reference output check this behavior. Table
captions, certainty phrases and recommendation rows remain present.

Results and raw markdown:

- [Initial comparison](results/2026-10-06/results.json)
- [Final Docling export](results/2026-10-06/footnotes-preserved/results.json)
- [Manifest and source hashes](pdf_ocr_manifest.json)
- [Reproducible runner](pdf_ocr.py)

The five-document Docling/Marker/PyMuPDF4LLM results in the original
[WHO PR #6](https://github.com/MedARC-AI/amfv/pull/6) came from that author.
They were not reproduced in this update.

## Firecrawl and FinePDFs status

The runner supports Firecrawl API v2 `auto` and forced `ocr` modes, per its
[official API guide](https://github.com/firecrawl/firecrawl-docs/blob/84e2b419134e753e6f22ac1f3fcb713256154487/advanced-scraping-guide.mdx).
It verifies that the public fixture URL has exactly the local PDF's SHA-256
before submitting it. Only the six-page public excerpts are configured. Missing
credentials produce an explicit skipped result. No Firecrawl request was sent.
Its hosted model revision is not pinned, so retain the API response metadata.

[FinePDFs](https://github.com/huggingface/finepdfs/tree/15aa381fae828c8acd6efa058fa0b41d2c602ede)
is a corpus-processing pipeline, not a drop-in equivalent of the Firecrawl API.
Its published code uses modified vendored Docling for embedded text and
`reducto/RolmOCR` served by vLLM for OCR. Its embedded-text extractor disables
table structure, unlike this AMFV configuration. A stock Docling run therefore
cannot be reported as a FinePDFs result. Its top-level code is AGPL-3.0; this
update did not copy it into the AMFV package.

No NVIDIA/vLLM inference host or remote OCR endpoint was provided for this Mac.
The next comparison needs the confirmed intended “finepdf” project, Firecrawl
credentials and actual FinePDFs outputs from the same hashed excerpts. A larger
test should also include naturally scanned pages and manually transcribed
ground truth, with table-cell/date/status associations and numeric/negation
errors graded separately.

## Open OCR candidates

These are candidates for the next controlled comparison, not winners established
by this two-excerpt diagnostic:

| Candidate | Role | Evidence/status |
| --- | --- | --- |
| [Tesseract](https://github.com/tesseract-ocr/tesseract) | Local CPU baseline for scanned text | Tested here; text anchors survived, table structure needs a separate parser. |
| [RolmOCR](https://huggingface.co/reducto/RolmOCR) | First OCR model for a faithful FinePDFs comparison | FinePDFs' published OCR model; Apache-2.0 model card. Not run here. |
| [PaddleOCR / PP-StructureV3 / PaddleOCR-VL](https://github.com/PaddlePaddle/PaddleOCR) | Challenger for layout/table-rich guideline pages | Official toolkit provides structured Markdown/JSON and table-cell coordinates. Not run here. |
| [olmOCR](https://github.com/allenai/olmocr) | Independent document-OCR challenger | Apache-2.0; supports local NVIDIA inference or a remote server. Not run here. |

Start with RolmOCR for FinePDFs fidelity, PaddleOCR's structure pipeline for
table quality, and Tesseract as the inexpensive baseline. Keep Firecrawl's
hosted parser as a separate backend rather than assuming its OCR model is
interchangeable with these candidates. Use native extraction for clean text
layers when its order and encoding survive source checks.

## Running the diagnostic

New local runs under `results/` are ignored by default. The reviewed
`results/2026-10-06/` evidence is deliberately retained in version control.
Use a new output directory when rerunning an experiment to preserve that evidence.

```bash
uv sync --dev --group ocr
uv run --group ocr python datasets/benchmarks/pdf_ocr.py \
  --output-dir /tmp/amfv-pdf-results --backend native tesseract
```

The optional `docling` backend uses the AMFV exporter from the separate WHO
scraper update. It records a skip until that module and its PDF dependencies
are installed. After the WHO change is merged, reproduce the final export with:

```bash
uv sync --dev --group pdf --group ocr
uv run --group pdf --group ocr python datasets/benchmarks/pdf_ocr.py \
  --output-dir /tmp/amfv-docling-results --backend docling
```

The initial default-export measurements are retained as historical evidence;
the runner uses the final AMFV exporter when that integration is available.

Tesseract and its English language data must be installed separately. Tesseract
5.5.3 was installed with Homebrew for this run. PDF model weights were downloaded
to the normal cache, and FinePDFs was inspected in `/tmp/amfv-finepdfs-20261006`.

With `FIRECRAWL_API_KEY` configured securely in the process environment:

```bash
uv run --group ocr python datasets/benchmarks/pdf_ocr.py \
  --output-dir /tmp/amfv-firecrawl-results --backend firecrawl-auto firecrawl-ocr
```

The second command calls the paid hosted service when credentials are present.
The missing-key path was exercised here; live Firecrawl integration is untested.
