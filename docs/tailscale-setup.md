# Tailscale でスマホから Web UI にアクセスする

AI Code Agent の Web UI に、外出先のスマホ等から安全にアクセスするための手順。
[Tailscale](https://tailscale.com/) は自分専用の VPN（tailnet）を張る仕組みで、ポート開放も固定IPも不要。

> **方針**: tailnet 内限定で公開する（`tailscale serve`）。インターネット全公開の `funnel` は**使わない**。
> このエージェントは任意コマンド実行 + GitLab PAT を持つため、自分のデバイスだけに限定する。

---

## 仕組み

```
スマホ（Tailscaleアプリ）
   │  同じ tailnet
   ▼
https://マシン名.<tailnet>.ts.net     ← tailscale serve が HTTPS 終端（正規Let's Encrypt証明書・自動更新）
   │
   ▼
localhost:<port>（AIエージェント本体）  ← アプリ側のコードは無改造
```

- `tailscale serve` は Tailscale クライアント内蔵のリバースプロキシ。ローカルポートを HTTPS で tailnet 内に公開する。
- アプリは今まで通り `localhost:<port>` で動くだけ。HTTPS化は Tailscale が前段で行う。

---

## 事前準備（一度きり）

1. **tailnet 管理画面で HTTPS Certificates を有効化**
   （[admin console](https://login.tailscale.com/admin/dns) の DNS 設定。MagicDNS も ON）
   - 証明書のマシン名が CT ログ（公開台帳）に載るが、tailnet名はランダム化され本名/組織名は出ない。マシン名に機密を入れなければ実害なし。
2. **スマホに Tailscale アプリ**をインストールし、**同じアカウント**でログイン。

---

## WSL版（Ubuntu内・ポート 8000）

```bash
# 1. 導入
curl -fsSL https://tailscale.com/install.sh | sh

# 2. 常駐サービス化（systemd）
sudo systemctl enable --now tailscaled

# 3. tailnet に参加 → 表示されるURLをブラウザで開いて認証
sudo tailscale up

# 4. 非rootサービスから状態取得できるように operator 設定
sudo tailscale set --operator=user

# 5. HTTPS化（1回だけ・永続）
sudo tailscale serve --bg 8000

# 6. アクセスURL確認
tailscale serve status        # https://マシン名.xxxx.ts.net が表示される
```

エージェント本体は systemd（`ai-codeagent.service`）で常駐しているため、ホストが起動していれば自動で立ち上がる。

---

## Windows版（ポート 8001）

Tailscale は GUI アプリ（タスクトレイ常駐・Windowsログイン時に自動起動）。

```powershell
# 1. 導入
winget install tailscale.tailscale

# 2. タスクトレイの Tailscale アプリからログイン（tailnet 参加）

# 3. HTTPS化（1回だけ・永続）
& "C:\Program Files\Tailscale\tailscale.exe" serve --bg 8001

# 4. アクセスURL確認
& "C:\Program Files\Tailscale\tailscale.exe" serve status
```

エージェント本体は `start.bat` で起動しておく必要がある。

---

## 使うとき

1. 対象ホストが**起動中**で、**エージェント本体も起動中**であること
   （本体が落ちていると `https://...ts.net` を開いても **502** になる）
2. スマホで `https://マシン名.xxxx.ts.net` を開く（ブックマーク／ホーム画面追加が便利）

---

## よく使うコマンド

| 用途 | コマンド |
|---|---|
| 接続状態の確認 | `tailscale status` |
| serve の確認 | `tailscale serve status` |
| serve の解除 | `tailscale serve --bg <port> off` |
| tailnet から切断 | `tailscale down` |
| マシン名を指定して参加 | `tailscale up --hostname=任意の名前` |

---

## 注意点・FAQ

- **`serve --bg` は1回だけでOK**。再起動しても・端末を閉じても有効（永続）。
- **WSL と Windows は別ノード**として登録される（別名・別IP）。ホスト名が衝突すると Tailscale が自動で連番（`-1`）を付ける。`--hostname` で明示できる。
  同一PCで両方入れる必要は無く、**使う方だけ登録**するのがおすすめ。
- **HTTPS（serve）推奨の理由**: 非localhostのHTTP（生IP `100.x.x.x:port`）はブラウザのセキュアコンテキスト外となり、**クリップボード（コピー／画像ペースト）・PWA・通知・カメラ**が無効になる。本アプリはクリップボードを使うため HTTPS にしておく。
- **社内プロキシ環境**では Tailscale の制御通信／DERP がブロックされる可能性がある。社外モードでの利用を想定。
