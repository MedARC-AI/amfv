from fastapi import APIRouter, HTTPException
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.review_common import (
    chunks_for_item,
    dataset_payload,
    documents_for_chunks_and_item,
    fact_decomp_rubric,
    item_payload,
    read_active_dataset,
    read_active_item,
    read_active_review_task,
)
from app.models import EvalFact, EvalType, FactDecompReview
from app.schemas import (
    ChunkSummary,
    FactDecompReviewPayload,
    FactDecompReviewSubmissionResponse,
    FactDecompReviewSubmit,
    ReviewFact,
)
from app.services.rubrics import validate_fact_decomp_ratings

router = APIRouter()


@router.get("/fact-decomp/{task_id}", response_model=FactDecompReviewPayload)
def read_fact_decomp_review(
    session: SessionDep,
    task_id: int,
    current_user: CurrentUser,
) -> FactDecompReviewPayload:
    task = read_active_review_task(session, task_id)
    item = read_active_item(session, task.item_a_id, EvalType.FACT_DECOMP)
    if item.dataset_id != task.dataset_id:
        raise HTTPException(status_code=404, detail="Review item not found")
    if item.author_user_id == current_user.id:
        raise HTTPException(status_code=403, detail="Cannot review your own item")
    dataset = read_active_dataset(session, task.dataset_id, EvalType.FACT_DECOMP)
    existing = session.exec(
        select(FactDecompReview).where(
            col(FactDecompReview.task_id) == task.id,
            col(FactDecompReview.user_id) == current_user.id,
        )
    ).first()
    chunks = chunks_for_item(session, item)
    documents = documents_for_chunks_and_item(session, item, chunks)
    facts = session.exec(
        select(EvalFact)
        .where(col(EvalFact.item_id) == item.id)
        .order_by(col(EvalFact.position))
    ).all()
    assert task.id is not None
    return FactDecompReviewPayload(
        dataset=dataset_payload(dataset),
        item=item_payload(item),
        task_id=task.id,
        facts=[ReviewFact.model_validate(fact) for fact in facts],
        rubric_dimensions=fact_decomp_rubric(),
        documents=documents,
        chunks=[ChunkSummary.model_validate(chunk) for chunk in chunks],
        allowed_actions=["save_review"],
        item_revision=item.revision,
        existing_review=existing.model_dump(mode="json") if existing else None,
    )


@router.post(
    "/fact-decomp/{task_id}", response_model=FactDecompReviewSubmissionResponse
)
def submit_fact_decomp_review(
    session: SessionDep,
    task_id: int,
    body: FactDecompReviewSubmit,
    current_user: CurrentUser,
) -> FactDecompReviewSubmissionResponse:
    task = read_active_review_task(session, task_id)
    item = read_active_item(session, task.item_a_id, EvalType.FACT_DECOMP)
    if item.dataset_id != task.dataset_id:
        raise HTTPException(status_code=404, detail="Review item not found")
    if item.author_user_id == current_user.id:
        raise HTTPException(status_code=403, detail="Cannot review your own item")
    read_active_dataset(session, task.dataset_id, EvalType.FACT_DECOMP)
    if body.item_revision != item.revision:
        raise HTTPException(status_code=409, detail="Item revision is stale")
    if session.exec(
        select(FactDecompReview).where(
            col(FactDecompReview.task_id) == task.id,
            col(FactDecompReview.user_id) == current_user.id,
        )
    ).first():
        raise HTTPException(status_code=409, detail="Review task already submitted")
    facts = session.exec(
        select(EvalFact)
        .where(col(EvalFact.item_id) == item.id)
        .order_by(col(EvalFact.position))
    ).all()
    try:
        ratings = validate_fact_decomp_ratings(
            item,
            list(facts),
            fact_calls=body.fact_calls,
            values=body.values,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=[{"level": "error", "message": str(exc)}],
        ) from exc
    assert task.id is not None
    review = FactDecompReview(
        task_id=task.id,
        user_id=current_user.id,
        item_revision=item.revision,
        ratings=ratings,
        reviewer_kind=current_user.reviewer_kind,
        comment=body.comments,
        flags={"confidence": body.confidence.value if body.confidence else None},
        source="web",
    )
    task.labels_count += 1
    session.add(review)
    session.add(task)
    session.commit()
    session.refresh(review)
    session.refresh(task)
    assert task.id is not None
    assert item.id is not None
    assert review.id is not None
    return FactDecompReviewSubmissionResponse(
        id=review.id,
        task_id=task.id,
        item_id=item.id,
        labels_count=task.labels_count,
    )
