# Ingest Format

This document is the contract for agents that generate data for `/admin/ingest`.
The upload is a UTF-8 JSON file. Select the matching dataset `Eval type` in the
admin form before upload.

Generated examples can be produced locally with:

```bash
uv run python scripts/generate_sample_ingest.py --out-dir data
```

`data/` is ignored and generated sample outputs should not be committed.

## Retrieval Items

Use this envelope when creating retrieval audit items and their source chunks.

```json
{
  "category_definitions": {
    "VERBATIM": "The answer appears word-for-word in one gold chunk."
  },
  "documents": [
    {
      "external_id": "doc-1",
      "title": "Document title",
      "content": "Optional full document text.",
      "metadata": {"source": "optional"},
      "chunks": [
        {
          "external_id": "doc-1-chunk-1",
          "text": "Chunk text.",
          "position": 0
        }
      ]
    }
  ],
  "items": [
    {
      "external_id": "query-1",
      "category": "VERBATIM",
      "lazy_query": "Question shown to annotators?",
      "expected_answer": "answer text",
      "gold_chunk_ids": ["doc-1-chunk-1"],
      "trap_chunk_ids": [],
      "machine_span": {
        "chunk_id": "doc-1-chunk-1",
        "start": 0,
        "end": 11,
        "text": "Chunk text."
      },
      "source": "LLM",
      "generator_name": "optional-generator",
      "priority_tag": "optional",
      "is_calibration": false,
      "calibration_reference": null,
      "is_trap": false,
      "trap_note": null,
      "why_not_answerable": null,
      "metadata": {}
    }
  ]
}
```

Required document fields:

- `external_id`: stable ID unique within the dataset.
- `title`: display title.
- `chunks`: list of chunk objects.

Required chunk fields:

- `external_id`: stable ID unique within the dataset.
- `text`: chunk body shown to annotators.
- `position`: integer order within the document.

Required item fields:

- `external_id`: stable query ID unique within the dataset.
- `category`: one of `VERBATIM`, `PARAPHRASE`, `MULTI_CHUNK`, `ADVERSARIAL`, `MULTI_DOCUMENT`.
- `lazy_query`: query shown to annotators. `prompt` or `prompt_text` are also accepted, but `lazy_query` is preferred.
- `gold_chunk_ids`: chunk external IDs that must exist in the same upload or already exist in the dataset.
- `source`: `LLM` or `HUMAN`.

Optional item fields:

- `expected_answer`
- `trap_chunk_ids`
- `machine_span`
- `generator_name`
- `priority_tag`
- `is_calibration`
- `calibration_reference`
- `is_trap`
- `trap_note`
- `why_not_answerable`
- `metadata`

Validation rules:

- Every `gold_chunk_ids[]` and `trap_chunk_ids[]` value must resolve to an ingested chunk.
- `machine_span.chunk_id` must resolve to an ingested chunk.
- `machine_span.start` and `machine_span.end` must be valid offsets inside that chunk.
- If `machine_span.text` is provided, it must exactly match `chunk.text[start:end]`.

Legacy retrieval item keys accepted for compatibility:

- `retrieval_subtype: "PARAGRAPH"` maps to `category: "PARAPHRASE"`.
- `retrieval_subtype: "MULTI_PARAGRAPH"` maps to `category: "MULTI_CHUNK"`.

## Pooled Candidates

Use this envelope after retrieval items and chunks have been ingested. It creates
or merges candidate chunks for Mode B relevance judging.

```json
{
  "pooled_candidates": [
    {
      "query_id": "query-1",
      "chunk_id": "doc-1-chunk-1",
      "doc_id": "doc-1",
      "chunk_text": "Chunk text, required only if the chunk is new.",
      "position": 0,
      "systems": ["bm25", "dense"],
      "ranks": {"bm25": 1, "dense": 3},
      "is_calibration": false,
      "is_trap": false,
      "reference_grade": 3
    }
  ]
}
```

Required fields:

- `query_id`: retrieval item `external_id`.
- `chunk_id`: chunk `external_id`.

Optional fields:

- `doc_id`: document external ID. Used when creating a missing chunk.
- `chunk_text`: chunk text. Used when creating a missing chunk.
- `position`: integer chunk order. Defaults to `0`.
- `systems`: list of retrieval systems that returned the candidate.
- `ranks`: object mapping system name to rank.
- `is_calibration`
- `is_trap`
- `reference_grade`: integer reference label, usually `0` through `3`.

Validation and merge rules:

- `query_id` must match an existing retrieval item in the selected dataset.
- If `chunk_id` already exists, it is reused.
- If `chunk_id` does not exist, the importer creates a document and chunk from `doc_id` and `chunk_text`.
- Candidates are deduplicated by `(query_id, chunk_id)`.
- Re-uploading a candidate merges `systems` and `ranks`.
- `pooled_results` is accepted as an alias for `pooled_candidates`.

## FACT_DECOMP Items

Use this envelope for the existing single-item fact decomposition workflow.

```json
{
  "documents": [],
  "items": [
    {
      "external_id": "fact-1",
      "eval_type": "FACT_DECOMP",
      "prompt": "Source statement to decompose.",
      "source": "LLM",
      "generator_name": "optional-generator",
      "author_kind": "LLM_GENERATED",
      "metadata": {},
      "facts": [
        {
          "fact_uuid": "optional-stable-id",
          "text": "Atomic fact stated by the source.",
          "polarity": "SHOULD_LIST"
        },
        {
          "text": "Unsupported or excluded fact.",
          "polarity": "SHOULD_NOT_LIST"
        }
      ]
    }
  ]
}
```

Required item fields:

- `external_id`: stable item ID unique within the dataset.
- `prompt` or `prompt_text`: source statement shown to reviewers.
- `source`: `LLM` or `HUMAN`.
- `facts`: list of fact objects.

Required fact fields:

- `text` or `fact_text`: atomic fact text.
- `polarity`: `SHOULD_LIST` or `SHOULD_NOT_LIST`.

Optional fields:

- `eval_type`: may be omitted when uploading into a `FACT_DECOMP` dataset.
- `generator_name`
- `author_kind`: `HUMAN_LAY`, `HUMAN_EXPERT`, or `LLM_GENERATED`.
- `metadata`
- `fact_uuid`: generated automatically if omitted.

Validation rules:

- Prompt text is required.
- Fact text cannot be empty.
- A valid FACT_DECOMP item must include at least one `SHOULD_LIST` fact and at least one `SHOULD_NOT_LIST` fact.
- Duplicate active prompt text in the same dataset is rejected.

## Admin Upload Notes

- Use `/admin/ingest`.
- Dataset `name` is the stable internal key.
- Dataset `display name` is the user-facing label.
- `subtype_targets_json` is retained for older flows and can usually be `{}`.
- Retrieval item uploads, pooled candidate uploads, and FACT_DECOMP uploads should be separate files.
