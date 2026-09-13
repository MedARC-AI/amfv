# Decomposer Eval — TODO

Open work for `decomposer/eval`. Metric definitions and semantics live in
[METRICS.md](METRICS.md); this file tracks what still needs building.

Ordered by blocking relationship, not by size. Everything under
[Blocking correct measurement](#blocking-correct-measurement) should land before
any comparative numbers are reported or quoted.

---

## Blocking correct measurement

### 1. Precision judge — *in progress*

No metric currently penalises over-extraction. A decomposer that emits thirty
atoms, restates every clause three ways, and hallucinates a plausible fact scores
`atom_recall = 1.0`. `combined_score` is close to monotonically increasing in
verbosity.

- [ ] Add the precision judge: one call per record over `(passage,
      extracted_atoms)`, returning `supported` / `verifiable` / `standalone` /
      `atomic` / `subsumed_by` per extracted atom.
- [ ] Judge the **passage**, never the required atoms — required atoms are a
      lower bound, so alignment-based precision would penalise correct
      finer-grained splitting.
- [ ] Use `extra_body={"guided_json": ...}` — five booleans per atom over a
      variable-length list fails freeform JSON often enough to matter.
- [ ] Length mismatch between returned labels and `extracted_atoms` raises,
      rather than padding.
- [ ] Empty extraction abstains from precision (`None`), does not score 1.0.
- [ ] Write the annotation guideline document alongside the prompt, covering at
      minimum the three rulings in METRICS.md (dropped qualifiers, strict
      `standalone`, `subsumed_by` direction). The judge prompt is a compression
      of this document; the calibration annotators get the same one.

### 2. Coverage judge returns indices

- [ ] Change the coverage judge output from `list[bool]` to
      `covered_by: list[int | None]`.
- [ ] Derive `merge_rate` from duplicate indices. Same call, same cost, and it
      closes the "return the whole passage as one atom" exploit that recall alone
      cannot detect.

### 3. Judge errors must not be recorded as model failures — *in progress*

Every judge currently does `except Exception: covered = [False] * n`, and the
`cache.set` sits **outside** the `try`. A 503, a truncation, or a parse failure is
written to disk permanently as "the decomposer missed every atom". Reruns read the
poisoned entry back. This affects `judge_atoms`, `judge_operators`,
`judge_source_claims`, `judge_operator_awareness`, and `judge_context_extraction`.

The same pattern reaches the dataset: `_validate_dataset_examples` treats a judge
error as `valid=False` and routes the example to the failures file, so judge
flakiness silently edits the dataset.

- [x] Never cache a failure. Raise or return a sentinel; let the next run retry.
- [x] Persist an `error` field with exception type and `finish_reason`.
- [x] Exclude errored records from metrics; do not zero-fill. Report
      `n_errored` / `error_rate` at the top of the report.
- [x] Narrow `except Exception` to `openai.APIError`, `json.JSONDecodeError`, and
      `pydantic.ValidationError`. A typo in a judge currently presents as "this
      model scores badly".
- [x] Set `max_retries` on the client — currently running the SDK default.
- [x] Purge the existing cache. Entries are indistinguishable from real results.
- [x] Purge or re-run any `decomposed_*.jsonl` / `results_*.jsonl` records with
      empty `extracted_atoms`. `_cmd_decompose` resumes off `_load_completed_ids`,
      so a record written after a vLLM hiccup is marked done and will never
      self-heal.

### 4. Judge calibration set

Judge validity is unmeasured, which caps the resolution of every number this eval
produces. Until this exists, a few points between two models is not interpretable.

- [ ] Hand-label 100–200 `(passage, extracted_atoms)` pairs against the
      annotation guideline from item 1.
- [ ] Report per-sub-judge agreement with the human labels; treat it as the
      ceiling on eval resolution and state it wherever results are reported.
- [ ] Seed the set from the unit-test fixtures (item 12) so they stay in sync.
- [ ] Implement `judge_distractor_agreement` as the continuous no-cost proxy —
      planted distractors are known-answer items, so a `verifiable = True` on one
      is a measured judge error.

### 4b. Domain-expert second pass on `supported` — *after pilot 1*

Pilot 1 uses non-domain annotators, which is fine for `standalone`, `atomic`, and
`subsumed_by`, and probably fine for most of `supported`. It is not reliable for
the subclass of `supported` failures where an atom quietly shifts an assertion in
a way that only reads as wrong with the background knowledge:

- **Relation shifts that look lexical** — passage says a drug *binds* a receptor,
  atom says it *activates* it. Binding is not activation; antagonists bind.
- **Endpoint substitutions** — *"increased progression-free survival"* →
  *"increased survival"*; *"reduced HbA1c"* → *"improved glycaemic control"*.
  Different endpoint, not a paraphrase.
- **Abbreviations and standard analyte names** — whether *"CRP levels fell"* is
  `standalone` depends on knowing CRP is a standard term rather than an
  unexpanded referent. Non-domain and domain annotators disagree here in both
  directions.

This is also where the judge is weakest, for the same reason, and where a missed
defect does the most downstream damage: the verifier fact-checks a subtly
different claim against real evidence and returns a confidently wrong verdict.
If neither the annotators nor the judge catch this class, the calibration number
will overstate judge quality on exactly the errors that matter most.

- [ ] Deliberately seed 10–20 known relation-shift and endpoint-substitution
      atoms into pilot 1, so the non-domain miss rate on this class is *measured*
      rather than assumed.
- [ ] Re-annotate the `supported` label on a domain-expert subset (~50 atoms,
      weighted toward atoms where pilot 1 annotators disagreed or marked
      `uncertain`).
- [ ] Report non-domain vs domain agreement on `supported` separately. If it is
      low, `supported` from pilot 1 is a ceiling estimate, not a measurement, and
      should be reported as such wherever the calibration number appears.
- [ ] Fold the resulting disagreements into the guideline as numbered rulings,
      then re-freeze.

### 5. Cache keys must cover the request payload

Keys are built from identifiers (`model`, claim IDs, operator names), not the
prompt. Editing `_SYSTEM_PROMPT`, a judge prompt, or an operator *description*
silently returns stale results: the run completes, metrics are byte-identical, and
the natural conclusion is that the change did nothing.

- [ ] Key on the full serialised request — model, messages, sampling params.
- [ ] Record prompt hash, sampling params, timestamp, and `finish_reason` in the
      cached value, so stale entries can be selectively invalidated.
- [ ] Add cache clearing / sweeping. Nothing evicts today.

---

## Reporting

### 6. Report the component metrics

- [ ] Add run health: `n_total`, `n_scored`, `n_errored`, `error_rate`,
      `empty_extraction_rate`. These go **first** — a non-trivial error rate makes
      everything below it suspect.
- [ ] Add the precision components (`faithfulness`, `verifiability`,
      `self_contained`, `atomicity`, `redundancy`), not just the rolled-up number.
- [ ] Add `merge_rate`, `completeness`, `split_ratio` (median + percentiles).
- [ ] Demote `passed` to debug-only. Keep it on records for triage; drop it from
      the report.
- [ ] Suppress the operator-awareness section when every trace is empty, instead
      of printing `0.000` across all eight operators for non-thinking models.
- [ ] Remove the leftover `print("Records: ", records)` in `compute_metrics` — it
      dumps the entire dataset to stdout on every report run.

### 7. Paired bootstrap confidence intervals

All models see identical examples, and example difficulty dominates the
between-model variance. Unpaired intervals will overlap even when one model is
reliably better on every example.

- [ ] Implement paired bootstrap over per-record differences for the continuous
      metrics; resample **records**, not scores.
- [ ] Report the difference with its interval, not two marginal intervals.
- [ ] Add a `compare` subcommand taking two results files.

---

## Dataset quality

### 8. Expand the operator taxonomy

The current eight operators miss the constructs that break decomposers hardest.
Each needs a documented gold-atom convention, not just a description — otherwise
"what counts as one atom" drifts across the dataset and caps achievable scores.

- [ ] Negation and scope — *"X did not reduce Y"* vs *"X reduced non-Y"*
- [ ] Hedging and modality — *"may reduce"*, *"is thought to"*; is the hedge part
      of the atom?
- [ ] Attribution — *"the authors argue X"*; the atom is about the claim, not the
      world
- [ ] Conditionals — *"in patients with Z, X reduces Y"*; the condition must
      survive decomposition
- [ ] Quantities and comparatives — *"reduced mortality by 12% vs placebo"*
- [ ] Enumeration — n-way splits, not just 2-way
- [ ] Long-distance coreference — referent several sentences back
- [ ] Relax the `apposition` / `relative_clause` instructions that forbid
      pronominalisation. They currently remove the decontextualisation problem
      `self_contained` exists to measure, leaving `coreference_fusion` as the only
      operator that probes it.
- [ ] Use or remove `Operator.relational_atom_template` — it is defined and never
      referenced, so relational atoms are currently improvised per example.

### 9. Vary passage length

Every example is 6–8 sentences with 2–4 required atoms. The decomposer's real
input is long-form medical text, where models drop the tail, lose coreference
chains across paragraphs, and start merging.

- [ ] Add a long-passage stratum built from 8–15 seed claims.
- [ ] Report metrics stratified by passage length / required-atom count.
- [ ] Consider seeding some strata from a medical source — SciFact claims are
      scientific-abstract findings, not clinical assertions.

### 10. Typed distractors

- [ ] Change `context_sentences` to carry a `kind`
      (`background` / `methodology` / `hedged` / `attributed`).
- [ ] Report `distractor_rate` broken down by kind. A rate near zero across a
      mixed bag means the distractors do not discriminate, not that the models are
      good; hedged and attributed sit near the real decision boundary.

### 11. Structural checks and sampling fixes

- [ ] Assert at generation: `len(seed_atoms) == len(seed_claims)`,
      `len(relational_atoms) == count(RELATION operators)`, every
      `context_sentence` appears verbatim in `passage`. Currently
      `required_atoms = seed_atoms + relational_atoms` with no length check, so a
      short `seed_atoms` list silently lowers the floor.
- [ ] Run the `atomic` judge over `required_atoms` once at the dataset level.
      SciFact claims are not uniformly single-predication; if they are not, the
      floor is loose and `split_ratio` is not readable as a precision signal.
- [ ] Require `n_claims >= n_ops + 1`. `min(3, max(2, n_ops))` gives 2 claims for
      2 operators, and some operator pairs are linguistically impossible on one
      claim pair (ellipsis + apposition). Those fail the filter, silently biasing
      the surviving operator-combination distribution.
- [ ] Add `dedup_key` to `seen` **after** successful generation, not before. A
      transient failure currently retires that (claims, operators) pair
      permanently.
- [ ] Print the connected-component size histogram from `group_claims`. Transitive
      union over shared `cited_doc_ids` tends to produce one hub plus a long tail;
      `weights = [len(g.claims)]` then samples almost entirely from the hub, where
      "thematically related" no longer holds at four hops. Cap group size or switch
      to direct doc-sharing pairs if so.
- [ ] Revisit `_any_too_similar` (Jaccard 0.75). It catches SciFact
      claim/counter-claim pairs only when they are lexically close; a
      differently-worded REFUTED claim can still be fused with its opposite,
      producing incoherent prose the generator "fixes" by quietly altering a fact.
- [ ] Confirm split usage. `splits: [train, validation]` leaks if the decomposer
      is ever trained on SciFact train. Pin the eval to `test` and document it.

---

## Hygiene

### 12. Tests

Coverage currently skips the fragile parts.

- [ ] `extract_json` — code fences, prose wrappers, all three `<think>` variants.
- [ ] Sampling invariants in `_next_candidate`.
- [ ] Scoring arithmetic in `_judge_examples` and `compute_metrics` — empty
      extraction abstains, all-defective gives precision 0, harmonic mean handles
      `completeness = 0` without dividing by zero.
- [ ] Hand-built `(passage, extracted_atoms, expected_labels)` fixtures covering
      each precision defect in isolation: dropped qualifier, unresolved pronoun,
      unsplit conjunction, subsumed duplicate. These double as the calibration set
      seed.

### 13. Small fixes

- [ ] `generate.py` uses `__import__("re")` twice inline to reimplement
      `_utils.split_thinking`, which is already imported in the sibling module and
      handles the vLLM stripped-opening-tag case. Use it.
- [ ] `thinking_trace` is captured on cache miss only and never cached, so it is
      `""` on every rerun.
- [ ] Pin `temperature=0` on judges; set an explicit `seed`. Generation currently
      runs at vLLM's default and is only reproducible via cache hit.
- [ ] `generate.py`'s docstring claims JSON-mode structured output; the call does
      not pass one. `_LLMOutput` is post-hoc validation only, so parse failures
      become silently dropped examples. Add `guided_json`.
- [ ] `generate_example` returns bare `None` on failure, making a down vLLM server
      look like a low yield rate. Distinguish the two.

---

## Configuration

### 14. Separate generator, judge, and models under test

`Qwen/Qwen3.6-35B-A3B` currently generates the dataset, filters it, judges every
metric, and appears as a model under test. This is acceptable for pipeline testing
— it keeps cluster model downloads small — but not for a dataset intended to
produce reportable numbers.

- [ ] For any dataset generating reportable results: generator, judge, and models
      under test from different model families.
- [ ] Record generator and judge model identity in the dataset file itself, so a
      results file cannot be read without knowing what produced it.
