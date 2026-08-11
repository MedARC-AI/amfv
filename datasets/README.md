# AMFV Datasets

Dataset ingestion, processing, and construction for the [Agentic Medical Fact Verifier](../README.md).

Covers ingesting and normalizing source corpora, generating synthetic data with frontier / top open models, building train / validation / test splits, and producing fact-database entries.

Workspace member (`amfv-datasets`).

## Optional dependencies

Sources that publish guidelines as PDFs need the `pdf` extra, which installs
Docling for PDF-to-markdown conversion:

```bash
uv sync --group pdf
```

Without it those scrapers still run but fall back to whatever HTML the landing
page exposes. See
[scraping/LICENSE_NOTES.md](amfv_datasets/scraping/LICENSE_NOTES.md) for how
`metadata.content_scope` reports this.

Sample WHO guideline PDFs and the markdown extracted from them are committed in
`test/fixtures/pdf/`. See [benchmarks/README.md](benchmarks/README.md) for how
to inspect and verify the conversion, and why Docling is the default backend.
