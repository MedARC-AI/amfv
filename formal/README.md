# AMFV logical evaluation

This directory contains small, executable models of AMFV's structural
invariants. The models use Lean's standard library only and are pinned to Lean
4.32.0.

The canonical developer guide is [`../FORMAL_LOGIC.md`](../FORMAL_LOGIC.md).
Read it before adding a model, changing the JSONL protocol, linking a Python
boundary, or updating the Lean toolchain.

```sh
lake build --wfail
python3 tools/check_lean_specs.py
python3 tools/check_logic_fixtures.py
```

## Quick reference

A Python annotation such as
`# lean-spec: AMFV.Scraping.UrlPolicy.accepted_chapter_has_allowed_authority`
links an implementation decision to a named theorem. Lean proves the theorem
about the model. Python regression tests exercise the corresponding production
behavior against the same counterexample and carry a matching
`# lean-spec-test:` annotation. CI requires each target to be a theorem with an
empty axiom footprint. The annotation does not claim that Lean verifies Python
bytecode, arbitrary web pages, clinical truth, or corpus completeness.

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
