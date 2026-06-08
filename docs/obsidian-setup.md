# Obsidian セットアップ手順（WSL環境）

## なぜ Windows版 Obsidian では動かないのか

Windows版 Obsidian から `\\wsl.localhost\Ubuntu\...` のパスを Vault として開こうとすると、以下のエラーが発生して使えない。

```
Error: EISDIR: illegal operation on a directory, watch '\\wsl.localhost\Ubuntu\home\user\AI-Codeagent\workspace\'
```

**原因**: Obsidian はファイルの変更を監視するために OS のファイル監視機能（`chokidar`）を使っている。この仕組みが WSL のネットワークファイルシステム（DrvFs）に対応していないため、ディレクトリを開いた時点でクラッシュする。

これは Obsidian の既知の未対応問題で、2020年からフォーラムに要望が上がっているが未修正のまま。

**試みたが効果なかった方法**:
- `\\wsl.localhost\Ubuntu\...` を直接 Vault に指定 → EISDIRエラー
- `net use` でドライブレター割り当て → 同じ問題（根本のファイルシステムが変わらないため）

**解決策**: WSL内に Obsidian をインストールして WSL上で直接動かす（WSLg使用）。

---

## セットアップ手順

WSLg（Windows 11 内蔵）を使うため、Ubuntu 側に X11 パッケージは不要。Obsidian は Windows のタスクバーに普通のアプリとして表示される。

### ① Obsidian インストール

```bash
cd /tmp
wget https://github.com/obsidianmd/obsidian-releases/releases/download/v1.12.7/obsidian_1.12.7_amd64.deb
sudo apt install ./obsidian_1.12.7_amd64.deb
```

> 最新バージョンは https://github.com/obsidianmd/obsidian-releases/releases で確認。

### ② 日本語フォント＋日本語入力エンジンのインストール

```bash
sudo apt install fonts-noto-cjk ibus ibus-mozc
```

> `fonts-noto-cjk`: 日本語表示の文字化け防止
> `ibus` / `ibus-mozc`: 日本語入力（IME）。設定は ④ で行う

### ③ wslu インストール（Windows連携ユーティリティ）

```bash
sudo apt install wslu
```

### ④ .bashrc 設定＋起動ラッパー作成（日本語入力＋GPUエラー抑制）

日本語入力（IME）と GPU エラー抑制を、起動ラッパーに一本化する。

**④-1. `.bashrc` に環境変数とエイリアスを追加**

```bash
cat >> ~/.bashrc << 'EOF'
export GTK_IM_MODULE=ibus
export QT_IM_MODULE=ibus
export XMODIFIERS=@im=ibus
alias obsidian='~/.local/bin/obsidian-ime'
EOF
source ~/.bashrc
```

> ⚠️ `.bashrc` で ibus-daemon を**直接起動してはいけない**。WSLg のディスプレイ接続が
> 確立する前に起動するとIME接続が壊れる。起動はラッパー（④-2）に一本化する。

**④-2. 起動ラッパーを作成**

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/obsidian-ime << 'EOF'
#!/bin/bash
export GTK_IM_MODULE=ibus
export QT_IM_MODULE=ibus
export XMODIFIERS=@im=ibus
export BROWSER=wslview

# ibus が既に健全なら何もしない（2回目以降は即起動）。
# 壊れている／未起動のときだけ再起動する（WSL再起動直後の初回のみ重い）。
if ! ibus engine mozc-jp 2>/dev/null; then
    # -x = XIM サポート。Electron アプリに必須
    pkill ibus-daemon 2>/dev/null
    sleep 0.5
    ibus-daemon -drx &
    # ibus が応答するまで待つ（最大10秒）
    # ← WSL 再起動直後の初回起動でも確実に日本語を効かせるための要。
    #   sleep 固定だと初回は初期化が間に合わず失敗する。
    for i in {1..20}; do
        if ibus engine mozc-jp 2>/dev/null; then
            break
        fi
        sleep 0.5
    done
    sleep 1
fi

# --disable-gpu: WSL の GPU エラー抑制
# nohup + & : ターミナルから完全に切り離し全出力を破棄（IBUS の surrounding-text 警告も消える）
nohup /opt/Obsidian/obsidian --disable-gpu "$@" >/dev/null 2>&1 &
EOF
chmod +x ~/.local/bin/obsidian-ime
```

> **日本語入力への切り替えは「半角/全角」キー**で行う。

### ⑤ xdg-open を差し替え（ファイルエクスプローラー誤起動防止）

WSL内の `xdg-open` がうまく動かず、Obsidian からファイルを開こうとすると Windows のファイルエクスプローラーが `\\wsl$\Ubuntu` を表示するだけになる問題を修正する。

```bash
sudo tee /usr/local/bin/xdg-open > /dev/null << 'EOF'
#!/bin/bash
WIN_PATH=$(wslpath -w "$1")
powershell.exe -c "Start-Process '$WIN_PATH'"
EOF
sudo chmod +x /usr/local/bin/xdg-open
```

### ⑥ 起動

```bash
obsidian
```

Vault として `/home/user/AI-Codeagent/workspace` を指定すればそのまま使える。

---

## 既知の問題

| 問題 | 状況 |
|---|---|
| PDF保存後に「開く」を押しても何も起こらない | ⑤の対応で誤起動は解消済み。PDF自体は手動で開く必要がある |
| 起動が少し遅い | WSLg経由のため。動作自体は問題なし |
| WSL再起動直後の初回起動で日本語入力が効かない | ④-2 のラッパーが「ibus が応答するまで待つ」方式なら解消。`sleep` 固定値だと初回は ibus 初期化が間に合わず再発するので、待機ループは消さないこと |
