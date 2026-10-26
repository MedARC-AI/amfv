# AMFV Datasets

Dataset ingestion, processing, and construction for the [Agentic Medical Fact Verifier](../README.md).

Covers ingesting and normalizing source corpora, generating synthetic data with frontier / top open models, building train / validation / test splits, and producing fact-database entries.

Workspace member (`amfv-datasets`).

## Drugs.com scraper

Drugs.com's current terms prohibit automated scraping and dataset creation
without prior written consent. The scraper is available for review and offline
testing, but live access is permission-gated. After obtaining written consent,
acknowledge it explicitly for the process that runs the scraper:

```bash
AMFV_ALLOW_DRUGSCOM_SCRAPE=1 uv run amfv-scrape \
  --source drugscom --url https://www.drugs.com/ibuprofen.html
```

Do not set the variable merely to bypass the guard. Scraped content is
proprietary and must not be committed or redistributed. See the
[engineering review](benchmarks/DRUGS_COM_REVIEW.md) for methodology,
bounded compatibility observations and limitations.

Restricted sources are excluded from `--source all`; select `drugscom`
explicitly after permission is confirmed.
