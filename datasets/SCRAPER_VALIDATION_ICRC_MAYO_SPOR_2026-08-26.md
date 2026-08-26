# ICRC, Mayo Clinic, and SPOR scraper validation — 2026-08-26

This report records focused live and offline validation for the three retained
sources. Live runs used an explicit local permission reference, bounded document
counts, in-memory source handling, and temporary JSONL output. The temporary
PDF, JSONL, HTML, Markdown, and review-output artifacts were deleted after the
checks. No credentials or signed download query values are recorded here.

## Result

All three scrapers passed their supported live paths and their combined focused
test suite. The checks also found and fixed direct-ICRC-PDF title handling: a
direct PDF now uses its first level-one Markdown heading rather than its
filename as the document title. Direct ICRC external IDs also avoid a duplicate
`icrc-icrc-` prefix.

Every resulting document now records both:

- `metadata.source_format_types`: normalized source formats such as `html` and
  `pdf`.
- `metadata.source_media_types`: corresponding MIME types such as `text/html`
  and `application/pdf`.

`provenance.content_type` remains `text/markdown` because it describes the
normalized output, while individual retrieval receipts retain the server's
reported content type.

## ICRC

| URL shape | Live target | Result |
|---|---|---|
| Legacy direct PDF | `https://www.icrc.org/en/doc/assets/files/publications/icrc-002-4126.pdf` | Passed; 93,361 Markdown characters, 2 sections, SHA-256 prefix `5f3e705fa7a3`; title recovered as `GUIDELINES FOR INVESTIGATING DEATHS IN CUSTODY`; source format `pdf`. |
| Numbered publication | `https://www.icrc.org/en/publication/4261-icrc-rules-on-personal-data-protection` | Passed; landing HTML plus resolved PDF, 50,332 characters, SHA prefix `d0282a3dcdbe`; source formats `html`, `pdf`. |
| Current slug-only publication | `https://www.icrc.org/en/publication/ethical-content-gathering-public-communications` | Passed; landing HTML plus resolved PDF, 29,804 characters, SHA prefix `a9f50dd5e118`; source formats `html`, `pdf`. |
| Older publication layout | `https://www.icrc.org/en/publication/0790-discover-icrc` | Passed; landing HTML plus resolved PDF, 68,921 characters, SHA prefix `750b1762879b`; Docling fallback completed successfully. |

The publication-page runs exercised official-host validation, shop-page PDF
selection, redacted signed queries, PDF magic-byte detection when the server
reported `application/octet-stream`, and both primary/fallback PDF conversion.

## Mayo Clinic

| Path | Result |
|---|---|
| `acne/symptoms-causes/syc-20368047` | Passed; 6,854 characters, 8 sections, published `2024-07-20`, SHA prefix `a3c9c91107ad`. |
| `atrial-fibrillation/diagnosis-treatment/drc-20350630` | Passed; 18,735 characters, 17 sections, published `2026-01-14`, SHA prefix `63d311e034c6`. |
| `zenkers-diverticulum/symptoms-causes/syc-20568839` | Passed; 17,398 characters, 7 sections, published `2024-10-24`, SHA prefix `c8f1ecfc350a`. |
| `zenkers-diverticulum/diagnosis-treatment/drc-20568846` | Passed; 7,924 characters, 12 sections, published `2024-10-24`, SHA prefix `c7aed29435af`. |
| A–Z discovery, first three current entries | Passed; `Abdominal aortic aneurysm`, `Absence seizure`, and `Acanthosis nigricans` produced 7,924/6,261/2,302 characters and 11/9/7 sections. |

These runs covered both supported article route types, current rendered AEM
markup, canonical-route checks, publication dates/authors, removal of recurring
site chrome, and A–Z listing-to-article discovery. All records reported source
format `html` and media type `text/html`.

## SPOR Evidence Alliance

| Input | Result |
|---|---|
| Official April-2018 report | Passed; the 5.9 MiB PDF inventory parsed in memory, three stale candidates were recorded/skipped, and the next reachable `2014 Ccsmh Guideline Update Delirium` PDF produced 56,885 characters, 39 sections, SHA prefix `8f79536a2531`. |
| Explicit JSON manifest: CCSMH long-term-care update | Passed; 40,965 characters, 26 sections, SHA prefix `81821cec235d`; Docling conversion. |
| Explicit JSON manifest: Alberta Health Services stage-III lung-cancer guideline | Passed; 40,090 characters, SHA prefix `1cf526b7e75e`; `pdf-inspector` conversion. |

The manifest run covered two publisher hosts and exact two-document completion.
All records reported source format `pdf` and media type `application/pdf`.
SPOR remains a historical, non-endorsing registry last updated in April 2018;
successful parsing does not establish that a listed guideline is current.

## Automated checks

- Focused scraper/CLI suite: `100 passed` after integration and format-metadata
  changes.
- Full repository suite: `190 passed`.
- `uv run ruff format .`: passed; one file was normalized.
- `uv run ruff check .`: passed.
- `git diff --check`: passed.
- `uv build --package amfv-datasets`: source distribution and wheel built
  successfully; disposable build outputs were deleted afterward.
