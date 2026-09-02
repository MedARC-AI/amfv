"""CLI tests that require the optional PydanticAI runtime."""

from pathlib import Path

import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from amfv_datasets.decomposition_eval.cli import main  # noqa: E402


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
