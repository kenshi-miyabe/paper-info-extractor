#!/usr/bin/env python3
"""Extract metadata from first page text via Ollama and rename the PDF."""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CONFIG_PATH = Path("./config.yml")

DEFAULT_FIELDS = [
    {"key": "title", "type": "string"},
    {"key": "year", "type": "number"},
    {"key": "authors", "type": "list"},
    {"key": "affiliation", "type": "list"},
    {"key": "journal", "type": "string"},
    {"key": "doi", "type": "string"},
    {"key": "keywords", "type": "list"},
    {"key": "msc", "type": "list"},
    {"key": "arxiv_category", "type": "string"},
    {"key": "summary_ja", "type": "string"},
]
DEFAULT_RENAME = {"author_key": "authors", "year_key": "year", "title_key": "title"}


class JsonExtractionError(Exception):
    """Raised when model output cannot be parsed as JSON."""

    def __init__(self, message: str, output: str) -> None:
        super().__init__(message)
        self.output = output


def load_first_page_text(pdf_path: Path) -> str:
    """Return extracted text from the first page of a PDF."""
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

    try:
        # Read into memory to avoid partial reads while cloud sync clients lock files.
        data = pdf_path.read_bytes()
        if not data:
            raise OSError(11, "Resource deadlock avoided")
        reader = PdfReader(io.BytesIO(data))
    except OSError as exc:
        hint = ""
        if exc.errno == 11:
            hint = "\n完全にダウンロードしてから再実行してください。"
        raise SystemExit(
            f"PDFの読み込みに失敗しました: {pdf_path}\n"
            f"詳細: {exc}{hint}"
        ) from exc
    if not reader.pages:
        raise SystemExit("PDFにページがありません。")

    return reader.pages[0].extract_text() or ""


def load_config(path: Path) -> dict[str, Any]:
    """Load YAML config or return built-in defaults when missing."""
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
    """Normalize field definitions to a consistent shape."""
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
    """Map config field types to prompt-friendly labels."""
    field_type = field_type.lower()
    if field_type in {"list", "array"}:
        return "array of strings"
    if field_type in {"number", "int", "float"}:
        return "number"
    return "string"


def build_prompt(text: str, fields: list[dict[str, Any]], instructions: str) -> str:
    """Build the LLM prompt used for metadata extraction."""
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
    """Run Ollama and parse the first JSON object from the output."""
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
    # Extract the first JSON object in case the model adds extra text.
    match = re.search(r"\{[\s\S]*\}", output)
    if not match:
        raise JsonExtractionError("JSONの抽出に失敗しました。", output)

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise JsonExtractionError("JSONの解析に失敗しました。", output) from exc


def sanitize_filename_component(text: str) -> str:
    """Make a string safe to use as part of a filename."""
    # Replace whitespace with underscore
    text = re.sub(r"\s+", "_", text.strip())
    # Remove characters not suitable for filenames
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    # Remove other control chars
    text = re.sub(r"[\x00-\x1f]", "", text)
    # Collapse multiple underscores
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def _should_normalize_case(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 4:
        return False
    upper = sum(1 for c in letters if c.isupper())
    lower = sum(1 for c in letters if c.islower())
    if lower == 0:
        return True
    return upper / max(1, upper + lower) >= 0.85


def _title_case_word(word: str) -> str:
    if not any(c.isalpha() for c in word):
        return word
    if any(c.isdigit() for c in word):
        return word
    small_words = {
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "by",
        "for",
        "from",
        "in",
        "into",
        "nor",
        "of",
        "on",
        "or",
        "per",
        "the",
        "to",
        "up",
        "via",
        "vs",
        "with",
        "yet",
    }
    if word.lower() in small_words:
        return word.lower()
    if word.isupper() and len(word) <= 3:
        return word
    lowered = word.lower()
    return lowered[:1].upper() + lowered[1:]


def normalize_caps_text(text: str) -> str:
    """Normalize ALL-CAPS text to a more readable title case."""
    if not text or not _should_normalize_case(text):
        return text

    tokens = re.split(r"(\s+)", text.strip())
    normalized = []
    for token in tokens:
        if token.isspace():
            normalized.append(token)
            continue
        parts = re.split(r"([-/])", token)
        parts = [_title_case_word(p) if p not in {"-", "/"} else p for p in parts]
        normalized.append("".join(parts))
    return "".join(normalized)


def normalize_meta_case(meta: dict[str, Any], rename_cfg: dict[str, Any]) -> None:
    """Normalize case for title/authors in place when they are all-caps."""
    title_key = str(rename_cfg.get("title_key") or "title")
    author_key = str(rename_cfg.get("author_key") or "authors")

    title = meta.get(title_key)
    if isinstance(title, str):
        meta[title_key] = normalize_caps_text(title)

    authors = meta.get(author_key)
    if isinstance(authors, list):
        meta[author_key] = [
            normalize_caps_text(str(a).strip()) for a in authors if str(a).strip()
        ]
    elif isinstance(authors, str):
        meta[author_key] = normalize_caps_text(authors)


def build_new_name(meta: dict[str, Any], rename_cfg: dict[str, Any]) -> str:
    """Create a new filename based on metadata and rename config."""
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
        # Prefer "Last, First" but fall back to the last token in the name.
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
    """Format extracted metadata as a labeled text block."""
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
    """Append a numeric suffix if needed to avoid overwriting existing files."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for i in range(2, 1000):
        # Keep trying with _2, _3, ... until a free name is found.
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise SystemExit(f"同名ファイルが多すぎます: {path}")


def append_ollama_log(log_path: Path, pdf_path: Path, output: str) -> None:
    """Append Ollama output to a shared log file when JSON parsing fails."""
    header = f"\n---\nPDF: {pdf_path}\n---\n"
    existing = ""
    if log_path.exists():
        existing = log_path.read_text(encoding="utf-8")
    log_path.write_text(existing + header + output + "\n", encoding="utf-8")


def main() -> int:
    """CLI entry point for extracting metadata and renaming a PDF."""
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
    pdf_paths: list[Path] = []
    if pdf_path.is_dir():
        # Only process PDFs directly under the given directory (no recursion).
        pdf_paths = sorted(p for p in pdf_path.glob("*.pdf") if p.is_file())
        if not pdf_paths:
            print(f"PDFが見つかりません: {pdf_path}", file=sys.stderr)
            return 1
    elif pdf_path.exists():
        pdf_paths = [pdf_path]
    else:
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

    any_failed = False
    log_path = Path(__file__).resolve().parent / "log.txt"
    for pdf_path in pdf_paths:
        text = load_first_page_text(pdf_path)
        if validation.get("require_abstract"):
            if "abstract" not in text.lower():
                # Optional guard to skip files without an Abstract section.
                print(
                    f"Abstract が見つからないためスキップします: {pdf_path}",
                    file=sys.stderr,
                )
                any_failed = True
                continue

        try:
            meta = call_ollama_extract(text, model, fields, instructions)
        except JsonExtractionError as exc:
            append_ollama_log(log_path, pdf_path, exc.output)
            print(
                f"JSONの解析に失敗しました。ログを確認してください: {log_path}",
                file=sys.stderr,
            )
            any_failed = True
            continue

        normalize_meta_case(meta, rename_cfg)

        new_name = build_new_name(meta, rename_cfg)
        new_path = pdf_path.with_name(new_name)
        new_path = uniquify_path(new_path)
        txt_path = new_path.with_suffix(".txt")

        print("metadata:")
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"new_name: {new_name}")

        if args.filename_only:
            continue

        if not args.info_only:
            if pdf_path != new_path:
                pdf_path.rename(new_path)

        txt_path.write_text(format_txt(meta, fields), encoding="utf-8")

    if args.msc_predict:
        print("msc_predict は現在無効です。", file=sys.stderr)

    if args.info_only:
        return 1 if any_failed else 0

    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
