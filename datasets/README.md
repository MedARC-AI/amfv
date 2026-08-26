# AMFV Datasets

Dataset ingestion, processing, and construction for the [Agentic Medical Fact Verifier](../README.md).

Covers ingesting and normalizing source corpora, generating synthetic data with frontier / top open models, building train / validation / test splits, and producing fact-database entries.

Workspace member (`amfv-datasets`).

## Scraping

Run a registered source with `amfv-scrape`. Each JSONL row includes the source
URL, stable external ID, normalized Markdown, source metadata, provenance, and
the wall-clock time for that document. The command also reports total elapsed
time and mean time per document on stderr.

ICRC, Mayo Clinic, and SPOR records expose `source_format_types` and
`source_media_types` in metadata. These describe the retrieved source
artifacts (for example, `html`, `pdf`, `text/html`, and `application/pdf`),
while `provenance.content_type` continues to describe the normalized Markdown
output.

```console
uv run amfv-scrape \
  --source nice \
  --documents 3 \
  --output /tmp/amfv-nice.jsonl
```

`--documents all` runs until the selected source is exhausted. Use a small
explicit limit first when checking a new source. Some source adapters require
an additional dependency group, browser installation, manifest, API key, or
document license; follow that adapter's help text and only run it when you are
authorized to retrieve the material.

## ICRC scraper

The `icrc` adapter accepts only official publication/document/PDF URLs and
requires `AMFV_ICRC_PERMISSION_ID` (or `AMFV_PERMISSION_ID`). Collection mode
walks the bounded official sitemap by default; `AMFV_ICRC_MANIFEST` remains an
optional deterministic override. Auto-discovery checks each publisher title
and description and rejects nonclinical law, institutional, annual-report,
poster, and similar material before following or converting a PDF. Retained
records include the matched clinical signals and sitemap position. Install the
PDF dependencies and browser once, then run:

```bash
uv sync --group pdf
uv run --group pdf playwright install chromium
uv run --group pdf amfv-scrape --source icrc --url https://www.icrc.org/en/publication/example
```

Official shop PDF/language selection uses an ephemeral browser. PDF bytes stay
in memory, signed query values are redacted, and catalogue WAF bypass is not
implemented. Transient transport, 429, and 5xx failures use bounded backoff
that records attempt counts and delays. Bounded collection mode skips isolated
stale entries, records them on the next completed document, and stops after 20
consecutive retrieval failures rather than returning fewer documents silently.
`--documents all` walks the complete sitemap inventory and reports any clinical
publication that could not be retrieved; expected nonclinical exclusions do
not count as failures.
Docling conversions run in isolated workers with a 900-second wall-time bound.
For unusually long scanned publications, set a larger finite bound explicitly,
for example `AMFV_DOCLING_TIMEOUT_SECONDS=1800`; the selected limit is retained
in conversion provenance.

## Mayo Clinic scraper

The `mayoclinic` adapter is permission-gated. Set `AMFV_MAYO_PERMISSION_ID`
(or `AMFV_PERMISSION_ID`), install Playwright Chromium, and select a direct
condition URL, a licensed `AMFV_MAYO_MANIFEST`, or the official condition sitemap:

```bash
uv run playwright install chromium
uv run amfv-scrape --source mayoclinic --documents 3 --output /data/mayo.jsonl
```

Rendered HTML stays in memory and is size-bounded. Mayo content is consumer
health information rather than a clinical-practice-guideline corpus, and the
source is excluded from implicit `--source all` runs.

Browser navigation, rendering timeouts, and HTTP 408/425/429/5xx throttling
use four bounded attempts. `Retry-After` is honored up to 60 seconds; otherwise
the adapter waits 5, 10, then 20 seconds. Successful retrieval receipts record
the attempt count and retry delays, while isolated stale sitemap entries are
recorded and skipped rather than silently reducing a requested corpus size.
Default discovery reads Mayo Clinic's official
`condition_consolidated_concepts.xml` and keeps only canonical
`symptoms-causes` and `diagnosis-treatment` condition pages; unrelated doctors,
organization, procedure, and other sitemap routes are excluded. A bounded run
fails if the inventory cannot satisfy its requested document count, and an
all-documents run reports any unavailable sitemap entries. Both the
legacy `#main-content` layout and the current `article.cmp-article` layout are
supported; appointment, newsletter, and products-and-services chrome is
removed from the converted Markdown.

## SPOR scraper

The `spor` adapter treats the official April-2018 asset map as a frozen
historical registry. Set `AMFV_SPOR_PERMISSION_ID` (or `AMFV_PERMISSION_ID`)
and install the PDF group:

```bash
uv sync --group pdf
uv run --group pdf amfv-scrape --source spor --documents 1 --output /data/spor.jsonl
```

`AMFV_SPOR_MANIFEST` can select curated direct publisher PDFs. The adapter
first walks the bounded English WordPress sitemap to record the current CPG
inventory surfaces, then reads the fixed report. It never crawls publisher
HTML. Report annotations that identify a PDF in a download query are retained,
while explicit manifests remain restricted to direct `.pdf` paths. Bounded
runs record/skip a bounded number of stale links; `--documents all` examines
the complete report inventory and reports any unavailable guideline instead
of silently truncating it. The registry's non-endorsement/currentness warning
is retained and PDF bytes stay in memory. Transient 429 and 5xx responses honor
bounded retry
backoff; transport failures receive one retry. Two transport failures open a
per-host circuit for the rest of that historical run, with every skipped URL
retained in provenance, so one defunct publisher cannot monopolize a trial.
