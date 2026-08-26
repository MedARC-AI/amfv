# ICRC, Mayo Clinic, and SPOR scraper validation — 2026-08-26

This report records focused live and offline validation for the three retained
sources. Live runs used an explicit local permission reference, bounded document
counts, in-memory source handling, and temporary JSONL output. The temporary
PDF, JSONL, HTML, Markdown, sitemap, manifest, and review-output artifacts were
deleted after the checks. No credentials or signed download query values are
recorded here.

## Result

The expanded live corpus produced **204 successful documents**: 99 ICRC, 69
Mayo Clinic, and 36 SPOR. This supersedes the initial 14-document smoke pass.
Across all 204 records there were:

- 204 unique external IDs and 204 unique normalized-content SHA-256 values;
- no empty content, malformed SHA-256 values, or incomplete JSONL records;
- no missing `metadata.source_format_types` or
  `metadata.source_media_types` values;
- 88 HTML records, 38 PDF records, and 78 records assembled from HTML plus PDF;
- normalized content sizes from 426 to 2,925,647 characters and section counts
  from 1 to 286.

Every resulting document records both:

- `metadata.source_format_types`: normalized retrieval formats such as `html`
  and `pdf`;
- `metadata.source_media_types`: corresponding MIME types such as `text/html`
  and `application/pdf`.

`provenance.content_type` remains `text/markdown` because it describes the
normalized output, while individual retrieval receipts retain the server's
reported content type.

The larger corpus found one additional defect in direct-ICRC-PDF title
selection. Full-page OCR can emit an H2 cover title before a later, unrelated H1
body heading. Title selection now searches only early front matter, accepts H1
or H2, and rejects contents, organization-only, numeric, colon-terminated, and
implausibly sized headings. Focused regression cases cover the observed PDF.
An end-to-end rerun of `icrc-002-118009` then selected the OCR cover heading
`Guidelines forassessment inemergencies` rather than the unrelated later body
heading `Injured GPS coordinates:`. The remaining fused words are faithful to
the OCR output rather than invented title text.

## ICRC

The official publication sitemap contained 728 English publication URLs. The
bounded live manifest used 160 candidates: two legacy/current direct PDFs and
158 evenly sampled publication landing pages, including 106 numbered and 52
slug-only routes. The run stopped after 99 complete documents because the next
optional tail entered another expensive full-page-OCR case.

The 99 completed documents covered:

| Dimension | Result |
|---|---|
| Retrieval shape | 2 PDF-only, 19 HTML-only, 78 HTML+PDF |
| PDF resolution | 80 converted, 3 pages with no direct PDF link, 16 unresolved PDF links retained as landing-page content |
| Conversion backend | 69 `pdf-inspector`, 11 Docling, 19 landing-page-only records |
| Content size | 426 / 50,332 / 2,925,647 characters (minimum / median / maximum) |
| Sections | 1 / 2 / 39 (minimum / median / maximum) |
| Metadata | 0 missing format types; 0 missing media types |

The run exercised legacy and current direct-PDF paths, numbered and slug-only
publication pages, shop-page PDF resolution, redacted signed queries, PDF
magic-byte detection for `application/octet-stream`, unresolved/no-link landing
fallbacks, native extraction, Docling, OCR, full-page OCR, and conversion
watchdogs.

## Mayo Clinic

The live corpus used 70 current official URLs across oncology, cardiology,
neurology, mental health, respiratory, digestive, dermatologic, renal,
reproductive, pediatric, and musculoskeletal topics. It intentionally mixed
both supported article route families.

| Dimension | Result |
|---|---|
| Successful documents | 69 of 70 candidates |
| Route families | 56 symptoms/causes; 13 diagnosis/treatment |
| Safely rejected route | 1 stale URL redirected to a different canonical article |
| Content size | 2,302 / 9,301 / 37,232 characters (minimum / median / maximum) |
| Sections | 6 / 11 / 34 (minimum / median / maximum) |
| Metadata | all 69 `html` and `text/html`; 0 missing values |

The scraper correctly rejected the moved page instead of assigning content from
a different canonical article. Successful pages exercised current rendered AEM
markup, canonical-route checks, publication dates/authors, recurring chrome
removal, and both article layouts. The A–Z index was temporarily returning HTTP
403 during the expanded run, so the validator did not hammer or bypass it; the
current direct-page corpus was used instead.

## SPOR Evidence Alliance

The official April-2018 asset-map PDF contained 6,488 link annotations. After
normalizing and deduplicating direct-PDF candidates, the bounded live pool used
448 candidates stratified across 132 asset-map pages and 67 publisher hosts.
The run stopped after 36 successful PDFs and 94 recorded stale candidates.

| Dimension | Result |
|---|---|
| Successful documents | 36 PDFs across 12 reachable publisher hosts |
| Conversion backend | 28 `pdf-inspector`; 8 Docling |
| Stale-link behavior | 94 failures recorded and skipped before later successes |
| Content size | 8,232 / 39,430 / 1,276,917 characters (minimum / median / maximum) |
| Sections | 1 / 1 / 286 (minimum / median / maximum) |
| Metadata | all 36 `pdf` and `application/pdf`; 0 missing values |

The successful set included government, cancer, mental-health, primary-care,
hypertension, laboratory-medicine, and nursing publishers. It exercised native
PDF extraction, Docling, ordinary OCR, full-page OCR fallback, multi-column and
very long documents, stale-link accumulation, and resumption after long stale
candidate sequences. SPOR remains a historical, non-endorsing registry last
updated in April 2018; successful parsing does not establish that a listed
guideline is current.

## Timing and throughput

Wall-clock intervals were recovered from the local task history after the
temporary corpora were deleted. These measurements include configured request
delays, retries, stale candidates, PDF conversion, OCR, and the interrupted
in-flight tail at the end of the bounded ICRC and SPOR runs. They therefore
describe observed production throughput rather than isolated parser CPU time.

| Source | Successful documents | Attempted candidates known | Wall time | Wall time per success | Successful throughput | Wall time per attempted candidate |
|---|---:|---:|---:|---:|---:|---:|
| ICRC | 99 | 99 complete plus 1 interrupted tail | 1:21:25 | 49.3 seconds | 1.22 documents/minute | Not reported because the final candidate was incomplete |
| Mayo Clinic | 69 | 70 | 14:05 | 12.2 seconds | 4.90 documents/minute | 12.1 seconds |
| SPOR | 36 | 130 (36 successful and 94 stale) plus 1 interrupted tail | 41:28 | 69.1 seconds | 0.87 documents/minute | 19.1 seconds across the 130 completed attempts |

The three runs overlapped. Their summed source-process time was approximately
2:16:58, while elapsed time from the beginning of ICRC to the bounded stop of
ICRC and SPOR was approximately 1:21:25. Across the 204 successful records this
is 40.3 seconds of summed source-process time per success, or 2.51 successful
documents per elapsed minute while the validations overlapped.

Exact per-document mean, median, percentile, minimum, and maximum
`provenance.scrape_duration_ms` values are unavailable for this run. Those
values were present in every temporary JSONL record, but the records were
deleted during the requested output cleanup before timing statistics were
requested. The table does not substitute estimates for those missing
distributions. Future large validation runs should aggregate timing fields into
the retained report before deleting their temporary records.

## Automated checks

- Focused ICRC regression suite: `25 passed` after the expanded live run found
  the OCR title edge case.
- Full repository suite with PDF dependencies: `193 passed`.
- `uv run ruff format .`: passed; one test file was normalized.
- `uv run ruff check .`: passed.
- `git diff --check`: passed.
- `uv build --offline --package amfv-datasets`: source distribution and wheel
  built successfully; disposable build outputs were deleted afterward.
