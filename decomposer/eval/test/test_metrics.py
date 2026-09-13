"""Tests for metrics computation."""

from __future__ import annotations

import pytest

from amfv_eval.metrics import compute_metrics


def _record(
    *,
    operators: list[str],
    atom_covered: list[bool],
    operators_noticed: dict[str, bool],
) -> dict:
    passed = all(atom_covered) and all(operators_noticed.values())
    return {
        "id": "train_00000",
        "split": "train",
        "n_operators": len(operators),
        "operators": operators,
        "required_atoms": [f"atom {i}" for i in range(len(atom_covered))],
        "evaluations": [
            {
                "model": "test-model",
                "atom_covered": atom_covered,
                "operators_noticed": operators_noticed,
                "passed": passed,
            }
        ],
        "passed": passed,
    }


class TestComputeMetrics:
    def test_empty(self) -> None:
        assert compute_metrics([]) == {}

    def test_all_pass(self) -> None:
        records = [
            _record(
                operators=["causal"],
                atom_covered=[True, True],
                operators_noticed={"causal": True},
            )
        ] * 4
        m = compute_metrics(records)
        assert m["total"] == 4
        assert m["passed"] == 4
        assert m["pass_rate"] == pytest.approx(1.0)
        assert m["atom_recall"] == pytest.approx(1.0)
        assert m["by_operator"]["causal"] == pytest.approx(1.0)

    def test_partial_pass(self) -> None:
        passing = _record(
            operators=["causal"],
            atom_covered=[True, True],
            operators_noticed={"causal": True},
        )
        failing = _record(
            operators=["causal"],
            atom_covered=[True, False],
            operators_noticed={"causal": False},
        )
        m = compute_metrics([passing, failing])
        assert m["total"] == 2
        assert m["passed"] == 1
        assert m["pass_rate"] == pytest.approx(0.5)
        assert m["atom_recall"] == pytest.approx(0.75)  # (1.0 + 0.5) / 2
        assert m["by_operator"]["causal"] == pytest.approx(0.5)  # (1 + 0) / 2

    def test_by_n_operators(self) -> None:
        one_op = _record(
            operators=["causal"],
            atom_covered=[True],
            operators_noticed={"causal": True},
        )
        two_op = _record(
            operators=["causal", "apposition"],
            atom_covered=[True, False],
            operators_noticed={"causal": True, "apposition": False},
        )
        m = compute_metrics([one_op, two_op])
        assert m["by_n_operators"][1]["passed"] == 1
        assert m["by_n_operators"][2]["passed"] == 0

    def test_operator_awareness_per_operator(self) -> None:
        records = [
            _record(
                operators=["causal", "temporal"],
                atom_covered=[True],
                operators_noticed={"causal": True, "temporal": False},
            ),
            _record(
                operators=["causal", "temporal"],
                atom_covered=[True],
                operators_noticed={"causal": True, "temporal": True},
            ),
        ]
        m = compute_metrics(records)
        assert m["by_operator"]["causal"] == pytest.approx(1.0)
        assert m["by_operator"]["temporal"] == pytest.approx(0.5)

    def test_uses_latest_evaluation(self) -> None:
        record = _record(
            operators=["causal"],
            atom_covered=[True],
            operators_noticed={"causal": True},
        )
        # Append a second (failing) evaluation
        record["evaluations"].append(
            {
                "model": "other-model",
                "atom_covered": [False],
                "operators_noticed": {"causal": False},
                "passed": False,
            }
        )
        record["passed"] = False
        m = compute_metrics([record])
        # Should reflect the latest (failing) evaluation
        assert m["atom_recall"] == pytest.approx(0.0)
        assert m["by_operator"]["causal"] == pytest.approx(0.0)