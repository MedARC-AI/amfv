#!/usr/bin/env python3
"""Check Lean proof hygiene and resolve Python ``lean-spec`` annotations."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMAL_ROOT = ROOT / "formal"
PYTHON_ROOTS = ("baseline", "datasets", "decomposer", "search", "training", "utils", "verifier")
TAG_RE = re.compile(r"^\s*#\s*lean-spec:\s*([A-Za-z_][A-Za-z0-9_.]*)\s*$")
TEST_TAG_RE = re.compile(r"^\s*#\s*lean-spec-test:\s*([A-Za-z_][A-Za-z0-9_.]*)\s*$")
DECL_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+[A-Za-z_][A-Za-z0-9_]*")
FORBIDDEN_RE = re.compile(
    r"^\s*(?:(?:private|protected|noncomputable|unsafe)\s+)*(?:axiom|constant|opaque)\s+"
    r"|\b(?:by\s+)?(?:sorry|admit)\b"
)


def fail(message: str) -> None:
    """Print a checker failure and terminate."""
    raise SystemExit(message)


def check_formal_hygiene() -> None:
    """Reject proof placeholders and assumption declarations."""
    failures: list[str] = []
    for path in sorted(FORMAL_ROOT.rglob("*.lean")):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            code = line.split("--", 1)[0]
            if FORBIDDEN_RE.search(code):
                failures.append(f"{path.relative_to(ROOT)}:{line_number}: forbidden proof escape")
    if failures:
        fail("\n".join(failures))
    manifest = json.loads((ROOT / "lake-manifest.json").read_text())
    if manifest.get("packages") != []:
        fail("formal library must remain dependency-free")


def collect_tags() -> list[str]:
    """Collect annotations and ensure they are attached to Python declarations."""
    tags: list[str] = []
    test_tags: set[str] = set()
    failures: list[str] = []
    for source_root in PYTHON_ROOTS:
        for path in sorted((ROOT / source_root).rglob("*.py")):
            lines = path.read_text().splitlines()
            for index, line in enumerate(lines):
                match = TAG_RE.match(line)
                test_match = TEST_TAG_RE.match(line)
                if test_match:
                    if index + 1 >= len(lines) or not DECL_RE.match(lines[index + 1]):
                        failures.append(
                            f"{path.relative_to(ROOT)}:{index + 1}: "
                            "lean-spec-test must immediately precede a test function"
                        )
                    test_tags.add(test_match.group(1))
                if not match:
                    continue
                if index + 1 >= len(lines) or not DECL_RE.match(lines[index + 1]):
                    failures.append(
                        f"{path.relative_to(ROOT)}:{index + 1}: lean-spec must immediately precede a function or class"
                    )
                tags.append(match.group(1))
    if failures:
        fail("\n".join(failures))
    if not tags:
        fail("no lean-spec annotations found")
    untested_tags = sorted(set(tags) - test_tags)
    if untested_tags:
        fail("lean-spec annotations without paired runtime tests:\n" + "\n".join(untested_tags))
    return tags


def resolve_tags(tags: list[str]) -> None:
    """Ask Lean to resolve every referenced declaration."""
    source = "import AMFV\n\n" + "\n".join(
        f"set_option pp.proofs false in\n#print {tag}\n#print axioms {tag}" for tag in sorted(set(tags))
    )
    source += "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".lean", dir=ROOT, delete=False) as handle:
        handle.write(source)
        check_path = Path(handle.name)
    try:
        result = subprocess.run(
            ["lake", "env", "lean", str(check_path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        check_path.unlink(missing_ok=True)
    if result.returncode:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        fail("one or more lean-spec annotations do not resolve")
    for tag in set(tags):
        if f"theorem {tag} " not in result.stdout:
            fail(f"lean-spec target is not a theorem: {tag}")
        if f"'{tag}' depends on axioms:" in result.stdout:
            fail(f"lean-spec theorem has an axiom dependency: {tag}")


def main() -> None:
    """Run all formal-source checks."""
    check_formal_hygiene()
    resolve_tags(collect_tags())


if __name__ == "__main__":
    main()
