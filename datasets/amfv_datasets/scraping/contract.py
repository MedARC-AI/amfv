"""Versioned serialized contract for normalized scraped documents.

The JSONL rows produced here are intentionally independent of any persistence
or deployment-specific dataset model. Consumers receive the canonical source
URL in ``url`` and choose dataset placement themselves.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlparse

if TYPE_CHECKING:
    from amfv_datasets.scraping.base import ScrapedDocument


SCRAPED_DOCUMENT_SCHEMA_VERSION: Final = 1
"""The only serialized scraped-document schema version currently supported."""

SCRAPED_DOCUMENT_V1_FIELDS: Final = (
    "schema_version",
    "source",
    "external_id",
    "title",
    "url",
    "content",
    "section_count",
    "metadata",
)
"""Top-level fields in the serialized scraped-document version 1 contract."""


class ScrapedDocumentContractError(ValueError):
    """Raised when a serialized scraped-document row violates the v1 contract."""


def serialize_scraped_document(document: ScrapedDocument) -> dict[str, Any]:
    """Validate and convert one scraped document to its versioned JSONL row.

    Args:
        document: Normalized source document to serialize.
    """
    return validate_scraped_document_row(asdict(document))


def validate_scraped_document_row(row: Mapping[str, object]) -> dict[str, Any]:
    """Validate one serialized scraped-document version 1 row.

    Metadata is deliberately an additive JSON object: source-specific metadata
    keys are retained as long as their values are JSON-safe. The top-level
    contract remains closed so consumers can reject incompatible artifact rows
    before persisting anything.

    Args:
        row: Parsed JSON object to validate.

    Returns:
        A plain dictionary containing the validated contract fields.

    Raises:
        ScrapedDocumentContractError: If the row does not match schema version 1.
    """
    if not isinstance(row, Mapping):
        raise ScrapedDocumentContractError("scraped-document row must be a JSON object")
    _validate_fields(row)
    _validate_schema_version(row["schema_version"])
    for field in ("source", "external_id", "title", "content"):
        _validate_nonempty_string(field, row[field])
    _validate_url(row["url"])
    _validate_section_count(row["section_count"])
    _validate_metadata(row["metadata"])
    return {field: row[field] for field in SCRAPED_DOCUMENT_V1_FIELDS}


def _validate_fields(row: Mapping[str, object]) -> None:
    fields = set(row)
    non_string_fields = [repr(field) for field in fields if not isinstance(field, str)]
    if non_string_fields:
        raise ScrapedDocumentContractError(
            f"scraped-document field names must be strings; got {', '.join(sorted(non_string_fields))}"
        )
    required_fields = set(SCRAPED_DOCUMENT_V1_FIELDS)
    missing = sorted(required_fields - fields)
    unexpected = sorted(fields - required_fields)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected fields: {', '.join(unexpected)}")
        raise ScrapedDocumentContractError(f"Invalid scraped-document fields ({'; '.join(details)})")


def _validate_schema_version(value: object) -> None:
    if type(value) is not int:
        raise ScrapedDocumentContractError(f"schema_version must be an integer; got {type(value).__name__}")
    if value != SCRAPED_DOCUMENT_SCHEMA_VERSION:
        raise ScrapedDocumentContractError(
            f"Unsupported scraped-document schema_version {value}; expected {SCRAPED_DOCUMENT_SCHEMA_VERSION}"
        )


def _validate_nonempty_string(field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ScrapedDocumentContractError(f"{field} must be a non-empty string")


def _validate_url(value: object) -> None:
    _validate_nonempty_string("url", value)
    assert isinstance(value, str)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ScrapedDocumentContractError("url must be an absolute http(s) URL")


def _validate_section_count(value: object) -> None:
    if type(value) is not int or value < 1:
        raise ScrapedDocumentContractError("section_count must be an integer of at least 1")


def _validate_metadata(value: object) -> None:
    if not isinstance(value, dict):
        raise ScrapedDocumentContractError("metadata must be a JSON object")
    _validate_json_value(value, path="metadata")


def _validate_json_value(value: object, *, path: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ScrapedDocumentContractError(f"{path} must not contain a non-finite number")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ScrapedDocumentContractError(f"{path} keys must be strings")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise ScrapedDocumentContractError(f"{path} must contain only JSON values; got {type(value).__name__}")


__all__ = [
    "SCRAPED_DOCUMENT_SCHEMA_VERSION",
    "SCRAPED_DOCUMENT_V1_FIELDS",
    "ScrapedDocumentContractError",
    "serialize_scraped_document",
    "validate_scraped_document_row",
]
