#!/usr/bin/env python3
"""Extract metadata from first page text via Ollama and rename the PDF."""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path("./config.yml")
LOG_DIR_NAME = "logs"
OLLAMA_API_URL = "http://localhost:11434/api/generate"

DEFAULT_FILENAME_PROMPT = """\
You are a metadata extractor for academic papers.
Extract only the metadata needed to rename a PDF from the first-page text.
Use only information explicitly written on the page.
Return a single JSON object with exactly these keys:
- "title": string
- "authors": array of strings
- "year": number or string
If a value is missing, use an empty string or empty array.
"""

DEFAULT_EXTRA_TEXT_PROMPT = """\
You are writing a companion text file for an academic paper.
Do not return JSON. Return plain text only.
Write concise labeled sections for affiliation, journal, doi, keywords, MSC,
arXiv category, and a short Japanese summary.
For factual fields, use only information explicitly written on the page.
For summaries, summarize based on the page.
"""


class JsonExtractionError(Exception):
    """Raised when model output cannot be parsed as JSON."""

    def __init__(
        self,
        message: str,
        output: str,
        kind: str = "json_parse_error",
    ) -> None:
        super().__init__(message)
        self.output = output
        self.kind = kind


class OllamaRunError(Exception):
    """Raised when Ollama exits with an error."""

    def __init__(self, message: str, output: str, kind: str = "ollama_error") -> None:
        super().__init__(message)
        self.output = output
        self.kind = kind


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class RunLogger:
    """Write a timestamped log file for one script execution."""

    def __init__(self, log_path: Path) -> None:
        self.path = log_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def write(self, message: str = "") -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}" if message else ""
        with self.path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")

    def section(self, title: str) -> None:
        self.write()
        self.write(f"=== {title} ===")


def create_log_path() -> Path:
    """Return a unique timestamped log file path."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_dir = Path(__file__).resolve().parent / LOG_DIR_NAME
    path = log_dir / f"log_{timestamp}.txt"
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = log_dir / f"log_{timestamp}_{index}.txt"
        if not candidate.exists():
            return candidate
    raise SystemExit("ログファイル名を決定できません。")


def format_duration(start: float) -> str:
    """Format elapsed seconds since start."""
    return f"{time.monotonic() - start:.2f}s"


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
            "models": ["gemma4:e4b"],
            "check_paper": False,
            "prompts": {
                "filename_json": DEFAULT_FILENAME_PROMPT,
                "extra_text": DEFAULT_EXTRA_TEXT_PROMPT,
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


def _normalize_models(config: dict[str, Any], override_model: str | None) -> list[str]:
    """Return model names in the order they should be attempted."""
    if override_model:
        return [override_model]

    configured = config.get("models")
    if isinstance(configured, list):
        models = [
            str(model).strip()
            for model in configured
            if model is not None and str(model).strip()
        ]
        if models:
            return models

    return ["gemma4:e4b"]


def get_prompt(config: dict[str, Any], key: str, default: str) -> str:
    """Return a configured prompt template."""
    prompts = config.get("prompts") or {}
    if isinstance(prompts, dict):
        prompt = prompts.get(key)
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
    return default.strip()


def render_prompt(template: str, text: str) -> str:
    """Insert first-page text into a configured prompt."""
    if "{text}" in template:
        return template.replace("{text}", text)
    return f"{template.rstrip()}\n\nTEXT:\n{text}\n"


def clean_ollama_output(output: str) -> str:
    """Remove terminal control sequences that break JSON parsing/log readability."""
    output = output.replace("\r\n", "\n").replace("\r", "\n")
    output = ANSI_ESCAPE_RE.sub("", output)
    output = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", output)
    return output.strip()


def extract_json_candidate(output: str) -> str:
    """Extract a likely JSON object from model output."""
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", output, re.IGNORECASE)
    if fenced:
        return fenced.group(1)

    start = output.find("{")
    if start == -1:
        raise JsonExtractionError("JSONの抽出に失敗しました。", output, "json_extract_error")

    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(output[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return output[start : index + 1]

    raise JsonExtractionError("JSONの抽出に失敗しました。", output, "json_extract_error")


def repair_json_text(text: str) -> str:
    """Repair common LLM JSON mistakes before parsing."""
    repaired: list[str] = []
    in_string = False
    escaped = False

    for char in text:
        if in_string:
            if escaped:
                repaired.append(char)
                escaped = False
                continue
            if char == "\\":
                repaired.append(char)
                escaped = True
                continue
            if char == '"':
                repaired.append(char)
                in_string = False
                continue
            if char == "\n":
                repaired.append("\\n")
                continue
            if char == "\t":
                repaired.append("\\t")
                continue
            repaired.append(char)
            continue

        if char == '"':
            in_string = True
        repaired.append(char)

    # Remove trailing commas before object/array closers.
    return re.sub(r",(\s*[}\]])", r"\1", "".join(repaired))


def call_ollama_generate(prompt: str, model: str, *, json_mode: bool) -> str:
    """Call Ollama's generate API and return the model response text."""
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }
    if json_mode:
        payload["format"] = "json"

    request = urllib.request.Request(
        OLLAMA_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise OllamaRunError(
            f"Ollama API がHTTPエラーを返しました: {exc.code}",
            clean_ollama_output(body),
            "http_error",
        ) from exc
    except urllib.error.URLError as exc:
        raise OllamaRunError(
            "Ollama API に接続できませんでした。",
            str(exc.reason),
            "connection_error",
        ) from exc
    except TimeoutError as exc:
        raise OllamaRunError("Ollama API がタイムアウトしました。", str(exc), "timeout") from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OllamaRunError(
            "Ollama API の応答JSONを解析できません。",
            body,
            "api_response_json_parse_error",
        ) from exc

    if "error" in data:
        raise OllamaRunError(
            "Ollama API がエラーを返しました。",
            str(data["error"]),
            "api_error",
        )

    return clean_ollama_output(str(data.get("response") or ""))


def call_ollama_filename_metadata(
    text: str,
    model: str,
    prompt_template: str,
) -> dict[str, Any]:
    """Ask Ollama for filename metadata using JSON mode."""
    prompt = render_prompt(prompt_template, text)
    output = call_ollama_generate(prompt, model, json_mode=True)
    candidate = extract_json_candidate(output)
    repaired = repair_json_text(candidate)

    try:
        meta = json.loads(repaired)
    except json.JSONDecodeError as exc:
        raise JsonExtractionError("JSONの解析に失敗しました。", output, "json_parse_error") from exc
    if not isinstance(meta, dict):
        raise JsonExtractionError("JSONがオブジェクトではありません。", output, "json_shape_error")
    return meta


def call_ollama_extra_text(
    text: str,
    model: str,
    prompt_template: str,
) -> str:
    """Ask Ollama for non-filename information as plain text."""
    prompt = render_prompt(prompt_template, text)
    return call_ollama_generate(prompt, model, json_mode=False)


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


def truncate_filename_component(text: str, max_len: int) -> str:
    """Truncate a filename component to a maximum length."""
    if max_len <= 0:
        return text
    if len(text) <= max_len:
        return text
    trimmed = text[:max_len]
    return trimmed.rstrip("_-")


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


def normalize_meta_case(meta: dict[str, Any]) -> None:
    """Normalize case for title/authors in place when they are all-caps."""
    title = meta.get("title")
    if isinstance(title, str):
        meta["title"] = normalize_caps_text(title)

    authors = meta.get("authors")
    if isinstance(authors, list):
        meta["authors"] = [
            normalize_caps_text(str(a).strip()) for a in authors if str(a).strip()
        ]
    elif isinstance(authors, str):
        meta["authors"] = normalize_caps_text(authors)


def build_new_name(meta: dict[str, Any]) -> str:
    """Create a new filename based on metadata and rename config."""
    author_max_len = 60
    title_max_len = 120

    title = str(meta.get("title") or "").strip()
    year = str(meta.get("year") or "").strip()
    authors = meta.get("authors") or []

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
    author_part = truncate_filename_component(author_part, author_max_len)
    title_part = truncate_filename_component(title_part, title_max_len)

    return f"{author_part}-{year_part}-{title_part}.pdf"


def build_txt_content(meta: dict[str, Any], extra_text: str) -> str:
    """Build the text file content from filename metadata and extra text."""
    authors = meta.get("authors") or []
    if isinstance(authors, list):
        author_text = ", ".join(
            str(author).strip() for author in authors if str(author).strip()
        )
    else:
        author_text = str(authors).strip()
    lines = [
        f"title: {str(meta.get('title') or '').strip()}",
        f"authors: {author_text}",
        f"year: {str(meta.get('year') or '').strip()}",
    ]
    content = "\n".join(lines)
    extra_text = extra_text.strip()
    if extra_text:
        content = f"{content}\n\n{extra_text}"
    return content + "\n"


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


def log_model_failure(
    logger: RunLogger,
    stage: str,
    model: str,
    exc: OllamaRunError | JsonExtractionError,
    start: float,
) -> None:
    """Write structured failure details for a model attempt."""
    logger.write(
        f"{stage}: failed model={model} kind={exc.kind} "
        f"elapsed={format_duration(start)}"
    )
    logger.write(f"{stage}: error={exc}")
    if exc.output:
        logger.write(f"{stage}: output_start")
        logger.write(exc.output)
        logger.write(f"{stage}: output_end")


def _prefer_model(models: list[str], preferred: str) -> list[str]:
    """Return models with preferred first while preserving configured order."""
    ordered = [preferred]
    ordered.extend(model for model in models if model != preferred)
    return ordered


def extract_filename_metadata_with_model_fallback(
    text: str,
    models: list[str],
    prompt_template: str,
    logger: RunLogger,
) -> tuple[dict[str, Any], str]:
    """Try configured models for filename metadata, logging each failure."""
    last_error = ""
    for index, model in enumerate(models, start=1):
        start = time.monotonic()
        logger.write(f"filename_json: start model={model}")
        try:
            meta = call_ollama_filename_metadata(text, model, prompt_template)
            logger.write(
                f"filename_json: success model={model} elapsed={format_duration(start)}"
            )
            logger.write(f"filename_json: metadata={json.dumps(meta, ensure_ascii=False)}")
            return meta, model
        except (OllamaRunError, JsonExtractionError) as exc:
            last_error = str(exc)
            log_model_failure(logger, "filename_json", model, exc, start)
            if index < len(models):
                print(
                    f"{model} で失敗したため次のモデルを試します。ログ: {logger.path}",
                    file=sys.stderr,
                )
                logger.write(f"filename_json: fallback next_model={models[index]}")

    raise JsonExtractionError(
        "すべてのモデルで抽出に失敗しました。",
        f"最後のエラー: {last_error}",
        "all_models_failed",
    )


def extract_extra_text_with_model_fallback(
    text: str,
    models: list[str],
    preferred_model: str,
    prompt_template: str,
    logger: RunLogger,
) -> str:
    """Try configured models for companion text, logging each failure."""
    ordered_models = _prefer_model(models, preferred_model)
    for index, model in enumerate(ordered_models, start=1):
        start = time.monotonic()
        logger.write(f"extra_text: start model={model}")
        try:
            output = call_ollama_extra_text(text, model, prompt_template)
            logger.write(
                f"extra_text: success model={model} chars={len(output)} "
                f"elapsed={format_duration(start)}"
            )
            return output
        except OllamaRunError as exc:
            log_model_failure(logger, "extra_text", model, exc, start)
            if index < len(ordered_models):
                print(
                    f"{model} で追加情報の生成に失敗したため次のモデルを試します。ログ: {logger.path}",
                    file=sys.stderr,
                )
                logger.write(f"extra_text: fallback next_model={ordered_models[index]}")
    logger.write("extra_text: failed all models; writing TXT without extra text")
    return ""


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

    run_start = time.monotonic()
    logger = RunLogger(create_log_path())
    logger.section("run")
    logger.write(f"args={vars(args)}")

    pdf_path = Path(args.pdf)
    pdf_paths: list[Path] = []
    if pdf_path.is_dir():
        # Only process PDFs directly under the given directory (no recursion).
        pdf_paths = sorted(p for p in pdf_path.glob("*.pdf") if p.is_file())
        if not pdf_paths:
            print(f"PDFが見つかりません: {pdf_path}", file=sys.stderr)
            logger.write(f"failed kind=no_pdf_found path={pdf_path}")
            logger.write(f"run: finished status=failed elapsed={format_duration(run_start)}")
            return 1
    elif pdf_path.exists():
        pdf_paths = [pdf_path]
    else:
        print(f"PDFが見つかりません: {pdf_path}", file=sys.stderr)
        logger.write(f"failed kind=pdf_not_found path={pdf_path}")
        logger.write(f"run: finished status=failed elapsed={format_duration(run_start)}")
        return 1

    try:
        config = load_config(Path(args.config))
    except SystemExit as exc:
        logger.write(f"config: failed kind=config_error path={args.config}")
        logger.write(f"error={exc}")
        logger.write(f"run: finished status=failed elapsed={format_duration(run_start)}")
        raise
    models = _normalize_models(config, args.model)
    filename_prompt = get_prompt(config, "filename_json", DEFAULT_FILENAME_PROMPT)
    extra_text_prompt = get_prompt(config, "extra_text", DEFAULT_EXTRA_TEXT_PROMPT)
    check_paper = bool(config.get("check_paper"))
    logger.write(f"config={Path(args.config)}")
    logger.write(f"models={models}")
    logger.write(f"check_paper={check_paper}")
    logger.write(f"pdf_count={len(pdf_paths)}")

    any_failed = False
    for pdf_path in pdf_paths:
        pdf_start = time.monotonic()
        logger.section(f"pdf {pdf_path}")
        try:
            text = load_first_page_text(pdf_path)
            logger.write(f"first_page_text_chars={len(text)}")
        except SystemExit as exc:
            logger.write(
                f"pdf: failed kind=pdf_read_error elapsed={format_duration(pdf_start)}"
            )
            logger.write(f"error={exc}")
            print(exc, file=sys.stderr)
            any_failed = True
            continue

        if check_paper:
            if "abstract" not in text.lower():
                # Optional guard to skip files without an Abstract section.
                print(
                    f"Abstract が見つからないためスキップします: {pdf_path}",
                    file=sys.stderr,
                )
                logger.write(
                    f"pdf: skipped kind=not_paper elapsed={format_duration(pdf_start)}"
                )
                any_failed = True
                continue

        try:
            meta, metadata_model = extract_filename_metadata_with_model_fallback(
                text, models, filename_prompt, logger
            )
        except JsonExtractionError as exc:
            logger.write(
                f"pdf: failed kind={exc.kind} elapsed={format_duration(pdf_start)}"
            )
            logger.write(f"error={exc}")
            print(
                f"抽出に失敗しました。ログを確認してください: {logger.path}",
                file=sys.stderr,
            )
            any_failed = True
            continue

        normalize_meta_case(meta)

        new_name = build_new_name(meta)
        new_path = pdf_path.with_name(new_name)
        new_path = uniquify_path(new_path)
        txt_path = new_path.with_suffix(".txt")

        print("metadata:")
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"new_name: {new_name}")
        logger.write(f"new_name={new_name}")

        if args.filename_only:
            logger.write("write_skipped=filename_only")
            logger.write(f"pdf: success elapsed={format_duration(pdf_start)}")
            continue

        extra_text = extract_extra_text_with_model_fallback(
            text,
            models,
            metadata_model,
            extra_text_prompt,
            logger,
        )

        if not args.info_only:
            if pdf_path != new_path:
                pdf_path.rename(new_path)
                logger.write(f"renamed_to={new_path}")
            else:
                logger.write("rename_skipped=same_path")
        else:
            logger.write("rename_skipped=info_only")

        txt_path.write_text(
            build_txt_content(meta, extra_text),
            encoding="utf-8",
        )
        logger.write(f"txt_written={txt_path}")
        logger.write(f"pdf: success elapsed={format_duration(pdf_start)}")

    if args.msc_predict:
        print("msc_predict は現在無効です。", file=sys.stderr)
        logger.write("msc_predict: disabled")

    if args.info_only:
        logger.write(
            f"run: finished status={'failed' if any_failed else 'success'} "
            f"elapsed={format_duration(run_start)}"
        )
        return 1 if any_failed else 0

    logger.write(
        f"run: finished status={'failed' if any_failed else 'success'} "
        f"elapsed={format_duration(run_start)}"
    )
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
