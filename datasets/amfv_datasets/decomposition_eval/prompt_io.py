"""Exact prompt loading, case framing, and no-network preview."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from amfv_datasets.decomposition_eval.models import (
    DecompositionCase,
    DecompositionPrediction,
    GenerationSettings,
    prompt_hash_for,
    validate_identifier,
)

__all__ = ["build_preview", "load_cases", "load_prompt", "render_case"]


def load_prompt(path: Path) -> str:
    """Load nonempty prompt bytes as strict UTF-8 without normalization."""
    prompt_bytes = path.read_bytes()
    if not prompt_bytes:
        raise ValueError(f"prompt file must not be empty: {path}")
    try:
        prompt_text = prompt_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"prompt file must contain valid UTF-8: {path}") from error
    if not prompt_text.strip():
        raise ValueError(f"prompt file must not be blank: {path}")
    return prompt_text


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
    prompt_id: str,
    model_id: str,
    model_revision: str | None,
    model_family: str,
    generation: GenerationSettings,
) -> dict[str, Any]:
    """Build the complete no-network preview of one model request contract."""
    validated_prompt_id = validate_identifier(prompt_id)
    return {
        "instructions": instructions,
        "case_envelope": render_case(case),
        "prompt_id": validated_prompt_id,
        "prompt_hash": prompt_hash_for(instructions.encode("utf-8")),
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
