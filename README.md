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
- `--model`: Ollama モデル名の上書き
- `--msc-predict`: `msc_predict.py` を実行して MSC 推定を追記

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

### フィールド設定

例:
```yaml
model: gemma4:e4b
fields:
  - key: title
    type: string
  - key: year
    type: number
  - key: authors
    type: list
  - key: affiliation
    type: list
  - key: journal
    type: string
  - key: doi
    type: string
  - key: keywords
    type: list
  - key: msc
    type: list
    description: "本文に明記されているMSC分類"
  - key: arxiv_category
    type: string
    description: "本文に明記されているarXivカテゴリ（例: cs.AI）"
  - key: summary_ja
    type: string
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
2. 抽出テキストと `config.yml` のフィールド定義からプロンプトを作成する
3. Ollama 経由で生成AIに投げ、JSON形式のメタデータを取得する
4. メタデータを元に新しいファイル名を組み立て、重複回避しつつリネームする
5. 同名の `*.txt` を作成し、抽出結果を整形して書き出す
