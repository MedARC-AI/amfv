# AMFV logical evaluation

This directory contains small, executable models of AMFV's structural
invariants. The models use Lean's standard library only and are pinned to Lean
4.32.0.

```sh
lake build --wfail
python3 tools/check_lean_specs.py
python3 tools/check_logic_fixtures.py
```

## What a proof link means

A Python annotation such as
`# lean-spec: AMFV.Scraping.UrlPolicy.accepted_chapter_has_allowed_authority`
links an implementation decision to a named theorem. Lean proves the theorem
about the model. Python regression tests exercise the corresponding production
behavior against the same counterexample and carry a matching
`# lean-spec-test:` annotation. CI requires each target to be a theorem with an
empty axiom footprint. The annotation does not claim that Lean verifies Python
bytecode, arbitrary web pages, clinical truth, or corpus completeness.

## Maintenance workflow

1. Reduce the disputed behavior to a finite model.
2. Add a concrete counterexample showing the old gate admits a bad state.
3. State and prove the hardened invariant without placeholders.
4. Patch the runtime and add a regression test using the same counterexample.
5. Place a `lean-spec` annotation directly above the supported Python symbol.
6. Run the two commands above together with pytest and Ruff.

Keep theorems narrow and operational. The repository checker rejects Lake
dependencies, proof placeholders, assumption declarations, tagged definitions,
tagged theorems with axiom dependencies, and proof links without paired runtime
tests.

## Logical evaluator

`lake exe amfv_logic` reads one `amfv.logic.v1` JSON object per line and emits
one structural result per line. Supported kinds are `verifier_receipt`,
`cache_admission`, and `evaluation_case`. The evaluator reports stable
violation codes for CI and development tooling. It does not judge medical
claims or evidence content.
