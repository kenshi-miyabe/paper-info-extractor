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
    {"key": "title", "type": "string", "stage": 1},
    {"key": "year", "type": "number", "stage": 1},
    {"key": "authors", "type": "list", "stage": 1},
    {"key": "journal", "type": "string", "stage": 1},
    {"key": "doi", "type": "string", "stage": 1},
    {"key": "keywords", "type": "list", "stage": 1},
    {"key": "msc", "type": "list", "stage": 1},
    {"key": "arxiv_category", "type": "string", "stage": 1},
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
            "prompt": {
                "stage1_instructions": "If a field is missing, use an empty string or empty array.",
                "stage2_instructions": "If a field is missing, use an empty string or empty array.",
            },
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
                "stage": int(item.get("stage", 1)),
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


def build_prompt(text: str, fields: list[dict[str, Any]], extra: str, context: dict[str, Any] | None) -> str:
    keys = ", ".join(f["key"] for f in fields)
    spec_lines = []
    for field in fields:
        desc = field.get("description", "").strip()
        desc_part = f" ({desc})" if desc else ""
        spec_lines.append(
            f"- {field['key']}: {_field_type_label(field['type'])}{desc_part}"
        )
    spec_lines = "\n".join(spec_lines)
    extra = (extra or "").strip()
    extra_line = f"\n{extra}\n" if extra else "\n"
    context_block = ""
    if context:
        context_block = (
            "\nKnown metadata (from stage 1):\n"
            + json.dumps(context, ensure_ascii=False, indent=2)
            + "\n"
        )
    return (
        "You are a metadata extractor. "
        "From the following first-page text of an academic paper, "
        "extract the fields listed below.\n\n"
        f"Fields:\n{spec_lines}\n"
        f"Return ONLY valid JSON with keys: {keys}.{extra_line}"
        f"TEXT:\n{text}\n"
        f"{context_block}"
    )


def call_ollama_extract(
    text: str,
    model: str,
    fields: list[dict[str, Any]],
    extra_instructions: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = build_prompt(text, fields, extra_instructions, context)

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


def _text_contains_arxiv(text: str, category: str) -> bool:
    if not category:
        return False
    pattern = re.compile(r"\b[a-z][a-z\-]*\.[A-Z]{2}\b", re.IGNORECASE)
    matches = pattern.findall(text)
    return category in matches


def _text_contains_msc(text: str, code: str) -> bool:
    if not code:
        return False
    pattern = re.compile(r"\b\d{2}[A-Z]\d{2}\b")
    return bool(pattern.search(text) and code in pattern.findall(text))


def _extract_msc_code(value: str) -> str:
    match = re.search(r"\b\d{2}[A-Z]\d{2}\b", value)
    return match.group(0) if match else ""


def _split_msc_labels(labels: list[str]) -> dict[str, str]:
    mapping = {}
    for label in labels:
        code = _extract_msc_code(label)
        if code:
            mapping[code] = label
    return mapping


def postprocess_predictions(
    meta: dict[str, Any], text: str, fields: list[dict[str, Any]]
) -> dict[str, Any]:
    field_keys = {f["key"] for f in fields}

    if "arxiv_category" in meta and "arxiv_category_predict" in field_keys:
        arxiv_val = str(meta.get("arxiv_category") or "").strip()
        if arxiv_val and not _text_contains_arxiv(text, arxiv_val):
            meta["arxiv_category_predict"] = arxiv_val
            meta["arxiv_category"] = ""
            if "arxiv_category_label" in meta and "arxiv_category_predict_label" in field_keys:
                label = str(meta.get("arxiv_category_label") or "").strip()
                if label:
                    meta["arxiv_category_predict_label"] = label
                meta["arxiv_category_label"] = ""

    if "msc" in meta and "msc_predict" in field_keys:
        msc_vals = meta.get("msc") or []
        if isinstance(msc_vals, str):
            msc_vals = [msc_vals]
        msc_vals = [str(v).strip() for v in msc_vals if str(v).strip()]
        if msc_vals:
            explicit = [v for v in msc_vals if _text_contains_msc(text, v)]
            inferred = [v for v in msc_vals if v not in explicit]
            if inferred:
                existing = meta.get("msc_predict") or []
                if isinstance(existing, str):
                    existing = [existing]
                meta["msc_predict"] = list(dict.fromkeys(existing + inferred))
            meta["msc"] = explicit
            if "msc_label" in meta and "msc_predict_label" in field_keys:
                labels = meta.get("msc_label") or []
                if isinstance(labels, str):
                    labels = [labels]
                labels = [str(v).strip() for v in labels if str(v).strip()]
                label_map = _split_msc_labels(labels)
                explicit_labels = [label_map[c] for c in explicit if c in label_map]
                inferred_labels = [label_map[c] for c in inferred if c in label_map]
                if inferred_labels:
                    existing_labels = meta.get("msc_predict_label") or []
                    if isinstance(existing_labels, str):
                        existing_labels = [existing_labels]
                    existing_labels = [str(v).strip() for v in existing_labels if str(v).strip()]
                    meta["msc_predict_label"] = list(
                        dict.fromkeys(existing_labels + inferred_labels)
                    )
                meta["msc_label"] = explicit_labels

    return meta


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
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"PDFが見つかりません: {pdf_path}", file=sys.stderr)
        return 1

    config = load_config(Path(args.config))
    fields = _normalize_fields(config.get("fields") or DEFAULT_FIELDS)
    rename_cfg = config.get("rename") or DEFAULT_RENAME
    model = args.model or config.get("model") or "gemma3:12b"
    prompt_cfg = config.get("prompt") or {}
    extra_stage1 = prompt_cfg.get(
        "stage1_instructions", "If a field is missing, use an empty string or empty array."
    )
    extra_stage2 = prompt_cfg.get(
        "stage2_instructions", "If a field is missing, use an empty string or empty array."
    )
    validation = config.get("validation") or {}

    text = load_first_page_text(pdf_path)
    if validation.get("require_abstract"):
        if "abstract" not in text.lower():
            print("Abstract が見つからないためスキップします。", file=sys.stderr)
            return 1

    fields_stage1 = [f for f in fields if f.get("stage", 1) == 1]
    fields_stage2 = [f for f in fields if f.get("stage", 1) == 2]

    meta_stage1 = call_ollama_extract(text, model, fields_stage1, extra_stage1)

    meta_stage2: dict[str, Any] = {}
    if fields_stage2:
        meta_stage2 = call_ollama_extract(
            text, model, fields_stage2, extra_stage2, context=meta_stage1
        )

    meta = {**meta_stage1, **meta_stage2}
    meta = postprocess_predictions(meta, text, fields)

    new_name = build_new_name(meta, rename_cfg)
    new_path = pdf_path.with_name(new_name)
    new_path = uniquify_path(new_path)
    txt_path = new_path.with_suffix(".txt")

    print("metadata:")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"new_name: {new_name}")

    if args.filename_only:
        return 0

    txt_path.write_text(format_txt(meta, fields), encoding="utf-8")

    if args.info_only:
        return 0

    pdf_path.rename(new_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
