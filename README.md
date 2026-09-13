# MedARC Agentic Medical Fact Verifier

An open-source reproduction of Baichuan-M3's medical fact verification system, built by [MedARC](https://www.medarc.ai/).

Under active development. 

## System Sketch

Following Baichuan-M3, the task is split into three models across four steps:

1. **Claim Decomposer** breaks input text (answers, documents, reasoning traces, model outputs) into individual medical claims.
2. **Fact Verifier** compares each claim against a database of previously fact-checked claims ("Claim X is supported by evidence set Y [under scope Z] as of date T").
3. For a new claim, a **Search Agent** is dispatched to find supporting or contradictory evidence from a curated medical corpus.
4. Results return to the **Fact Verifier**, which scores the claim on a five-level scale (strongly supported, weakly supported, unclear, weakly unsubstantiated, strongly unsubstantiated) and writes a new entry to the fact database.

## Components

| Component | Role | Workspace |
|-----------|------|-----------|
| [`baseline`](baseline/README.md)   | End-to-end pipeline on off-the-shelf LLMs (no training) | Independent |
| [`decomposer`](decomposer/README.md) | Long-form text → atomic medical claims | Yes |
| [`verifier`](verifier/README.md)   | Claim + evidence → five-level supported↔unsubstantiated score | Yes |
| [`search`](search/README.md)       | Claim → supporting / contradictory sources | Yes |
| [`datasets`](datasets/README.md)   | Dataset ingestion, construction, and synthetic data | Yes |
| [`utils`](utils/README.md)         | Shared helpers used across AMFV packages | Yes |
| [`training`](training/README.md)   | Training experiments and recipes for the above | Independent |

The **Workspace** column marks membership in the root `uv` workspace. Independent packages (`baseline`, `training`) are excluded so they can evolve on their own.

## Evaluation

AMFV-Bench lives under [`datasets/`](datasets/README.md): a frozen, stage-separated test set so later work can say “retrieval A beats B” instead of optimizing against vibes. Gold Likert labels, scope, and evidence spans are graded by **clinicians** against cited guideline text. The scorer is deterministic and does not call an LLM. Annotation rubric: [`datasets/amfv_datasets/eval/taxonomy.md`](datasets/amfv_datasets/eval/taxonomy.md). Physicians who want to label or second-read cases should start there.
