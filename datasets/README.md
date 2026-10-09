# AMFV Datasets

Dataset ingestion, processing, and construction for the [Agentic Medical Fact Verifier](../README.md).

Covers ingesting and normalizing source corpora, generating synthetic data with frontier / top open models, building train / validation / test splits, and producing fact-database entries.

Workspace member (`amfv-datasets`).

## Cancer Care Ontario

Install the optional browser dependency and its Chromium runtime:

```bash
uv sync --extra cco --package amfv-datasets
uv run playwright install chromium
uv run amfv-scrape --source cco --documents 10 -o data/cco.jsonl
```

For one guideline, pass `--url` with its CCO detail-page URL. Listing runs skip
failed pages with a warning and count only successful documents toward the limit;
explicit URL failures raise an error. The scraper follows the master listing's
"Load more" links and retains version, status, authors and PDF-link metadata.

The output contains the HTML summary sections, with `metadata.content_scope`
set to `summary`. The linked clinical guideline PDF is recorded as
`metadata.pdf_url` and is not converted by this scraper. Archived guidance is
retained with its source status for downstream filtering.
