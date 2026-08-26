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
bounded live manifest used 160 candidates: two direct PDFs and
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

The run exercised direct-PDF paths, numbered and slug-only
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

## Throughput profiling and optimization follow-up

A later same-day follow-up profiled the three retained sources by phase, then
repeated the exact same bounded live corpus after optimization. The corpus mixed
native PDF conversion, Docling fallback, ICRC shop resolution, both Mayo route
families, the SPOR report parser, stale publisher hosts, and a reachable SPOR
PDF. Source payloads and normalized output were kept only in memory; the two
temporary profile summaries were deleted after their aggregate results were
recorded here.

The request policy was not relaxed. ICRC and SPOR still enforce a five-second
minimum interval between scrape-attempt starts, Mayo still enforces ten seconds,
and retry, timeout, DNS/public-address, conversion-quality, and OCR behavior are
unchanged. Processing time now counts toward that minimum interval instead of a
full delay being added after processing. ICRC manifest runs also lazily open one
shop browser and reuse its page rather than launching Chromium for every shop
product. Direct-URL ICRC runs retain the isolated one-product browser lifecycle.

### Before and after

| Source/profile corpus | Before wall time | After wall time | Wall-time reduction | Before mean / median / p90 per success | After mean / median / p90 per success |
|---|---:|---:|---:|---:|---:|
| ICRC, 4/4 documents | 118.292 s | 87.658 s | 30.634 s (25.90%) | 29.573 / 15.420 / 85.962 s | 21.894 / 7.603 / 71.575 s |
| Mayo Clinic, 4/4 documents | 33.632 s | 31.968 s | 1.664 s (4.95%) | 8.377 / 10.528 / 10.659 s | 7.966 / 9.648 / 10.270 s |
| SPOR, 1 success after 3 stale candidates | 184.145 s | 171.857 s | 12.288 s (6.67%) | 184.144 / 184.144 / 184.144 s | 171.857 / 171.857 / 171.857 s |

The ICRC sample comprised the direct PDF `icrc-002-4126`, two current
shop-backed publications, and the Docling-backed `0790-discover-icrc`. The Mayo
sample comprised two symptoms/causes and two diagnosis/treatment routes. The
SPOR sample used the official report, encountered the same three stale
candidates, and emitted the same CCSMH delirium guideline.

### Bottleneck attribution

| Source | Measured phases and conclusion |
|---|---|
| ICRC | The difficult Docling conversion remained dominant: 68.013 s before and 64.058 s after. The three ordinary `pdf-inspector` conversions totaled only 0.227 s before and 0.218 s after. Three separate shop resolutions cost 24.756 s before; browser reuse reduced them to 8.307 s total (6.771 s startup/first product, then 0.791 s and 0.745 s). HTTP downloads totaled 10.240 s before and 7.057 s after. Both browser reuse and start-interval pacing are material; OCR remains the intentional quality-preserving tail. |
| Mayo Clinic | Baseline browser fetches totaled 2.673 s and HTML normalization only 0.058 s, while three fixed post-document sleeps totaled 30 s. In the after run browser fetches were actually slower at 4.479 s and normalization remained 0.056 s, yet wall time still fell because fetch/parse work satisfied part of each unchanged ten-second start interval. The publisher/browser plus courtesy interval—not HTML parsing—is the remaining limit. |
| SPOR | Baseline retrieval consumed about 64.049 s: two dead-host attempts took 31.201 s and 31.036 s, while inventory and successful-PDF downloads were fast. Inventory annotation parsing took 0.661 s and Docling took 104.358 s. Afterward retrieval remained about 64.371 s, parsing 0.642 s, and Docling 102.536 s. Replacing three full five-second sleeps with only the unelapsed start interval removed roughly 10.7 s. Transport retry/timeout and Docling quality gates remain intact to avoid dropping temporarily reachable or scan-heavy documents. |

Observed wall-time differences include normal publisher and OCR variance, so
the table does not attribute every saved millisecond to code. The phase split is
what supports the conclusions above.

### Quality and count equivalence

The before/after document arrays were compared mechanically. All nine documents
matched exactly on external ID, title, canonical URL, normalized content
SHA-256, content byte count, section count, source format types, source media
types, conversion backend, and source-PDF SHA-256 where applicable. Counts were
also identical: ICRC 4/4, Mayo 4/4, and SPOR one success after the same three
stale candidates. No scraper increased throughput by skipping a conversion,
shortening content, weakening a route check, or suppressing a stale candidate.

The implementation now retains enough metrics for future runs without keeping
scraped corpora:

- successful HTTP and browser receipts record `retrieval_duration_ms`;
- each document records `provenance.phase_timings_ms` for its applicable
  retrieval, inventory parsing, HTML normalization, shop resolution, and PDF
  conversion phases;
- each successful record retains `request_start_pacing_delay_ms`;
- `ScrapeTiming.as_dict()` and the CLI completion line report mean, median, and
  nearest-rank p90 document time in addition to minimum, maximum, total wall
  time, and the raw per-document durations.

Format provenance remains unchanged: all records still carry
`metadata.source_format_types` and `metadata.source_media_types`, while the
normalized output remains `text/markdown`.

## Distributed beginning/middle/end validation follow-up

A final same-day pass tested fixed positions across each source instead of
requesting the first N records. The run exercised 58 selected positions or
candidates and produced 44 complete documents: 21 ICRC, 16 Mayo Clinic, and 7
SPOR. The other 14 positions were stale SPOR publisher links and remain recorded
as failures rather than being silently replaced with easier documents.

Across the 44 successful records there were 44 unique external IDs and 44
unique normalized-content SHA-256 values. Every record had nonempty content,
matching content byte counts and hashes, `text/markdown` normalized output,
nonempty source format and media-type metadata, and valid section counts. The
shared text-quality diagnostics found no likely-corrupt output, invalid control
characters, or Unicode replacement characters.

### ICRC positional sample

The current official sitemap contained 728 English `/en/publication/` URLs.
The validator selected exactly 21 entries at five-percent intervals from the
unchanged sitemap order: indices 0, 36, 72, 109, 145, 181, 218, 254, 290, 327,
363, 399, 436, 472, 508, 545, 581, 617, 654, 690, and 727. All 21 emitted a
document, so the run covered the first, midpoint, and final sitemap entries
rather than a leading slice.

| Dimension | Result |
|---|---|
| Retrieval shape | 16 HTML+PDF; 5 landing-page-only |
| PDF resolution | 16 converted; 2 unresolved; 3 with no direct PDF link |
| Conversion backend | 14 `pdf-inspector`; 2 Docling |
| Content size | 855 / 50,121 / 718,654 characters (minimum / median / maximum) |
| Sections | 1 / 2 / 12 (minimum / median / maximum) |
| Per-document time | 87.596 / 5.128 / 367.859 seconds (mean / median / p90); 1.506 / 904.361 seconds (minimum / maximum) |
| Metadata and text quality | 0 missing formats/media types; 0 likely-corrupt outputs; 0 invalid/replacement characters |

The 20th-percentile publication reached the configured 900-second Docling
watchdog and safely retained its landing page with the timeout in `pdf_error`.
The 45th- and 75th-percentile publications completed full-page OCR in 367.859
and 417.957 seconds respectively. The final sitemap publication also completed
as a landing-page-only record, demonstrating that the tail was actually read.

This sample exposed a browser-reuse lifecycle defect on the 5th-percentile
publication. Its shop product lacked the expected English PDF option. The clean
retry path attempted to open a second synchronous Playwright context before
closing the reused context, which Playwright rejects. The recovery path now
closes and removes the failed browser context before opening one fresh context.
The same unchanged 21-position manifest then continued through all remaining
positions; the unavailable PDF was represented honestly as landing-page-only
content with its resolution error retained.

### Mayo Clinic alphabetic sample

The publisher began returning HTTP 403 while a complete A-Z index enumeration
was in progress, so the validator did not hammer or bypass it. Instead, 16
current official article URLs were fixed from the beginning, middle, and end
alphabet bands A, M, and Z. The set included 12 symptoms/causes routes and 4
diagnosis/treatment routes, including both route types in the A and Z bands.

| Dimension | Result |
|---|---|
| Successful documents | 16 of 16 fixed URLs |
| Route families | 12 symptoms/causes; 4 diagnosis/treatment |
| Content size | 4,170 / 9,150 / 21,808 characters (minimum / median / maximum) |
| Source sections | 7 / 11 / 17 (minimum / median / maximum) |
| Per-document time | 9.502 / 10.051 / 10.412 seconds (mean / median / p90); 1.851 / 10.513 seconds (minimum / maximum) |
| Metadata and text quality | all HTML and `text/html`; 0 likely-corrupt outputs; 0 invalid/replacement characters |

No normalized article contained the checked appointment, newsletter,
advertising, product-and-services, or Mayo Clinic Press chrome markers.

### SPOR positional sample

The validator selected exactly 21 candidates at five-percent intervals: indices
0, 22, 45, 67, 90, 113, 135, 158, 181, 203, 226, 249, 271, 294, 317, 339,
362, 385, 407, 430, and 453. Seven PDFs succeeded at indices 22, 45, 90, 113,
203, 385, and 453; all 14 stale positions were retained in provenance. The
successful records span asset-map pages 13, 17, 25, 31, 113, 214, and the final
page 235.

| Dimension | Result |
|---|---|
| Successful/stale candidates | 7 successful PDFs; 14 recorded stale links |
| Conversion backend | 6 `pdf-inspector`; 1 Docling |
| Content size | 17,270 / 41,860 / 469,434 characters (minimum / median / maximum) |
| Sections | 1 / 1 / 273 (minimum / median / maximum) |
| Per-document time | 112.953 / 19.365 / 678.533 seconds (mean / median / p90); 4.287 / 678.533 seconds (minimum / maximum) |
| Metadata and text quality | all PDF and `application/pdf`; 0 likely-corrupt outputs; 0 invalid/replacement characters |

The final inventory candidate on page 235 completed through full-page Docling
OCR in 678.533 seconds. Keeping this fixed tail candidate materially changed
the observed latency distribution and confirmed that the parser can finish a
large scan-heavy document at the end of the official inventory.

### Local inspection subset

Eight representative local outputs were retained under
`outputs/distributed-validation-2026-08-26/`: one clinically relevant ICRC
record, four Mayo records from A, M, and Z with both route types, and three SPOR
records from early, middle, and final-page successes. Three broad-publication
ICRC samples were removed when the retained scope was narrowed to clinical
material.
Readable Markdown, full JSONL records with metadata/provenance, and the aggregate
JSON summary are available there. `outputs/` is git-ignored so publisher content
cannot be added to the PR accidentally.

## Official sitemap and clinical-scope follow-up

The SPOR parser finds 460 unique candidates in the current official report:
equivalent HTTP/HTTPS aliases collapse intentionally, while legitimate dynamic
download URLs with `.pdf` in a query value remain discoverable. The candidates
span 132 report pages and 71 publisher hosts. Explicit operator manifests
remain stricter and accept only direct PDF paths.

### Current sitemap inventories

| Source | Bounded sitemap result | Clinical inclusion boundary | Discovery time |
|---|---|---|---:|
| ICRC | 17 child sitemaps; 728 unique English publication URLs | Auto-discovery fetches publication metadata, requires a clinical signal in the publisher title or description, and rejects annual reports, law, institutional material, posters, recruitment, and similar nonclinical publications before PDF retrieval. The conservative URL-title inspection set contains 55 candidates. | 17.018 s wall time; 8.284 s summed HTTP retrieval time |
| Mayo Clinic | One official 2.2 MB condition sitemap; 2,401 unique supported pages: 1,202 symptoms/causes and 1,199 diagnosis/treatment | Route validation excludes doctors, departments, organizations, procedures, translations, and all non-condition sections. | 0.273 s retrieval in the retained run |
| SPOR | 8 English WordPress child sitemaps; 4 current CPG-related site surfaces | Sitemap pages are inventory metadata only. Ingested documents remain the 460 PDFs explicitly linked from the official Canadian clinical-practice-guideline report; publisher HTML is never crawled. | 7.576 s wall time; 2.208 s summed HTTP retrieval time |

ICRC's raw sitemap is intentionally not treated as a clinical corpus. The
clinical gate runs after landing-page retrieval but before shop resolution,
PDF download, or OCR. Each retained record stores
`clinical_relevance_terms`, `clinical_relevance_basis`, `discovery_scope`, its
raw sitemap index/total, and the cumulative number of nonclinical candidates
excluded before that record. Known-positive tests include nursing, nutrition,
mental-health, surgery, first-aid, and pre-hospital bleeding guidance. Known
negative tests include the Geneva Conventions, economic-livelihood material,
annual reports, health-care-violence policy, COVID response reporting, and a
nonclinical web-scraping publication.

The newly listed 2025 publication `ICRC Pre-hospital Emergency Care: Guidance
for Managing Severe Bleeding` was then run end to end. It produced 30,621
characters from the landing HTML and full PDF with unique normalized hash
`e4e077962f2a1fea2e2350308d208a02eaf42bc03665e97039fb72827467aa05`.
The record correctly reports `html` plus `pdf`, `text/html` plus
`application/pdf`, and clinical signals `bleeding`, `hospital`, and
`pre-hospital`. Its 89.042-second document time split into 1.688 seconds for
landing retrieval, 7.471 seconds for shop resolution, 1.641 seconds for PDF
retrieval, and 77.202 seconds for quality-preserving Docling conversion.

SPOR's current sitemap also exposes its asset-map landing page, CPG database,
report page, and a related CPG project. The database page still states that the
guidelines cover 1996 through April 2018, were not quality-assessed, and are not
endorsed. No evidence of a newer replacement guideline database was found, so
the fixed report remains the document inventory and its staleness warning
remains mandatory.

### Current Mayo beginning/middle/end run

The sitemap inventory enabled a stronger positional run than the earlier
alphabet sample. Twenty-one entries were selected at exact five-percent
intervals: indices 0, 120, 240, 360, 480, 600, 720, 840, 960, 1080, 1200,
1320, 1440, 1560, 1680, 1800, 1920, 2040, 2160, 2280, and 2400. All 21
completed, with 21 unique IDs and 21 unique content hashes. The sample included
11 symptoms/causes and 10 diagnosis/treatment pages.

| Dimension | Result |
|---|---|
| Wall time | 201.663 seconds |
| Per-document time | 9.595 / 10.066 / 10.293 seconds (mean / median / p90); 2.033 / 10.881 seconds (minimum / maximum) |
| Content size | 1,868 / 7,757 / 25,523 characters (minimum / median / maximum) |
| Formats | all `html`; all `text/html`; normalized output `text/markdown` |
| Completeness | indices 0 and 2,400 both completed; no empty, duplicate-ID, or duplicate-content records |

The complete 21-record JSONL, first/middle/final Markdown, Mayo inventory
summary, raw ICRC audit inventory, conservative ICRC clinical candidate list,
and full 460-entry SPOR manifest are retained locally under
`outputs/sitemap-validation-2026-08-26/`. The directory is git-ignored.

### Linux and PR compatibility

The production changes use existing project dependencies and patterns:
`httpx`, the shared secure downloader and pacing helpers, `lxml` with entity
and network resolution disabled, existing Playwright lifecycle helpers, typed
dataclasses, lazy `ScrapeRun` iterators, and pytest `MockTransport`/browser test
doubles. No committed path, executable, browser channel, credential, or timing
value is specific to the validation Mac. Chromium installation remains an
explicit deployment prerequisite for Mayo and ICRC shop pages, just as before.
All new automated tests are offline and platform-neutral.

## Automated checks

- Focused ICRC/Mayo/SPOR/CLI regression suite after sitemap and scope changes:
  `120 passed`.
- Full repository suite with PDF dependencies: `216 passed`.
- Targeted Ruff formatting completed.
- Repository-wide `uv run ruff check .`: passed.
- `git diff --check`: passed.
- `uv build --offline --package amfv-datasets`: source distribution and wheel
  built successfully; disposable build outputs were deleted afterward.
