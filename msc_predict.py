#!/usr/bin/env python3
"""Print MSC top-level codes and labels from msc.csv."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--msc-csv", default="msc.csv", help="MSC対応表のCSV")
    args = parser.parse_args()

    msc_path = Path(args.msc_csv)
    if not msc_path.exists():
        print(f"msc.csvが見つかりません: {msc_path}", file=sys.stderr)
        return 1

    top_levels: dict[str, str] = {}
    with msc_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("code") or "").strip()
            label = (row.get("label") or "").strip()
            if not code or not label:
                continue
            if re.fullmatch(r"\d{2}-XX", code):
                top_levels[code[:2]] = label
            elif re.fullmatch(r"\d{2}", code):
                top_levels[code] = label

    if not top_levels:
        print("msc.csv の内容が不正です。", file=sys.stderr)
        return 1

    for code in sorted(top_levels):
        print(f"{code}: {top_levels[code]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
