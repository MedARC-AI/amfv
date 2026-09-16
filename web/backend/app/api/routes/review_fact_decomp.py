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
from app.models import EvalFact, EvalItem, EvalType, FactDecompReview
from app.schemas import (
    AuthoredFactDecompReviewPayload,
    ChunkSummary,
    FactDecompReviewPayload,
    FactDecompReviewSubmissionResponse,
    FactDecompReviewSubmit,
    ModelEvalReviewSubmit,
    ModelFactDecompReviewPayload,
    ResponseClaimSpan,
    ReviewFact,
    ReviewModelClaim,
)
from app.services.fact_decomp_review import (
    IMPORTANCE_RUBRIC_ID,
    CorrectionMetadataError,
    ModelCorrectionRating,
    importance_guide,
    is_model_correction_item,
    project_model_claims,
)
from app.services.rubrics import validate_fact_decomp_ratings

router = APIRouter()

MODEL_REVIEW_MODE = "MODEL_LABEL_CORRECTION"
AUTHORED_REVIEW_MODE = "AUTHORED_RUBRIC"


def _review_mode(item: EvalItem) -> str:
    """Read the server-owned review mode marker, never a client field."""

    return MODEL_REVIEW_MODE if is_model_correction_item(item) else AUTHORED_REVIEW_MODE


def _model_claims(item: EvalItem, facts: list[EvalFact]) -> list[ReviewModelClaim]:
    try:
        _metadata, claims = project_model_claims(item, facts)
    except CorrectionMetadataError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return claims


def _existing_model_review(review: FactDecompReview | None) -> dict | None:
    if review is None or not isinstance(review.ratings, dict):
        return None
    try:
        ratings = ModelCorrectionRating.model_validate(review.ratings)
    except ValueError as exc:
        raise HTTPException(
            status_code=500, detail="Stored correction review is invalid"
        ) from exc
    return ratings.model_dump(
        mode="json",
        include={"rubric_id", "claim_reviews", "human_claims", "coverage_checked"},
    )


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
    review_mode = _review_mode(item)
    if review_mode == MODEL_REVIEW_MODE:
        return ModelFactDecompReviewPayload(
            dataset=dataset_payload(dataset),
            item=item_payload(item),
            task_id=task.id,
            documents=documents,
            chunks=[ChunkSummary.model_validate(chunk) for chunk in chunks],
            allowed_actions=["save_model_eval"],
            item_revision=item.revision,
            existing_review=_existing_model_review(existing),
            review_mode=MODEL_REVIEW_MODE,
            user_prompt=item.lazy_query,
            assistant_response=item.prompt_text,
            claims=_model_claims(item, list(facts)),
            guide=importance_guide(),
        )
    return AuthoredFactDecompReviewPayload(
        dataset=dataset_payload(dataset),
        item=item_payload(item),
        task_id=task.id,
        facts=[ReviewFact.model_validate(fact) for fact in facts],
        guide=importance_guide(authored=True),
        rubric_dimensions=fact_decomp_rubric(),
        documents=documents,
        chunks=[ChunkSummary.model_validate(chunk) for chunk in chunks],
        allowed_actions=["save_review"],
        item_revision=item.revision,
        existing_review=existing.model_dump(mode="json") if existing else None,
        review_mode=AUTHORED_REVIEW_MODE,
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
    if _review_mode(item) != AUTHORED_REVIEW_MODE:
        raise HTTPException(
            status_code=400,
            detail="Imported model evaluations require correction review",
        )
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
            duplicate_flags=body.duplicate_flags,
            looks_good=body.looks_good,
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


@router.post(
    "/fact-decomp/{task_id}/model-eval",
    response_model=FactDecompReviewSubmissionResponse,
)
def submit_model_eval_review(
    session: SessionDep,
    task_id: int,
    body: ModelEvalReviewSubmit,
    current_user: CurrentUser,
) -> FactDecompReviewSubmissionResponse:
    """Persist final labels and response-backed claims for an imported item."""

    task = read_active_review_task(session, task_id)
    item = read_active_item(session, task.item_a_id, EvalType.FACT_DECOMP)
    if item.dataset_id != task.dataset_id:
        raise HTTPException(status_code=404, detail="Review item not found")
    if item.author_user_id == current_user.id:
        raise HTTPException(status_code=403, detail="Cannot review your own item")
    read_active_dataset(session, task.dataset_id, EvalType.FACT_DECOMP)
    if _review_mode(item) != MODEL_REVIEW_MODE:
        raise HTTPException(
            status_code=400,
            detail="Only imported model evaluations accept correction reviews",
        )
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
    claims = _model_claims(item, list(facts))
    if body.rubric_id != IMPORTANCE_RUBRIC_ID:
        raise HTTPException(status_code=409, detail="Review rubric is stale")
    expected_positions = [claim.position for claim in claims]
    submitted_positions = [review.position for review in body.claim_reviews]
    if submitted_positions != expected_positions:
        raise HTTPException(
            status_code=400,
            detail="claim_reviews must cover each model claim position exactly once in source order",
        )
    for claim, judgment in zip(claims, body.claim_reviews, strict=True):
        changed_label = (
            judgment.label is not None and judgment.label != claim.proposed_label
        )
        if not (
            judgment.looks_good or judgment.duplicate or judgment.issue or changed_label
        ):
            raise HTTPException(
                status_code=400, detail="Each claim needs an explicit review decision"
            )
    for claim in body.human_claims:
        try:
            claim.response_spans = _validate_submitted_spans(
                item.prompt_text, claim.response_spans
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    assert task.id is not None
    try:
        ratings = ModelCorrectionRating(
            schema_version=2,
            review_mode=MODEL_REVIEW_MODE,
            rubric_id=body.rubric_id,
            claim_reviews=body.claim_reviews,
            human_claims=body.human_claims,
            coverage_checked=body.coverage_checked,
        ).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    review = FactDecompReview(
        task_id=task.id,
        user_id=current_user.id,
        item_revision=item.revision,
        ratings=ratings,
        reviewer_kind=current_user.reviewer_kind,
        source="web_model_eval",
    )
    task.labels_count += 1
    session.add(review)
    session.add(task)
    session.commit()
    session.refresh(review)
    session.refresh(task)
    assert item.id is not None
    assert review.id is not None
    return FactDecompReviewSubmissionResponse(
        id=review.id,
        task_id=task.id,
        item_id=item.id,
        labels_count=task.labels_count,
    )


def _validate_submitted_spans(
    response: str, spans: list[ResponseClaimSpan]
) -> list[ResponseClaimSpan]:
    if not spans:
        raise ValueError("Each human claim requires at least one response span")
    for span in spans:
        if span.end > len(response) or response[span.start : span.end] != span.text:
            raise ValueError("Response span text does not match the assistant response")
    for previous, current in zip(spans, spans[1:], strict=False):
        if current.start < previous.start or current.start < previous.end:
            raise ValueError("Response spans must be ordered and non-overlapping")
    return spans
