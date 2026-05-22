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

### ② 日本語フォントインストール（文字化け防止）

```bash
sudo apt install fonts-noto-cjk
```

### ③ wslu インストール（Windows連携ユーティリティ）

```bash
sudo apt install wslu
```

### ④ .bashrc にエイリアス追加（GPU エラー抑制）

```bash
echo "alias obsidian='BROWSER=wslview obsidian --disable-gpu 2>/dev/null'" >> ~/.bashrc
source ~/.bashrc
```

> `--disable-gpu`: WSL環境でElectronアプリを動かすと出る GPU エラーを抑制  
> `2>/dev/null`: ターミナルへのエラー出力を非表示にする

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
