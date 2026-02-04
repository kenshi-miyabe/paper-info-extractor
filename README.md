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

### オプション（rename_from_first_page.py）

- `--filename-only`: ファイル名だけ確認（書き出しなし）
- `--info-only`: TXTだけ出力（リネームなし）
- `--config`: 設定ファイルのパス（デフォルトは `config.yml`）
- `--model`: Ollama モデル名の上書き

### 注意

- Ollama を起動してから実行してください（例: `ollama serve`）。
- DOI から BibTeX を取得する場合は [doi2bib](https://www.doi2bib.org/) が便利です。

## 設定ファイル（config.yml）

抽出する項目や出力内容は `config.yml` で変更できます。  
`fields` に追加した項目が TXT に出力され、プロンプトにも反映されます。

### バリデーション

論文以外のPDFを避けたい場合は `validation` を設定します。  
`require_abstract: true` の場合、1ページ目に `Abstract` が無いPDFはスキップします。

例:
```yaml
validation:
  require_abstract: true
```

### ステージ分割

LLMの負荷を下げるため、`stage` を使って抽出を2回に分けます。  
`stage: 1` はタイトル等の基礎情報、`stage: 2` は推定や要約に使う想定です。
`prompt.stage1_instructions` / `prompt.stage2_instructions` で各ステージの指示文を分けられます。

例:
```yaml
model: gemma3:12b
prompt:
  stage1_instructions: >
    If a field is missing, use an empty string or empty array.
  stage2_instructions: >
    If a field is missing, use an empty string or empty array.
fields:
  - key: title
    type: string
    stage: 1
  - key: year
    type: number
    stage: 1
  - key: authors
    type: list
    stage: 1
  - key: journal
    type: string
    stage: 1
  - key: doi
    type: string
    stage: 1
  - key: keywords
    type: list
    stage: 1
  - key: msc
    type: list
    stage: 1
    description: "本文に明記されているMSC分類"
  - key: msc_label
    type: list
    stage: 1
    description: "msc の各コードに対応するカテゴリ名（例: 68T05 Learning and adaptive systems）"
  - key: arxiv_category
    type: string
    stage: 1
    description: "本文に明記されているarXivカテゴリ（例: cs.AI）"
  - key: arxiv_category_label
    type: string
    stage: 1
    description: "arXivカテゴリの名称（例: Logic in Computer Science）"
  - key: msc_predict
    type: list
    stage: 2
    description: "本文にMSCが無い場合の推定MSC分類"
  - key: msc_predict_label
    type: list
    stage: 2
    description: "msc_predict の各コードに対応するカテゴリ名（例: 68T05 Learning and adaptive systems）"
  - key: arxiv_category_predict
    type: string
    stage: 2
    description: "本文にarXivカテゴリが無い場合の推定"
  - key: arxiv_category_predict_label
    type: string
    stage: 2
    description: "推定したarXivカテゴリの名称"
  - key: summary_ja
    type: string
    stage: 2
    description: 日本語の要約
rename:
  author_key: authors
  year_key: year
  title_key: title
```

`type` は `string` / `number` / `list` を想定しています。  
`description` を書くと、プロンプト内で「そのキーが何を表すか」を明示できます。  
コロン（`:`）を含む場合は `"..."` で囲んでください。  
`label` を指定すると TXT の項目名を変更できます（例: `label: 要約（日本語）`）。  
`msc_label` / `msc_predict_label` は対応するコードの説明を同じ並びで入れる想定です。

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
     例: `ollama pull gemma3:12b`

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
