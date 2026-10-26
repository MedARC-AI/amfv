# Drugs.com scraper review

Local engineering review, October 25, 2026. This report covers scraper behavior,
bounded compatibility observations and source restrictions. It does not contain
or redistribute Drugs.com article text.

## Goal and source restriction

The scraper normalizes supported Drugs.com medication pages into AMFV's shared
`ScrapedDocument` contract. It supports targeted URLs and alphabetical corpus
discovery while preserving source metadata and skipping unusable detail pages.

Drugs.com's current [Terms and Conditions](https://www.drugs.com/support/terms.html)
expressly prohibit automated scraping and dataset creation without prior written
consent, including research use. The scraper therefore fails before network
access unless `AMFV_ALLOW_DRUGSCOM_SCRAPE=1` is set. Set that acknowledgement
only after obtaining permission from the publisher. The code and offline tests
can be reviewed without enabling it.

Drugs.com is excluded from `--source all`, so an ordinary multi-source run does
not fail or contact the restricted source unexpectedly. It must be selected by
name after permission is confirmed.

The terms are the controlling source; `robots.txt` is not a grant of permission.
This repository does not include scraped Drugs.com content.

## Supported discovery and extraction

Discovery walks `/alpha/{letter}.html`, follows linked two-letter subindexes and
falls back to direct listings such as `0-9`. It recognizes root consumer pages
and the `mtm`, `cons`, `pro`, `monograph` and `cdi` namespaces. Unrelated site
tools are excluded and normalized namespace/slug pairs are deduplicated.

Extraction selects the main content region, removes navigation and non-print
widgets, converts links and images to absolute Markdown, drops unrelated cards
and resource blocks, and records namespace, content length, reviewer, update
date and credited data source when present. Output metadata marks the content
as proprietary, links the publisher terms and records that permission is
required.

HTTP 429 and common transient server errors, plus transport failures, receive
up to three attempts with one- and two-second backoff. Permanent HTTP errors
fail immediately. A failed detail page is logged and skipped; a persistent
listing failure stops discovery rather than silently presenting an incomplete
corpus as complete.

## Bounded compatibility observation

Before the current terms restriction was identified, one page from each of the
six supported namespaces was fetched and parsed on the same day. No output was
committed or retained. This was a compatibility observation, not permission for
a corpus run and not a representative accuracy sample.

| Namespace | HTTP/extraction result | Extracted characters | Reviewer/date metadata | Credited data source |
| --- | --- | ---: | --- | --- |
| consumer | success | 17,974 | present | present |
| mtm | success | 10,223 | absent | present |
| cons | success | 18,280 | absent | absent |
| pro | success | 105,571 | absent | absent |
| monograph | success | 73,902 | absent | absent |
| cdi | success | 35,037 | present | absent |

Observed extraction success was 6/6, with 10,223-105,571 characters and a
median of 26,659 characters. Reviewer and last-updated metadata appeared on 2/6
templates; an explicit credited data source appeared on 2/6. Metadata absence
does not prove a parsing error because the visible templates differ.

The consumer output exposed four cleanup defects: a duplicated title, “Save to
list”, “Play pronunciation” and a non-print featured-drug carousel/resource
block. The DOM cleanup now removes the first article H1, buttons, exact `#`
toggle links, `noprint` blocks and the `more-resources` container. Synthetic
tests reproduce those structures. No post-fix live request was made after the
terms restriction was confirmed.

## Scale and runtime expectation

Drugs.com describes more than 24,000 drug records. At the configured five-second
delay between detail attempts, delay alone would require at least 33.3 hours for
24,000 attempts, before network and parsing time. This is a planning estimate,
not a measured corpus duration. A full run must not be attempted without prior
written consent.

## Validation and limitations

Offline tests use `httpx.MockTransport` and cover permission gating, bounded
retry behavior, all six namespaces, both index layouts, URL rejection,
deduplication, metadata extraction, boilerplate removal, failure skipping,
shared listing limits, registry dispatch, CLI output and absolute image URLs.

Validation after adoption onto the shared registry:

- `uv run pytest -q datasets/test`: 64 passed.
- `uv run ruff check datasets`: passed.
- `uv run ruff format --check datasets`: passed.

This review does not establish current full-corpus coverage, clinical accuracy,
duplicate rate across page families, or redistribution rights. Those require
publisher permission followed by a separately approved, bounded validation
plan. If permission is obtained, begin with a small stratified sample and record
success rate, content hashes, metadata completeness, residual boilerplate and
elapsed time without committing proprietary article text.
