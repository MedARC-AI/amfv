# AMFV Datasets

Dataset ingestion, processing, and construction for the [Agentic Medical Fact Verifier](../README.md).

Covers ingesting and normalizing source corpora, generating synthetic data with frontier / top open models, building train / validation / test splits, and producing fact-database entries.

Workspace member (`amfv-datasets`).

## AMFV-Bench v0

Stage-separated gold eval for decomposer, retrieval, and verifier. 48 NICE-grounded cases, eight per stratum. The scorer does not call a network or an LLM. There is no LLM-as-judge.

**Gold labels are clinician work.** Engineers score pipelines; they do not assign Likert verdicts. v0 is a seed (`annotator`: `seed-v0-clinician`) until a named clinician locks it and a second clinician grades independently. Rubric: [`amfv_datasets/eval/taxonomy.md`](amfv_datasets/eval/taxonomy.md).

If you are a physician and want to grade or second-read these cases, that is the intended next step — start from the rubric and `gold/v0.jsonl`.

### Add a case (clinicians)

1. Edit [`amfv_datasets/eval/gold/_v0.py`](amfv_datasets/eval/gold/_v0.py) (or append a JSON object to [`amfv_datasets/eval/gold/v0.jsonl`](amfv_datasets/eval/gold/v0.jsonl) if you are not regenerating).
2. Keep quoted NICE spans at most 280 characters. Store `source_id`, URL, and section heading; do not paste chapters.
3. Put guideline numbers that appear in the gold claim into `input_text` as well, or the faithfulness metric will treat them as invented.
4. Use the stratum contract:
   - `strongly_supported` → verdict `+2`
   - `weakly_supported` → verdict `+1`
   - `refuted` → verdict `-2`
   - `insufficient` → verdict `0`
   - `population_mismatch` → verdict `0` and tag `population_mismatch`
   - `temporal` → verdict `-2` and tag `guideline_update`
5. Regenerate and validate:

```bash
uv run python -m amfv_datasets.eval.gold._v0
uv run pytest datasets/test/test_eval_schema.py datasets/test/test_eval_score.py
```

v0 is frozen at 48 cases (eight per stratum). Further cases belong in a later split, not a silent expansion of `v0.jsonl`. Non-clinicians: propose `input_text` plus a NICE URL in an issue; do not edit gold verdicts.

### Score a pipeline

Write one JSON object per case:

```json
{
  "case_id": "amfv-v0-ss-01",
  "predicted_claims": [
    {
      "text": "An adult with clinic blood pressure 148/92 mmHg should be offered ABPM to confirm hypertension.",
      "atomicity_ok": true,
      "verdict": 2,
      "retrieved_evidence": [
        {
          "source_id": "nice-ng136",
          "section": "Diagnosing hypertension",
          "pyramid_tier": "guideline"
        }
      ]
    }
  ]
}
```

`verdict` is the Med-V1 Likert: `-2` … `+2`. Retrieval is scored on `source_id` and `section`, not a chunk hash. Population-mismatch claims must be scored `0` (NEI), not supported. Omitting `verdict` on a matched claim counts as a verifier miss. Omitting a population-mismatch claim counts as an abstention miss. Retrieval is not scored on `insufficient` cases (planted evidence is off-topic by design).

```bash
uv run amfv-eval --predictions path/to/predictions.jsonl
uv run python -m amfv_datasets.eval.score --predictions path/to/predictions.jsonl
```

Pass `--gold path/to/other.jsonl` to score a custom split. Omit it to use v0.

Rates are stage-separated: decomposer (`coverage`, `precision`, `faithfulness`, `atomicity`), retrieval (`document_hit`, `section_hit`, `recency_hit`, `tier_hit`), verifier (`exact_5way`, `coarse_3way`, `scope_abstention`).

