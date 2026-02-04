#!/usr/bin/env python3
"""Extract title/year/authors from first page text via Ollama and rename the PDF."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def load_first_page_text(pdf_path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ModuleNotFoundError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ModuleNotFoundError as exc:
            raise SystemExit(
                "pypdf (or PyPDF2) が見つかりません。\n"
                "例: uv add pypdf"
            ) from exc

    reader = PdfReader(pdf_path)
    if not reader.pages:
        raise SystemExit("PDFにページがありません。")

    return reader.pages[0].extract_text() or ""


def call_ollama_extract(text: str, model: str) -> dict[str, Any]:
    prompt = (
        "You are a metadata extractor. "
        "From the following first-page text of an academic paper, "
        "extract title, publication year, authors, journal, doi, keywords, and MSC. "
        "Return ONLY valid JSON with keys: "
        "title (string), year (number), authors (array of strings), "
        "journal (string), doi (string), keywords (array of strings), msc (array of strings). "
        "If a field is missing, use an empty string or empty array.\n\n"
        f"TEXT:\n{text}\n"
    )

    result = subprocess.run(
        ["ollama", "run", model],
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        raise SystemExit(
            "ollama の実行に失敗しました。\n"
            f"stderr: {result.stderr.strip()}"
        )

    output = result.stdout.strip()
    # Extract first JSON object from output, in case the model adds extra text.
    match = re.search(r"\{[\s\S]*\}", output)
    if not match:
        raise SystemExit("JSONの抽出に失敗しました。出力を確認してください。")

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise SystemExit("JSONの解析に失敗しました。出力を確認してください。") from exc


def sanitize_filename_component(text: str) -> str:
    # Replace whitespace with underscore
    text = re.sub(r"\s+", "_", text.strip())
    # Remove characters not suitable for filenames
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    # Remove other control chars
    text = re.sub(r"[\x00-\x1f]", "", text)
    # Collapse multiple underscores
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def build_new_name(meta: dict[str, Any]) -> str:
    title = str(meta.get("title") or "").strip()
    year = str(meta.get("year") or "").strip()
    authors = meta.get("authors") or []

    if isinstance(authors, str):
        authors = [authors]

    authors = [str(a).strip() for a in authors if str(a).strip()]
    surnames = []
    for name in authors:
        if "," in name:
            surname = name.split(",", 1)[0].strip()
        else:
            parts = name.split()
            surname = parts[-1] if parts else name
        if surname:
            surnames.append(surname)
    author_part = "_".join(surnames)

    title_part = sanitize_filename_component(title)
    author_part = sanitize_filename_component(author_part)
    year_part = sanitize_filename_component(year)

    return f"{author_part}-{year_part}-{title_part}.pdf"


def format_txt(meta: dict[str, Any]) -> str:
    def _list(value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(v).strip() for v in value if str(v).strip())
        return str(value).strip()

    lines = [
        f"title: {str(meta.get('title') or '').strip()}",
        f"year: {str(meta.get('year') or '').strip()}",
        f"authors: {_list(meta.get('authors') or [])}",
        f"journal: {str(meta.get('journal') or '').strip()}",
        f"doi: {str(meta.get('doi') or '').strip()}",
        f"keywords: {_list(meta.get('keywords') or [])}",
        f"msc: {_list(meta.get('msc') or [])}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "pdf",
        nargs="?",
        default="./papers/1-s2.0-S0890540125001208-main.pdf",
        help="対象PDFのパス",
    )
    parser.add_argument(
        "--model",
        default="gemma3:12b",
        help="Ollamaモデル名",
    )
    parser.add_argument(
        "--filename-only",
        action="store_true",
        help="新しいファイル名だけ表示（書き出しなし）",
    )
    parser.add_argument(
        "--info-only",
        action="store_true",
        help="リネームは行わずにTXTのみ出力",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"PDFが見つかりません: {pdf_path}", file=sys.stderr)
        return 1

    text = load_first_page_text(pdf_path)
    meta = call_ollama_extract(text, args.model)

    new_name = build_new_name(meta)
    new_path = pdf_path.with_name(new_name)
    txt_path = new_path.with_suffix(".txt")

    print("metadata:")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"new_name: {new_name}")

    if args.filename_only:
        return 0

    txt_path.write_text(format_txt(meta), encoding="utf-8")

    if args.info_only:
        return 0

    if new_path.exists() and new_path != pdf_path:
        print(f"既に存在します: {new_path}", file=sys.stderr)
        return 1

    pdf_path.rename(new_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
