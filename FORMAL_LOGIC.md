# Maintaining AMFV's formal logic

AMFV uses Lean to specify and test structural rules at boundaries where an
ordinary program can return plausible-looking but invalid data. The current
models cover source URL admission, scraper accounting, verification receipts,
cache admission, and evaluation success.

This is an engineering layer, not a claim that AMFV is wholly verified. Lean
proves properties of the finite models in `formal/`. Python tests and shared
fixtures connect those models to runtime behavior.

## Why this layer exists

Medical fact verification depends on more than a model's final score. The
system must also preserve where evidence came from, which evidence a receipt
uses, whether cached work belongs to the same scope, and whether an evaluation
actually agrees with its reference.

These rules are:

- small enough to state precisely;
- important enough that silent failure is expensive;
- stable enough to serve as interfaces between components; and
- easy to under-test if only happy paths are exercised.

Lean gives these rules an executable, reviewable definition. It is a
development and CI dependency only; AMFV's Python runtime does not invoke Lean.

## What is and is not proved

The Lean library proves the named theorems about the types and functions
declared under `formal/`. The repository then checks that selected Python
symbols point to real theorems and have paired runtime tests.

This establishes a maintained correspondence. It does not prove:

- arbitrary Python bytecode correct;
- arbitrary HTML or JSON correctly parsed;
- a medical claim true or false;
- evidence clinically sufficient;
- a source corpus complete; or
- the network, clock, or upstream source trustworthy.

When adding a theorem, state its assumptions in its types or surrounding
documentation. Do not describe a model property as a property of the whole
application unless the runtime bridge actually enforces it.

## Repository layout

| Path | Responsibility |
| --- | --- |
| `lean-toolchain` | Pins the exact Lean release used locally and in CI. |
| `lakefile.toml` | Declares the `AMFV` library and `amfv_logic` executable. |
| `lake-manifest.json` | Records the dependency-free Lake project state. |
| `formal/AMFV.lean` | Imports the public formal library. |
| `formal/AMFV/Logic/` | General adversarial logical gates. |
| `formal/AMFV/Scraping/` | URL policy and scraper-accounting models. |
| `formal/AMFV/Verification/` | Receipt, cache, verdict, and evaluation models. |
| `formal/AMFV/LogicalEval.lean` | JSONL logical-evaluation executable. |
| `formal/AMFV/ProofBoundary.lean` | Explicit statements about the proof boundary. |
| `formal/fixtures/logical-eval.jsonl` | Shared Lean/Python conformance cases. |
| `tools/check_lean_specs.py` | Proof hygiene, dependency, tag, test, and axiom checks. |
| `tools/check_logic_fixtures.py` | Replays shared fixtures through Lean. |
| `.github/workflows/formal-maintenance.yml` | Pull-request, push, scheduled, and manual gate. |

The formal project intentionally uses only Lean's standard library. Keeping the
model small and dependency-free makes it easier for maintainers to audit and
keeps CI failures attributable to this repository.

## Local setup

Install Lean through `elan`, Lean's toolchain manager. From the repository root,
confirm that the pinned version is selected:

```sh
lean --version
lake --version
cat lean-toolchain
```

`elan` reads `lean-toolchain` automatically. The expected pin is
`leanprover/lean4:v4.32.0`.

Install the Python development environment separately:

```sh
uv sync --dev
```

No Lean package installation is required. A successful first build downloads
or selects the pinned toolchain and builds the local project:

```sh
lake build --wfail
```

## The normal maintenance loop

Use the same counterexample from discovery through proof and runtime repair:

1. Identify a structural state that AMFV must reject or account for.
2. Reduce it to the smallest finite model that preserves the failure.
3. Add the counterexample to Lean and show why the old gate is insufficient.
4. State and prove a narrow invariant for the repaired gate.
5. Patch the Python boundary that enforces the rule.
6. Add a focused Python regression test using the same counterexample.
7. Link the runtime symbol and test to the theorem.
8. Add or update shared JSONL fixtures when the rule is part of the logical
   evaluator protocol.
9. Run the full maintenance gate before requesting review.

Prefer a theorem about one admission or accounting decision over a theorem that
recreates an entire Python subsystem. Small models are easier to review, reuse,
and keep aligned.

## Linking Python to a theorem

Put a `lean-spec` comment immediately above the supported Python function or
class:

```python
# lean-spec: AMFV.Scraping.UrlPolicy.accepted_chapter_has_allowed_authority
def is_allowed_chapter(...):
    ...
```

Put the matching `lean-spec-test` comment immediately above a pytest test:

```python
# lean-spec-test: AMFV.Scraping.UrlPolicy.accepted_chapter_has_allowed_authority
def test_rejects_off_domain_chapter(...):
    ...
```

The fully qualified name must resolve to a Lean `theorem`, not merely a
definition. Every runtime tag must have at least one paired test tag.

`tools/check_lean_specs.py` enforces that:

- both annotations are attached directly to Python declarations;
- each runtime annotation has a paired runtime test;
- every named declaration exists and is a theorem;
- tagged theorems have an empty axiom footprint;
- Lean sources contain no `sorry`, `admit`, `axiom`, `constant`, or `opaque`
  proof escape; and
- `lake-manifest.json` contains no external packages.

If a Python symbol implements several independent rules, use a small validating
function for each rule instead of making one annotation stand for an ambiguous
bundle of behavior.

## Shared logical-evaluation fixtures

`lake exe amfv_logic` accepts one `amfv.logic.v1` JSON object per line and emits
one result per line. Supported input kinds are:

- `verifier_receipt`;
- `cache_admission`; and
- `evaluation_case`.

Each fixture in `formal/fixtures/logical-eval.jsonl` includes
`expected_valid` and `expected_violations`. Violation codes are part of the
developer-facing protocol: keep them stable unless a deliberate protocol change
is documented and applied to both implementations.

When extending the protocol:

1. define the rule and violation code in the Lean model;
2. update `formal/AMFV/LogicalEval.lean`;
3. update the Python mirror;
4. add valid, invalid, boundary, and malformed fixtures;
5. make both implementations return the same ordered result; and
6. run both the Lean fixture replay and Python conformance tests.

Fixtures should include the smallest counterexample, not just realistic large
objects. For time rules, cover exact boundaries, unreasonable values, UTC
normalization, and daylight-saving folds where applicable. For collections,
cover missing fields, wrong shapes, unknown identifiers, duplicates, overlaps,
and empty directional evidence.

## Required checks

Run these commands from the repository root:

```sh
lake build --wfail
python3 tools/check_lean_specs.py
python3 tools/check_logic_fixtures.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

When changing a package's public or packaging surface, also build that package,
for example:

```sh
uv build --package amfv-verifier
```

`--wfail` is intentional: new Lean warnings are maintenance failures rather
than deferred cleanup.

## Continuous maintenance

`.github/workflows/formal-maintenance.yml` runs when formal files, proof-link
tools, the workflow itself, or Python component files change. It also runs every
Monday and can be started manually with `workflow_dispatch`.

The workflow:

1. checks out the repository;
2. installs the version from `lean-toolchain`;
3. builds Lean with warnings treated as errors;
4. verifies theorem links, paired tests, dependencies, and axiom footprints;
5. replays the shared logical-evaluation fixtures.

The scheduled run is a drift detector. Do not solve a scheduled failure by
loosening a theorem, deleting a counterexample, or weakening the checker. First
identify whether the toolchain, model, runtime link, or fixture protocol drifted.

## Updating Lean

Treat a Lean upgrade as its own pull request:

1. change `lean-toolchain` to the intended exact release;
2. run `lake update` to refresh `lake-manifest.json`;
3. run every required check above;
4. inspect warnings and proof changes rather than applying broad mechanical
   rewrites;
5. confirm `packages` remains empty in `lake-manifest.json`;
6. record meaningful language or behavior changes in the pull-request body.

Do not use an unpinned channel such as `stable` or `nightly`. Do not add Mathlib
or another Lean dependency merely to shorten a small proof; propose that change
explicitly with its maintenance and supply-chain cost.

## Diagnosing failures

### A `lean-spec` target does not resolve

Import its module from `formal/AMFV.lean`, check the fully qualified namespace,
and run:

```sh
lake env lean formal/AMFV.lean
python3 tools/check_lean_specs.py
```

### A theorem has an axiom dependency

Inspect it directly:

```lean
#print axioms AMFV.Namespace.theorem_name
```

Remove the assumption or proof escape. Do not suppress the checker.

### Lean and Python disagree on a fixture

Reduce the failing record to the smallest JSON object that still disagrees.
Then compare:

```sh
lake exe amfv_logic < formal/fixtures/logical-eval.jsonl
python3 tools/check_logic_fixtures.py
uv run pytest
```

Determine whether the model, Python mirror, or expected protocol result is
wrong. The formal implementation is not automatically authoritative about
medical meaning; the intended rule must be reviewed.

### CI passes locally but fails on the schedule

Confirm the checked-in toolchain and manifest match the local environment, then
rerun with a clean Lake build directory if necessary. If the failure is caused
by an upstream action change, pin or repair the workflow in a focused pull
request rather than bypassing the formal gate.

## Review checklist

Before merging a formal-logic change, verify:

- [ ] The motivating bad state is concrete and reproducible.
- [ ] The model is smaller than the runtime behavior it constrains.
- [ ] Assumptions and proof boundaries are explicit.
- [ ] The theorem has no proof placeholders or axiom dependencies.
- [ ] The Python enforcement point has a `lean-spec` tag.
- [ ] A focused regression test has the matching `lean-spec-test` tag.
- [ ] Shared fixtures cover both acceptance and rejection when applicable.
- [ ] Violation codes and protocol changes are deliberate and documented.
- [ ] Lean remains a development/CI dependency, not a runtime dependency.
- [ ] All required local and CI checks pass.

## Choosing the next proof target

Good targets are deterministic admission, accounting, freshness, scope, and
agreement rules whose failure can silently contaminate later stages. Avoid
using Lean to restate broad product intent or to make claims about clinical
truth that require empirical evidence.

The practical question is: **what invalid state could still look valid to the
next component?** Start there.
