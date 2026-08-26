"""Isolated Docling worker used to enforce a hard conversion deadline."""

from __future__ import annotations

import argparse
import contextlib
import sys

from amfv_datasets.scraping.pdf import _WORKER_RESULT_MARKER, _convert_with_docling


def main() -> int:
    """Read one PDF from stdin and emit a framed UTF-8 Markdown result."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--name", required=True)
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--full-page-ocr", action="store_true")
    parser.add_argument("--running-header")
    args = parser.parse_args()
    data = sys.stdin.buffer.read()
    try:
        # Third-party progress output must never be confused with the framed
        # Markdown payload on stdout.
        with contextlib.redirect_stdout(sys.stderr):
            markdown = _convert_with_docling(
                data,
                ocr=args.ocr,
                full_page_ocr=args.full_page_ocr,
                running_header=args.running_header,
                name=args.name,
            )
    except Exception as exc:  # noqa: BLE001 - normalize backend-specific errors for the parent
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(_WORKER_RESULT_MARKER)
    sys.stdout.buffer.write(markdown.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
