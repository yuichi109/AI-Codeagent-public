#!/bin/bash
# ============================================================
#  AI-Codeagent セットアップ & 設定管理スクリプト
#  使い方:
#    初回セットアップ: ./setup.sh
#    プロキシ切り替え: sudo ./setup.sh proxy
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"
VENV_DIR="$SCRIPT_DIR/venv"
SERVICE_NAME="ai-codeagent"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PROXY_CONF="/etc/systemd/system/docker.service.d/http-proxy.conf"

# ──────────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────────
info()    { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
ok()      { echo -e "\033[1;32m[ OK ]\033[0m  $*"; }
warn()    { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
error()   { echo -e "\033[1;31m[ERR ]\033[0m  $*" >&2; }
section() { echo -e "\n\033[1;37m══ $* ══\033[0m"; }

require_root() {
    if [ "$EUID" -ne 0 ]; then
        error "このオプションは sudo が必要です: sudo $0 $*"
        exit 1
    fi
}

# .env のキーを upsert する（存在すれば置換・なければ追記）
set_env_var() {
    local key="$1" val="$2"
    if [ -f "$ENV_FILE" ] && grep -qE "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}

# ──────────────────────────────────────────────
# メニュー表示
# ──────────────────────────────────────────────
show_menu() {
    echo ""
    echo "╔══════════════════════════════════════════╗"
    echo "║     AI-Codeagent セットアップメニュー    ║"
    echo "╚══════════════════════════════════════════╝"
    echo ""
    echo "  1) 初回セットアップ（新規インストール）"
    echo "  2) プロキシ設定切り替え（社内 ⇔ 社外）"
    echo "  3) サービス操作（起動 / 停止 / 再起動 / 状態確認）"
    echo "  4) 終了"
    echo ""
    read -rp "選択 [1-4]: " MENU_CHOICE
}

# ──────────────────────────────────────────────
# 1) 初回セットアップ
# ──────────────────────────────────────────────
cmd_setup() {
    section "初回セットアップ"

    # sudo NOPASSWD チェック（まっさらな Ubuntu では未設定の場合がある）
    section "sudo NOPASSWD チェック"
    CURRENT_USER="$(whoami)"
    if sudo -n true 2>/dev/null; then
        ok "sudo NOPASSWD は有効です"
    else
        warn "sudo NOPASSWD が無効です。設定します（パスワードを1回入力）..."
        echo "${CURRENT_USER} ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/nopass > /dev/null
        sudo chmod 440 /etc/sudoers.d/nopass
        ok "sudo NOPASSWD を設定しました（/etc/sudoers.d/nopass）"
    fi

    # .env 作成（空ファイル・上書きなし）
    # APIキー等の設定はブラウザ（/setup）で行う
    if [ ! -f "$ENV_FILE" ]; then
        touch "$ENV_FILE"
        ok ".env を作成しました（空ファイル）"
    else
        ok ".env はすでに存在します（スキップ）"
    fi

    # サーバーポート設定（対話・単一ソース APP_PORT）
    section "サーバーポート"
    CURRENT_PORT="$(grep -E '^APP_PORT=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '[:space:]' || true)"
    DEFAULT_PORT="${CURRENT_PORT:-8000}"
    while true; do
        read -rp "サーバーのポート番号 [デフォルト: ${DEFAULT_PORT}]: " INPUT_PORT
        INPUT_PORT="${INPUT_PORT:-$DEFAULT_PORT}"
        if [[ "$INPUT_PORT" =~ ^[0-9]+$ ]] && [ "$INPUT_PORT" -ge 1 ] && [ "$INPUT_PORT" -le 65535 ]; then
            APP_PORT="$INPUT_PORT"
            break
        fi
        warn "1〜65535 の数値を入力してください"
    done
    set_env_var "APP_PORT" "$APP_PORT"
    ok "ポート ${APP_PORT} を .env に設定しました（systemd に反映します）"

    # Python venv
    section "Python 仮想環境"
    if ! python3 -c "import ensurepip" &>/dev/null 2>&1; then
        info "python3-venv が見つかりません。インストール中..."
        sudo apt-get install -y python3-venv
        ok "python3-venv をインストールしました"
    fi
    if [ ! -d "$VENV_DIR" ]; then
        info "venv を作成中..."
        python3 -m venv "$VENV_DIR"
        ok "venv を作成しました"
    else
        ok "venv はすでに存在します（スキップ）"
    fi

    info "依存パッケージをインストール中..."
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" -q
    ok "依存パッケージをインストールしました"

    # workspace ディレクトリ
    section "workspace ディレクトリ"
    mkdir -p "$SCRIPT_DIR/workspace"
    ok "workspace/ を確認しました"

    # Manim チェック（オプション）
    section "Manim（アニメーション生成・オプション）"
    if command -v manim &>/dev/null; then
        ok "manim はインストール済みです: $(manim --version 2>&1 | head -1)"
    else
        warn "manim が見つかりません（render_manim ツールが動作しません）"
        read -rp "  今すぐインストールしますか？ [y/N]: " INSTALL_MANIM
        if [[ "$INSTALL_MANIM" =~ ^[Yy]$ ]]; then
            sudo apt-get install -y libcairo2-dev libpango1.0-dev ffmpeg
            "$VENV_DIR/bin/pip" install manim -q
            ok "manim をインストールしました"
        else
            info "スキップ（後で: sudo apt install libcairo2-dev libpango1.0-dev ffmpeg && venv/bin/pip install manim）"
        fi
    fi

    # Docker（自動インストール＋グループ追加）
    section "Docker"
    if command -v docker &>/dev/null; then
        ok "Docker はインストール済みです: $(docker --version)"
    else
        info "Docker が見つかりません。インストール中..."
        sudo apt-get install -y ca-certificates curl gnupg lsb-release
        sudo install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        sudo chmod a+r /etc/apt/keyrings/docker.gpg
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
            sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
        sudo apt-get update -q
        sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
        ok "Docker をインストールしました"
    fi
    # カレントユーザーを docker グループに追加（sudoなし実行のため）
    if groups "$USER" | grep -qw docker; then
        ok "ユーザー '$USER' はすでに docker グループに所属しています"
    else
        sudo usermod -aG docker "$USER"
        ok "ユーザー '$USER' を docker グループに追加しました"
        warn "グループ変更を反映するには一度ログアウト＆ログインが必要です"
    fi

    # bubblewrap（自動インストール）
    section "bubblewrap（サンドボックス）"
    if command -v bwrap &>/dev/null; then
        ok "bubblewrap はインストール済みです: $(bwrap --version 2>&1 | head -1)"
    else
        info "bubblewrap をインストール中..."
        sudo apt-get install -y bubblewrap
        ok "bubblewrap をインストールしました"
    fi

    # Node.js（MCP サーバー用）
    section "Node.js（MCP クライアント用）"
    if command -v node &>/dev/null; then
        ok "Node.js はインストール済みです: $(node --version)"
    else
        info "Node.js 22.x をインストール中..."
        curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
        sudo apt-get install -y nodejs
        ok "Node.js をインストールしました: $(node --version)"
    fi
    # Playwright MCP 用 Chromium のプリインストール
    info "Playwright Chromium（MCP用）をインストール中..."
    if npx --yes @playwright/mcp install-browser chromium 2>&1 | tail -3; then
        ok "Playwright Chromium をインストールしました"
    else
        warn "Playwright Chromium のインストールに失敗しました（後で手動で実行してください）"
        warn "  npx @playwright/mcp install-browser chromium"
    fi
    # Playwright システム依存パッケージ（chrome-for-testing の実行に必要）
    info "Playwright システム依存パッケージをインストール中..."
    if sudo npx --yes playwright install-deps chrome-for-testing 2>&1 | tail -3; then
        ok "Playwright システム依存パッケージをインストールしました"
    else
        warn "Playwright システム依存パッケージのインストールに失敗しました（後で手動で実行してください）"
        warn "  sudo npx playwright install-deps chrome-for-testing"
    fi

    # Ansible + community.vmware コレクション
    section "Ansible"
    if command -v ansible &>/dev/null; then
        ok "Ansible はインストール済みです: $(ansible --version | head -1)"
    else
        info "Ansible をインストール中..."
        sudo apt-get install -y ansible
        ok "Ansible をインストールしました"
    fi
    info "community.vmware コレクションをインストール中..."
    if ansible-galaxy collection install community.vmware --upgrade 2>&1 | tail -3; then
        ok "community.vmware コレクションをインストールしました"
    else
        warn "community.vmware のインストールに失敗しました（後で手動で実行してください）"
        warn "  ansible-galaxy collection install community.vmware --upgrade"
    fi

    # systemd サービス（毎回ユニットを再生成。ポートは .env の APP_PORT を単一ソースとする）
    section "systemd サービス登録"
    CURRENT_USER="$(whoami)"
    # 注: \${APP_PORT} は systemd が EnvironmentFile(.env) から展開する（ここでは bash 展開させない）。
    #     Environment を先に置き、未設定時の既定 8000 を EnvironmentFile が上書きする。
    sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=AI Code Agent (FastAPI + uvicorn)
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${SCRIPT_DIR}
Environment=APP_PORT=8000
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/uvicorn server:app --host 0.0.0.0 --port \${APP_PORT}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
    sudo systemctl restart "$SERVICE_NAME"
    ok "サービスを登録・起動しました（ポート ${APP_PORT}）"

    # 完了メッセージ
    section "セットアップ完了"
    echo ""
    ok "AI-Codeagent のセットアップが完了しました！"
    echo ""
    echo "  ブラウザで以下にアクセスして設定を行ってください:"
    echo ""
    echo "    http://localhost:${APP_PORT}/setup"
    echo ""
}

# ──────────────────────────────────────────────
# 2) プロキシ設定切り替え
# ──────────────────────────────────────────────
cmd_proxy() {
    require_root "proxy"

    section "プロキシ設定切り替え"

    # 現在の状態
    if [ -f "$PROXY_CONF" ]; then
        CURRENT_MODE="社内モード（Docker プロキシ: あり）"
    else
        CURRENT_MODE="社外モード（Docker プロキシ: なし）"
    fi
    echo "現在の設定: $CURRENT_MODE"
    echo ""
    echo "  1) 社内モード（プロキシあり）に切り替え"
    echo "  2) 社外モード（プロキシなし）に切り替え"
    echo "  3) キャンセル"
    echo ""
    read -rp "選択 [1-3]: " PROXY_CHOICE

    case "$PROXY_CHOICE" in
        1)
            # プロキシURL を .env から読むか手入力
            PROXY_URL=""
            if [ -f "$ENV_FILE" ]; then
                PROXY_URL=$(grep -E "^HTTPS_PROXY=|^https_proxy=" "$ENV_FILE" | head -1 | cut -d'=' -f2-)
            fi

            if [ -z "$PROXY_URL" ]; then
                read -rp "プロキシURL（例: http://10.210.1.23:3128）: " PROXY_URL
            else
                echo "プロキシURL: $PROXY_URL（.env から読み込み）"
            fi

            if [ -z "$PROXY_URL" ]; then
                error "プロキシURLが空です"
                exit 1
            fi

            mkdir -p "$(dirname "$PROXY_CONF")"
            cat > "$PROXY_CONF" << EOF
[Service]
Environment="HTTP_PROXY=${PROXY_URL}"
Environment="HTTPS_PROXY=${PROXY_URL}"
Environment="NO_PROXY=localhost,127.0.0.1,192.168.0.0/16,172.16.0.0/12,10.0.0.0/8"
EOF
            ok "$PROXY_CONF を作成しました"
            systemctl daemon-reload
            systemctl restart docker
            ok "Docker を再起動しました"
            echo ""
            ok "社内モードに切り替えました（プロキシ: $PROXY_URL）"
            ;;

        2)
            if [ -f "$PROXY_CONF" ]; then
                rm -f "$PROXY_CONF"
                ok "$PROXY_CONF を削除しました"
                systemctl daemon-reload
                systemctl restart docker
                ok "Docker を再起動しました"
            else
                warn "Docker プロキシ設定はすでに存在しません"
            fi
            echo ""
            warn ".env のプロキシ設定も確認してください"
            echo "  社外では以下を空にする必要があります:"
            echo "  HTTP_PROXY= / HTTPS_PROXY= / http_proxy= / https_proxy="
            echo ""
            ok "社外モードに切り替えました"
            ;;

        *)
            info "キャンセルしました"
            ;;
    esac
}

# ──────────────────────────────────────────────
# 3) サービス操作
# ──────────────────────────────────────────────
cmd_service() {
    section "サービス操作"
    echo "  1) 起動"
    echo "  2) 停止"
    echo "  3) 再起動"
    echo "  4) 状態確認"
    echo "  5) キャンセル"
    echo ""
    read -rp "選択 [1-5]: " SVC_CHOICE

    case "$SVC_CHOICE" in
        1) sudo systemctl start "$SERVICE_NAME"   && ok "起動しました" ;;
        2) sudo systemctl stop "$SERVICE_NAME"    && ok "停止しました" ;;
        3) sudo systemctl restart "$SERVICE_NAME" && ok "再起動しました" ;;
        4) systemctl status "$SERVICE_NAME" ;;
        *) info "キャンセルしました" ;;
    esac
}

# ──────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────

# 引数で直接サブコマンド指定も可能
# 例: ./setup.sh proxy → プロキシ切り替えへ直行
case "${1:-}" in
    proxy)   cmd_proxy ;;
    service) cmd_service ;;
    install) cmd_setup ;;
    *)
        # 引数なし → メニュー表示
        show_menu
        case "$MENU_CHOICE" in
            1) cmd_setup ;;
            2) cmd_proxy ;;
            3) cmd_service ;;
            *) info "終了します" ;;
        esac
        ;;
esac
