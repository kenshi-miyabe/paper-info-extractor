#!/usr/bin/env python3
"""Extract metadata from first page text via Ollama and rename the PDF."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CONFIG_PATH = Path("./config.yml")

DEFAULT_FIELDS = [
    {"key": "title", "type": "string"},
    {"key": "year", "type": "number"},
    {"key": "authors", "type": "list"},
    {"key": "journal", "type": "string"},
    {"key": "doi", "type": "string"},
    {"key": "keywords", "type": "list"},
    {"key": "msc", "type": "list"},
    {"key": "arxiv_category", "type": "string"},
    {"key": "summary_ja", "type": "string"},
]
DEFAULT_RENAME = {"author_key": "authors", "year_key": "year", "title_key": "title"}


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


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "model": "gemma3:12b",
            "validation": {"require_abstract": False},
            "fields": DEFAULT_FIELDS,
            "rename": DEFAULT_RENAME,
            "prompt": {"instructions": "If a field is missing, use an empty string or empty array."},
        }

    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("PyYAML が見つかりません。例: uv add pyyaml") from exc

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit("config.yml の形式が不正です。")
    return data


def _normalize_fields(fields: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for item in fields:
        if not isinstance(item, dict) or "key" not in item:
            continue
        normalized.append(
            {
                "key": str(item.get("key")),
                "type": str(item.get("type", "string")),
                "label": str(item.get("label") or item.get("key")),
                "description": str(item.get("description") or ""),
            }
        )
    return normalized


def _field_type_label(field_type: str) -> str:
    field_type = field_type.lower()
    if field_type in {"list", "array"}:
        return "array of strings"
    if field_type in {"number", "int", "float"}:
        return "number"
    return "string"


def build_prompt(text: str, fields: list[dict[str, Any]], instructions: str) -> str:
    keys = ", ".join(f["key"] for f in fields)
    spec_lines = []
    for field in fields:
        desc = field.get("description", "").strip()
        desc_part = f" ({desc})" if desc else ""
        spec_lines.append(
            f"- {field['key']}: {_field_type_label(field['type'])}{desc_part}"
        )
    spec_lines = "\n".join(spec_lines)
    instructions = (instructions or "").strip()
    extra_line = f"\n{instructions}\n" if instructions else "\n"
    return (
        "You are a metadata extractor. "
        "From the following first-page text of an academic paper, "
        "extract the fields listed below.\n\n"
        f"Fields:\n{spec_lines}\n"
        f"Return ONLY valid JSON with keys: {keys}.{extra_line}"
        f"TEXT:\n{text}\n"
    )


def call_ollama_extract(
    text: str,
    model: str,
    fields: list[dict[str, Any]],
    instructions: str,
) -> dict[str, Any]:
    prompt = build_prompt(text, fields, instructions)

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


def build_new_name(meta: dict[str, Any], rename_cfg: dict[str, Any]) -> str:
    title_key = str(rename_cfg.get("title_key") or "title")
    year_key = str(rename_cfg.get("year_key") or "year")
    author_key = str(rename_cfg.get("author_key") or "authors")

    title = str(meta.get(title_key) or "").strip()
    year = str(meta.get(year_key) or "").strip()
    authors = meta.get(author_key) or []

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


def format_txt(meta: dict[str, Any], fields: list[dict[str, Any]]) -> str:
    def _list(value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(v).strip() for v in value if str(v).strip())
        return str(value).strip()

    lines = []
    for field in fields:
        key = field["key"]
        label = field.get("label") or key
        value = meta.get(key)
        if field["type"].lower() in {"list", "array"}:
            value_str = _list(value or [])
        else:
            value_str = str(value or "").strip()
        lines.append(f"{label}: {value_str}")
    return "\n".join(lines) + "\n"


def uniquify_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for i in range(2, 1000):
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise SystemExit(f"同名ファイルが多すぎます: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "pdf",
        nargs="?",
        default="./papers/1-s2.0-S0890540125001208-main.pdf",
        help="対象PDFのパス",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="設定ファイルのパス",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Ollamaモデル名（設定ファイルの上書き用）",
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
    parser.add_argument(
        "--msc-predict",
        action="store_true",
        help="(未使用) 互換性のため残しています",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"PDFが見つかりません: {pdf_path}", file=sys.stderr)
        return 1

    config = load_config(Path(args.config))
    fields = _normalize_fields(config.get("fields") or DEFAULT_FIELDS)
    rename_cfg = config.get("rename") or DEFAULT_RENAME
    model = args.model or config.get("model") or "gemma3:12b"
    instructions = (config.get("prompt") or {}).get(
        "instructions", "If a field is missing, use an empty string or empty array."
    )
    validation = config.get("validation") or {}

    text = load_first_page_text(pdf_path)
    if validation.get("require_abstract"):
        if "abstract" not in text.lower():
            print("Abstract が見つからないためスキップします。", file=sys.stderr)
            return 1

    meta = call_ollama_extract(text, model, fields, instructions)

    new_name = build_new_name(meta, rename_cfg)
    new_path = pdf_path.with_name(new_name)
    new_path = uniquify_path(new_path)
    txt_path = new_path.with_suffix(".txt")

    print("metadata:")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"new_name: {new_name}")

    if args.filename_only:
        return 0

    if not args.info_only:
        if pdf_path != new_path:
            pdf_path.rename(new_path)

    txt_path.write_text(format_txt(meta, fields), encoding="utf-8")

    if args.msc_predict:
        print("msc_predict は現在無効です。", file=sys.stderr)

    if args.info_only:
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
