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

## Formal logical evaluation

AMFV uses a dependency-free Lean 4.32.0 library to model structural invariants
at high-risk boundaries such as source URL admission, scraper accounting,
verification receipts, cache freshness, and evaluation success.

```sh
lake build --wfail
python3 tools/check_lean_specs.py
python3 tools/check_logic_fixtures.py
```

Python functions annotated with `# lean-spec:` are backed by a named theorem
and an adversarial runtime test. This establishes conformance to the modeled
invariant; it does not claim that Lean verifies Python bytecode, arbitrary HTML,
medical truth, or corpus completeness.

See [`FORMAL_LOGIC.md`](FORMAL_LOGIC.md) for the complete developer and
maintenance guide. The shorter [`formal/README.md`](formal/README.md) is a
directory-level quick reference.
