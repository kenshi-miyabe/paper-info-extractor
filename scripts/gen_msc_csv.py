#!/usr/bin/env python3
"""Generate msc.csv from the MSC 2020 PDF."""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from pypdf import PdfReader

PDF_PATH = Path("/tmp/msc2020.pdf")
OUT_PATH = Path("msc.csv")

TOP_RE = re.compile(r"^(\d{2})\s+([A-Za-z].+)$")
CODE_RE = re.compile(r"^(\d{2}[A-Z]\d{2})\s+(.+)$")


def main() -> int:
    if not PDF_PATH.exists():
        print(f"Missing PDF: {PDF_PATH}", file=sys.stderr)
        return 1

    reader = PdfReader(PDF_PATH)
    top_levels: dict[str, str] = {}
    codes: dict[str, str] = {}

    for page in reader.pages:
        text = page.extract_text() or ""
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            m_top = TOP_RE.match(line)
            if m_top:
                code, label = m_top.group(1), m_top.group(2).strip()
                # Avoid mis-detecting line starts that are actually code entries.
                if not re.match(r"\d{2}[A-Z]\d{2}\b", line):
                    top_levels.setdefault(code, label)
                continue

            m_code = CODE_RE.match(line)
            if m_code:
                code, label = m_code.group(1), m_code.group(2).strip()
                codes.setdefault(code, label)

    if not codes:
        print("No MSC codes extracted. Check PDF parsing.", file=sys.stderr)
        return 1

    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["code", "label"])
        for code in sorted(top_levels):
            writer.writerow([code, top_levels[code]])
        for code in sorted(codes):
            writer.writerow([code, codes[code]])

    print(f"Wrote {OUT_PATH} (top-level: {len(top_levels)}, codes: {len(codes)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
