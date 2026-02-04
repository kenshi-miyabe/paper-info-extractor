#!/bin/zsh
set -euo pipefail

# Set project directory explicitly when pasting into Automator.
# If this script is executed as a file, it will fall back to its own location.
PROJECT_DIR="${PROJECT_DIR:-}"
if [[ -z "$PROJECT_DIR" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "${(%):-%N}")" && pwd)"
  PROJECT_DIR="$SCRIPT_DIR"
fi

# Ensure Homebrew and local binaries are available in Automator.
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

UV="/opt/homebrew/bin/uv"
if [[ ! -x "$UV" ]]; then
  echo "uv が見つかりません。先に uv をインストールしてください。" >&2
  exit 1
fi

if [[ ! -f "$PROJECT_DIR/rename_from_first_page.py" ]]; then
  echo "rename_from_first_page.py が見つかりません: $PROJECT_DIR" >&2
  echo "Automator に貼り付ける場合は PROJECT_DIR を絶対パスで指定してください。" >&2
  exit 1
fi

cd "$PROJECT_DIR"

# Finder から渡された PDF を順に処理する
for pdf in "$@"; do
  "$UV" run python "$PROJECT_DIR/rename_from_first_page.py" --msc-predict "$pdf"
done
