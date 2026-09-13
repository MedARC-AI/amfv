"""Tests for SciFact grouping logic."""

from __future__ import annotations

import pytest

from amfv_eval.scifact import group_claims
from amfv_eval.types import ScifactClaim


def _claim(id: int, doc_ids: list[int], label: str = "SUPPORTED") -> ScifactClaim:
    return ScifactClaim(
        id=id,
        claim=f"Claim {id}.",
        evidence={},
        cited_doc_ids=doc_ids,
        split="train",
        label=label,  # type: ignore[arg-type]
    )


class TestGroupClaims:
    """Connected-components grouping behaviour."""

    def test_direct_share(self) -> None:
        claims = [_claim(1, [10, 20]), _claim(2, [20])]
        groups = group_claims(claims)
        assert len(groups) == 1
        assert len(groups[0].claims) == 2

    def test_transitive(self) -> None:
        # 1-2 share doc 10; 2-3 share doc 20 → all three in one group
        claims = [_claim(1, [10]), _claim(2, [10, 20]), _claim(3, [20])]
        groups = group_claims(claims)
        assert len(groups) == 1
        assert len(groups[0].claims) == 3

    def test_two_separate_groups(self) -> None:
        # The design example: c1(docs=[1,2,3,4]), c2(docs=[2]), c3(docs=[4])
        # all connect transitively → one 3-member group.
        # c4(docs=[5]) is a disjoint singleton → excluded (< 2 members).
        c1 = _claim(1, [1, 2, 3, 4])
        c2 = _claim(2, [2])
        c3 = _claim(3, [4])
        c4 = _claim(4, [5])
        groups = group_claims([c1, c2, c3, c4])
        assert len(groups) == 1
        assert {c.id for c in groups[0].claims} == {1, 2, 3}

    def test_singleton_excluded(self) -> None:
        # c4 shares no docs with anyone → excluded from output
        c1 = _claim(1, [1, 2, 3, 4])
        c2 = _claim(2, [2])
        c3 = _claim(3, [4])
        c4 = _claim(4, [5])
        groups = group_claims([c1, c2, c3, c4])
        all_claim_ids = {c.id for g in groups for c in g.claims}
        assert 4 not in all_claim_ids

    def test_no_docs_all_singletons(self) -> None:
        claims = [_claim(i, []) for i in range(5)]
        groups = group_claims(claims)
        assert groups == []

    def test_empty_input(self) -> None:
        assert group_claims([]) == []

    def test_groups_sorted_by_size_descending(self) -> None:
        # Group A: 3 claims; Group B: 2 claims
        group_a = [_claim(i, [100]) for i in range(1, 4)]
        group_b = [_claim(i, [200]) for i in range(4, 6)]
        groups = group_claims(group_a + group_b)
        assert len(groups) == 2
        assert len(groups[0].claims) >= len(groups[1].claims)

    def test_doc_ids_union(self) -> None:
        claims = [_claim(1, [1, 2]), _claim(2, [2, 3])]
        groups = group_claims(claims)
        assert groups[0].doc_ids == frozenset([1, 2, 3])