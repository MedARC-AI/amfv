# Decomposer Eval

A synthetic eval for the AMFV [claim decomposer](../README.md) — the first stage
of the fact-verification pipeline, which breaks long-form medical text into
atomic, independently verifiable claims.

## Why synthetic

Decomposition has no natural ground truth. Given a paragraph, reasonable
annotators disagree about how many claims it contains, so scoring a decomposer
against a hand-written reference mostly measures conformance to one annotator's
granularity.

This eval inverts the problem. Rather than decomposing text and asking what the
atoms should have been, it starts from atoms that are known — SciFact claims —
and *composes* them into a passage using a catalogue of linguistic fusion
operators (conjunction reduction, apposition, ellipsis, coreference, causal and
temporal linking). Because the construction is known, so is the answer: the
seed claims must be recoverable, and each relational operator adds exactly one
further claim that a correct decomposer has to surface.

Each passage also contains planted **distractors** — background and
methodological sentences that assert nothing verifiable and must *not* be
extracted. These are the only ground-truth negatives in the pipeline.

## What it measures

Recovering the seed claims is necessary but not sufficient, so the eval scores
two axes:

- **Completeness** — were the known claims recovered, and recovered *separately*
  rather than merged into one atom?
- **Precision** — is each extracted atom faithful to the passage, verifiable,
  interpretable on its own, and non-redundant?

Precision matters because the downstream verifier receives each atom with no
surrounding context. An atom with a dangling pronoun is unresolvable; an atom
that has quietly dropped a qualifier ("in a murine model") gets fact-checked as
a broader claim than the text supported, and the verifier returns a confidently
wrong verdict.

See [METRICS.md](METRICS.md) for definitions and [TODO.md](TODO.md) for open work.

## Status

Prototype. The scoring design is settled; several components are still being
built, and judge accuracy has not yet been measured against human labels — so
current numbers are useful for finding failure modes, not for ranking models.