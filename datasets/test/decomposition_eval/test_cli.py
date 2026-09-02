"""CLI tests that require the optional PydanticAI runtime."""

import os
from pathlib import Path

import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from amfv_datasets.decomposition_eval.cli import main  # noqa: E402


def _source_alias(kind: str, *, cases: Path, prompt: Path, tmp_path: Path) -> Path:
    if kind == "exact-input":
        return cases
    if kind == "normalized-input":
        (tmp_path / "nested").mkdir()
        return tmp_path / "nested" / ".." / cases.name
    if kind == "symlink-prompt":
        alias = tmp_path / "prompt-symlink.txt"
        alias.symlink_to(prompt)
        return alias
    if kind == "hardlink-prompt":
        alias = tmp_path / "prompt-hardlink.txt"
        os.link(prompt, alias)
        return alias
    raise AssertionError(f"unknown test alias: {kind}")


def test_preview_cli_needs_no_provider_or_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Write a preview without resolving credentials or constructing a runtime."""
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Exact prompt.", encoding="utf-8")
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        '{"schema_version":1,"case_id":"case-a","user_prompt":"u","assistant_response":"a"}\n',
        encoding="utf-8",
    )
    output = tmp_path / "preview.json"
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-appear")

    main(
        [
            "preview",
            "--input",
            str(cases),
            "--prompt-file",
            str(prompt),
            "--prompt-id",
            "prompt-a",
            "--model-id",
            "model-a",
            "--model-family",
            "generic",
            "--output",
            str(output),
        ]
    )

    assert "Exact prompt." in output.read_text()
    assert "must-not-appear" not in output.read_text()


@pytest.mark.parametrize(
    "alias_kind",
    ["exact-input", "normalized-input", "symlink-prompt", "hardlink-prompt"],
)
def test_preview_rejects_source_alias_without_modifying_source(tmp_path: Path, alias_kind: str) -> None:
    """Reject lexical and inode aliases before preview publication."""
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Exact prompt.\n", encoding="utf-8")
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        '{"schema_version":1,"case_id":"case-a","user_prompt":"u","assistant_response":"a"}\n',
        encoding="utf-8",
    )
    original_cases = cases.read_bytes()
    original_prompt = prompt.read_bytes()
    output = _source_alias(alias_kind, cases=cases, prompt=prompt, tmp_path=tmp_path)

    with pytest.raises(ValueError, match=r"output path must not alias the (input|prompt) file"):
        main(
            [
                "preview",
                "--input",
                str(cases),
                "--prompt-file",
                str(prompt),
                "--prompt-id",
                "prompt-a",
                "--model-id",
                "model-a",
                "--model-family",
                "generic",
                "--output",
                str(output),
            ]
        )

    assert cases.read_bytes() == original_cases
    assert prompt.read_bytes() == original_prompt
