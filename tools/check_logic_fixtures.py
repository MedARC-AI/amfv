#!/usr/bin/env python3
"""Run shared JSONL fixtures through the Lean logical evaluator."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "formal" / "fixtures" / "logical-eval.jsonl"


def main() -> None:
    """Evaluate every fixture and compare stable result fields."""
    lines = [line for line in FIXTURES.read_text().splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]
    process = subprocess.run(
        ["lake", "exe", "amfv_logic"],
        cwd=ROOT,
        input="\n".join(lines) + "\n",
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode:
        raise SystemExit(process.stderr or process.stdout)
    outputs = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    if len(outputs) != len(records):
        raise SystemExit(f"expected {len(records)} oracle responses; got {len(outputs)}")
    failures: list[str] = []
    for index, (record, output) in enumerate(zip(records, outputs, strict=True), start=1):
        expected = {
            "valid": record["expected_valid"],
            "violations": record["expected_violations"],
        }
        actual = {
            "valid": output.get("valid"),
            "violations": output.get("violations"),
        }
        if actual != expected:
            failures.append(f"fixture {index}: expected {expected!r}; got {actual!r}")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"{len(records)} logical-evaluation fixtures passed")


if __name__ == "__main__":
    main()
