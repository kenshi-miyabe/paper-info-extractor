# paper-info-extractor

論文PDFから情報を抽出し、その内容に応じてファイル名をリネームし、同じ名前のTXTに要約/情報を書き出すスクリプトです。

## 概要

- 論文PDFを入力として情報を抽出します
- 抽出した情報に基づいてPDFのファイル名をリネームします
- 同じ名前の`*.txt`ファイルに、抽出した内容を書き出します

## 使い方（Pythonスクリプト）

1. 対象のPDFを指定のフォルダに置く
2. スクリプトを実行する
3. リネームされたPDFと同名の`*.txt`が生成される

※ PDFの代わりにディレクトリを渡すと、その直下の`*.pdf`をまとめて処理します（サブディレクトリは対象外）。

### オプション（rename_from_first_page.py）

- `--filename-only`: ファイル名だけ確認（書き出しなし）
- `--info-only`: TXTだけ出力（リネームなし）
- `--config`: 設定ファイルのパス（デフォルトは `config.yml`）
- `--model`: Ollama モデル名の上書き（指定時はこの1つだけを試します）
- `--msc-predict`: `msc_predict.py` を実行して MSC 推定を追記

### 注意

- Ollama を起動してから実行してください（例: `ollama serve`）。
- DOI から BibTeX を取得する場合は [doi2bib](https://www.doi2bib.org/) が便利です。

## 設定ファイル（config.yml）

`config.yml` では、使うモデル、2段階抽出のプロンプト、論文チェックの有無だけを設定します。

例:
```yaml
models:
  - gemma4:e4b
  - llama3.1:8b

check_paper: false

prompts:
  filename_json: |
    Extract title, authors, and year.
    Return JSON with keys: title, authors, year.

  extra_text: |
    Return plain text sections for affiliation, journal, doi, keywords,
    MSC, arXiv category, and a short Japanese summary.
```

`models` は上から順に試されます。Ollama API の実行失敗やJSON解析失敗が起きた場合は、その時点の失敗内容を実行ログに追記して次のモデルへ進みます。  

抽出は2段階で行います。1回目はOllama APIのJSONモードを使い、ファイル名に必要な `title` / `authors` / `year` だけをJSONで取得します。2回目はそれ以外の要約や補足情報をプレーンテキストで生成し、同名のTXTに追記します。

`check_paper: true` の場合、1ページ目に `Abstract` が無いPDFはスキップします。
各プロンプトに `{text}` を書くと、その位置にPDFの1ページ目テキストを差し込みます。`{text}` が無い場合は、プロンプト末尾に自動で追加します。

## ログ

実行ごとに `logs/log_YYYYMMDD_HHMMSS.txt` を作成します。成功時はPDFごとの所要時間、使用モデル、出力先を書きます。失敗時は `timeout`、`json_parse_error`、`connection_error` などの種別と、モデル出力やAPI応答を記録します。

## 抽出対象

- 著者名
- 出版年
- タイトル

## リネーム規則

出力ファイル名は以下の形式を想定しています。  
`著者名-出版年-タイトル.pdf`

補足:
- 著者が複数いる場合は、著者名を`_`で連結します
- 著者名は姓のみを使用します
- タイトル中のスペースは`_`に置換します
- ファイル名に不適切な文字は適宜置換・削除します

## セットアップ

1. 依存ツールのインストール  
   - `uv` をインストール（例: `brew install uv`）
   - `Ollama` をインストールし、モデルを取得  
     例: `ollama pull gemma4:e4b`

2. 依存パッケージの用意  
   - リポジトリ直下で `uv sync` を実行

3. Ollama を起動  
   - 既に起動済みであればOK（例: `ollama serve`）

## Finder から右クリックで実行する場合（Automator）

1. Automator を開き、「クイックアクション」を新規作成
2. 画面上部の「ワークフローが受け取る項目」を「PDFファイル」に設定（Finder）
3. 「ユーティリティ」から「シェルスクリプトを実行」を追加
4. シェルは `/bin/zsh`、入力は「引数として」を選択
5. `run_from_finder.sh` を開き、`PROJECT_DIR` を自分の環境に合わせて絶対パスで指定する  
   例: `PROJECT_DIR="/Users/ユーザー名/パス/paper-info-extractor"`
6. 修正後の内容をそのままコピーして貼り付ける
7. 好きな名前で保存すると、Finder の右クリックメニューに表示されます

## 内部動作（エンジニア向け）

ざっくり以下の流れで動作します。

1. PDFの1ページ目を読み込み、テキストを抽出する
2. 抽出テキストと `config.yml` のプロンプトからLLM入力を作成する
3. Ollama APIのJSONモードで、ファイル名に必要な `title` / `authors` / `year` を取得する
4. メタデータを元に新しいファイル名を組み立て、重複回避しつつリネームする
5. Ollama APIで要約などの追加情報をプレーンテキスト生成する
6. 同名の `*.txt` にファイル名用メタデータと追加情報を書き出す
