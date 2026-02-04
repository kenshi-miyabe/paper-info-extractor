#!/usr/bin/env python3
"""Extract and print text from the first page of a PDF."""

from __future__ import annotations

from pathlib import Path
import sys


PDF_PATH = Path("./papers/1-s2.0-S0890540125001208-main.pdf")


def _load_reader(path: Path):
    try:
        from pypdf import PdfReader  # type: ignore
        return PdfReader(path)
    except ModuleNotFoundError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
            return PdfReader(path)
        except ModuleNotFoundError as exc:
            raise SystemExit(
                "pypdf (or PyPDF2) が見つかりません。\n"
                "例: pip install pypdf"
            ) from exc


def main() -> int:
    if not PDF_PATH.exists():
        print(f"PDFが見つかりません: {PDF_PATH}", file=sys.stderr)
        return 1

    reader = _load_reader(PDF_PATH)
    if not reader.pages:
        print("PDFにページがありません。", file=sys.stderr)
        return 1

    text = reader.pages[0].extract_text() or ""
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
