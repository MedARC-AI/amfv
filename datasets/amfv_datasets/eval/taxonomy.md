# AMFV-Bench v0 annotation rubric

Hand this to a second clinician for independent grading. Labels are the measurement standard for decomposer, retrieval, and verifier PRs. Do not grade by vibe; grade against the cited NICE span.

## Who labels

Gold verdicts, scope, as-of dates, failure-mode tags, and quoted spans are **clinician work**. They are not a job for an LLM judge, a model’s parametric knowledge, or engineering intuition.

- **Clinicians** grade each claim against the cited span, under the stated population and as-of date.
- **Engineers** run pipelines and `amfv-eval`. They may propose `input_text` and a source URL. They do not set `verdict`, `scope`, or `failure_modes`.
- v0 ships as a **seed** (`annotator`: `seed-v0-clinician`): protocol-complete draft labels, not a locked gold standard. A named clinician must lock the split. A second clinician then grades independently. Do not average Likert scores; adjudicate to one label.

If you are a physician: read this rubric, grade `gold/v0.jsonl` (or a later split) under your own annotator id, and record disagreements on verdict, scope, or section. That is the highest-leverage contribution on this benchmark.

## Unit of annotation

One **case** is a long-form `input_text` (`document`, `reasoning_trace`, or `model_output`) plus one or more **atomic claims**. Score claims, not the whole paragraph.

A claim is atomic when it is a single, coreference-resolved, independently checkable medical fact. Split “offer ABPM and start an ACE inhibitor” into two claims. Keep numbers, units, age bands, and hedges. Do not reduce “eGFR 45 mL/min/1.73 m²” to “low eGFR”.

## Verdict (Med-V1 Likert)

Grade the claim against the cited evidence, under the stated **scope** and **as-of date**.

| Score | Label | Use when |
| --- | --- | --- |
| +2 | Strong agreement | The span directly supports the claim for this population. |
| +1 | Partial agreement | Directionally true but hedged, incomplete, or “consider” rather than “offer”. |
| 0 | Neutral / NEI | Evidence does not address the claim, or the claim is out of scope for this source. |
| −1 | Partial contradiction | Mixed or indirect evidence against the claim. |
| −2 | Strong contradiction | The span directly refutes the claim as stated. |

Collapse to SciFact-style 3-way only in reporting: `{+1,+2} → support`, `{−1,−2} → refute`, `0 → NEI`. Never collapse during annotation.

## Scope and time

Every claim carries:

- **scope.population** — who (adults ≥18, under 16, pregnant, aged ≥80, Black African or African–Caribbean family origin, …)
- **scope.condition** — which indication
- **scope.setting** — optional care setting
- **as_of** — ISO date the verdict is valid

The AMFV cache key is “claim X is supported by evidence Y [under scope Z] as of date T”. If Z or T is missing, the label is not usable for cache or RL.

## Abstention (non-negotiable)

If the retrieved source is the **wrong population** (adult guideline vs child; non-pregnant vs pregnant), the gold verdict is **0** and `failure_modes` includes `population_mismatch`. Do **not** mark +2 because the disease name matched. Cite a span from the mismatched source that does **not** already refute the claim (a contraindication quote is −2, a different stratum). A pipeline that supports these cases has failed.

## Temporal / superseded guidance

If the input treats a withdrawn guideline as current, gold verdict is **−2**, `failure_modes` includes `guideline_update`, and gold evidence is the **replacement** document with `replaces` set to the withdrawn id (for example NG238 `replaces` `nice-cg181`). Retrieving only the withdrawn id is a recency miss.

## Evidence pointers

Cite `source_id`, canonical URL, **section heading**, and a quoted span of at most 280 characters. Do not paste chapters. Do not use chunk hashes. Pyramid tier for NICE guidance is `guideline`.

## Failure-mode tags

Tag every high-stakes probe. Multiple tags are allowed.

| Tag | Probe |
| --- | --- |
| `dosage` | Dose, frequency, titration |
| `unit` | mg vs mcg, mmHg, mL/min/1.73 m² |
| `contraindication` | Explicit do-not-use |
| `pregnancy` | Pregnancy or preconception |
| `pediatrics` | Under 18 |
| `renal_dosing` | eGFR, ACR, CKD |
| `guideline_update` | Withdrawn vs current |
| `population_mismatch` | Right disease, wrong pop |
| `hedge` | “may” / “consider” vs “offer” |
| `mcq_distractor` | Reciting an incorrect option as if true |

## Strata (v0)

48 cases, 8 per stratum:

1. `strongly_supported` — verdict +2
2. `weakly_supported` — verdict +1
3. `refuted` — verdict −2
4. `insufficient` — verdict 0, evidence off-topic
5. `population_mismatch` — verdict 0 + `population_mismatch`
6. `temporal` — verdict −2 + `guideline_update`

## What not to grade

- Fluency of the input
- Whether a retrieval chunk id matches a previous index
- Parametric model knowledge that is not in the cited span

## Disagreement protocol

A second **clinician** grades from this rubric and the same JSON schema. Record disagreements on verdict, scope, or section. Do not average Likert scores across annotators; adjudicate to one gold label before treating the case as locked.
