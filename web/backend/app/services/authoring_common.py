from __future__ import annotations

from fastapi import HTTPException

from app.schemas import AuthoringConflict, ValidationPreview
from app.services.documents import EvidenceSpanValidationError

__all__ = [
    "invalid_preview",
    "raise_authoring_conflict",
    "raise_evidence_error",
    "raise_validation_error",
    "raise_validation_flags",
]


def invalid_preview(message: str) -> ValidationPreview:
    """Return one validation failure in the canonical API shape."""

    return ValidationPreview(ok=False, flags=[{"level": "error", "message": message}])


def raise_evidence_error(error: EvidenceSpanValidationError) -> None:
    """Translate evidence validation failures into the authoring API shape."""

    raise_validation_flags(
        [{"level": "error", "message": message} for message in error.messages]
    )


def raise_validation_error(preview: ValidationPreview) -> None:
    """Raise one invalid preview as the corresponding submission error."""

    raise_validation_flags(preview.flags)


def raise_validation_flags(flags: list[dict[str, str]]) -> None:
    """Raise the canonical authoring validation HTTP error."""

    raise HTTPException(status_code=400, detail=flags)


def raise_authoring_conflict(
    *,
    code: str,
    message: str,
    item_id: int | None = None,
    expected_item_revision: int | None = None,
    actual_item_revision: int | None = None,
    request_id: str | None = None,
) -> None:
    """Raise the canonical structured authoring conflict."""

    detail = AuthoringConflict(
        code=code,
        message=message,
        item_id=item_id,
        expected_item_revision=expected_item_revision,
        actual_item_revision=actual_item_revision,
        request_id=request_id,
    )
    raise HTTPException(status_code=409, detail=detail.model_dump(exclude_none=True))
