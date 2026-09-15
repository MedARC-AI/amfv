"""Tests for exact prompt loading and no-network preview."""

from pathlib import Path

import pytest

from amfv_datasets.decomposition_eval.models import GenerationSettings
from amfv_datasets.decomposition_eval.prompt_io import build_preview, load_cases, load_prompt, render_case


def test_prompt_bytes_and_case_envelope_are_exact(tmp_path: Path) -> None:
    """Keep exact prompt bytes and deterministic case framing."""
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_bytes("Café\r\nKeep spacing.\n".encode())
    case_path = tmp_path / "cases.jsonl"
    case_path.write_text(
        '{"schema_version":1,"case_id":"case-a","user_prompt":"Why?","assistant_response":"Because."}\n',
        encoding="utf-8",
    )

    instructions = load_prompt(prompt_path)
    case = load_cases(case_path)[0]
    preview = build_preview(
        case,
        instructions=instructions,
        model_id="model-a",
        model_revision="revision-a",
        model_family="gpt-oss",
        generation=GenerationSettings(max_tokens=20, reasoning_effort="medium"),
    )

    assert instructions == "Café\r\nKeep spacing.\n"
    assert preview["instructions"] == instructions
    assert preview["model"]["model_revision"] == "revision-a"
    assert preview["case_envelope"] == render_case(case)
    assert preview["case_envelope"] == (
        "## Query\n\n```plaintext\nWhy?\n```\n\n## Response\n\n```plaintext\nBecause.\n```"
    )


def test_default_prompt_and_duplicate_case_validation(tmp_path: Path) -> None:
    """Load packaged instructions and require unique case identities."""
    assert "Use only vital or semi-important" in load_prompt()
    with pytest.raises(FileNotFoundError):
        load_prompt(tmp_path / "missing.txt")
    blank = tmp_path / "blank.txt"
    blank.write_text("   ", encoding="utf-8")
    with pytest.raises(ValueError, match="must not be blank"):
        load_prompt(blank)
    cases = tmp_path / "cases.jsonl"
    row = '{"schema_version":1,"case_id":"same","user_prompt":"u","assistant_response":"a"}\n'
    cases.write_text(row + row, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate case_id"):
        load_cases(cases)


def test_response_only_case_omits_query_section(tmp_path: Path) -> None:
    """Render documents and reasoning traces without an invented query."""
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        '{"schema_version":1,"case_id":"document-a","assistant_response":"Standalone document."}\n',
        encoding="utf-8",
    )

    case = load_cases(cases)[0]

    assert case.user_prompt is None
    assert render_case(case) == "## Response\n\n```plaintext\nStandalone document.\n```"


def test_invalid_case_errors_do_not_disclose_input_values(tmp_path: Path) -> None:
    """Report validation locations without echoing case content."""
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        '{"schema_version":1,"case_id":"case-a","user_prompt":" ",'
        '"assistant_response":"RESPONSE_SECRET","metadata":"METADATA_SECRET"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as captured:
        load_cases(cases)

    assert "metadata" in str(captured.value)
    assert "RESPONSE_SECRET" not in str(captured.value)
    assert "METADATA_SECRET" not in str(captured.value)
    assert captured.value.__cause__ is None

    cases.write_text(
        '{"schema_version":1,"case_id":"case-a","user_prompt":" ","assistant_response":"RESPONSE_SECRET"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as captured:
        load_cases(cases)
    assert "user_prompt" in str(captured.value)
    assert "RESPONSE_SECRET" not in str(captured.value)
