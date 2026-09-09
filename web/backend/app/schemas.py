from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlmodel import Field, SQLModel
from sqlmodel.main import SQLModelConfig

from app.models import (
    AssignmentKind,
    AssignmentMode,
    EvalType,
    FactPolarity,
    ItemStatus,
    ItemVerdict,
    JudgmentConfidence,
    PooledCandidate,
    RetrievalCategory,
    UserPublic,
)


class EvidenceSpan(SQLModel):
    chunk_id: int
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_order(self) -> EvidenceSpan:
        if self.end <= self.start:
            raise ValueError("Evidence span end must be greater than start")
        return self


class RecommendedTask(SQLModel):
    kind: AssignmentKind | str
    eval_type: EvalType
    dataset_id: int
    title: str
    reason: str
    assignment_id: int | None = None
    task_id: int | None = None


class HomeSummary(SQLModel):
    user: UserPublic
    outstanding_counts: dict[str, int] = Field(default_factory=dict)
    authored_total: int = 0
    reviewed_total: int = 0
    recommended_task: RecommendedTask | None = None


class DatasetSummary(SQLModel):
    id: int
    name: str
    display_name: str
    eval_type: EvalType
    is_active: bool


class DatasetCreate(SQLModel):
    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    eval_type: EvalType
    description: str | None = None
    is_active: bool = True


class DocumentSummary(SQLModel):
    id: int
    dataset_id: int
    external_id: str
    title: str
    source_url: str | None = None
    is_active: bool


class DocumentCreate(SQLModel):
    dataset_id: int
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    external_id: str | None = None


class DocumentDetail(DocumentSummary):
    content: str
    paragraphs: list[dict] = Field(default_factory=list)
    chunks: list[ChunkSummary] = Field(default_factory=list)


class ChunkSummary(SQLModel):
    id: int
    dataset_id: int
    document_id: int
    external_id: str
    text: str
    position: int


class SourceDocumentImportRow(BaseModel):
    """One versioned source-document row accepted by the generic importer."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    source: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    content: str = Field(min_length=1)
    section_count: int = Field(ge=1)
    metadata: dict[str, Any]

    @field_validator("source", "external_id", "title", "content")
    @classmethod
    def validate_nonempty_text(cls, value: str) -> str:
        """Reject whitespace-only identity and content fields."""
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        """Require the producer's canonical source URL to be absolute HTTP(S)."""
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("must be an absolute http(s) URL")
        return value


class DocumentImportError(SQLModel):
    """One bounded, line-local document import failure."""

    line: int = Field(ge=1)
    message: str


class DocumentImportSummary(SQLModel):
    """Counts and bounded errors produced by a document JSONL import."""

    created: int
    unchanged: int
    rejected: int
    errors: list[DocumentImportError]
    dry_run: bool


class FactDecompImportSummary(SQLModel):
    """Counts and bounded errors produced by a FACT_DECOMP JSONL import."""

    created: int
    unchanged: int
    rejected: int
    errors: list[DocumentImportError]
    dry_run: bool


RetrievalReviewAction = Literal["accept", "reject"]
FactDecompReviewAction = Literal["save_review", "save_model_eval"]
RelevanceReviewAction = Literal["grade_relevance"]
ReviewAction = RetrievalReviewAction | FactDecompReviewAction | RelevanceReviewAction


class ReviewTaskPayload(SQLModel):
    dataset_id: int
    eval_type: EvalType
    item_id: int | None = None
    assignment_id: int | None = None
    task_id: int | None = None
    prompt_text: str
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    allowed_actions: list[ReviewAction] = Field(default_factory=list)
    item_revision: int


class ReviewDataset(SQLModel):
    id: int
    name: str
    display_name: str
    eval_type: EvalType


class ReviewItem(SQLModel):
    id: int
    dataset_id: int
    eval_type: EvalType
    prompt_text: str
    status: ItemStatus
    revision: int
    category: RetrievalCategory | None = None
    expected_answer: str | None = None
    why_not_answerable: str | None = None


class ReviewFact(SQLModel):
    fact_text: str
    polarity: FactPolarity
    position: int


ModelClaimLabel = Literal["substantive", "incidental", "borderline"]
MAX_CLAIMS = 10_000
MAX_CLAIM_TEXT_LENGTH = 20_000
MAX_SPANS_PER_CLAIM = 100


class ResponseClaimSpan(SQLModel):
    """A code-point span selected from the assistant response."""

    model_config = SQLModelConfig(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=MAX_CLAIM_TEXT_LENGTH)

    @field_validator("start", "end", mode="before")
    @classmethod
    def _validate_integer_offsets(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Response span offsets must be integers")
        return value

    @model_validator(mode="after")
    def _validate_order(self) -> ResponseClaimSpan:
        if self.end <= self.start:
            raise ValueError("Response span end must be greater than start")
        if not self.text.strip():
            raise ValueError("Response span text must not be blank")
        return self


class ReviewModelClaim(SQLModel):
    """One imported claim with reviewer-visible response provenance."""

    model_config = SQLModelConfig(extra="forbid")

    claim_text: str = Field(min_length=1)
    position: int = Field(ge=0)
    response_spans: list[ResponseClaimSpan] = Field(
        min_length=1, max_length=MAX_SPANS_PER_CLAIM
    )
    proposed_label: ModelClaimLabel

    @field_validator("claim_text")
    @classmethod
    def _validate_claim_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Claim text must not be blank")
        return value


class FinalModelClaim(SQLModel):
    """A final human claim linked to an immutable model claim, or newly added."""

    model_config = SQLModelConfig(extra="forbid")

    original_position: int | None = Field(ge=0)
    claim_text: str = Field(min_length=1, max_length=MAX_CLAIM_TEXT_LENGTH)
    response_spans: list[ResponseClaimSpan] = Field(
        min_length=1, max_length=MAX_SPANS_PER_CLAIM
    )
    label: ModelClaimLabel

    @field_validator("original_position", mode="before")
    @classmethod
    def _strict_position(cls, value: object) -> object:
        if value is not None and type(value) is not int:
            raise ValueError("Original position must be an integer or null")
        return value

    @field_validator("claim_text")
    @classmethod
    def _validate_claim_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Claim text must not be blank")
        return value


class ModelCorrectionReview(SQLModel):
    final_claims: list[FinalModelClaim] = Field(max_length=MAX_CLAIMS)


class ReviewRubricDimension(SQLModel):
    key: str
    label: str
    options: list[str] = Field(default_factory=list)


class NextReviewRecommendation(SQLModel):
    kind: Literal["retrieval_audit", "fact_decomp", "relevance"]
    eval_type: EvalType
    dataset_id: int
    title: str
    reason: str
    review_url: str
    reservation_state: Literal["existing", "created", "selected"]
    assignment_id: int | None = None
    task_id: int | None = None
    item_id: int | None = None


class ReviewClaimRequest(SQLModel):
    """Request one review claim in a selected evaluation dataset."""

    eval_type: EvalType
    mode: AssignmentMode = AssignmentMode.ITEM_AUDIT
    dataset_id: int | None = None


class AssignmentRelease(SQLModel):
    """Record why an incomplete assignment is being returned to the queue."""

    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _validate_reason(self) -> AssignmentRelease:
        if not self.reason.strip():
            raise ValueError("Release reason must not be blank")
        return self


class AssignmentReleaseResponse(SQLModel):
    id: int
    released: bool = True
    release_reason: str


class AssignmentTerminalConflict(SQLModel):
    """Structured conflict returned when an assignment has a terminal state."""

    code: Literal["assignment_terminal_conflict"] = "assignment_terminal_conflict"
    message: str
    assignment_id: int


class AssignmentTerminalConflictResponse(SQLModel):
    """FastAPI's HTTPException envelope for a terminal assignment conflict."""

    detail: AssignmentTerminalConflict


class RetrievalReviewPayload(SQLModel):
    kind: Literal["retrieval_audit"] = "retrieval_audit"
    dataset: ReviewDataset
    item: ReviewItem
    assignment_id: int
    documents: list[DocumentDetail] = Field(default_factory=list)
    chunks: list[ChunkSummary] = Field(default_factory=list)
    gold_evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    trap_evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    allowed_actions: list[RetrievalReviewAction] = Field(default_factory=list)
    item_revision: int
    existing_submission: RetrievalReviewSubmission | None = None


class FactDecompReviewPayloadBase(SQLModel):
    """Fields shared by the authored and imported review payloads."""

    kind: Literal["fact_decomp"] = "fact_decomp"
    dataset: ReviewDataset
    item: ReviewItem
    task_id: int
    documents: list[DocumentDetail] = Field(default_factory=list)
    chunks: list[ChunkSummary] = Field(default_factory=list)
    item_revision: int


class AuthoredFactDecompReviewPayload(FactDecompReviewPayloadBase):
    review_mode: Literal["AUTHORED_RUBRIC"]
    facts: list[ReviewFact]
    rubric_dimensions: list[ReviewRubricDimension]
    allowed_actions: list[Literal["save_review"]]
    existing_review: dict | None = None


class ModelFactDecompReviewPayload(FactDecompReviewPayloadBase):
    review_mode: Literal["MODEL_LABEL_CORRECTION"]
    user_prompt: str | None = Field(max_length=1_000_000)
    assistant_response: str = Field(max_length=1_000_000)
    claims: list[ReviewModelClaim] = Field(max_length=MAX_CLAIMS)
    allowed_actions: list[Literal["save_model_eval"]]
    existing_review: ModelCorrectionReview | None = None


FactDecompReviewPayload = Annotated[
    AuthoredFactDecompReviewPayload | ModelFactDecompReviewPayload,
    Field(discriminator="review_mode"),
]


class RelevanceReviewPayload(SQLModel):
    kind: Literal["relevance"] = "relevance"
    dataset: ReviewDataset
    item: ReviewItem
    assignment_id: int
    candidate: PooledCandidate
    document: DocumentDetail
    chunk: ChunkSummary
    allowed_actions: list[RelevanceReviewAction] = Field(default_factory=list)
    item_revision: int
    existing_submission: dict | None = None


RubricScore = Literal[1, 2, 3, 4]


class RetrievalReviewSubmit(SQLModel):
    question_validity: RubricScore | None = None
    evidence_quality: RubricScore | None = None
    answer_correctness: RubricScore | None = None
    answer_faithfulness: RubricScore | None = None
    accept_as_gold: bool | None = None
    notes: str | None = None
    skipped: bool = False
    skip_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _validate_submission_shape(self) -> RetrievalReviewSubmit:
        scores = (
            self.question_validity,
            self.evidence_quality,
            self.answer_correctness,
            self.answer_faithfulness,
        )
        if self.skipped:
            if self.skip_reason is None or not self.skip_reason.strip():
                raise ValueError("Skipped reviews require a skip_reason")
            if self.accept_as_gold is not None or any(
                score is not None for score in scores
            ):
                raise ValueError(
                    "Skipped reviews cannot include rubric scores or a verdict"
                )
            return self
        if self.skip_reason is not None:
            raise ValueError("Non-skipped reviews cannot include a skip_reason")
        if self.accept_as_gold is None or any(score is None for score in scores):
            raise ValueError(
                "Non-skipped reviews require every rubric score and accept_as_gold"
            )
        return self


class RetrievalReviewSubmission(SQLModel):
    """Canonical persisted retrieval-review state returned to a reviewer."""

    id: int
    question_validity: int | None = Field(default=None, ge=1, le=4)
    evidence_quality: int | None = Field(default=None, ge=1, le=4)
    answer_correctness: int | None = Field(default=None, ge=1, le=4)
    answer_faithfulness: int | None = Field(default=None, ge=1, le=4)
    notes: str | None = None
    verdict: ItemVerdict | None = None
    skipped: bool
    skip_reason: str | None = None


class RelevanceReviewSubmit(SQLModel):
    grade: int = Field(ge=0, le=3)
    confidence: JudgmentConfidence | None = None
    item_revision: int


class ReviewSubmissionResponse(SQLModel):
    id: int
    assignment_id: int
    item_id: int
    kind: Literal["retrieval_audit", "relevance"]
    completed: bool = True


class FactDecompReviewSubmissionResponse(SQLModel):
    id: int
    task_id: int
    item_id: int
    labels_count: int
    completed: bool = True


class FactDecompReviewSubmit(SQLModel):
    fact_calls: list[str]
    values: dict[str, str]
    comments: str | None = None
    confidence: JudgmentConfidence | None = None
    item_revision: int


class ModelEvalReviewSubmit(SQLModel):
    """Position-aligned human corrections for an imported model decomposition."""

    model_config = SQLModelConfig(extra="forbid")

    final_claims: list[FinalModelClaim] = Field(max_length=MAX_CLAIMS)
    item_revision: int


class CreateRetrievalDraftSubmit(SQLModel):
    model_config = SQLModelConfig(extra="forbid")

    dataset_id: int
    document_ids: list[int] = Field(default_factory=list)
    category: RetrievalCategory
    question: str = Field(min_length=1)
    expected_answer: str | None = None
    unanswerable: bool = False
    gold_evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    trap_evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    why_not_answerable: str | None = None


class FactDraft(SQLModel):
    model_config = SQLModelConfig(extra="forbid")

    fact_text: str = Field(min_length=1)
    polarity: FactPolarity
    provenance_spans: list[EvidenceSpan] = Field(default_factory=list)


class CreateFactDecompDraftSubmit(SQLModel):
    model_config = SQLModelConfig(extra="forbid")

    dataset_id: int
    document_id: int | None = None
    source_text: str = Field(min_length=1)
    facts: list[FactDraft]
    item_id: int | None = Field(default=None, gt=0)
    expected_item_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_draft_identity(self) -> CreateFactDecompDraftSubmit:
        if (self.item_id is None) != (self.expected_item_revision is None):
            raise ValueError(
                "item_id and expected_item_revision must be supplied together"
            )
        return self


class FactDecompSaveCommand(CreateFactDecompDraftSubmit):
    """A durable fact save command identified by a client-generated key."""

    request_id: str = Field(min_length=1, max_length=128)


class ValidationPreview(SQLModel):
    ok: bool
    flags: list[dict[str, str]] = Field(default_factory=list)


class RetrievalCreateResponse(SQLModel):
    id: int
    dataset_id: int
    eval_type: EvalType
    category: RetrievalCategory
    status: ItemStatus
    prompt_text: str
    expected_answer: str | None = None
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    trap_evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    document_ids: list[int] = Field(default_factory=list)
    item_revision: int
    validation: ValidationPreview


class FactDecompCreateResponse(SQLModel):
    id: int
    dataset_id: int
    eval_type: EvalType
    status: ItemStatus
    prompt_text: str
    document_id: int | None = None
    facts: list[FactDraft] = Field(default_factory=list)
    item_revision: int
    validation: ValidationPreview


class FactDecompSaveReceiptResponse(SQLModel):
    request_id: str
    request_hash: str
    command: Literal["draft", "submit"]
    request: CreateFactDecompDraftSubmit
    response: FactDecompCreateResponse
    replayed: bool


class RetrievalSubmissionBatchSubmit(SQLModel):
    request_id: str = Field(min_length=1, max_length=128)
    items: list[CreateRetrievalDraftSubmit] = Field(min_length=1)


class RetrievalSubmissionBatchResponse(SQLModel):
    request_id: str
    item_ids: list[int]
    replayed: bool


class AuthoringItemState(SQLModel):
    """The durable state used to reconcile an uncertain authoring response."""

    id: int
    dataset_id: int
    eval_type: EvalType
    status: ItemStatus
    item_revision: int


class AuthoringConflict(SQLModel):
    """Structured conflict returned for stale drafts or reused idempotency keys."""

    code: str
    message: str
    item_id: int | None = None
    expected_item_revision: int | None = None
    actual_item_revision: int | None = None
    request_id: str | None = None


class AuthoringConflictResponse(SQLModel):
    """FastAPI's HTTPException envelope for an authoring conflict."""

    detail: AuthoringConflict


class AdminModerationAction(SQLModel):
    action: Literal["approve", "reject", "return_to_draft"]
    dataset_id: int
    expected_item_revision: int
    reason: str | None = None


class AdminItemSummary(SQLModel):
    id: int
    dataset_id: int
    eval_type: EvalType
    status: ItemStatus
    prompt_text: str
    category: RetrievalCategory | None = None
    document_id: int | None = None
    revision: int
    validation_flags: list[dict] = Field(default_factory=list)


class AdminExportEvidenceChunk(SQLModel):
    id: int
    document_id: int
    external_id: str
    position: int
    text: str


class AdminExportEvidenceDocument(SQLModel):
    id: int
    external_id: str
    title: str


class AdminExportFact(SQLModel):
    fact_text: str
    polarity: FactPolarity
    position: int


class AdminExportFactReview(SQLModel):
    id: int
    user_id: str
    item_revision: int
    ratings: dict[str, Any]
    reviewer_kind: str
    comment: str | None
    flags: dict[str, Any]
    source: str


class AdminExportRetrievalReview(SQLModel):
    id: int
    assignment_id: int
    user_id: str
    question_validity: int | None
    evidence_quality: int | None
    answer_correctness: int | None
    answer_faithfulness: int | None
    notes: str | None
    verdict: ItemVerdict | None
    skipped: bool
    skip_reason: str | None


class AdminExportGenerator(SQLModel):
    model_id: str
    model_revision: str | None
    prompt_id: str
    prompt_hash: str
    pydantic_ai_version: str
    generation: dict[str, str | int | float]


class AdminExportModelClaim(SQLModel):
    claim: str
    position: int
    spans: list[ResponseClaimSpan]
    proposed_label: ModelClaimLabel


class AdminExportReviewTask(SQLModel):
    id: int
    is_active: bool
    is_gold: bool
    labels_count: int
    priority_score: float


class AdminExportCorrectionReview(SQLModel):
    id: int
    user_id: str
    item_revision: int
    proposed_labels: list[ModelClaimLabel]
    final_claims: list[FinalModelClaim]
    reviewer_kind: str
    source: str


class AdminExportItemBase(SQLModel):
    id: int
    dataset_id: int
    eval_type: EvalType
    status: ItemStatus
    prompt_text: str
    expected_answer: str | None
    category: RetrievalCategory | None
    evidence_spans: list[EvidenceSpan]
    evidence_chunks: list[AdminExportEvidenceChunk]
    evidence_documents: list[AdminExportEvidenceDocument]
    facts: list[AdminExportFact]
    review_task_count: int
    retrieval_reviews: list[AdminExportRetrievalReview]


class AdminExportAuthoredItem(AdminExportItemBase):
    review_mode: Literal["AUTHORED"]
    fact_decomp_reviews: list[AdminExportFactReview]


class AdminExportModelCorrectionItem(SQLModel):
    review_mode: Literal["MODEL_LABEL_CORRECTION"]
    id: int
    dataset_id: int
    eval_type: Literal[EvalType.FACT_DECOMP]
    status: ItemStatus
    external_id: str
    case_id: str
    arm_id: str
    canonical_row_sha256: str
    user_prompt: str | None
    assistant_response: str
    generator: AdminExportGenerator
    claims: list[AdminExportModelClaim]
    review_task: AdminExportReviewTask | None
    review_task_count: int
    correction_reviews: list[AdminExportCorrectionReview]


AdminExportItem = Annotated[
    AdminExportAuthoredItem | AdminExportModelCorrectionItem,
    Field(discriminator="review_mode"),
]


class AdminTaskGenerationResult(SQLModel):
    dataset_id: int
    created: int
    existing: int


class AdminExport(SQLModel):
    dataset_id: int | None = None
    offset: int
    limit: int
    total: int
    next_offset: int | None = None
    items: list[AdminExportItem] = Field(default_factory=list)


class AdminAgreementMetric(SQLModel):
    dimension: str
    alpha: float | None
    n: int


class AdminUserMetric(SQLModel):
    user_id: str
    email: str
    role: str
    reviewer_kind: str
    authored_items: int
    fact_decomp_reviews: int
    retrieval_qa_reviews: int
    relevance_judgments: int


class AdminUserMetricPage(SQLModel):
    offset: int
    limit: int
    total: int
    next_offset: int | None = None
    items: list[AdminUserMetric] = Field(default_factory=list)


class AdminInterUserAgreementMetric(SQLModel):
    dimension: str
    left_user_id: str
    right_user_id: str
    kappa: float | None
    overlap: int
