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

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    _err_log = Path(__file__).parent / "server.log"
    _err_log.parent.mkdir(parents=True, exist_ok=True)
    import traceback
    _err_log.open("a", encoding="utf-8").write(
        "[tray] import error:\n" + traceback.format_exc()
    )
    sys.exit(1)

BASE_DIR = Path(__file__).parent


def _resolve_port() -> int:
    """BASE_DIR/.env の APP_PORT を単一ソースとして読む（cwd 非依存・Windows 既定 8001）。
    server プロセスの config.APP_PORT も同じ .env を読むため両者が一致する。"""
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        try:
            text = env_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = env_file.read_text(encoding="cp932", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("APP_PORT=") and "#" not in line.split("=", 1)[0]:
                val = line.partition("=")[2].strip()
                if val.isdigit():
                    return int(val)
    return 8001


PORT = _resolve_port()
URL = f"http://localhost:{PORT}"

# Windows の絵文字フォント候補（Segoe UI Emoji が最優先）
_EMOJI_FONTS = [
    "C:/Windows/Fonts/seguiemj.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
]

_server_proc: subprocess.Popen | None = None
_lock = threading.Lock()
_LOG_FILE = BASE_DIR / "server.log"
_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5MB
_LOG_BACKUP_COUNT = 3


def _rotate_log():
    """起動時にログファイルが上限を超えていたらローテーションする。"""
    if not _LOG_FILE.exists() or _LOG_FILE.stat().st_size < _LOG_MAX_BYTES:
        return
    for i in range(_LOG_BACKUP_COUNT - 1, 0, -1):
        src = _LOG_FILE.with_suffix(f".log.{i}")
        dst = _LOG_FILE.with_suffix(f".log.{i + 1}")
        if src.exists():
            src.replace(dst)
    _LOG_FILE.replace(_LOG_FILE.with_suffix(".log.1"))


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


def _get_python_exe() -> str:
    """venv の python.exe を返す（pythonw.exe では uvicorn が動かないため）"""
    # まず BASE_DIR/venv を優先（tray.py の起動方法に依存しない）
    venv_python = BASE_DIR / "venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    python_exe = Path(sys.executable).parent / "python.exe"
    if python_exe.exists():
        return str(python_exe)
    return sys.executable


def _load_env() -> dict:
    """BASE_DIR/.env を読み込んで現在の環境変数にマージした辞書を返す"""
    env = os.environ.copy()
    # Windows のデフォルト cp932 を回避してサーバープロセスを UTF-8 モードで動かす
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        try:
            text = env_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = env_file.read_text(encoding="cp932", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip()
    return env


_VCREDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"


def _ensure_vcredist():
    """onnxruntime の DLL が読めない場合に VC++ を自動サイレントインストールする。"""
    result = subprocess.run(
        [_get_python_exe(), "-c", "import onnxruntime"],
        capture_output=True,
        creationflags=_no_window_flag(),
    )
    if result.returncode == 0:
        return
    stderr = result.stderr.decode("utf-8", errors="replace").lower()
    if "dll" not in stderr and "dyn" not in stderr:
        return
    with open(_LOG_FILE, "a", encoding="utf-8", buffering=1) as lf:
        lf.write("[tray] VC++ not found, installing...\n")
        import urllib.request, tempfile
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".exe", delete=False)
            tmp.close()
            urllib.request.urlretrieve(_VCREDIST_URL, tmp.name)
            subprocess.run(
                [tmp.name, "/install", "/quiet", "/norestart"],
                creationflags=_no_window_flag(),
            )
            lf.write("[tray] VC++ installed OK\n")
        except Exception as e:
            lf.write(f"[tray] VC++ install failed: {e}\n")


def _sync_requirements():
    """requirements.txt の差分を自動インストールする（起動時・再起動時に実行）"""
    req_file = BASE_DIR / "requirements.txt"
    if not req_file.exists():
        return
    with open(_LOG_FILE, "a", encoding="utf-8", buffering=1) as log_fh:
        log_fh.write("[tray] pip install -r requirements.txt ...\n")
        result = subprocess.run(
            [_get_python_exe(), "-m", "pip", "install", "-r", str(req_file)],
            cwd=str(BASE_DIR),
            creationflags=_no_window_flag(),
            capture_output=True,
            text=True,
        )
        if result.stdout:
            log_fh.write(result.stdout)
        if result.stderr:
            log_fh.write(result.stderr)
        log_fh.write(
            "[tray] pip install 完了\n" if result.returncode == 0
            else f"[tray] pip install 失敗 (code={result.returncode})\n"
        )


def _free_port(port: int):
    """ポートを使用している既存プロセスを終了する。"""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return  # 空きポート、何もしない
    except Exception:
        return
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True,
            creationflags=_no_window_flag(),
        )
        for line in result.stdout.splitlines():
            if f":{port} " in line and "LISTENING" in line:
                pid = int(line.strip().split()[-1])
                if pid > 0:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/F"],
                        capture_output=True, creationflags=_no_window_flag(),
                    )
                    time.sleep(1)
                break
    except Exception:
        pass


def _start_server():
    global _server_proc
    with _lock:
        if _server_proc and _server_proc.poll() is None:
            return
        _free_port(PORT)
        _ensure_vcredist()
        _sync_requirements()
        _rotate_log()
        log_fh = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)
        _server_proc = subprocess.Popen(
            [_get_python_exe(), "-m", "uvicorn", "server:app",
             "--host", "0.0.0.0", "--port", str(PORT)],
            cwd=str(BASE_DIR),
            env=_load_env(),
            stdout=log_fh,
            stderr=log_fh,
            creationflags=_no_window_flag(),
        )


def _stop_server():
    global _server_proc
    with _lock:
        if _server_proc and _server_proc.poll() is None:
            pid = _server_proc.pid
            if sys.platform == "win32":
                # プロセスツリーごと終了（worker 子プロセスも含む）
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(pid)],
                    capture_output=True,
                )
            else:
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


def on_open_log(icon, item):
    import os
    _LOG_FILE.touch(exist_ok=True)
    os.startfile(str(_LOG_FILE))


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
        if not running and prev is True:
            # サーバーが予期せず停止した（setup 保存による os._exit 含む）→ 自動再起動
            time.sleep(1)
            _start_server()
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
    menu = pystray.Menu(
        pystray.MenuItem(f"🌐  ブラウザで開く  ({URL})", on_open, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("🔄  再起動", on_restart),
        pystray.MenuItem("📄  ログを開く", on_open_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("⏹  停止して終了", on_stop),
    )

    icon = pystray.Icon(
        "ai-code-agent",
        _make_icon("stopped"),
        "AI Code Agent — 起動準備中...",
        menu=menu,
    )

    def _wait_for_server(timeout: int = 30, interval: float = 0.5) -> bool:
        import urllib.request
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                urllib.request.urlopen(URL, timeout=2)
                return True
            except Exception:
                time.sleep(interval)
        return False

    def _startup():
        _start_server()
        if _is_running():
            icon.icon = _make_icon("running")
            icon.title = f"AI Code Agent — {URL}"
            _wait_for_server()
            webbrowser.open(URL)

    threading.Thread(target=_startup, daemon=True).start()
    threading.Thread(target=_monitor, args=(icon,), daemon=True).start()

    icon.run()


if __name__ == "__main__":
    main()
