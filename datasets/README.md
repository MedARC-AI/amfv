# AMFV Datasets

Dataset ingestion, processing, and construction for the [Agentic Medical Fact Verifier](../README.md).

Covers ingesting and normalizing source corpora, generating synthetic data with frontier / top open models, building train / validation / test splits, and producing fact-database entries.

Workspace member (`amfv-datasets`).

## CDC guideline scraper

The CDC scraper discovers records from the CDC Stacks **Guidelines and
Recommendations** collection, then keeps English, public-domain records with an
official CDC MMWR full-text HTML version:

```bash
uv run amfv-scrape --source cdc --documents 10 -o data/cdc.jsonl
```

To scrape one record, pass its CDC Stacks page rather than its PDF URL:

```bash
uv run amfv-scrape --source cdc \
  --url https://stacks.cdc.gov/view/cdc/182510 \
  -o data/cdc-182510.jsonl
```

The scraper does not require Selenium, a browser, GROBID, or OCR. It follows
the collection's DOI to the semantic full-text article on `cdc.gov` and records
the Stacks URL, DOI, authors, publication type, year, license, and content scope
in metadata. PDF-only records, non-English translations, and records whose full
text is hosted by an external publisher are skipped instead of emitting a
Stacks abstract as though it were the complete guideline. CDC Stacks' declared
10-second crawl delay is applied between collection listing pages.
