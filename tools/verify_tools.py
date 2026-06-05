"""保存時の自動構文チェック（検証ループ）。

write_file / edit_file でコードファイルを保存した直後に、ファイル種別に応じた
構文チェックを実行し、その合否をツール結果へ注入する。これにより：

- ユーザーが実行ログを手で貼り付ける作業がなくなる（保存した瞬間に構文エラーが
  エージェントへ自動で突き返される）
- 構文チェッカーが客観的に PASS/FAIL を判定するので、モデルの主観的な往復
  （ダブルクォート↔シングルクォート↔エスケープ）が収束する

実行（run）はせず構文チェックのみ。ネットワーク不要・軽量。
チェッカーのバイナリが無い環境では黙ってスキップ（None を返す）。
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

# 構文チェックの対象になる write 系ツール
_TARGET_TOOLS = {"write_file", "edit_file"}

_CHECK_TIMEOUT = 15


def _run(cmd: list[str]) -> tuple[int, str]:
    """サブプロセスを shell=False で実行し (returncode, stderr+stdout) を返す。"""
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_CHECK_TIMEOUT,
        shell=False,
    )
    detail = (proc.stderr or "").strip() or (proc.stdout or "").strip()
    return proc.returncode, detail


def verify_file_syntax(abs_path: str) -> dict | None:
    """ファイル拡張子に応じた構文チェックを実行する。

    返り値:
        {"ok": bool, "checker": str, "detail": str} … チェックを実行した場合
        None … 対象拡張子でない / チェッカーが無い / 例外が起きた場合（スキップ）
    """
    try:
        path = Path(abs_path)
        if not path.is_file():
            return None
        ext = path.suffix.lower()

        # --- Python: py_compile（pure Python・常に動く）---
        if ext == ".py":
            rc, detail = _run([sys.executable, "-m", "py_compile", str(path)])
            return {
                "ok": rc == 0,
                "checker": "py_compile",
                "detail": "" if rc == 0 else detail[:800],
            }

        # --- JSON: 組込パーサ（即時）---
        if ext == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
                return {"ok": True, "checker": "json", "detail": ""}
            except json.JSONDecodeError as e:
                return {"ok": False, "checker": "json", "detail": str(e)[:800]}

        # --- YAML: PyYAML（無ければスキップ）---
        if ext in (".yml", ".yaml"):
            try:
                import yaml  # type: ignore
            except ImportError:
                return None
            try:
                # 複数ドキュメント（--- 区切り）にも対応
                list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
                return {"ok": True, "checker": "yaml", "detail": ""}
            except yaml.YAMLError as e:
                return {"ok": False, "checker": "yaml", "detail": str(e)[:800]}

        # --- Shell: bash -n（構文のみ・実行しない。Linux のみ）---
        if ext in (".sh", ".bash"):
            bash = shutil.which("bash")
            if not bash:
                return None
            rc, detail = _run([bash, "-n", str(path)])
            return {
                "ok": rc == 0,
                "checker": "bash -n",
                "detail": "" if rc == 0 else detail[:800],
            }

        # --- JavaScript / TypeScript: node --check ---
        if ext in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
            node = shutil.which("node")
            if not node:
                return None
            # node --check は TS を解釈できないため JS 系のみ対象
            if ext in (".ts", ".tsx"):
                return None
            rc, detail = _run([node, "--check", str(path)])
            return {
                "ok": rc == 0,
                "checker": "node --check",
                "detail": "" if rc == 0 else detail[:800],
            }

        return None
    except Exception:
        # 検証の失敗で本処理を止めない
        return None


def augment_tool_result_with_verify(
    name: str, args: dict, result_str: str
) -> tuple[str, dict | None]:
    """write_file / edit_file の結果に構文チェック結果を注入して返す。

    返り値:
        (新しい result_str, UI表示用 verdict or None)
        verdict = {"ok": bool, "checker": str, "detail": str, "path": str}
    """
    if name not in _TARGET_TOOLS:
        return result_str, None

    try:
        result_data = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return result_str, None

    if not isinstance(result_data, dict) or result_data.get("error"):
        return result_str, None

    abs_path = result_data.get("path")
    if not abs_path:
        return result_str, None

    verdict = verify_file_syntax(abs_path)
    if verdict is None:
        return result_str, None

    result_data["syntax_check"] = verdict
    if not verdict["ok"]:
        result_data["note"] = (
            f"⚠️ 構文エラーがあります（{verdict['checker']}）。"
            "read_file で該当ファイルを確認し、エラー箇所を直してから次に進むこと。"
            "同じ修正を繰り返さず、エラーメッセージの行番号・内容に従って修正すること。"
        )

    verdict_for_ui = {**verdict, "path": abs_path}
    return json.dumps(result_data, ensure_ascii=False), verdict_for_ui
