# PDF backend comparison

Why `amfv_datasets.scraping.pdf` defaults to Docling.

WHO publishes guidelines as PDFs and exposes only a short Overview on the
landing page, so the guideline body has to come from the PDF. Picking the
converter is a correctness decision rather than a formatting one: a backend that
silently drops rows from a GRADE evidence table produces text that reads fine
but states the wrong thing, which is the worst possible failure for fact
verification.

## Sample

Five WHO guidelines, chosen for a spread of length and table density. All five
are born-digital with a clean text layer (0-2 near-empty pages out of 25
sampled per document), so OCR is pure overhead and is disabled.

| Publication | Guideline | Pages |
| --- | --- | --- |
| 9789240121805 | Prevention of bloodstream infections, part 2: central venous catheters | 152 |
| 9789240124233 | Consolidated HIV guidelines: service delivery | 50 |
| 9789240073593 | Carbohydrate intake for adults and children | 100 |
| 9789240084278 | Mental health policy and strategic action plan | 184 |
| 9789241597906 | Hand hygiene in health care | 270 |

Backends were run over a comparable page excerpt of each document.

## Results

Measured on an Apple Silicon laptop, CPU only.

| Backend | Docs converted | Seconds per doc | Stray page-number lines | Notes |
| --- | --- | --- | --- | --- |
| Docling | 5 / 5 | 2.7-13.7 warm, 126.5 first run | 0 | Chosen |
| PyMuPDF4LLM | 5 / 5 | 1.9-5.1 | 40 across 2 docs | Fastest, corrupts table headers |
| Marker | spot check only | 72.7 | 0 | Drops table rows |
| MinerU | not run | - | - | Ruled out on cost after Docling met the bar |

Docling's 126.5 s first run is one-off model loading; later documents in the
same process took 2.7-13.7 s.

### Why not PyMuPDF4LLM

It is the fastest option and its table row counts are close to Docling's, but it
emitted 40 bare page-number lines across two documents and corrupted table
header rows. Stray page numbers become sentence fragments once a document is
chunked for retrieval.

### Why not Marker

On the table-dense carbohydrate guideline Marker recovered 87 table rows where
Docling and PyMuPDF4LLM both found ~136, dropping outcome-category rows from a
GRADE evidence table. The loss is silent: the surrounding prose is intact, so
nothing signals that the evidence table is now incomplete. It was also the
slowest at 72.7 s per document.

### Honest caveat

On 9789240084278 PyMuPDF4LLM emitted 42 table rows against Docling's 13. That
gap was not spot-checked, so it is not established which is closer to the
source. It did not change the decision, since the Marker and PyMuPDF4LLM
failures above are disqualifying on their own, but it is the open question if
someone revisits this.

## Decision

Docling. It converted all five documents, recovered the most heading structure,
left no page furniture in the output, preserved table rows, and runs on CPU so
contributors and CI do not need a GPU. It is MIT licensed.

## Seeing the extraction for yourself

`datasets/test/fixtures/pdf/` holds each sample PDF next to the markdown the
scraper produces from it:

| Input PDF | Extracted output |
| --- | --- |
| `who_9789240121805_excerpt.pdf` | `who_9789240121805_excerpt.expected.md` |
| `who_9789240124233_excerpt.pdf` | `who_9789240124233_excerpt.expected.md` |

Open the two side by side to judge the conversion without running anything. The
first shows a GRADE recommendation keeping its certainty rating; the second
shows a recommendation table keeping its rows and columns.

## Verifying

Three layers, so CI never depends on a PDF stack:

**1. Always runs, including CI.** `uv run pytest` covers the scraper wiring with
a stub converter. CI installs only `--group dev`, so the PDF tests report as
skipped and a Docling release cannot break the build.

**2. Real conversion, opt-in.** Installs Docling and converts the sample PDFs
for real:

```bash
uv sync --group pdf
uv run --group pdf pytest datasets/test/test_scraping_pdf_fixtures.py -v
```

These assert on meaning rather than formatting — that the recommendation text,
its certainty rating and its table rows are present — so they tolerate a Docling
upgrade that reflows whitespace.

**3. Byte-exact diff, opt-in.** Compares conversion against the committed
`.expected.md` files:

```bash
AMFV_CHECK_GOLDEN=1 uv run --group pdf pytest datasets/test/test_scraping_pdf_fixtures.py
```

This one is intentionally sensitive to the Docling version, which is why it is
off by default. If a Docling upgrade changes the output, review the diff and
then accept it:

```bash
AMFV_UPDATE_GOLDEN=1 uv run --group pdf pytest datasets/test/test_scraping_pdf_fixtures.py
```

The regenerated `.expected.md` files then show the change in review.

## Reproducing the benchmark

```bash
uv sync --group pdf
uv run --group pdf python datasets/benchmarks/pdf_backends.py
```

That runs against the committed excerpt fixtures. Pass `--pdf-dir` to point at
full guideline PDFs, and `--backend` to select backends. Marker and MinerU are
not project dependencies; install them separately to include them.
