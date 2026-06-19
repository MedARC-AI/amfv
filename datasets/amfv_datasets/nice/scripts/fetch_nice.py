from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "datasets"))

from amfv_datasets.nice.fetcher import NiceGuideline

# guidelines covering the high-stake cases mentioned in notion such as dosage, pregnancy, renal dosing, etc.
RECOMMENDED_GUIDELINES: dict[str, str] = {
    "ng28": "Type 2 diabetes in adults: management",
    "ng17": "Type 1 diabetes in adults: diagnosis and management",
    "ng45": "Chronic kidney disease in adults: assessment and management",
    "ng106": "Hypertension in adults: diagnosis and management",
    "ng89": "Sepsis: recognition, diagnosis and early management",
    "ng51": "Sepsis (update)",
    "cg181": "Cardiovascular disease: risk assessment and reduction",
    "ng185": "COVID-19 rapid guideline: managing COVID-19",
    "ng58": "Multimorbidity: clinical assessment and management",
    "ng5": "Medicines optimisation: the safe and effective use of medicines",
}


def list_chapters(guideline: NiceGuideline) -> None:
    overview_html = guideline._get(guideline.overview_url)
    title, chapters = guideline._parse_overview(overview_html)
    print(title)
    for url, slug, _ in chapters:
        marker = " [skip]" if guideline._should_skip(slug) else ""
        print(f"  {slug:<50} {url}{marker}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guideline", "-g", help="Guideline code, e.g. ng28.")
    parser.add_argument("--out-dir", "-o", type=Path, help="Output directory. Defaults to data/nice/<code>.")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between requests.")
    parser.add_argument("--max-retries", type=int, default=3, help="Retries per chapter on transient errors.")
    parser.add_argument("--list-only", action="store_true", help="List chapter URLs without fetching content.")
    parser.add_argument("--show-recommended", action="store_true", help="List recommended guideline codes.")
    args = parser.parse_args()

    if args.show_recommended:
        for code, title in RECOMMENDED_GUIDELINES.items():
            print(f"{code:<8} {title}")
        return

    if not args.guideline:
        parser.error("--guideline is required")

    code = args.guideline.lower().strip()
    out_dir = args.out_dir or Path("data") / "nice" / code
    guideline = NiceGuideline(code, delay=args.delay)

    if args.list_only:
        list_chapters(guideline)
        return

    guideline.fetch_all(verbose=True, max_retries=args.max_retries)
    if guideline.failed_chapters:
        guideline.retry_failed(verbose=True, max_retries=args.max_retries)
    guideline.save(out_dir, verbose=True)

    if guideline.failed_chapters:
        for slug, error in guideline.failed_chapters:
            print(f"failed: {slug}: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()