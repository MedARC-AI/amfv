"""Operator definitions for decomposer eval-set generation.

Each operator describes a linguistic transformation that fuses two or more
atomic claims into a complex sentence.  Operators fall into two categories:

- ``NON_RELATION``: the fused passage encodes the same set of atomic facts as
  the seed claims; no new relational atom is introduced.
- ``RELATION``: the fusion introduces one new relational atom (e.g. a causal
  link) that a decomposer must also extract.
"""

from __future__ import annotations

from amfv_eval.types import Operator, OperatorCategory

__all__ = ["OPERATORS", "OPERATOR_NAMES", "get_operator"]

# ---------------------------------------------------------------------------
# Non-relation operators
# ---------------------------------------------------------------------------

_COREFERENCE_FUSION = Operator(
    name="coreference_fusion",
    category=OperatorCategory.NON_RELATION,
    description=(
        "Merge two claims about the same entity by replacing the repeated entity "
        "reference in the second claim with a pronoun or demonstrative (it, they, "
        "this, these).  The passage must make the referent unambiguous."
    ),
    example=(
        "Aspirin reduces inflammation, and it also inhibits platelet aggregation."
    ),
)

_APPOSITION = Operator(
    name="apposition",
    category=OperatorCategory.NON_RELATION,
    description=(
        "Embed one claim as an appositive noun phrase inserted directly after the "
        "shared entity in the other claim.  Both claims must name the entity "
        "explicitly (never use a pronoun for the entity in the output)."
    ),
    example=(
        "BRCA1, a tumor suppressor gene, is located on chromosome 17."
    ),
)

_RELATIVE_CLAUSE = Operator(
    name="relative_clause",
    category=OperatorCategory.NON_RELATION,
    description=(
        "Embed one claim as a relative clause (who, which, that) modifying the "
        "shared entity in the other claim.  The entity must be named explicitly "
        "in the output—never left as 'the drug' or another vague reference."
    ),
    example=(
        "Gefitinib, which targets EGFR, was approved by the FDA in 2003."
    ),
)

_CONJUNCTION_REDUCTION = Operator(
    name="conjunction_reduction",
    category=OperatorCategory.NON_RELATION,
    description=(
        "Join two claims that share a subject or verb phrase using 'and', omitting "
        "the repeated constituent in the second conjunct."
    ),
    example=(
        "The treatment significantly lowered blood pressure and reduced LDL cholesterol."
    ),
)

_ELLIPSIS = Operator(
    name="ellipsis",
    category=OperatorCategory.NON_RELATION,
    description=(
        "Join two parallel claims where the second clause omits a repeated verb "
        "phrase, leaving only the contrasting constituent (gapping)."
    ),
    example=(
        "Group A received the mRNA vaccine, and Group B the placebo."
    ),
)

# ---------------------------------------------------------------------------
# Relation operators (each adds exactly one new relational atom to gold)
# ---------------------------------------------------------------------------

_CAUSAL = Operator(
    name="causal",
    category=OperatorCategory.RELATION,
    description=(
        "Connect two claims with an explicit causal link using 'because', "
        "'as a result of', or a similar causal connective.  One claim is the "
        "cause and the other is the effect."
    ),
    example=(
        "The tumor volume decreased significantly because bevacizumab inhibited "
        "VEGF-mediated angiogenesis."
    ),
    relational_atom_template=(
        "The {cause} caused {effect}."
    ),
)

_CONTRASTIVE = Operator(
    name="contrastive",
    category=OperatorCategory.RELATION,
    description=(
        "Connect two claims using 'whereas', 'while', or 'in contrast' to signal "
        "that the outcomes or properties of two entities differ."
    ),
    example=(
        "Patients in the treatment arm showed improved survival, whereas those "
        "in the control arm did not."
    ),
    relational_atom_template=(
        "The outcome of {entity_a} contrasted with the outcome of {entity_b}."
    ),
)

_TEMPORAL = Operator(
    name="temporal",
    category=OperatorCategory.RELATION,
    description=(
        "Connect two claims using 'later', 'subsequently', 'after', or 'before' "
        "to signal that one event precedes the other."
    ),
    example=(
        "Patients received the hepatitis B vaccine and later developed protective "
        "antibody titers."
    ),
    relational_atom_template=(
        "{later_event} occurred after {earlier_event}."
    ),
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

OPERATORS: dict[str, Operator] = {
    op.name: op
    for op in [
        _COREFERENCE_FUSION,
        _APPOSITION,
        _RELATIVE_CLAUSE,
        _CONJUNCTION_REDUCTION,
        _ELLIPSIS,
        _CAUSAL,
        _CONTRASTIVE,
        _TEMPORAL,
    ]
}

OPERATOR_NAMES: list[str] = list(OPERATORS.keys())

NON_RELATION_OPERATORS: list[str] = [
    name for name, op in OPERATORS.items() if op.category == OperatorCategory.NON_RELATION
]

RELATION_OPERATORS: list[str] = [
    name for name, op in OPERATORS.items() if op.category == OperatorCategory.RELATION
]


def get_operator(name: str) -> Operator:
    """Return an operator by name.

    Args:
        name: Operator name as it appears in :data:`OPERATOR_NAMES`.

    Raises:
        KeyError: If ``name`` is not a registered operator.
    """
    if name not in OPERATORS:
        raise KeyError(f"Unknown operator {name!r}. Valid names: {OPERATOR_NAMES}")
    return OPERATORS[name]
