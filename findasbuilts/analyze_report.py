"""Scratch analysis of a findasbuilts report CSV (not part of the tool).

Usage: python analyze_report.py <report.csv>

Prints verdict counts, the STAMP rows (path + designation + page + evidence
snippet), and checks the known false-positive docs from the 2026-09-04 partial
run are now NO_STAMP.
"""

import csv
import sys
from collections import Counter

KNOWN_FALSE_POSITIVES = [
    # docs that were STAMP in the partial run due to water-system/project/file
    # numbers, bidder's/operator's licenses, or bank accounts near a "P.E."
    # (identified 2026-09-04 from the 83-STAMP partial report)
]

def main(path: str) -> None:
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    print(f"rows: {len(rows)}")
    verdicts = Counter(r["verdict"] for r in rows)
    print(f"verdicts: {dict(verdicts)}")

    stamps = [r for r in rows if r["verdict"] == "STAMP"]
    print(f"\nSTAMP ({len(stamps)}):")
    for r in stamps:
        ev = (r.get("evidence") or "").replace("\n", " | ")
        print(f"  {r['file_path']}")
        print(f"    kw={r.get('stamp_keywords')} page={r.get('stamp_pages')} src={r.get('text_source')}")
        print(f"    evidence: {ev[:200]}")

    errors = [r for r in rows if r["verdict"] == "ERROR"]
    if errors:
        print(f"\nERROR ({len(errors)}):")
        for r in errors[:20]:
            print(f"  {r['file_path']}: {r.get('error')}")

if __name__ == "__main__":
    main(sys.argv[1])
