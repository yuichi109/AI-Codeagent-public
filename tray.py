"""
AI Code Agent - タスクトレイ常駐スクリプト
ダブルクリック不要・黒窓なしでエージェントを管理する。
"""
import os
import sys
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).parent
PORT = 8001
URL = f"http://localhost:{PORT}"

# Windows の絵文字フォント候補（Segoe UI Emoji が最優先）
_EMOJI_FONTS = [
    "C:/Windows/Fonts/seguiemj.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
]

_server_proc: subprocess.Popen | None = None
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# アイコン生成
# ---------------------------------------------------------------------------

def _load_emoji_font(size: int) -> ImageFont.FreeTypeFont | None:
    for path in _EMOJI_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return None


def _make_icon(status: str = "running") -> Image.Image:
    """🤖 絵文字アイコンを生成する（256×256 RGBA）"""
    sz = 256
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 背景円: 緑=起動中 / 赤=停止
    bg = (34, 197, 94, 255) if status == "running" else (239, 68, 68, 255)
    draw.ellipse([4, 4, sz - 4, sz - 4], fill=bg)

    # 🤖 絵文字を中央に描画
    emoji = "🤖"
    font = _load_emoji_font(160)
    if font:
        try:
            bbox = draw.textbbox((0, 0), emoji, font=font, embedded_color=True)
            x = (sz - (bbox[2] - bbox[0])) // 2 - bbox[0]
            y = (sz - (bbox[3] - bbox[1])) // 2 - bbox[1]
            draw.text((x, y), emoji, font=font, embedded_color=True)
        except Exception:
            font = None

    if not font:
        # フォールバック: "AI" テキスト
        fb_font = _load_emoji_font(100) or ImageFont.load_default()
        draw.text((sz // 2, sz // 2), "AI", fill="white", font=fb_font, anchor="mm")

    return img


def _make_ico_bytes() -> bytes:
    """複数サイズを含む .ico バイト列を返す"""
    import io
    base = _make_icon("running")
    sizes = [16, 32, 48, 128, 256]
    imgs = [base.resize((s, s), Image.LANCZOS) for s in sizes]
    buf = io.BytesIO()
    imgs[0].save(buf, format="ICO", sizes=[(s, s) for s in sizes],
                 append_images=imgs[1:])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# サーバー管理
# ---------------------------------------------------------------------------

def _no_window_flag() -> int:
    """Windows の CREATE_NO_WINDOW フラグ (黒窓を出さない)"""
    return 0x08000000 if sys.platform == "win32" else 0


def _start_server():
    global _server_proc
    with _lock:
        if _server_proc and _server_proc.poll() is None:
            return
        _server_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app",
             "--host", "0.0.0.0", "--port", str(PORT)],
            cwd=str(BASE_DIR),
            env=os.environ.copy(),
            creationflags=_no_window_flag(),
        )


def _stop_server():
    global _server_proc
    with _lock:
        if _server_proc and _server_proc.poll() is None:
            _server_proc.terminate()
            try:
                _server_proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                _server_proc.kill()
        _server_proc = None


def _is_running() -> bool:
    return _server_proc is not None and _server_proc.poll() is None


# ---------------------------------------------------------------------------
# トレイメニューのアクション
# ---------------------------------------------------------------------------

def on_open(icon, item):
    webbrowser.open(URL)


def on_restart(icon, item):
    icon.icon = _make_icon("stopped")
    icon.title = "AI Code Agent — 再起動中..."
    _stop_server()
    time.sleep(1)
    _start_server()
    icon.icon = _make_icon("running")
    icon.title = f"AI Code Agent — {URL}"


def on_stop(icon, item):
    _stop_server()
    icon.stop()


# ---------------------------------------------------------------------------
# サーバー死活監視（アイコン色を自動更新）
# ---------------------------------------------------------------------------

def _monitor(icon: pystray.Icon):
    prev = None
    while True:
        running = _is_running()
        if running != prev:
            icon.icon = _make_icon("running" if running else "stopped")
            icon.title = (f"AI Code Agent — 起動中 {URL}"
                          if running else "AI Code Agent — 停止中")
            prev = running
        time.sleep(3)


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main():
    _start_server()

    menu = pystray.Menu(
        pystray.MenuItem(f"🌐  ブラウザで開く  ({URL})", on_open, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("🔄  再起動", on_restart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("⏹  停止して終了", on_stop),
    )

    icon = pystray.Icon(
        "ai-code-agent",
        _make_icon("running"),
        f"AI Code Agent — {URL}",
        menu=menu,
    )

    threading.Thread(target=_monitor, args=(icon,), daemon=True).start()

    # 起動後2秒でブラウザを自動オープン
    threading.Timer(2.0, lambda: webbrowser.open(URL)).start()

    icon.run()


if __name__ == "__main__":
    main()
