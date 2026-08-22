# AMFV Datasets

Dataset ingestion, processing, and construction for the [Agentic Medical Fact Verifier](../README.md).

Covers ingesting and normalizing source corpora, generating synthetic data with frontier / top open models, building train / validation / test splits, and producing fact-database entries.

Workspace member (`amfv-datasets`).

## Scraped-document JSONL contract

`amfv-scrape` emits one deterministic UTF-8 JSON object per line for each
normalized source document. Version 1 has exactly these top-level fields:

```json
{
  "schema_version": 1,
  "source": "nice",
  "external_id": "nice-ng235",
  "title": "Cardiovascular disease",
  "url": "https://www.nice.org.uk/guidance/ng235",
  "content": "## Overview\n\n...",
  "section_count": 2,
  "metadata": {
    "ref": "NG235",
    "slug": "ng235"
  }
}
```

`source`, `external_id`, `title`, and `content` are non-empty strings; `url` is
the source's canonical absolute HTTP(S) URL; and `section_count` is a positive
integer. `metadata` is a JSON object whose source-specific keys are additive.
Consumers must not require or branch on NICE-private metadata keys. Dataset
placement is intentionally absent: an artifact consumer supplies its own target
dataset and maps `url` to its persisted source-URL field if it has one.

The producer validates rows before writing them and explicitly rejects any
schema version other than `1`. The checked-in contract fixture is
`test/fixtures/scraping/nice_document_v1.jsonl`.
