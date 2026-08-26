# AMFV Datasets

Dataset ingestion, processing, and construction for the [Agentic Medical Fact Verifier](../README.md).

Covers ingesting and normalizing source corpora, generating synthetic data with frontier / top open models, building train / validation / test splits, and producing fact-database entries.

Workspace member (`amfv-datasets`).

## Scraping

Run a registered source with `amfv-scrape`. Each JSONL row includes the source
URL, stable external ID, normalized Markdown, source metadata, provenance, and
the wall-clock time for that document. The command also reports total elapsed
time and mean time per document on stderr.

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
also requires `AMFV_ICRC_MANIFEST`; direct `--url` mode does not. Install the
PDF dependencies and browser once, then run:

```bash
uv sync --group pdf
uv run --group pdf playwright install chromium
uv run --group pdf amfv-scrape --source icrc --url https://www.icrc.org/en/publication/example
```

Official shop PDF/language selection uses an ephemeral browser. PDF bytes stay
in memory, signed query values are redacted, and catalogue WAF bypass is not
implemented. Transient transport, 429, and 5xx failures use bounded backoff
that records attempt counts and delays. Collection mode skips isolated stale
manifest entries, records them on the next completed document, and stops after
20 consecutive failures rather than returning fewer documents silently.
Docling conversions run in isolated workers with a 900-second wall-time bound.
For unusually long scanned publications, set a larger finite bound explicitly,
for example `AMFV_DOCLING_TIMEOUT_SECONDS=1800`; the selected limit is retained
in conversion provenance.
