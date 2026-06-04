# AMFV Baseline

The full Baichuan-M3 pipeline for the
[Agentic Medical Fact Verifier](../README.md) on off-the-shelf LLMs, no
training. Makes the system runnable end to end and sets a target for the
components we later specialize.

## Pipeline

```
long-form answer
  → claims            (prompted decomposer)
  → retrieval         (BM25 + dense, or BM25 + ColBERT)
  → evidence spans
  → verifier verdicts (prompted Med-V1)
  → weighted verification report
  → cache update
```

Claims hit a cache of previously verified facts first; new claims trigger
retrieval + verification and are written back. A simple ReACT loop ties the
steps together.

Independent package — excluded from the root `uv` workspace.
