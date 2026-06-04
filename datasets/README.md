# AMFV Datasets

Dataset ingestion, processing, and construction for the
[Agentic Medical Fact Verifier](../README.md) — the data the other components
depend on:

- claim-decomposition data for the [decomposer](../decomposer/README.md)
- claim/evidence verdict data for the [verifier](../verifier/README.md)
- claim → sources data for the [search agent](../search/README.md)
- the curated medical corpus used for retrieval and search

Covers ingesting and normalizing source corpora, generating synthetic data with
frontier / top open models, building train / validation / test splits, and
producing fact-database entries.

Workspace member (`amfv-datasets`).
