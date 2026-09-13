"""Core data types for decomposer eval-set generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

__all__ = [
    "OperatorCategory",
    "ScifactClaim",
    "ClaimGroup",
    "Operator",
    "EvalExample",
]


class OperatorCategory(StrEnum):
    """Whether an operator introduces a new relational atom."""

    NON_RELATION = "non_relation"
    RELATION = "relation"


@dataclass(frozen=True)
class ScifactClaim:
    """A single claim from the SciFact dataset.

    Args:
        id: Numeric claim identifier.
        claim: The claim text.
        evidence: Mapping from doc_id (str) to list of evidence dicts.
            Empty for NEI claims.
        cited_doc_ids: All document IDs cited by this claim (superset of
            evidence keys).
        split: Dataset split this claim belongs to.
        label: Aggregate veracity label; one of SUPPORTED, REFUTED, NEI.
    """

    id: int
    claim: str
    evidence: dict[str, list[dict]]
    cited_doc_ids: list[int]
    split: Literal["train", "validation", "test"]
    label: Literal["SUPPORTED", "REFUTED", "NEI"]


@dataclass(frozen=True)
class ClaimGroup:
    """A set of thematically related claims sharing at least one source document.

    Args:
        claims: Member claims (all from the same split).
        doc_ids: Union of all cited doc IDs across member claims.
        split: Dataset split.
    """

    claims: tuple[ScifactClaim, ...]
    doc_ids: frozenset[int]
    split: Literal["train", "validation", "test"]


@dataclass(frozen=True)
class Operator:
    """A linguistic operator that fuses atomic claims into a complex sentence.

    Args:
        name: Unique operator identifier used in eval metrics.
        category: Whether this operator adds a relational atom to gold.
        description: Instruction for the LLM describing how to apply this operator.
        example: A concrete example passage produced by this operator.
        relational_atom_template: Template string describing the added atom (only
            for RELATION category operators).
    """

    name: str
    category: OperatorCategory
    description: str
    example: str
    relational_atom_template: str | None = None


@dataclass
class EvalExample:
    """A single decomposer eval example.

    Args:
        id: Unique identifier of the form ``{split}_{index:05d}``.
        split: Dataset split.
        passage: The fused passage embedding all source claims.
        operators: Operator names applied to produce the passage, in order.
        required_atoms: Complete list of atomic claims a perfect decomposer must
            extract.  Includes contextualized seed atoms plus any relational
            atoms added by RELATION operators.
        source_claims: The SciFact claims used as seeds.
        n_operators: Number of operators applied (redundant but useful for
            stratified metrics).
        thinking_trace: Raw extended thinking returned by the LLM during
            generation (for debugging; not part of the eval contract).
    """

    id: str
    split: Literal["train", "validation", "test"]
    passage: str
    operators: list[str]
    required_atoms: list[str]
    source_claims: list[ScifactClaim]
    n_operators: int = field(init=False)
    thinking_trace: str = ""
    context_sentences: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.n_operators = len(self.operators)

    def to_dict(self) -> dict:
        """Serialize to a JSON-serializable dict for JSONL output."""
        d: dict = {
            "id": self.id,
            "split": self.split,
            "passage": self.passage,
            "operators": self.operators,
            "n_operators": self.n_operators,
            "required_atoms": self.required_atoms,
            "context_sentences": self.context_sentences,
            "source_claims": [
                {
                    "id": c.id,
                    "claim": c.claim,
                    "label": c.label,
                    "cited_doc_ids": c.cited_doc_ids,
                }
                for c in self.source_claims
            ],
        }
        if self.thinking_trace:
            d["thinking_trace"] = self.thinking_trace
        return d
