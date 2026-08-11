# Source licensing notes for scraped corpora

## WHO (World Health Organization)

Publications on [who.int](https://www.who.int/publications) published since
November 2016 are licensed under **Creative Commons Attribution-NonCommercial-
ShareAlike 3.0 IGO** (CC BY-NC-SA 3.0 IGO).

- **Non-commercial use and adaptation** are permitted.
- **Attribution** to WHO is required.
- **Share-alike**: derivatives must use the same or a similar licence.

Pre-2017 publications were not reissued under this licence. Use
`metadata.publication_date` to filter when building corpora.

### Suggested attribution

> © World Health Organization {year}. *{publication title}*.
> Licensed under CC BY-NC-SA 3.0 IGO.
> https://creativecommons.org/licenses/by-nc-sa/3.0/igo/

Each scraped document also records `metadata.license` and
`metadata.attribution`.

### Content scope

The WHO scraper emits the HTML **Overview** from the publication landing page
followed by the guideline body converted from the linked PDF
(`metadata.download_url`). `metadata.content_scope` records which was captured:

- `full` — Overview plus converted PDF body. `metadata.pdf_backend` and
  `metadata.pdf_bytes` describe the conversion.
- `overview` — Overview only, because no PDF was linked, the download failed, or
  conversion failed. Both PDF metadata fields are `null`.

PDF conversion needs the optional `pdf` extra (`uv sync --group pdf`). Without
it every document degrades to `overview`, so check `content_scope` before
treating a corpus as full text.

### Committed test fixtures

`datasets/test/fixtures/pdf/` holds six-page excerpts of two WHO guidelines, so
conversion can be tested against real guideline text offline:

| Fixture | Source | Pages |
| --- | --- | --- |
| `who_9789240121805_excerpt.pdf` | [Guidelines for the prevention of bloodstream infections and other infections associated with the use of intravascular catheters: part 2: central venous catheters](https://www.who.int/publications/i/item/9789240121805) | 30-35 |
| `who_9789240124233_excerpt.pdf` | [Consolidated HIV guidelines: service delivery](https://www.who.int/publications/i/item/9789240124233) | 11-16 |

Each PDF sits next to a `.expected.md` file holding the markdown the scraper
extracts from it, so the conversion can be inspected without running anything.
Those markdown files are derivative works of the PDFs and carry the same licence.

Both are redistributed unmodified apart from page selection, under CC BY-NC-SA
3.0 IGO:

> © World Health Organization. Licensed under CC BY-NC-SA 3.0 IGO.
> https://creativecommons.org/licenses/by-nc-sa/3.0/igo/

They are test data for non-commercial research use. Note that this licence is
more restrictive than the repository's Apache-2.0 licence, which covers the code
only and does not extend to these files.
