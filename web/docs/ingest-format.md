# Source-document import format

The web application accepts producer-owned source documents through a generic,
versioned JSONL boundary. It does not scrape sites, schedule downloads, or know
deployment-specific dataset names.

## Endpoint

`POST /api/v1/admin/documents/import` is a superuser-only multipart request with:

- `dataset_id`: the ID of an active `RETRIEVAL` dataset;
- `file`: a UTF-8 JSONL artifact;
- `dry_run`: optional boolean, default `false`.

The Documents admin page exposes the same typed API. A dry run performs the
same validation, duplicate, and conflict checks, then rolls the request back.
The response reports `created`, `unchanged`, `rejected`, `errors`, and
`dry_run`. Error details are bounded to 100 rows.

## JSONL v1 row

Each nonblank line is one JSON object with exactly these top-level fields:

```json
{
  "schema_version": 1,
  "source": "example-producer",
  "external_id": "stable-document-id",
  "title": "Document title",
  "url": "https://example.org/document",
  "content": "Full normalized document text.",
  "section_count": 1,
  "metadata": {"producer_field": "preserved"}
}
```

The contract requires:

- `schema_version` is the integer `1`;
- `source`, `external_id`, `title`, and `content` are nonempty strings;
- `url` is an absolute HTTP(S) URL;
- `section_count` is a positive integer;
- `metadata` is a JSON object and may contain additive producer fields;
- unknown top-level fields and unsupported versions are rejected.

The canonical producer fixture is
`datasets/test/fixtures/scraping/nice_document_v1.jsonl`. Producers own site
access and serialization; the web consumer maps `url` to `Document.source_url`,
stores a SHA-256 content hash, and adds `section_count` to preserved source
metadata. Rows do not carry a web `dataset_id`.

## Limits and idempotency

Defaults are deliberately request-specific rather than a global upload ceiling:

- artifact: 512 MiB;
- one document's UTF-8 content: 16 MiB;
- one row's serialized producer metadata: 16 KiB.

Within a dataset, `(dataset_id, external_id)` is the identity. Re-importing the
same content is `unchanged`; the same external ID with different content is
rejected rather than silently overwritten. Invalid rows are isolated, while an
artifact-level failure rolls back the request.

## Producer validation

The datasets package owns the neutral v1 validator and serializer. Validate the
fixture and producer contract from the monorepo root with:

```bash
uv run pytest datasets/test
```

The legacy `/api/v1/admin/ingest` item-envelope surface remains unimplemented;
do not use it for documents or describe it as a working import path.
