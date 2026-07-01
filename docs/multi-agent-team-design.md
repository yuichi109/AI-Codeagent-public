# 協調型マルチエージェント（チーム方式）設計書

> 作成: 2026-06-22
> ステータス: 設計フェーズ（未実装）
> 関連: [multi-agent-dispatch-design.md](multi-agent-dispatch-design.md)（既存=パイプライン方式）
> 元ネタ: Claude Code Agent Teams（実験的機能・v2.1.178時点）

---

## 0. 一行サマリ

既存の**パイプライン方式**（逐次・ファイルのみ・会話なし）はそのまま温存し、
**Claude Code Agent Teams に倣った「チーム方式」**（並列・mailbox での直接通信・共有タスクリスト）を
**もう一つの実行モードとしてトグルで追加**する。

---

## 1. 背景と重要な方針転換

### 既存（パイプライン方式）の根幹原則

[multi-agent-dispatch-design.md](multi-agent-dispatch-design.md) では次が**最重要原則**として定義されている：

- エージェント同士は**会話しない**。通信手段はファイルのみ
- 理由：会話型は「確認・相談・訂正」を繰り返して**トークン爆発・無駄ループ**を起こす

### 今回の決定（2026-06-22・ユーザー判断）

- **チーム方式では、この「会話なし」原則を意図的に外す**。Claude Code 方式に倣い、
  mailbox による**エージェント間の直接通信を変に限定せず**に入れる。
- 理由：「多少トークンが膨れるのは仕方ない。Claude Code の協調型に興味があり、その効果を取りに行く」
- **ただしパイプライン方式は廃止しない**。決定論的・手順が決まった作業（インフラ構築等）には
  低コスト・予測可能なパイプライン方式が向く。両者を**トグルで使い分ける**。

### この設計書の立場

| | パイプライン方式（既存） | **チーム方式（本書）** |
|---|---|---|
| 通信 | ファイルのみ・会話なし | **mailbox で直接通信（非限定）** |
| 実行 | 逐次 | **並列（同時最大3体・既定）** |
| リード | 計画して退場 | **計画＋監視＋動的再割当＋統合** |
| 向く仕事 | 定型・手順確定 | 探索・レビュー・新機能・競合仮説デバッグ |
| コスト | 予測可能・低 | 高い（割り切り済み） |

> 注意：チーム方式の「会話あり」は、既存設計書の戒め（会話＝罠）を**承知の上で**の選択。
> Claude Code 同様、後述の §10 のコスト制御策で「爆発の形」だけは避ける。

---

## 2. Claude Code Agent Teams の調査まとめ（出典: 公式ドキュメント v2.1.178）

### 4つの構成要素

| 構成要素 | 役割 |
|---|---|
| **Team Lead（リード）** | メインセッション。teammate を spawn し、計画・割当・監視・**動的再割当**・最終統合 |
| **Teammates** | 独立した Claude インスタンス。各自**独自コンテキスト窓＋ツール権限**。リードの会話履歴は引き継がない |
| **共有タスクリスト** | pending / in-progress / completed の3状態＋**依存関係**。**ファイルロックで claim 競合を防止**。teammate が次タスクを**自分で取る（self-claim）** |
| **Mailbox** | エージェント間メッセージング。lead↔teammate、**teammate↔teammate** のP2P。**自動配信**（ポーリング不要）・宛先は名前指定 |

### 動作の要点

- **spawn**: リードがユーザー指示で teammate を生成。ユーザー承認なしには生成しない
- **コンテキスト**: teammate 起動時に CLAUDE.md / MCP / skills は読むが、**リードの会話履歴は渡らない**。spawn プロンプトで個別文脈を与える
- **依存解決**: あるタスク完了で、それに依存する pending タスクが**自動で unblock**
- **タスク状態の保存先**: `~/.claude/tasks/{team-name}/`（ローカル・永続）／team config は `~/.claude/teams/{team-name}/config.json`（セッション終了で破棄）
- **plan 承認モード**: teammate を read-only plan mode で走らせ、リードが計画を承認/差し戻し
- **入れ子禁止**: teammate は teammate を生めない。リードのみが管理
- **推奨規模**: 3〜5体、1体あたり5〜6タスク

### トークン制御の考え方（Claude Code がやっていること）

1. 各 teammate が**独立コンテキスト**＝全員の会話を全員が読む構造を作らない
2. メッセージは**宛先指定・1対1**（全体ブロードキャストを基本にしない）
3. リードが**要約して統合**（生ログを全部は通さない）
4. **人数・タスク数の推奨上限**で規模を抑える
5. 公式も「シングルセッションより**大幅にトークンを使う**」と明記し、**並列で得する仕事に限定推奨**

---

## 3. 採用方針（このプロジェクトでどう作るか）

- **Claude Code 準拠**：共有タスクリスト（self-claim・依存・ロック）＋ mailbox（非限定の直接通信）＋ 並列。
- **mailbox は変に限定しない**（ユーザー決定）。teammate↔teammate も許可。
  ただし §10 のコスト制御（独立コンテキスト・要約配信・人数上限・往復のソフト上限）は維持。
- **同時実行 teammate 数の既定 = 3**（`.env` で可変・§9）。
- **タスクの取り方 = self-claim**（teammate が空きタスクを自分で取る・Claude Code 方式・2026-06-22 決定）。リードは介入時のみ再割当。
- **mailbox は最初から土台に組み込む**（後付けにしない・2026-06-22 決定・§11）。後付けは並列ループの作り直しを招くため。
- **トグルで方式切替**：既存 `agent_mode`（quality/balance/economy）と同じ配線で
  「パイプライン方式 ⇔ チーム方式」を1つ追加。パイプライン方式は**無改修で温存**。
- **入れ子禁止**（Claude Code に倣う）。リードのみが spawn・管理。

---

## 4. 現状資産の棚卸し（流用できるもの）

| 必要なもの | 現状 | 流用可否 |
|---|---|---|
| 独立コンテキストの役割エージェント | `tools/multi_agent_tools.py: run_sub_agent`（役割別 messages・ツール権限分離） | ✅ ほぼそのまま核に使える |
| ツール権限分離 | `AGENT_ALLOWED_TOOLS` | ✅ |
| 計画生成 | `dispatch_task`（JSON 計画） | △ 依存グラフ・チーム編成を出すよう拡張 |
| 計画確認フロー | `multi_agent_stream` の plan_ready / `_interpret_plan_response` | ✅ 共通利用 |
| 最終統合 | `generate_final_report` | ✅ リード統合の土台 |
| ジョブ領域 | `workspace/{scope}/jobs/{job_id}/` | ✅ mailbox / tasks.json の置き場に流用 |
| 非同期基盤 | asyncio（BGワーカー `agent_core.py`） | ✅ 並列の土台 |
| 並列実行 | なし | ❌ 新規（§7） |
| mailbox | なし | ❌ 新規（§6） |
| 共有タスクリスト（claim/lock） | `status.md` のみ | ❌ `tasks.json` 新規（§6） |

> ポイント：**「独立コンテキスト」「ツール権限分離」はすでに実装済み**。
> 新規に作るのは主に **並列・mailbox・共有タスクリスト・リードの監視ループ** の4点。

---

## 5. アーキテクチャ全体像

```
ユーザー指示（チーム方式トグル ON）
  │
  ▼
リード（Lead）= 既存ディスパッチャーの格上げ
  ├─ 計画（plan.json：roles＋tasks＋depends_on＋team編成）
  ├─ 確認（plan_ready → ▶実行 / ✕キャンセル / 自然言語修正）   ← 既存フロー共通
  ├─ teammate を spawn（最大同時3体）
  │     ├─ teammate A（独自コンテキスト＋権限）┐
  │     ├─ teammate B                          ├─ asyncio で並列
  │     └─ teammate C                          ┘
  │            ↕ mailbox（job_dir/mailbox/）で直接通信（lead↔tm, tm↔tm）
  │            ↕ 共有タスクリスト（job_dir/tasks.json）を claim/complete
  ├─ 監視ループ：完了/アイドル通知を受け、依存 unblock・再割当・差し戻し
  └─ 全完了 → generate_final_report（要約統合）→ ユーザーへ
```

---

## 6. データ構造（スキーマ案）

### plan.json（拡張）

```jsonc
{
  "mode": "team",                       // "pipeline" | "team"
  "roles": ["design", "coding", "debug"],
  "max_parallel": 3,                    // 同時実行上限（既定は .env）
  "tasks": {
    "t1": {
      "role": "design", "prompt": "...",
      "depends_on": [],                 // ← 依存グラフ
      "files": ["design.md"],           // ← 担当ファイル（衝突回避）
      "preset_id": "...", "model": "...", "timeout_sec": 600
    },
    "t2": { "role": "coding", "prompt": "...", "depends_on": ["t1"], "files": ["src/app.py"] }
  }
}
```

### tasks.json（共有タスクリスト・claim/lock）

```jsonc
[
  { "id": "t1", "role": "design", "state": "completed",  "owner": "designer", "depends_on": [] },
  { "id": "t2", "role": "coding", "state": "in_progress","owner": "coder",    "depends_on": ["t1"] },
  { "id": "t3", "role": "debug",  "state": "pending",    "owner": null,        "depends_on": ["t2"] }
]
```

- **claim**: `pending` かつ `depends_on` が全て `completed` のタスクのみ取得可
- **ロック**: `tasks.json.lock`（`os.open(O_CREAT|O_EXCL)` 等のファイルロック）で同時 claim の競合防止
- **状態遷移**: pending → in_progress → completed（自動 unblock は完了時にリードが依存を再評価）

### mailbox（job_dir/mailbox/{recipient}.jsonl）

```jsonc
// 1行1メッセージ（追記）
{"ts":"...","from":"coder","to":"designer","subject":"API形式の確認","body":"レスポンスは {data:[...]} で良い？"}
```

- 各 teammate は**ターン冒頭で自分宛 jsonl の未読だけ読む**（既読オフセットを保持）
- 配信は「ファイル追記」＝擬似的な自動配信。リアルタイム性は不要（ターン境界で十分）
- **宛先は名前指定**（全体ブロードキャストはツールで作らない。複数宛は複数送信）

### team config（job_dir/team.json）

```jsonc
{
  "members": [
    {"name":"designer","role":"design","model":"gpt-5.4"},
    {"name":"coder","role":"coding","model":"gpt-5.4-mini"},
    {"name":"debugger","role":"debug","model":"gpt-5.4-mini"}
  ]
}
```

- teammate はこれを読んで**他メンバーの名前を知る**（mailbox の宛先解決に使う）

---

## 7. 並列実行モデル

- `plan.json` の `depends_on` から**トポロジカルにフェーズ分け**し、
  **同一フェーズ内のタスクを `asyncio.gather` で並列実行**。
- 同時数は `max_parallel`（既定3）で**セマフォ制限**（`asyncio.Semaphore`）。
- フェーズ完了ごとにリードが介入：①完了タスクで依存を unblock ②成果を点検し差し戻し判定
  ③次フェーズの teammate を起動（必要なら再割当）。
- **ファイル衝突回避**：`tasks[].files` が重なるタスクは同フェーズで並列にしない
  （Claude Code の「teammate ごとに別ファイルを持たせる」ベストプラクティスに準拠）。

### SSE 表示（UI）

- 既存は単一ストリーム。チーム方式では**複数 teammate の進捗を1本の SSE に多重化**する
  （`{type:"agent_chunk", agent:"coder", content:"..."}` のように `agent` フィールドで識別）。
- まずは「どの teammate が今何をしているか」のラベル付きログで十分。
  個別ペイン表示（Claude Code の split-pane 相当）は将来課題。

---

## 8. 新規ツール（※ [[project-dual-tool-registry]]：server.py と agent_core.py の両方に登録）

| ツール | 用途 | 引数（案） |
|---|---|---|
| `send_message` | 他エージェントへメッセージ送信 | `to`(名前), `subject`, `body` |
| `read_messages` | 自分宛の未読メッセージ取得 | （なし。呼び出し元名から解決） |
| `claim_task` | 次の実行可能タスクを取得（self-claim） | `task_id`(任意) |
| `complete_task` | タスクを完了にする | `task_id`, `summary` |
| `list_tasks` | タスクリストの現状参照 | （なし） |

- いずれも `job_dir` スコープ内のファイル操作に閉じる（既存の安全境界を踏襲）。
- teammate のシステムプロンプトに「mailbox / タスクの使い方」を明記（探索させない・名指しで具体化）。

---

## 9. 設定（config / .env）

| キー | 既定 | 意味 |
|---|---|---|
| `MULTI_AGENT_TEAM_ENABLED` | `true` | チーム方式トグルをUIに出すか |
| `MULTI_AGENT_MAX_PARALLEL` | `3` | 同時実行 teammate 数の上限 |
| `MULTI_AGENT_MAX_MESSAGES` | `20` | 1ジョブの mailbox 総送信数のソフト上限（暴走ガード） |
| （既存）`MULTI_AGENT_MAX_ITERATIONS` / `MULTI_AGENT_TIMEOUT_SEC` | 既存値 | 各 teammate のループ/時間上限 |

---

## 10. トークン／コスト制御（「会話あり」でも爆発させない）

ユーザーは「多少の増加は許容」だが、**爆発の"形"だけは構造的に避ける**（Claude Code と同じ思想）：

1. **独立コンテキスト**：teammate は自分の文脈のみ。全員の会話を全員に配らない
2. **要約統合**：リードは成果ファイル/要約を読む。生の全 messages は通さない
3. **宛先指定のみ**：ブロードキャストAPIを作らない（複数宛は複数送信＝送信者が意識する）
4. **人数上限3＋タスクは自己完結サイズ**：規模で抑える
5. **mailbox 総数のソフト上限**（`MULTI_AGENT_MAX_MESSAGES`）：往復の暴走を検知したらリードが介入/打ち切り
6. **既存の歯止め流用**：10連続同一ループ検知・3回リトライ・タイムアウト催促

---

## 11. 実装ステップ（各 Step 後にユーザー確認）

> ★方針（2026-06-22 ユーザー決定）：**mailbox を後付けにしない**。
> 並列の土台を作る時点で「最初からメッセージをやり取りできるターンループ」を組み込み、
> 後から無理に挟み込む作り変えを避ける。タスクの取り方は **self-claim**（Claude Code 方式）。

```
Step 1: 協調コア（トグル ＋ 並列 ＋ 共有タスクリスト ＋ mailbox を一体で）
        - plan.json に depends_on・フェーズ分け・asyncio.gather + Semaphore(=3)
        - tasks.json：self-claim（空きタスクを teammate が自分で取る）＋ファイルロック＋依存 unblock
        - mailbox：team.json で名前解決・jsonl 配信・既読オフセット。
          teammate のターンループに「冒頭で自分宛を読む／send_message できる」を最初から組み込む
        - 新ツール（send_message / read_messages / claim_task / complete_task / list_tasks）を
          dual-tool-registry（server.py ＋ agent_core.py）に登録
        - UI トグル「パイプライン ⇔ チーム」追加（パイプラインは無改修）
        難易度 ★★★

Step 2: リード監視ループ ＋ コスト歯止め
        - フェーズ後の点検・差し戻し・再割当（self-claim を基本にリードが介入時のみ）
        - mailbox 総数ソフト上限・暴走検知での打ち切り
        難易度 ★★★

Step 3: 仕上げ（複数 teammate の SSE 多重化表示・plan 承認モード・最終統合の磨き込み）
        難易度 ★★★★
```

> 各 Step は「パイプライン方式を壊さない」ことを最優先に検証。

---

## 12. 既知の課題・非対応（MVP では割り切る）

- **入れ子チーム非対応**（teammate は spawn 不可・Claude Code と同じ）
- **セッション跨ぎの teammate 復元なし**（ジョブ単位で完結。途中再開は将来課題）
- **split-pane 個別表示なし**（1本の SSE にラベル付き多重化で代替）
- **ファイル衝突は files 宣言ベースの回避のみ**（厳密な FS 隔離はしない）
- **リード固定**（途中でリーダー交代しない）

---

## 13. 未決事項（§Cで詰める / レビューで決める）

- [x] `claim` 方式：**self-claim を主**にする（Claude Code 方式・2026-06-22 決定）。リードは介入時のみ再割当
- [x] mailbox：**MVP（Step 1）から土台に組み込む**（後付けしない・2026-06-22 決定）
- [ ] mailbox の既読管理：オフセットファイル方式で十分か、メッセージに `read` フラグを持たせるか
- [ ] teammate の命名規則：役割名そのまま（designer/coder）か、ユーザーが spawn 時に指定可能にするか
- [ ] plan 承認モード（read-only で計画→リード承認）を MVP に入れるか Step 4 に回すか
- [ ] パイプライン方式の既存「会話なし」原則ドキュメントとの整合（両論併記で残す前提）
- [ ] SSE 多重化のフロント表示形式（ラベル付き単一ログ / 折りたたみ per-agent / ペイン）
```
