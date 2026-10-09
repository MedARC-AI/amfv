# AMFV Datasets

Dataset ingestion, processing, and construction for the [Agentic Medical Fact Verifier](../README.md).

Covers ingesting and normalizing source corpora, generating synthetic data with frontier / top open models, building train / validation / test splits, and producing fact-database entries.

Workspace member (`amfv-datasets`).

## WHO guidelines

Install the optional PDF converter and scrape guideline publications:

```bash
uv sync --dev --group pdf
uv run --group pdf amfv-scrape --source who --documents 10 -o data/who.jsonl
```

Use `--url https://www.who.int/publications/i/item/9789240121805` for one
publication. Discovery uses the WHO guideline listing API; an HTML fallback is
available for the first page. A later API failure stops the scrape rather than
repeating the first page.

Failed publication landing pages are logged and skipped during listing runs;
only successful records count toward `--documents`. An explicit URL failure
raises the source's `WhoFetchError`.

The output combines the landing-page Overview and converted PDF body.
`metadata.content_scope` is `full` only when PDF conversion succeeds; missing
links, failed downloads, unavailable Docling or partial conversions fall back to
`overview` with a logged warning. PDF conversion may download model weights on
first use. The Python API also accepts `include_full_text=False` for an explicit
Overview-only run.

Publisher policy is recorded separately from document-specific licence
verification. See [source licensing notes](amfv_datasets/scraping/LICENSE_NOTES.md)
before reusing the corpus. The [inherited backend comparison](benchmarks/README.md)
documents the original author's measurements and the real-PDF test procedure.
