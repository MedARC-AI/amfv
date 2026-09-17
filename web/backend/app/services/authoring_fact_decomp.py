from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, update
from sqlmodel import Session, col, select

from app.models import (
    Dataset,
    Document,
    EvalFact,
    EvalItem,
    EvalType,
    FactDecompSaveReceipt,
    ItemSource,
    ItemStatus,
    User,
    get_datetime_utc,
)
from app.schemas import (
    CreateFactDecompDraftSubmit,
    EvidenceSpan,
    FactDecompCreateResponse,
    FactDecompSaveReceiptResponse,
    FactDraft,
    OwnedFactDecompItemDetail,
    OwnedFactDecompItemList,
    OwnedFactDecompItemSummary,
    ValidationPreview,
)
from app.services.authoring_common import (
    invalid_preview,
    raise_authoring_conflict,
    raise_validation_flags,
)
from app.services.documents import EvidenceSpanValidationError, validate_evidence_spans
from app.services.validation import validate_item

__all__ = [
    "canonical_fact_save_hash",
    "claim_fact_draft_update",
    "ensure_fact_decomp_dataset",
    "ensure_optional_source_document",
    "fact_decomp_save_receipt_response",
    "fact_models",
    "fact_provenance_dicts",
    "list_owned_fact_items",
    "preview_fact_decomp_validation",
    "read_fact_decomp_save_receipt",
    "read_owned_fact_item",
    "read_owned_fact_item_detail",
    "reconcile_fact_decomp_save",
    "validate_fact_provenance",
]


def list_owned_fact_items(
    session: Session,
    author_user_id: Any,
    *,
    status: ItemStatus,
    offset: int,
    limit: int,
) -> OwnedFactDecompItemList:
    """List one author's fact items in latest-update order."""

    scope = (
        select(EvalItem, Dataset.display_name)
        .join(Dataset, col(EvalItem.dataset_id) == col(Dataset.id))
        .where(
            col(EvalItem.author_user_id) == author_user_id,
            col(EvalItem.eval_type) == EvalType.FACT_DECOMP,
            col(EvalItem.status) == status,
        )
    )
    total = session.exec(
        select(func.count())
        .select_from(EvalItem)
        .where(
            col(EvalItem.author_user_id) == author_user_id,
            col(EvalItem.eval_type) == EvalType.FACT_DECOMP,
            col(EvalItem.status) == status,
        )
    ).one()
    rows = session.exec(
        scope.order_by(col(EvalItem.updated_at).desc(), col(EvalItem.id).desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return OwnedFactDecompItemList(
        items=[
            OwnedFactDecompItemSummary(
                id=item.id,
                dataset_id=item.dataset_id,
                dataset_name=dataset_name,
                source_preview=item.prompt_text[:160],
                status=item.status,
                item_revision=item.revision,
                updated_at=item.updated_at,
            )
            for item, dataset_name in rows
            if item.id is not None
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


def read_owned_fact_item_detail(
    session: Session, author_user_id: Any, item_id: int
) -> OwnedFactDecompItemDetail:
    """Read a current owned fact item with position-scoped provenance."""

    item = session.get(EvalItem, item_id)
    if (
        item is None
        or item.author_user_id != author_user_id
        or item.eval_type != EvalType.FACT_DECOMP
    ):
        raise HTTPException(status_code=404, detail="Fact-decomposition item not found")
    dataset = session.get(Dataset, item.dataset_id)
    if dataset is None:
        raise HTTPException(
            status_code=500, detail="Saved fact item could not be loaded."
        )
    document = (
        session.get(Document, item.document_id)
        if item.document_id is not None
        else None
    )
    facts = session.exec(
        select(EvalFact)
        .where(col(EvalFact.item_id) == item_id)
        .order_by(col(EvalFact.position), col(EvalFact.id))
    ).all()
    positions = {fact.position for fact in facts}
    metadata = item.item_metadata
    if metadata is None:
        if item.evidence_spans:
            raise HTTPException(
                status_code=500, detail="Saved fact provenance could not be loaded."
            )
        provenance: object = []
    elif isinstance(metadata, dict):
        if "fact_provenance" not in metadata and item.evidence_spans:
            raise HTTPException(
                status_code=500, detail="Saved fact provenance could not be loaded."
            )
        provenance = metadata.get("fact_provenance", [])
    else:
        raise HTTPException(
            status_code=500, detail="Saved fact provenance could not be loaded."
        )
    if not isinstance(provenance, list):
        raise HTTPException(
            status_code=500, detail="Saved fact provenance could not be loaded."
        )
    spans_by_position: dict[int, list[EvidenceSpan]] = {
        position: [] for position in positions
    }
    try:
        for mapping in provenance:
            if not isinstance(mapping, dict):
                raise ValueError("invalid mapping")
            position = mapping.get("fact_position")
            if type(position) is not int or position not in positions:
                raise ValueError("invalid position")
            span = EvidenceSpan.model_validate(
                {key: value for key, value in mapping.items() if key != "fact_position"}
            )
            spans_by_position[position].append(span)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(
            status_code=500, detail="Saved fact provenance could not be loaded."
        ) from exc

    reason = None
    if item.status != ItemStatus.DRAFT:
        reason = "item_not_draft"
    elif not item.is_active:
        reason = "item_inactive"
    elif not dataset.is_active:
        reason = "dataset_inactive"
    elif item.document_id is not None and (
        document is None
        or not document.is_active
        or document.dataset_id != item.dataset_id
    ):
        reason = "document_inactive"
    assert item.id is not None
    return OwnedFactDecompItemDetail(
        id=item.id,
        dataset_id=item.dataset_id,
        dataset_name=dataset.display_name,
        eval_type=item.eval_type,
        status=item.status,
        item_revision=item.revision,
        updated_at=item.updated_at,
        source_text=item.prompt_text,
        document_id=item.document_id,
        facts=[
            FactDraft(
                fact_text=fact.fact_text,
                polarity=fact.polarity,
                provenance_spans=spans_by_position[fact.position],
            )
            for fact in facts
        ],
        can_edit=reason is None,
        read_only_reason=reason,
    )


def canonical_fact_save_hash(
    request: CreateFactDecompDraftSubmit,
    *,
    command: str,
) -> str:
    """Hash a canonical fact authoring request and server-owned command."""

    encoded = json.dumps(
        {
            "command": command,
            "request": request.model_dump(mode="json"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def read_fact_decomp_save_receipt(
    session: Session,
    author_user_id: Any,
    request_id: str,
) -> FactDecompSaveReceipt | None:
    """Read one author's durable fact-save receipt."""

    return session.exec(
        select(FactDecompSaveReceipt).where(
            col(FactDecompSaveReceipt.author_user_id) == author_user_id,
            col(FactDecompSaveReceipt.request_id) == request_id,
        )
    ).first()


def reconcile_fact_decomp_save(
    receipt: FactDecompSaveReceipt,
    request_hash: str,
) -> FactDecompSaveReceiptResponse:
    """Return a matching fact receipt or its canonical idempotency conflict."""

    if receipt.request_hash != request_hash:
        raise_authoring_conflict(
            code="fact_save_request_conflict",
            message="request_id was already used for a different fact save.",
            item_id=receipt.item_id,
            request_id=receipt.request_id,
        )
    return fact_decomp_save_receipt_response(receipt, replayed=True)


def fact_decomp_save_receipt_response(
    receipt: FactDecompSaveReceipt,
    *,
    replayed: bool,
) -> FactDecompSaveReceiptResponse:
    """Build the durable fact-save receipt response."""

    if receipt.command == "draft":
        command = "draft"
    elif receipt.command == "submit":
        command = "submit"
    else:
        raise RuntimeError(f"Unsupported fact save command: {receipt.command}")
    return FactDecompSaveReceiptResponse(
        request_id=receipt.request_id,
        request_hash=receipt.request_hash,
        command=command,
        request=CreateFactDecompDraftSubmit.model_validate(receipt.request_payload),
        response=FactDecompCreateResponse.model_validate(receipt.response_payload),
        replayed=replayed,
    )


def read_owned_fact_item(
    session: Session,
    body: CreateFactDecompDraftSubmit,
    current_user: User,
) -> EvalItem | None:
    """Read an owned fact draft, preserving authoring conflict semantics."""

    if body.item_id is None:
        return None
    item = session.get(EvalItem, body.item_id)
    if (
        item is None
        or item.author_user_id != current_user.id
        or item.eval_type != EvalType.FACT_DECOMP
    ):
        raise HTTPException(
            status_code=404, detail="Fact-decomposition draft not found"
        )
    if item.dataset_id != body.dataset_id:
        raise_authoring_conflict(
            code="item_dataset_conflict",
            message="A draft cannot be moved to a different dataset.",
            item_id=body.item_id,
        )
    return item


def claim_fact_draft_update(
    session: Session,
    body: CreateFactDecompDraftSubmit,
    current_user: User,
    *,
    document: Document | None,
    validation: ValidationPreview,
    status: ItemStatus,
) -> EvalItem:
    """Claim an exact draft revision before replacing its facts."""

    assert body.item_id is not None
    assert body.expected_item_revision is not None
    provenance = fact_provenance_dicts(body.facts)
    updated_item_id = session.exec(
        update(EvalItem)
        .where(
            col(EvalItem.id) == body.item_id,
            col(EvalItem.author_user_id) == current_user.id,
            col(EvalItem.eval_type) == EvalType.FACT_DECOMP,
            col(EvalItem.dataset_id) == body.dataset_id,
            col(EvalItem.status) == ItemStatus.DRAFT,
            col(EvalItem.is_active).is_(True),
            col(EvalItem.revision) == body.expected_item_revision,
        )
        .values(
            document_id=document.id if document is not None else None,
            prompt_text=body.source_text,
            evidence_spans=provenance,
            item_metadata={"fact_provenance": provenance},
            validation_flags=validation.flags,
            status=status,
            revision=col(EvalItem.revision) + 1,
            updated_at=get_datetime_utc(),
        )
        .returning(col(EvalItem.id))
    ).first()
    if updated_item_id is not None:
        item = session.get(EvalItem, updated_item_id)
        assert item is not None
        return item

    session.expire_all()
    current = session.get(EvalItem, body.item_id)
    if (
        current is None
        or current.author_user_id != current_user.id
        or current.eval_type != EvalType.FACT_DECOMP
    ):
        raise HTTPException(
            status_code=404, detail="Fact-decomposition draft not found"
        )
    if current.status != ItemStatus.DRAFT or not current.is_active:
        raise_authoring_conflict(
            code="item_not_editable",
            message="Only an active draft can be saved or submitted.",
            item_id=body.item_id,
            expected_item_revision=body.expected_item_revision,
            actual_item_revision=current.revision,
        )
    raise_authoring_conflict(
        code="item_revision_conflict",
        message="The draft was changed by another save; reload its current revision.",
        item_id=body.item_id,
        expected_item_revision=body.expected_item_revision,
        actual_item_revision=current.revision,
    )
    raise AssertionError("authoring conflict should have raised")


def preview_fact_decomp_validation(
    session: Session, body: CreateFactDecompDraftSubmit
) -> ValidationPreview:
    """Preview fact-decomposition validation without persisting a draft."""

    dataset_error = _fact_decomp_dataset_error(session, body.dataset_id)
    if dataset_error is not None:
        return invalid_preview(dataset_error)
    document = _read_optional_source_document(
        session, body.dataset_id, body.document_id
    )
    if body.document_id is not None and document is None:
        return invalid_preview("Source document is unavailable.")
    try:
        validate_fact_provenance(session, body, document)
    except EvidenceSpanValidationError as exc:
        return ValidationPreview(
            ok=False,
            flags=[{"level": "error", "message": message} for message in exc.messages],
        )
    item = EvalItem(
        dataset_id=body.dataset_id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        prompt_text=body.source_text,
        status=ItemStatus.DRAFT,
        id=body.item_id,
    )
    result = validate_item(session, item, facts=fact_models(0, body.facts))
    return ValidationPreview(ok=result.ok, flags=result.as_flags())


def ensure_fact_decomp_dataset(session: Session, dataset_id: int) -> None:
    """Ensure a write targets an active fact-decomposition dataset."""

    error = _fact_decomp_dataset_error(session, dataset_id)
    if error is not None:
        raise_validation_flags([{"level": "error", "message": error}])


def ensure_optional_source_document(
    session: Session, dataset_id: int, document_id: int | None
) -> Document | None:
    """Read an optional active source document or raise the authoring error."""

    document = _read_optional_source_document(session, dataset_id, document_id)
    if document_id is not None and document is None:
        raise_validation_flags(
            [{"level": "error", "message": "Source document is unavailable."}]
        )
    return document


def validate_fact_provenance(
    session: Session,
    body: CreateFactDecompDraftSubmit,
    document: Document | None,
) -> None:
    """Validate every submitted fact provenance span against its source scope."""

    spans = [span for fact in body.facts for span in fact.provenance_spans]
    if not spans:
        return
    validate_evidence_spans(
        session,
        dataset_id=body.dataset_id,
        spans=spans,
        allowed_document_ids=(
            {document.id} if document is not None and document.id is not None else None
        ),
    )


def fact_models(item_id: int, facts: list[FactDraft]) -> list[EvalFact]:
    """Build ordered persisted fact rows."""

    return [
        EvalFact(
            item_id=item_id,
            fact_text=fact.fact_text,
            polarity=fact.polarity,
            position=position,
        )
        for position, fact in enumerate(facts)
    ]


def fact_provenance_dicts(facts: list[FactDraft]) -> list[dict]:
    """Flatten fact-local provenance spans for the authored item."""

    rows: list[dict] = []
    for fact_position, fact in enumerate(facts):
        for span in fact.provenance_spans:
            row = span.model_dump()
            row["fact_position"] = fact_position
            rows.append(row)
    return rows


def _fact_decomp_dataset_error(session: Session, dataset_id: int) -> str | None:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None or not dataset.is_active:
        return "Dataset is not active or does not exist."
    if dataset.eval_type != EvalType.FACT_DECOMP:
        return "Dataset is not a fact-decomposition dataset."
    return None


def _read_optional_source_document(
    session: Session, dataset_id: int, document_id: int | None
) -> Document | None:
    if document_id is None:
        return None
    return session.exec(
        select(Document).where(
            col(Document.id) == document_id,
            col(Document.dataset_id) == dataset_id,
            col(Document.is_active) == True,  # noqa: E712
        )
    ).first()
