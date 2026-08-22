import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import EmailStr
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, Enum):
    user = "user"
    data_admin = "data_admin"
    admin = "admin"


class ReviewerKind(str, Enum):
    human = "human"
    expert = "expert"


class EvalType(str, Enum):
    RETRIEVAL = "RETRIEVAL"
    FACT_DECOMP = "FACT_DECOMP"


class RetrievalCategory(str, Enum):
    VERBATIM = "VERBATIM"
    PARAPHRASE = "PARAPHRASE"
    MULTI_CHUNK = "MULTI_CHUNK"
    ADVERSARIAL = "ADVERSARIAL"
    MULTI_DOCUMENT = "MULTI_DOCUMENT"


class ItemSource(str, Enum):
    HUMAN = "HUMAN"
    LLM = "LLM"


class ItemStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"


class FactPolarity(str, Enum):
    SHOULD_LIST = "SHOULD_LIST"
    SHOULD_NOT_LIST = "SHOULD_NOT_LIST"


class AuthorKind(str, Enum):
    HUMAN_LAY = "HUMAN_LAY"
    HUMAN_EXPERT = "HUMAN_EXPERT"
    LLM_GENERATED = "LLM_GENERATED"


class AssignmentMode(str, Enum):
    ITEM_AUDIT = "ITEM_AUDIT"
    RELEVANCE = "RELEVANCE"


class AssignmentKind(str, Enum):
    REGULAR = "REGULAR"
    DOUBLE = "DOUBLE"
    CALIBRATION = "CALIBRATION"
    TRAP = "TRAP"


class JudgmentConfidence(str, Enum):
    EASY_CALL = "EASY_CALL"
    DELIBERATED = "DELIBERATED"


class ItemVerdict(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


class NiceImportJobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class NiceImportLimit(str, Enum):
    ten = "10"
    twenty = "20"
    fifty = "50"
    all = "all"


class NiceImportItemStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    retry_pending = "retry_pending"
    failed = "failed"
    skipped = "skipped"


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)
    discord_handle: str | None = Field(default=None, max_length=255)
    medical_profession: str | None = Field(default=None, max_length=100)
    role: UserRole = Field(default=UserRole.user, max_length=32)
    reviewer_kind: ReviewerKind = Field(default=ReviewerKind.human, max_length=32)
    expertise_note: str | None = Field(default=None, max_length=500)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    invite_token: str = Field(min_length=16, max_length=255)
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    discord_handle: str | None = Field(default=None, max_length=255)
    medical_profession: str | None = Field(default=None, max_length=100)


# Properties to receive via API on update, all are optional
class UserUpdate(UserBase):
    email: EmailStr | None = Field(default=None, max_length=255)  # type: ignore[assignment]
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)
    discord_handle: str | None = Field(default=None, max_length=255)
    medical_profession: str | None = Field(default=None, max_length=100)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    retrieval_dataset_id: int | None = Field(
        default=None, foreign_key="dataset.id", nullable=True, index=True
    )
    fact_decomp_dataset_id: int | None = Field(
        default=None, foreign_key="dataset.id", nullable=True, index=True
    )
    dataset_streak_remaining: int = Field(default=0, nullable=False)
    display_name: str | None = Field(default=None, max_length=255)
    label_count_session: int = Field(default=0, nullable=False)
    label_count_total: int = Field(default=0, nullable=False)
    created_item_count: int = Field(default=0, nullable=False)
    items_authored_total: int = Field(default=0, nullable=False)
    profile_completed: bool = Field(default=False, nullable=False)
    llm_model: str | None = Field(default=None, max_length=255)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    @property
    def vote_count_session(self) -> int:
        return self.label_count_session

    @property
    def vote_count_total(self) -> int:
        return self.label_count_total


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


class SignupInviteBase(SQLModel):
    role: UserRole = Field(default=UserRole.user, max_length=32)
    expires_at: datetime | None = None
    max_redemptions: int = Field(default=1, ge=1, le=100)


class SignupInviteCreate(SignupInviteBase):
    pass


class SignupInvitePublic(SignupInviteBase):
    id: uuid.UUID
    created_at: datetime | None = None
    redeemed_count: int
    disabled_at: datetime | None = None
    created_by_user_id: uuid.UUID | None = None


class SignupInviteCreated(SignupInvitePublic):
    token: str


class SignupInvitesPublic(SQLModel):
    data: list[SignupInvitePublic]
    count: int


class SignupInvitePreview(SQLModel):
    role: UserRole
    expires_at: datetime | None = None
    remaining_redemptions: int


class SignupInvite(SignupInviteBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    token_hash: str = Field(unique=True, index=True, max_length=64)
    created_by_user_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", nullable=True
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    redeemed_count: int = Field(default=0, ge=0)
    disabled_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class TimestampMixin(SQLModel):
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
        nullable=False,
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
        nullable=False,
    )


class Dataset(TimestampMixin, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True, nullable=False)
    display_name: str = Field(nullable=False)
    description: str | None = Field(default=None)
    eval_type: EvalType = Field(nullable=False, max_length=32)
    subtype_targets: dict | None = Field(default=None, sa_column=Column(JSON))
    category_definitions: dict | None = Field(default=None, sa_column=Column(JSON))
    double_rate: float = Field(default=0.15, nullable=False)
    trap_rate: float = Field(default=0.05, nullable=False)
    is_active: bool = Field(default=True, nullable=False)


class Document(TimestampMixin, table=True):
    __table_args__ = (
        UniqueConstraint("dataset_id", "external_id", name="uq_document_dataset_external"),
    )

    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(foreign_key="dataset.id", nullable=False, index=True)
    external_id: str = Field(nullable=False, index=True)
    title: str = Field(nullable=False)
    content: str = Field(nullable=False)
    paragraphs: list[dict] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    doc_metadata: dict | None = Field(default=None, sa_column=Column("metadata", JSON))
    is_active: bool = Field(default=True, nullable=False)


class Chunk(TimestampMixin, table=True):
    __table_args__ = (
        UniqueConstraint("dataset_id", "external_id", name="uq_chunk_dataset_external"),
    )

    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(foreign_key="dataset.id", nullable=False, index=True)
    document_id: int = Field(foreign_key="document.id", nullable=False, index=True)
    external_id: str = Field(nullable=False, index=True)
    text: str = Field(nullable=False)
    position: int = Field(default=0, nullable=False)


class EvalItem(TimestampMixin, table=True):
    __tablename__ = "eval_item"
    __table_args__ = (
        UniqueConstraint("dataset_id", "external_id", name="uq_eval_item_dataset_external"),
        Index("ix_eval_item_dataset_status_type", "dataset_id", "status", "eval_type"),
    )

    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(foreign_key="dataset.id", nullable=False, index=True)
    external_id: str | None = Field(default=None, index=True)
    eval_type: EvalType = Field(nullable=False, max_length=32)
    category: RetrievalCategory | None = Field(default=None, max_length=32)
    document_id: int | None = Field(default=None, foreign_key="document.id", nullable=True, index=True)
    source: ItemSource = Field(nullable=False, max_length=32)
    author_kind: AuthorKind | None = Field(default=None, max_length=32)
    author_user_id: uuid.UUID | None = Field(default=None, foreign_key="user.id", nullable=True, index=True)
    generator_name: str | None = Field(default=None)
    prompt_text: str = Field(nullable=False)
    lazy_query: str | None = Field(default=None)
    expected_answer: str | None = Field(default=None)
    evidence_spans: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    gold_chunk_ids: list[int] | None = Field(default=None, sa_column=Column(JSON))
    trap_chunk_ids: list[int] | None = Field(default=None, sa_column=Column(JSON))
    why_not_answerable: str | None = Field(default=None)
    machine_span: dict | None = Field(default=None, sa_column=Column(JSON))
    priority_tag: str | None = Field(default=None, index=True)
    is_calibration: bool = Field(default=False, nullable=False)
    calibration_reference: dict | None = Field(default=None, sa_column=Column(JSON))
    is_trap: bool = Field(default=False, nullable=False)
    trap_note: str | None = Field(default=None)
    flagged_ambiguous: bool = Field(default=False, nullable=False)
    item_metadata: dict | None = Field(default=None, sa_column=Column("metadata", JSON))
    validation_flags: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    rejection_reason: str | None = Field(default=None)
    status: ItemStatus = Field(nullable=False, max_length=32)
    revision: int = Field(default=1, nullable=False)
    is_active: bool = Field(default=True, nullable=False)


class EvalFact(TimestampMixin, table=True):
    __tablename__ = "eval_fact"

    id: int | None = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="eval_item.id", nullable=False, index=True)
    fact_uuid: str = Field(index=True, nullable=False)
    fact_text: str = Field(nullable=False)
    polarity: FactPolarity = Field(nullable=False, max_length=32)
    position: int = Field(default=0, nullable=False)


class ReviewTask(TimestampMixin, table=True):
    __tablename__ = "review_task"
    __table_args__ = (
        UniqueConstraint("item_a_id", name="uq_review_task_item"),
        Index("ix_review_task_priority", "dataset_id", "is_active", "labels_count", "priority_score"),
    )

    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(foreign_key="dataset.id", nullable=False, index=True)
    item_a_id: int = Field(foreign_key="eval_item.id", nullable=False, index=True)
    is_gold: bool = Field(default=False, nullable=False)
    priority_score: float = Field(default=0.0, nullable=False)
    labels_count: int = Field(default=0, nullable=False)
    is_active: bool = Field(default=True, nullable=False)


class FactDecompReview(TimestampMixin, table=True):
    __tablename__ = "fact_decomp_review"
    __table_args__ = (UniqueConstraint("task_id", "user_id", name="uq_fact_decomp_review_task_user"),)

    id: int | None = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="review_task.id", nullable=False, index=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False, index=True)
    item_revision: int = Field(nullable=False)
    ratings: dict | None = Field(default=None, sa_column=Column(JSON))
    reviewer_kind: ReviewerKind = Field(default=ReviewerKind.human, nullable=False, max_length=32)
    comment: str | None = Field(default=None)
    flags: dict | None = Field(default=None, sa_column=Column(JSON))
    source: str = Field(default="web", nullable=False)


class CalibrationStatus(TimestampMixin, table=True):
    __tablename__ = "calibration_status"
    __table_args__ = (
        UniqueConstraint("user_id", "dataset_id", name="uq_calibration_user_dataset"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False, index=True)
    dataset_id: int = Field(foreign_key="dataset.id", nullable=False, index=True)
    completed_at: datetime = Field(default_factory=get_datetime_utc, nullable=False)


class PooledCandidate(TimestampMixin, table=True):
    __tablename__ = "pooled_candidate"
    __table_args__ = (
        UniqueConstraint("item_id", "chunk_id", name="uq_pooled_candidate_item_chunk"),
        Index("ix_pooled_candidate_dataset_item", "dataset_id", "item_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(foreign_key="dataset.id", nullable=False, index=True)
    item_id: int = Field(foreign_key="eval_item.id", nullable=False, index=True)
    chunk_id: int = Field(foreign_key="chunk.id", nullable=False, index=True)
    systems: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    ranks: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    is_calibration: bool = Field(default=False, nullable=False)
    is_trap: bool = Field(default=False, nullable=False)
    reference_grade: int | None = Field(default=None)


class Assignment(TimestampMixin, table=True):
    __table_args__ = (
        Index("ix_assignment_queue", "dataset_id", "mode", "completed_at", "position"),
        Index(
            "uq_assignment_live_mode_target_user",
            "mode",
            "target_id",
            "user_id",
            unique=True,
            sqlite_where=text("released_at IS NULL"),
            postgresql_where=text("released_at IS NULL"),
        ),
        Index(
            "uq_assignment_live_mode_target_slot",
            "mode",
            "target_id",
            "slot",
            unique=True,
            sqlite_where=text("released_at IS NULL"),
            postgresql_where=text("released_at IS NULL"),
        ),
        CheckConstraint("slot >= 0", name="ck_assignment_slot_nonnegative"),
    )

    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(foreign_key="dataset.id", nullable=False, index=True)
    mode: AssignmentMode = Field(nullable=False, max_length=32)
    target_id: int = Field(nullable=False, index=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False, index=True)
    kind: AssignmentKind = Field(default=AssignmentKind.REGULAR, nullable=False, max_length=32)
    assigned_at: datetime = Field(default_factory=get_datetime_utc, nullable=False)
    completed_at: datetime | None = Field(default=None, nullable=True, index=True)
    released_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
        nullable=True,
        index=True,
    )
    release_reason: str | None = Field(default=None, max_length=500)
    slot: int = Field(default=0, ge=0, nullable=False)
    position: int = Field(default=0, nullable=False)


class RetrievalQAReview(TimestampMixin, table=True):
    __tablename__ = "retrieval_qa_review"
    __table_args__ = (
        UniqueConstraint("assignment_id", name="uq_retrieval_qa_review_assignment"),
        CheckConstraint(
            "question_validity IS NULL OR question_validity BETWEEN 1 AND 4",
            name="ck_retrieval_qa_review_question_validity_range",
        ),
        CheckConstraint(
            "evidence_quality IS NULL OR evidence_quality BETWEEN 1 AND 4",
            name="ck_retrieval_qa_review_evidence_quality_range",
        ),
        CheckConstraint(
            "answer_correctness IS NULL OR answer_correctness BETWEEN 1 AND 4",
            name="ck_retrieval_qa_review_answer_correctness_range",
        ),
        CheckConstraint(
            "answer_faithfulness IS NULL OR answer_faithfulness BETWEEN 1 AND 4",
            name="ck_retrieval_qa_review_answer_faithfulness_range",
        ),
        CheckConstraint(
            "("
            "skipped = TRUE "
            "AND skip_reason IS NOT NULL "
            "AND length(trim(skip_reason)) > 0 "
            "AND question_validity IS NULL "
            "AND evidence_quality IS NULL "
            "AND answer_correctness IS NULL "
            "AND answer_faithfulness IS NULL "
            "AND verdict IS NULL"
            ") OR ("
            "skipped = FALSE "
            "AND skip_reason IS NULL "
            "AND question_validity IS NOT NULL "
            "AND question_validity BETWEEN 1 AND 4 "
            "AND evidence_quality IS NOT NULL "
            "AND evidence_quality BETWEEN 1 AND 4 "
            "AND answer_correctness IS NOT NULL "
            "AND answer_correctness BETWEEN 1 AND 4 "
            "AND answer_faithfulness IS NOT NULL "
            "AND answer_faithfulness BETWEEN 1 AND 4 "
            "AND verdict IS NOT NULL "
            "AND verdict IN ('ACCEPT', 'REJECT')"
            ")",
            name="ck_retrieval_qa_review_submission_shape",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    assignment_id: int = Field(foreign_key="assignment.id", nullable=False, index=True)
    item_id: int = Field(foreign_key="eval_item.id", nullable=False, index=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False, index=True)
    checks: dict | None = Field(default=None, sa_column=Column(JSON))
    question_validity: int | None = Field(default=None, ge=1, le=4)
    evidence_quality: int | None = Field(default=None, ge=1, le=4)
    answer_correctness: int | None = Field(default=None, ge=1, le=4)
    answer_faithfulness: int | None = Field(default=None, ge=1, le=4)
    notes: str | None = Field(default=None)
    span: dict | None = Field(default=None, sa_column=Column(JSON))
    span_overlap: float | None = Field(default=None)
    confidence: JudgmentConfidence | None = Field(default=None, max_length=32)
    verdict: ItemVerdict | None = Field(default=None, max_length=32)
    skipped: bool = Field(default=False, nullable=False)
    skip_reason: str | None = Field(default=None, max_length=500)
    started_at: datetime = Field(default_factory=get_datetime_utc, nullable=False)
    submitted_at: datetime = Field(default_factory=get_datetime_utc, nullable=False)


class RelevanceJudgment(TimestampMixin, table=True):
    __tablename__ = "relevance_judgment"
    __table_args__ = (UniqueConstraint("assignment_id", name="uq_relevance_judgment_assignment"),)

    id: int | None = Field(default=None, primary_key=True)
    assignment_id: int = Field(foreign_key="assignment.id", nullable=False, index=True)
    candidate_id: int = Field(foreign_key="pooled_candidate.id", nullable=False, index=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False, index=True)
    grade: int | None = Field(default=None)
    confidence: JudgmentConfidence | None = Field(default=None, max_length=32)
    skipped: bool = Field(default=False, nullable=False)
    skip_reason: str | None = Field(default=None)
    started_at: datetime = Field(default_factory=get_datetime_utc, nullable=False)
    submitted_at: datetime = Field(default_factory=get_datetime_utc, nullable=False)


class Adjudication(TimestampMixin, table=True):
    __table_args__ = (
        UniqueConstraint("mode", "target_id", "resolved_at", name="uq_open_adjudication_mode_target"),
        Index("ix_adjudication_dataset_mode", "dataset_id", "mode", "resolved_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(foreign_key="dataset.id", nullable=False, index=True)
    mode: AssignmentMode = Field(nullable=False, max_length=32)
    target_id: int = Field(nullable=False, index=True)
    judgment_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    adjudicator_user_id: uuid.UUID | None = Field(default=None, foreign_key="user.id", nullable=True, index=True)
    final: dict | None = Field(default=None, sa_column=Column(JSON))
    resolved_at: datetime | None = Field(default=None, nullable=True, index=True)


class NiceDownload(TimestampMixin, table=True):
    __tablename__ = "nice_download"

    id: int | None = Field(default=None, primary_key=True)
    reference: str = Field(index=True, unique=True, nullable=False)
    slug: str = Field(nullable=False)
    title: str = Field(nullable=False)
    page_url: str = Field(nullable=False)
    pdf_url: str = Field(nullable=False)
    page_count: int = Field(default=0, nullable=False)
    char_count: int = Field(default=0, nullable=False)
    content: str = Field(nullable=False)
    requested_by_user_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", nullable=True, index=True
    )


class NiceImportJob(TimestampMixin, table=True):
    __tablename__ = "nice_import_job"
    __table_args__ = (
        Index("ix_nice_import_job_active_slot", "active_slot", unique=True),
        Index("ix_nice_import_job_status", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    status: NiceImportJobStatus = Field(
        default=NiceImportJobStatus.pending, nullable=False, max_length=32
    )
    requested_limit: NiceImportLimit = Field(nullable=False, max_length=16)
    target_count: int | None = Field(default=None, nullable=True)
    completed_count: int = Field(default=0, nullable=False)
    failed_count: int = Field(default=0, nullable=False)
    started_by_user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, index=True
    )
    started_at: datetime | None = Field(default=None, nullable=True)
    finished_at: datetime | None = Field(default=None, nullable=True)
    last_error: str | None = Field(default=None, nullable=True)
    lease_owner: str | None = Field(default=None, nullable=True, max_length=128)
    lease_expires_at: datetime | None = Field(default=None, nullable=True, index=True)
    heartbeat_at: datetime | None = Field(default=None, nullable=True)
    active_slot: int | None = Field(default=1, nullable=True)


class NiceImportItem(TimestampMixin, table=True):
    __tablename__ = "nice_import_item"
    __table_args__ = (
        UniqueConstraint("job_id", "reference", name="uq_nice_import_item_job_reference"),
        Index(
            "ix_nice_import_item_pending",
            "job_id",
            "status",
            "next_attempt_at",
            "lease_expires_at",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="nice_import_job.id", nullable=False, index=True)
    reference: str = Field(nullable=False, max_length=32)
    slug: str = Field(nullable=False)
    title: str = Field(nullable=False)
    page_url: str = Field(nullable=False)
    status: NiceImportItemStatus = Field(
        default=NiceImportItemStatus.pending, nullable=False, max_length=32
    )
    attempt_count: int = Field(default=0, nullable=False)
    last_attempt_at: datetime | None = Field(default=None, nullable=True)
    next_attempt_at: datetime | None = Field(default=None, nullable=True, index=True)
    last_error: str | None = Field(default=None, nullable=True)
    lease_expires_at: datetime | None = Field(default=None, nullable=True, index=True)


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)
