# AI Code Agent プロジェクト

このプロジェクトは Azure OpenAI を使った高機能コードエージェントです。
**設計の詳細は `docs/design.md` を必ず参照してください。**

## プロジェクト概要
Web チャット UI からコードの生成・編集・レビュー・実行ができるエージェント。
ファイル操作・コマンド実行・Web 検索・コードリントをツールとして持つ。

## 重要なパス
- 本プロジェクト: `~/AI-Codeagent`
- 作業ディレクトリ: `~/AI-Codeagent/workspace`
- ツール実装: `~/AI-Codeagent/tools/`
- 設計ドキュメント: `~/AI-Codeagent/docs/design.md`

## 作業再開時の確認事項
1. `docs/design.md` を読んで設計とフェーズを把握する
2. 現在の実装フェーズと次のアクションを確認する
3. `.env` が存在するか確認する (なければ `.env.example` を参照して作成)
4. `uvicorn server:app --reload` で起動できるか確認する

## 開発環境
- 実行環境: WSL (Ubuntu)
- Python 仮想環境: `~/AI-Codeagent/venv`
- 起動: `source venv/bin/activate && uvicorn server:app --reload`
- UI アクセス: http://localhost:8000

## アーキテクチャ概要
```
server.py       ← FastAPI エントリポイント (スリム)
config.py       ← .env から設定読み込み
prompts.py      ← システムプロンプト
tools/
  file_tools.py    ← read_file / write_file / list_files
  command_tools.py ← run_command (shell=False + ホワイトリスト)
  web_tools.py     ← web_search / web_fetch
  code_tools.py    ← code_lint (ruff)
index.html      ← チャット UI (Catppuccin テーマ)
```
