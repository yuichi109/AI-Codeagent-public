# AI Code Agent

Azure OpenAI / Azure AI Foundry・Gemini・ローカル LLM に対応した、Web チャット UI で動かす自律コードエージェント。
コードの生成・編集・実行から、ファイル操作・Web 調査・Office ファイル・GitLab 連携まで一気通貫で扱える。

---

## 主な機能

### AIモデル
- **Azure OpenAI / Azure AI Foundry** — gpt-5.4-mini をメインに、nano（軽量・高速）や gpt-4.1（高精度）へ UI から即切り替え
- **Google Gemini** — Gemini 2.5 Flash / Pro を利用可能
- **ローカル LLM** — Ollama・LM Studio・vLLM（Qwen3 等）との併用対応
- チャット中にモデルを切り替えてもセッションは継続

### コード・ファイル操作
- ファイルの読み書き・編集・ディレクトリ一覧・glob・grep
- コマンド実行（bubblewrap サンドボックス、タイムアウト・ホワイトリスト付き）
- バックグラウンドプロセス起動・監視・停止
- コード静的解析（Python: ruff / JS・TS: eslint）
- Manim アニメーション生成

### Monaco テキストエディタ（ブラウザ内 VSCode 相当）
- ファイルツリー付きのマルチタブエディタ（別タブで独立表示）
- ドラッグ＆ドロップでファイル移動
- AI インライン補完（Ctrl+Space）
- 未保存変更の警告、自動言語検出

### Web 調査
- Web 検索（Tavily → DuckDuckGo → SearXNG フォールバック）
- URL テキスト取得・詳細調査（複数ページ横断）

### Office / ドキュメント
- Word（.docx）/ Excel（.xlsx）/ PowerPoint（.pptx）の読み書き・編集
- PDF 読み取り（pdfplumber）・PDF 生成（fpdf2、日本語対応）
- ファイルはチャット欄へドラッグ＆ドロップでアップロード

### RAG 知見データベース
- `rag_save` で記録、`rag_search` で類似検索（ベクトル DB）
- モデルが自律的に参照してプロジェクト固有の知識を活用

### GitLab 連携
- イシュー・MR 閲覧・作成・コメント
- リポジトリクローン・ブランチ操作
- コミット・push（PAT 認証）

### Ansible 実行
- プレイブック一覧のチェックボックス選択 → 実行・ストリーム表示

### Windows ネイティブ操作（WSL2 環境）
- PowerShell コマンド実行（`run_powershell`）
- WinGet・レジストリ操作・GUI アプリ起動

### セッション管理
- 会話履歴の保存・復元・アーカイブ・保護（消えない設定）
- 長い会話は自動圧縮（コンテキスト節約）

### スキルシステム
- `skills/*/SKILL.md` にスラッシュコマンドを定義するだけで即反映（再起動不要）
- 標準スキル: `/commit` `/get-proj` `/save` `/ansible` `/boost`

---

## 対応環境

| 環境 | 起動方法 |
|---|---|
| **Linux / WSL2**（推奨） | systemd サービス（`ai-codeagent.service`）で常駐 |
| **Windows ネイティブ** | タスクトレイアイコン常駐（`start.bat` → `tray.py`）|

---

## セットアップ

→ **[docs/setup.md](docs/setup.md)** を参照

```bash
# Linux / WSL2
git clone https://gitlab.com/yuichi.matsuo/AI-Codeagent.git
cd AI-Codeagent
./setup.sh install
# ブラウザで http://localhost:8000/setup を開いて API キーを設定
```

```bat
rem Windows ネイティブ版
git clone https://gitlab.com/yuichi.matsuo/AI-Codeagent.git AI-Codeagent-win
cd AI-Codeagent-win
git checkout for_windows
start.bat
```

---

## 起動・停止（Linux / WSL2）

```bash
# 起動・停止・再起動
sudo systemctl start ai-codeagent
sudo systemctl stop ai-codeagent
sudo systemctl restart ai-codeagent

# ログ確認
journalctl -u ai-codeagent -n 50
```

ブラウザで **http://localhost:8000** を開く。

---

## 必要な設定（.env）

```env
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://xxx.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-5.4-mini
AZURE_OPENAI_DEPLOYMENTS=gpt-5.4-mini,gpt-4.1-mini,gpt-4.1
AZURE_OPENAI_API_VERSION=2025-01-01-preview
```

その他の設定項目（GitLab PAT・Gemini・ローカル LLM・SearXNG 等）は `.env.example` を参照。

---

## ドキュメント

| ファイル | 内容 |
|---|---|
| [docs/setup.md](docs/setup.md) | 詳細セットアップ手順（WSL2 / Windows） |
| [docs/changelog.md](docs/changelog.md) | 実装済み機能の変更履歴 |
| [docs/roadmap.md](docs/roadmap.md) | 今後の実装予定 |
| [docs/test-checklist.md](docs/test-checklist.md) | テスト確認項目 |
