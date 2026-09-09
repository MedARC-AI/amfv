# AMFV Datasets

Dataset ingestion, processing, and construction for the [Agentic Medical Fact Verifier](../README.md).

Covers ingesting and normalizing source corpora, generating synthetic data with frontier / top open models, building train / validation / test splits, and producing fact-database entries.

Workspace member (`amfv-datasets`).

## Decomposition-evaluation JSONL contract

Install the `decomposition-eval` extra to preview prompts or generate model
artifacts. The command always reads the evaluation instructions from a caller-
supplied UTF-8 file; the package contains no default evaluation prompt.
PydanticAI requests provider-native JSON Schema output and validates it as a
Pydantic model; generation does not use function tools or prompted JSON. The
model copies exact source quotations but does not calculate character offsets.
The harness deterministically resolves those quotations to Python code-point
spans before it builds an import row. Each model request presents the query and
response in labeled, three-backtick Markdown blocks.

Generator input contains `schema_version`, `case_id`, `assistant_response`, and
an optional `user_prompt`. Omitting the prompt supports response-only material
such as documents and reasoning traces. Version 1 output is a strict
`FACT_DECOMP` import row.
Each ordered claim has nonblank text, one or more exact Python code-point spans
in the assistant response, and one label: `substantive`, `incidental`, or
`borderline`. Zero claims is valid. Unknown fields are rejected.

- `substantive`: Correctness materially affects information, reasoning, conclusions, or actions.
- `incidental`: Correctness has little bearing on that substantive content.
- `borderline`: Context leaves verification relevance unclear. This is not uncertainty about factual truth.

Verification includes substantive and borderline claims. Incidental claims remain
in the artifact for human review. Repeated assertions retain their relevance
labels; downstream processing handles duplication. The same labels apply to
Q/A inputs and documents. The website preserves original model labels and final
human labels. The previous four-label contract is replaced without conversion.

`external_id` is the SHA-256 digest of `case_id`, a null byte, and `arm_id`.
The prompt hash covers the exact prompt-file bytes. Canonical output contains no
timestamps, run identifiers, credentials, provider messages, or free-form
metadata, so rerunning the same case and arm produces a stable identity.

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
