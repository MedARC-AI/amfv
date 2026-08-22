from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from uuid import UUID

from sqlmodel import Session, col, select

from app.models import (
    EvalItem,
    FactDecompReview,
    PooledCandidate,
    RelevanceJudgment,
    RetrievalQAReview,
    ReviewerKind,
    ReviewTask,
    User,
)

JudgmentMap = dict[str, dict[UUID, str]]


class JudgmentVolumeExceeded(ValueError):
    """Raised before a synchronous agreement calculation exceeds its row budget."""

    def __init__(self, max_rows: int) -> None:
        self.max_rows = max_rows
        super().__init__(
            f"Agreement exceeds the synchronous limit of {max_rows} judgments."
        )


@dataclass(frozen=True)
class AgreementSummary:
    dimension: str
    alpha: float | None
    n: int


def cohen_kappa(
    left: dict[str, str], right: dict[str, str], min_overlap: int = 1
) -> tuple[float | None, int]:
    keys = sorted(set(left) & set(right))
    if len(keys) < min_overlap:
        return None, len(keys)
    observed = sum(1 for key in keys if left[key] == right[key]) / len(keys)
    left_counts = Counter(left[key] for key in keys)
    right_counts = Counter(right[key] for key in keys)
    expected = sum(
        (left_counts[label] / len(keys)) * (right_counts[label] / len(keys))
        for label in set(left_counts) | set(right_counts)
    )
    if expected == 1:
        return (1.0 if observed == 1 else 0.0), len(keys)
    return (observed - expected) / (1 - expected), len(keys)


def krippendorff_alpha_nominal(items: JudgmentMap) -> tuple[float | None, int]:
    return _krippendorff_alpha(items, lambda a, b: 0.0 if a == b else 1.0)


def krippendorff_alpha_ordinal(
    items: JudgmentMap, order: list[str]
) -> tuple[float | None, int]:
    rank = {value: idx for idx, value in enumerate(order)}
    return _krippendorff_alpha(items, lambda a, b: float((rank[a] - rank[b]) ** 2))


def dataset_agreement(
    session: Session,
    dataset_id: int,
    reviewer_kind: ReviewerKind | None = None,
) -> list[AgreementSummary]:
    by_dimension = dataset_judgments(session, dataset_id, reviewer_kind=reviewer_kind)
    return agreement_summaries(by_dimension)


def agreement_summaries(
    by_dimension: dict[str, JudgmentMap],
) -> list[AgreementSummary]:
    """Summarize a previously loaded judgment set without issuing queries."""
    summaries = []
    for dimension, items in sorted(by_dimension.items()):
        if dimension == "relevance_grade":
            alpha, n = krippendorff_alpha_ordinal(items, ["0", "1", "2", "3"])
        else:
            alpha, n = krippendorff_alpha_nominal(items)
        if n == 0:
            continue
        summaries.append(AgreementSummary(dimension=dimension, alpha=alpha, n=n))
    return summaries


def reviewer_mean_kappa(
    session: Session,
    user_id: UUID,
    min_overlap: int,
    dataset_id: int | None = None,
) -> tuple[float | None, int]:
    by_dimension = dataset_judgments_for_all(session, dataset_id=dataset_id)
    return reviewer_mean_kappa_from_judgments(
        by_dimension,
        user_id,
        min_overlap=min_overlap,
    )


def reviewer_mean_kappa_from_judgments(
    by_dimension: dict[str, JudgmentMap],
    user_id: UUID,
    *,
    min_overlap: int,
) -> tuple[float | None, int]:
    """Compute one reviewer's mean kappa from a shared judgment snapshot."""
    kappas: list[float] = []
    overlap_total = 0
    for judgments in by_dimension.values():
        reviewer_labels: dict[str, str] = {}
        rest_labels: dict[str, str] = {}
        for item_key, labels in judgments.items():
            if user_id not in labels:
                continue
            rest = [
                label for reviewer_id, label in labels.items() if reviewer_id != user_id
            ]
            if not rest:
                continue
            reviewer_labels[item_key] = labels[user_id]
            rest_labels[item_key] = Counter(rest).most_common(1)[0][0]
        kappa, n = cohen_kappa(reviewer_labels, rest_labels, min_overlap=min_overlap)
        if kappa is not None:
            kappas.append(kappa)
            overlap_total += n
    if not kappas:
        return None, overlap_total
    return sum(kappas) / len(kappas), overlap_total


def dataset_judgments(
    session: Session, dataset_id: int, reviewer_kind: ReviewerKind | None = None
) -> dict[str, JudgmentMap]:
    all_judgments = dataset_judgments_for_all(
        session, dataset_id=dataset_id, reviewer_kind=reviewer_kind
    )
    return all_judgments


def dataset_judgments_for_all(
    session: Session,
    *,
    dataset_id: int | None = None,
    reviewer_kind: ReviewerKind | None = None,
    user_ids: set[UUID] | None = None,
    max_rows: int | None = None,
) -> dict[str, JudgmentMap]:
    """Load a filtered judgment snapshot within an optional hard row ceiling."""

    output: dict[str, JudgmentMap] = defaultdict(lambda: defaultdict(dict))
    remaining = max_rows
    statement = (
        select(FactDecompReview, ReviewTask, EvalItem)
        .join(ReviewTask, col(FactDecompReview.task_id) == col(ReviewTask.id))
        .join(
            EvalItem,
            col(ReviewTask.item_a_id) == col(EvalItem.id),
        )
    )
    if dataset_id is not None:
        statement = statement.where(col(ReviewTask.dataset_id) == dataset_id)
    if reviewer_kind is not None:
        statement = statement.where(
            col(FactDecompReview.reviewer_kind) == reviewer_kind
        )
    if user_ids is not None:
        statement = statement.where(col(FactDecompReview.user_id).in_(user_ids))
    fact_rows = session.exec(
        statement.limit(remaining + 1) if remaining is not None else statement
    ).all()
    remaining = _consume_row_budget(fact_rows, remaining, max_rows)
    for review, task, item in fact_rows:
        if review.item_revision != item.revision:
            continue
        if not review.ratings:
            continue
        for key, value in review.ratings.items():
            if key == "fact_calls" and isinstance(value, dict):
                for fact_uuid, call in value.items():
                    output["fact_call"][f"fact:{fact_uuid}"][review.user_id] = str(call)
            elif key == "found_in":
                continue
            elif isinstance(value, str):
                output[key][f"task:{task.id}"][review.user_id] = value
    item_statement = (
        select(RetrievalQAReview, EvalItem)
        .join(EvalItem, col(RetrievalQAReview.item_id) == col(EvalItem.id))
        .join(User, col(RetrievalQAReview.user_id) == col(User.id))
    )
    if dataset_id is not None:
        item_statement = item_statement.where(col(EvalItem.dataset_id) == dataset_id)
    if reviewer_kind is not None:
        item_statement = item_statement.where(col(User.reviewer_kind) == reviewer_kind)
    if user_ids is not None:
        item_statement = item_statement.where(
            col(RetrievalQAReview.user_id).in_(user_ids)
        )
    item_rows = session.exec(
        item_statement.limit(remaining + 1) if remaining is not None else item_statement
    ).all()
    remaining = _consume_row_budget(item_rows, remaining, max_rows)
    for judgment, item in item_rows:
        if judgment.skipped:
            continue
        for dimension, value in (
            ("question_validity", judgment.question_validity),
            ("evidence_quality", judgment.evidence_quality),
            ("answer_correctness", judgment.answer_correctness),
            ("answer_faithfulness", judgment.answer_faithfulness),
        ):
            if value is not None:
                output[f"item_{dimension}"][f"item:{item.id}"][judgment.user_id] = str(
                    value
                )
        if judgment.verdict:
            output["item_verdict"][f"item:{item.id}"][judgment.user_id] = (
                judgment.verdict.value
            )
    relevance_statement = (
        select(RelevanceJudgment, PooledCandidate)
        .join(
            PooledCandidate,
            col(RelevanceJudgment.candidate_id) == col(PooledCandidate.id),
        )
        .join(User, col(RelevanceJudgment.user_id) == col(User.id))
    )
    if dataset_id is not None:
        relevance_statement = relevance_statement.where(
            col(PooledCandidate.dataset_id) == dataset_id
        )
    if reviewer_kind is not None:
        relevance_statement = relevance_statement.where(
            col(User.reviewer_kind) == reviewer_kind
        )
    if user_ids is not None:
        relevance_statement = relevance_statement.where(
            col(RelevanceJudgment.user_id).in_(user_ids)
        )
    relevance_rows = session.exec(
        relevance_statement.limit(remaining + 1)
        if remaining is not None
        else relevance_statement
    ).all()
    _consume_row_budget(relevance_rows, remaining, max_rows)
    for judgment, candidate in relevance_rows:
        if judgment.skipped or judgment.grade is None:
            continue
        output["relevance_grade"][f"candidate:{candidate.id}"][judgment.user_id] = str(
            judgment.grade
        )
    return output


def _consume_row_budget(
    rows: Sequence[object],
    remaining: int | None,
    max_rows: int | None,
) -> int | None:
    if remaining is None:
        return None
    if len(rows) > remaining:
        assert max_rows is not None
        raise JudgmentVolumeExceeded(max_rows)
    return remaining - len(rows)


def _krippendorff_alpha(items: JudgmentMap, distance) -> tuple[float | None, int]:
    filtered = {key: labels for key, labels in items.items() if len(labels) >= 2}
    if not filtered:
        return None, 0
    observed = 0.0
    observed_pairs = 0
    pooled: list[str] = []
    for labels in filtered.values():
        values = list(labels.values())
        pooled.extend(values)
        for left, right in combinations(values, 2):
            observed += distance(left, right)
            observed_pairs += 1
    if observed_pairs == 0 or len(pooled) < 2:
        return None, len(filtered)
    expected = 0.0
    expected_pairs = len(pooled) * (len(pooled) - 1) // 2
    counts = Counter(pooled)
    labels = sorted(counts)
    for left_idx, left in enumerate(labels):
        for right in labels[left_idx + 1 :]:
            expected += counts[left] * counts[right] * distance(left, right)
    if expected_pairs == 0 or expected == 0:
        return (1.0 if observed == 0 else None), len(filtered)
    observed /= observed_pairs
    expected /= expected_pairs
    return 1 - (observed / expected), len(filtered)
