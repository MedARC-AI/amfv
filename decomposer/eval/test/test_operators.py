"""Tests for operator definitions and registry."""

from __future__ import annotations

import pytest

from amfv_eval.operators import (
    NON_RELATION_OPERATORS,
    OPERATOR_NAMES,
    OPERATORS,
    RELATION_OPERATORS,
    get_operator,
)
from amfv_eval.types import OperatorCategory


class TestOperatorRegistry:
    """Sanity checks on the operator registry."""

    def test_all_eight_operators_present(self) -> None:
        assert len(OPERATORS) == 8

    def test_five_non_relation(self) -> None:
        assert len(NON_RELATION_OPERATORS) == 5

    def test_three_relation(self) -> None:
        assert len(RELATION_OPERATORS) == 3

    def test_relation_operators_have_template(self) -> None:
        for name in RELATION_OPERATORS:
            op = OPERATORS[name]
            assert op.relational_atom_template is not None, (
                f"{name} is a RELATION operator but has no relational_atom_template"
            )

    def test_non_relation_operators_have_no_template(self) -> None:
        for name in NON_RELATION_OPERATORS:
            op = OPERATORS[name]
            assert op.relational_atom_template is None, (
                f"{name} is a NON_RELATION operator but has a relational_atom_template"
            )

    def test_operator_names_match_keys(self) -> None:
        for name, op in OPERATORS.items():
            assert op.name == name

    @pytest.mark.parametrize("name", OPERATOR_NAMES)
    def test_get_operator_roundtrip(self, name: str) -> None:
        assert get_operator(name).name == name

    def test_get_operator_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown operator"):
            get_operator("nonexistent_operator")

    def test_all_operators_have_description_and_example(self) -> None:
        for name, op in OPERATORS.items():
            assert op.description, f"{name} has empty description"
            assert op.example, f"{name} has empty example"