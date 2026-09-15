"""Exact prompt loading, case framing, and no-network preview."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from amfv_datasets.decomposition_eval.models import (
    DecompositionCase,
    DecompositionPrediction,
    GenerationSettings,
)

__all__ = ["build_preview", "default_prompt_path", "load_cases", "load_prompt", "render_case", "validate_output_path"]

_DEFAULT_PROMPT_PACKAGE = "amfv_datasets.decomposition_eval.prompts"
_DEFAULT_PROMPT_NAME = "decomposition-04-elementcheck-claimify.txt"
_MAX_PROMPT_LENGTH = 100_000


def default_prompt_path() -> Path | None:
    """Return the default resource path when the package uses the local filesystem."""
    resource = files(_DEFAULT_PROMPT_PACKAGE).joinpath(_DEFAULT_PROMPT_NAME)
    return resource if isinstance(resource, Path) else None


def validate_output_path(output_path: Path, *, input_path: Path, prompt_path: Path | None = None) -> None:
    """Reject an output path that aliases either immutable source file."""
    resolved_prompt_path = prompt_path if prompt_path is not None else default_prompt_path()
    sources = [("input", input_path)]
    if resolved_prompt_path is not None:
        sources.append(("prompt", resolved_prompt_path))
    for source_name, source_path in sources:
        same_resolved_path = output_path.resolve() == source_path.resolve()
        try:
            same_file = output_path.samefile(source_path)
        except FileNotFoundError:
            same_file = False
        if same_resolved_path or same_file:
            raise ValueError(f"output path must not alias the {source_name} file: {output_path}")


def _validate_prompt_bytes(prompt_bytes: bytes, source: str) -> str:
    """Decode and validate exact prompt bytes without normalization."""
    if not prompt_bytes:
        raise ValueError(f"prompt must not be empty: {source}")
    try:
        prompt_text = prompt_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"prompt must contain valid UTF-8: {source}") from error
    if not prompt_text.strip():
        raise ValueError(f"prompt must not be blank: {source}")
    if len(prompt_text) > _MAX_PROMPT_LENGTH:
        raise ValueError(f"prompt must contain at most {_MAX_PROMPT_LENGTH} Unicode code points: {source}")
    return prompt_text


def load_prompt(path: Path | None = None) -> str:
    """Load nonempty prompt bytes as strict UTF-8 without normalization."""
    if path is not None:
        return _validate_prompt_bytes(path.read_bytes(), str(path))
    resource = files(_DEFAULT_PROMPT_PACKAGE).joinpath(_DEFAULT_PROMPT_NAME)
    return _validate_prompt_bytes(resource.read_bytes(), f"packaged resource {_DEFAULT_PROMPT_NAME}")


def load_cases(path: Path) -> list[DecompositionCase]:
    """Parse a complete JSONL input and reject blank or duplicate cases."""
    cases: list[DecompositionCase] = []
    case_ids: set[str] = set()
    with path.open(encoding="utf-8", newline="") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                case = DecompositionCase.model_validate_json(line)
            except ValidationError as error:
                details = []
                for item in error.errors(include_input=False, include_url=False):
                    location = ".".join(str(part) for part in item["loc"]) or "row"
                    details.append(f"{location}: {item['msg']}")
                raise ValueError(f"invalid case at {path}:{line_number}: {'; '.join(details)}") from None
            if case.case_id in case_ids:
                raise ValueError(f"duplicate case_id at {path}:{line_number}: {case.case_id}")
            case_ids.add(case.case_id)
            cases.append(case)
    if not cases:
        raise ValueError(f"case file must contain at least one case: {path}")
    return cases


def render_case(case: DecompositionCase) -> str:
    """Render the deterministic guidance-free case envelope."""
    sections: list[str] = []
    if case.user_prompt is not None:
        sections.append(f"## Query\n\n```plaintext\n{case.user_prompt}\n```")
    sections.append(f"## Response\n\n```plaintext\n{case.assistant_response}\n```")
    return "\n\n".join(sections)


def build_preview(
    case: DecompositionCase,
    *,
    instructions: str,
    model_id: str,
    model_revision: str | None,
    model_family: str,
    generation: GenerationSettings,
) -> dict[str, Any]:
    """Build the complete no-network preview of one model request contract."""
    return {
        "instructions": instructions,
        "case_envelope": render_case(case),
        "model": {
            "model_id": model_id,
            "model_revision": model_revision,
            "family": model_family,
            "generation": generation.model_dump(mode="json", exclude_none=True),
        },
        "output_schema": DecompositionPrediction.model_json_schema(),
    }


def preview_json(preview: dict[str, Any]) -> str:
    """Serialize a preview as deterministic readable JSON."""
    return json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
