import base64
import re
import shutil
import subprocess
from pathlib import Path

from config import ALLOWED_WORK_DIR


def _find_manim() -> str:
    """manim コマンドのフルパスを返す。見つからなければ 'manim' を返す（エラーは実行時に出る）"""
    found = shutil.which("manim")
    if found:
        return found
    # ユーザーローカルインストール（pip install --user）
    local_bin = Path.home() / ".local" / "bin" / "manim"
    if local_bin.exists():
        return str(local_bin)
    return "manim"

MANIM_TIMEOUT = 120  # seconds


def render_manim(
    code: str = None,
    file_path: str = None,
    scene_name: str = None,
    quality: str = "l",
) -> dict:
    """
    Manim コードをレンダリングして最終フレームの PNG を返す。

    Args:
        code:       Manim Python コード（直接渡す場合）
        file_path:  workspace 内の .py ファイルパス（code と排他）
        scene_name: レンダリングするシーン名（省略時は自動検出）
        quality:    l=低画質/高速, m=中画質, h=高画質
    """
    work_dir = ALLOWED_WORK_DIR

    # ── スクリプトを準備 ──────────────────────────────────────
    if code:
        script_path = work_dir / "_manim_render_temp.py"
        script_path.write_text(code, encoding="utf-8")
    elif file_path:
        script_path = (work_dir / file_path).resolve()
        if not str(script_path).startswith(str(work_dir.resolve())):
            return {"error": "パストラバーサル検出"}
        if not script_path.exists():
            return {"error": f"ファイルが見つかりません: {file_path}"}
    else:
        return {"error": "code または file_path のどちらかが必要です"}

    # ── シーン名を自動検出 ────────────────────────────────────
    if not scene_name:
        content = script_path.read_text(encoding="utf-8")
        matches = re.findall(r"class\s+(\w+)\s*\(.*?Scene.*?\)", content)
        if not matches:
            return {"error": "Manim の Scene クラスが見つかりません（class XxxScene(Scene): の形式で定義してください）"}
        scene_name = matches[-1]  # 最後に定義されたシーンを使用

    # ── manim render 実行 ─────────────────────────────────────
    media_dir = work_dir / "media"
    cmd = [
        _find_manim(), "render",
        str(script_path),
        scene_name,
        f"-q{quality}",
        "--save_last_frame",          # 最終フレームを PNG として保存
        "--media_dir", str(media_dir),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=MANIM_TIMEOUT,
            cwd=str(work_dir),
        )
    except subprocess.TimeoutExpired:
        return {"error": f"レンダリングタイムアウト（{MANIM_TIMEOUT}秒）"}
    except FileNotFoundError:
        return {
            "error": "manim コマンドが見つかりません。"
                     "pip install manim でインストールしてください。"
        }

    stdout = result.stdout or ""
    stderr = result.stderr or ""

    # ── PNG 出力ファイルを探す ────────────────────────────────
    # Manim CE の出力先: media/images/<ScriptStem>/<SceneName>.png
    # バージョンによってパスが変わるため、最新の PNG を検索
    png_candidates = list(media_dir.glob(f"**/{scene_name}.png")) if media_dir.exists() else []

    if not png_candidates and media_dir.exists():
        # フォールバック: media 以下で最近更新された PNG を探す
        all_pngs = list(media_dir.glob("**/*.png"))
        if all_pngs:
            png_candidates = [max(all_pngs, key=lambda p: p.stat().st_mtime)]

    if not png_candidates:
        return {
            "error": "レンダリング結果の PNG が見つかりません",
            "returncode": result.returncode,
            "stdout": stdout[-1000:],
            "stderr": stderr[-1000:],
        }

    png_path = png_candidates[0]
    image_base64 = base64.b64encode(png_path.read_bytes()).decode("utf-8")

    return {
        "rendered": True,
        "scene_name": scene_name,
        "image_base64": image_base64,
        "mime": "image/png",
        "message": f"✅ {scene_name} をレンダリングしました（{png_path.name}）",
        "stdout": stdout[-500:],
        "stderr": stderr[-500:],
    }
