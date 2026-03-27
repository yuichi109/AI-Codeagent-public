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

    # .env 作成（空ファイル・上書きなし）
    # APIキー等の設定はブラウザ（/setup）で行う
    if [ ! -f "$ENV_FILE" ]; then
        touch "$ENV_FILE"
        ok ".env を作成しました（空ファイル）"
    else
        ok ".env はすでに存在します（スキップ）"
    fi

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

    # bubblewrap（自動インストール）
    section "bubblewrap（サンドボックス）"
    if command -v bwrap &>/dev/null; then
        ok "bubblewrap はインストール済みです: $(bwrap --version 2>&1 | head -1)"
    else
        info "bubblewrap をインストール中..."
        sudo apt-get install -y bubblewrap
        ok "bubblewrap をインストールしました"
    fi

    # systemd サービス（自動登録）
    section "systemd サービス登録"
    if systemctl is-enabled "$SERVICE_NAME" &>/dev/null 2>&1; then
        ok "サービスはすでに登録済みです"
        sudo systemctl restart "$SERVICE_NAME"
        ok "サービスを再起動しました"
    else
        CURRENT_USER="$(whoami)"
        sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=AI Code Agent (FastAPI + uvicorn)
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${VENV_DIR}/bin/uvicorn server:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5
EnvironmentFile=${ENV_FILE}

[Install]
WantedBy=multi-user.target
EOF
        sudo systemctl daemon-reload
        sudo systemctl enable "$SERVICE_NAME"
        sudo systemctl start "$SERVICE_NAME"
        ok "サービスを登録・起動しました"
    fi

    # 完了メッセージ
    section "セットアップ完了"
    echo ""
    ok "AI-Codeagent のセットアップが完了しました！"
    echo ""
    echo "  ブラウザで以下にアクセスして設定を行ってください:"
    echo ""
    echo "    http://localhost:8000/setup"
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
