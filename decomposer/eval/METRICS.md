# Decomposer Eval Metrics

Reference for the metrics reported by `amfv-eval eval` / `amfv-eval report`, the
judge labels they are derived from, and the semantics that make them
interpretable.

**Status: prototype.** Parts of this document describe the target design rather
than the current implementation. Open work is tracked in [TODO.md](TODO.md).

Read [Required atom semantics](#required-atom-semantics) first. Several metrics
are only meaningful under the lower-bound convention described there, and the
most likely way to misread this eval is to assume `required_atoms` is an
exhaustive reference.

---

## Required atom semantics

`required_atoms` is a **lower bound**, not an enumeration.

Each eval example is built by fusing `n` SciFact seed claims with a list of
operators. The required set is:

```
required_atoms = seed_atoms + relational_atoms
len(required_atoms) == len(seed_claims) + count(RELATION operators)
```

A perfect decomposer must produce **at least** this many atoms, and must cover
every one of them. It may legitimately produce more — a seed claim containing two
predications should arguably be split further, and the eval does not treat that
as an error.

Three consequences follow, and they drive most of the design below:

1. **Precision cannot be computed by aligning extracted atoms against the
   required set.** An extracted atom matching nothing in it is expected behaviour
   under a lower bound. Precision is therefore judged against the **passage
   alone** (see [Precision](#precision)), with the required set absent from the
   computation entirely.
2. **Recall is saturable by not decomposing.** A single "atom" containing the
   whole passage covers every required atom. Recall alone cannot detect this; see
   [`merge_rate`](#completeness).
3. **Precision metrics survive dataset regeneration.** Because the precision
   judge never sees the required set, regenerating it does not invalidate cached
   precision results.

### Distractors

Each passage also contains 1–2 planted `context_sentences`: background,
methodological, or framing statements that are **not** independently verifiable
and that a correct decomposer must **not** extract.

These are the only ground-truth labels in the pipeline. Everything else is a
judge's opinion. They are used both as a model metric
([`distractor_rate`](#distractor-metrics)) and as a continuous judge check
([`judge_distractor_agreement`](#judge-health)).

---

## Judge labels

Two judge calls produce all model-facing metrics.

### Coverage judge

Input: `required_atoms`, `extracted_atoms`. Output, one entry per required atom:

| Field | Type | Meaning |
|---|---|---|
| `covered_by` | `int \| None` | 1-based index into `extracted_atoms` of the atom covering this required atom, or `null` if uncovered |

Returning the **index** rather than a boolean is what makes under-splitting
detectable: duplicate indices mean one extracted atom absorbed multiple required
atoms. Same call, same cost as a boolean version.

### Precision judge

Input: the **passage** and `extracted_atoms`. No required atoms. Output, one
entry per extracted atom:

| Field | Type | Meaning |
|---|---|---|
| `supported` | `bool` | Entailed by the passage — nothing added, no qualifier dropped |
| `verifiable` | `bool` | Asserts a concrete checkable fact; not framing, methodology, or meta-commentary |
| `standalone` | `bool` | Interpretable without the passage — no unresolved pronoun or definite description |
| `atomic` | `bool` | Exactly one predication |
| `subsumed_by` | `int \| None` | 1-based index of another extracted atom that duplicates or subsumes this one |

An atom is **clean** when `supported ∧ verifiable ∧ standalone ∧ atomic ∧
(subsumed_by is None)`.

`atomic` is load-bearing, not cosmetic. Without it, a decomposer that returns the
entire passage as one atom scores `supported=T, verifiable=T, standalone=T,
subsumed_by=None` → precision 1.0, while the coverage judge marks every required
atom covered → recall 1.0. Perfect score, zero decomposition.

### Label definitions requiring an explicit ruling

Three cases are genuinely ambiguous and must be resolved by fiat, identically in
the judge prompt and the human annotation guideline. Drift between the two makes
the calibration number meaningless.

**Dropped qualifiers → `supported = False`.**
Passage: *"In a murine model, X reduced tumour volume."* Atom: *"X reduces tumour
volume."* The atom is strictly more general and is therefore not entailed. This
is the most consequential decomposer failure for the downstream pipeline: the
verifier fact-checks an overbroad claim against human evidence and returns a
confidently wrong verdict. State it explicitly or judges will call it close
enough.

**`standalone` is strict.**
*"The treatment reduced blood pressure"* is not standalone even when the passage
names the treatment three sentences earlier. The verifier receives the atom with
no passage attached, so anything requiring the passage to interpret is broken.

**`subsumed_by` marks the less informative atom**, regardless of position. Given
*"X reduced BP"* and *"X reduced BP by 12%"*, the first is subsumed. The index is
therefore not constrained to be earlier in the list.

---

## Reported metrics

### Run health

Report these **first**. If the error rate is non-trivial every number below it is
suspect, and nothing else on the report says so.

| Metric | Definition |
|---|---|
| `n_total` | Records in the input file |
| `n_scored` | Records contributing to metrics |
| `n_errored` | Records excluded due to judge or inference failure |
| `error_rate` | `n_errored / n_total` |
| `empty_extraction_rate` | Fraction of records where the model returned no atoms |

Judge failures are **errors, not zeros**. A truncated response, a 503, or a JSON
parse failure must never be recorded as "the decomposer missed every atom", and
must never be written to the cache. Errored records are excluded from every mean
rather than zero-filled.

Records with `extracted_atoms == []` **abstain** from precision (it is undefined,
not 1.0) but still score 0 on completeness, so they cannot escape by returning
nothing. They are counted separately in `empty_extraction_rate` so the abstention
is visible.

### Completeness

| Metric | Definition |
|---|---|
| `atom_recall` | `mean(covered_by is not None)` over required atoms |
| `merge_rate` | Fraction of required atoms sharing a `covered_by` index with another required atom |
| `completeness` | `atom_recall * (1 - merge_rate)` |

`merge_rate` is the defence against the recall exploit described above. It is
anchored to a known floor rather than to a judge's opinion about predication
count, which makes it a sharper under-splitting signal than `atomicity`.

### Precision

All means are over **extracted atoms**, pooled per record.

| Metric | Definition |
|---|---|
| `faithfulness` | `mean(supported)` |
| `verifiability` | `mean(verifiable)` |
| `self_contained` | `mean(standalone)` |
| `atomicity` | `mean(atomic)` |
| `redundancy` | `mean(subsumed_by is not None)` |
| `precision` | `mean(clean)` |

**Always report the components, not just `precision`.** A model at 0.55 precision
is unactionable. A model at 0.55 because `self_contained` is 0.60 while
everything else exceeds 0.95 tells you exactly which prompt to change.

### Combined score

```
combined_score = harmonic_mean(completeness, precision)
```

**This is not an F1.** A real F1 shares a true-positive set between numerator and
denominator. These two do not: completeness is measured against the required
floor, precision is measured against the passage with the required set absent. An
extracted atom can be precision-clean and match nothing in the required set,
which is correct behaviour rather than a false positive. `combined_score` is a
scalar that penalises imbalance between two unrelated rates — useful for ranking,
but do not interpret its components as standard precision/recall.

Computed **per record, then macro-averaged**, so every example is weighted
equally and records where precision abstains can be dropped cleanly. This differs
from taking the harmonic mean of the two macro-averages, sometimes materially on
records with only 2–4 required atoms.

`combined_score` is the least useful line on the report. Two models at 0.71 can
fail in completely different ways.

### Distractor metrics

| Metric | Definition |
|---|---|
| `distractor_rate` | Fraction of planted `context_sentences` extracted as atoms |
| `distractor_rate_by_kind` | The above, split by distractor `kind` |

Ground-truth labels and a known denominator, which makes this the highest-quality
precision-adjacent signal available. It is kept separate from `verifiability`
rather than folded into it: `verifiability` is judge-inferred over arbitrary
atoms, `distractor_rate` is measured against planted known-negatives. Folding the
certain thing into the uncertain thing discards the only calibration anchor in
the pipeline.

Because both penalise the same error, **only one may feed `combined_score`**
(currently `verifiability`, via `precision`). `distractor_rate` is reported
alongside.

> **Distractor difficulty.** The generator is instructed to write sentences that
> make no verifiable claim, and the decomposer is instructed not to extract
> framing. Both sides know the rule, so `distractor_rate` near zero across all
> models means the distractors do not discriminate, not that the models are good.
> The `kind` field (`background`, `methodology`, `hedged`, `attributed`) exists to
> expose this: hedged and attributed distractors sit near the real decision
> boundary, background and methodology do not.

### Granularity

| Metric | Definition |
|---|---|
| `split_ratio` | `len(extracted_atoms) / len(required_atoms)`, reported as median + percentiles |

No judge call required. The fastest read on whether a model tracks the dataset's
granularity: near 1.0 means aligned; a heavy left tail means under-splitting; a
long right tail means over-splitting or hallucination, which `faithfulness` and
`redundancy` then localise.

Interpretable only if required atoms are themselves atomic — see
[Known limitations](#known-limitations).

### Judge health

| Metric | Definition |
|---|---|
| `judge_distractor_agreement` | Of planted distractors the model extracted, the fraction the precision judge labelled `verifiable = False` |

A **judge** metric, not a model metric. Planted distractors are known-answer
items, so any case labelled `verifiable = True` is a measured judge error. This
runs continuously on every eval at no additional cost and gives a partial answer
to "should I trust `verifiability` on the atoms where there is no answer key".

The denominator is small and model-dependent — it only exists for distractors
that were extracted, so a good model yields few samples. Treat it as a smoke
alarm, not a calibration. Real calibration requires the human-labelled set.

### Training-only

| Metric | Scope |
|---|---|
| `reward_score` | Training signal only; thinking models only |

`reward_score` includes an operator-awareness term derived from the reasoning
trace, so it is undefined for models run with `enable_thinking: false`. It must
not be used for cross-model comparison — **use `combined_score` for that.**
Reports should suppress the operator-awareness section entirely when every trace
is empty, rather than printing `0.000` across all operators, which reads as a
finding rather than an absence of data.

### Debug-only

| Field | Scope |
|---|---|
| `passed` | Debugging and record triage only |

`passed` is `all(covered) and not any(context_extracted)`. Over 2–4 required
atoms and 1–2 distractors it flips on a single judge error, and it collapses
several independent failure modes into one bit. **It is not reported as a
metric** — read the component metrics instead. It remains on records so failures
can be filtered out for inspection.

---

## Statistical comparison

All models see identical examples, so comparisons should be **paired**. Example
difficulty varies substantially (2 vs 4 required atoms, easy vs hard operators),
and that variance dominates the between-model difference; unpaired intervals on
two models will overlap heavily even when one is reliably better on every
example. Pairing cancels the shared difficulty term.

**Paired bootstrap** on the continuous metrics (`combined_score`, `precision` and
its components, `completeness`). Compute per-record differences
`d_i = score_A(i) - score_B(i)`, resample **records** with replacement keeping A
and B together, and take the 2.5th/97.5th percentiles of the resampled mean
differences:

```python
diffs = np.array([a - b for a, b in zip(scores_a, scores_b)])   # paired, same order
boot = [rng.choice(diffs, len(diffs), replace=True).mean() for _ in range(10_000)]
lo, hi = np.percentile(boot, [2.5, 97.5])
```

Resampling **records** rather than scores independently is what preserves the
pairing. Report the **difference with its interval**, not two separate intervals:
overlapping marginal intervals do not imply a non-significant difference.

Neither pairing nor bootstrapping addresses judge validity. These intervals
describe sampling noise only. A *differential* judge bias between two models —
for instance a judge favouring a particular phrasing style — is invisible to them
and will read as a real difference. That is what the calibration set is for.

Not yet implemented; see [TODO.md](TODO.md).

---

## Structural checks

Cheap assertions requiring no judge call. Both catch silent corruption that every
downstream metric would otherwise inherit.

**At generation** (dataset integrity):

- `len(seed_atoms) == len(seed_claims)`
- `len(relational_atoms) == count(RELATION operators)`
- every `context_sentence` appears verbatim in `passage`

Without the first two, a short `seed_atoms` list silently lowers the floor for
that example and nothing surfaces it.

**At eval** (sanity):

- `len(extracted_atoms) >= len(required_atoms)` — flag violations rather than failing
- coverage judge returned exactly `len(required_atoms)` entries
- precision judge returned exactly `len(extracted_atoms)` entries

Length mismatches are **errors, not padding**. Padding with `False` biases recall
down; padding precision skews the number in an unknown direction. Raise and route
to the error path.

---

## Caching

Cache keys must cover the **full request payload** — system prompt, user message,
model, and sampling parameters — not just the semantic inputs. Keying on
identifiers alone means a prompt edit silently returns stale results with no
warning, which is a particularly costly failure during prompt iteration: the run
completes, the metrics come back byte-identical, and the natural conclusion is
that the change had no effect.

Note that operator *descriptions* are interpolated into both the generation and
validation prompts, so keying on operator names alone does not invalidate on an
edit to `operators.py`.

Cached values should additionally record the prompt hash, sampling parameters,
timestamp, and `finish_reason`, so suspicious results can be audited and stale
entries selectively invalidated rather than clearing the whole cache.

---

## Known limitations

**Judge validity is unmeasured.** No sub-judge has been checked against human
labels. Until the calibration set exists, the judge's agreement rate is the
ceiling on this eval's resolution, and differences of a few points between models
are not interpretable. `judge_distractor_agreement` is a partial proxy only.

**Required atoms may not be atomic.** SciFact claims are not uniformly single-
predication — *"Aspirin reduces inflammation and inhibits platelet aggregation"*
is one claim with two predications. If seed atoms inherit this, the floor is loose
by an unknown amount and `split_ratio` above 1.0 may reflect correct splitting
rather than over-splitting. Running the `atomic` judge over `required_atoms`
themselves is a one-off dataset-level pass that resolves which regime applies.

**Operator coverage is narrow.** The taxonomy omits the constructs that break
decomposers hardest — negation and scope, hedging and modality, attribution,
conditionals, quantities and comparatives, n-way enumeration, and long-distance
coreference. In particular, `apposition` and `relative_clause` explicitly instruct
the generator to name entities rather than pronominalise, which removes the
decontextualisation problem that `self_contained` is designed to measure; only
`coreference_fusion` currently probes it.

**Passage length is uniform.** Every example is 6–8 sentences with 2–4 required
atoms. Nothing measures behaviour on the long-form input the decomposer is
actually built for, where models characteristically drop the tail and lose
coreference chains across paragraphs.

---

## Recommendations for dataset generation

**The generator and the judge should be different models, from different model
families.** A judge grading a dataset written by its own family will
systematically under-detect that family's characteristic failure modes, and any
model under test from the same family gets a corresponding advantage. The
generator should also differ from every model under test.

The current configuration uses `Qwen/Qwen3.6-35B-A3B` for generation, filtering,
judging, and as one model under test. This is a **testing convenience** — it keeps
the number of model downloads on the cluster small — and is not appropriate for a
dataset intended to produce reportable numbers.
