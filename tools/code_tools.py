import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from config import ALLOWED_WORK_DIR


def _resolve_safe_path(path: str) -> Path:
    target = (ALLOWED_WORK_DIR / path).resolve()
    if not str(target).startswith(str(ALLOWED_WORK_DIR)):
        raise PermissionError(f"アクセス禁止: '{path}' は作業ディレクトリ外です")
    return target


def code_lint(file_path: str = None, code: str = None, language: str = None) -> dict:
    if not file_path and not code:
        return {"error": "file_path または code のいずれかを指定してください"}

    # 言語の推測
    if not language and file_path:
        ext = Path(file_path).suffix.lower()
        language = {"py": "python", ".py": "python", ".js": "javascript", ".ts": "typescript"}.get(ext, "python")
    language = language or "python"

    if language == "python":
        return _lint_python(file_path, code)
    elif language in ("javascript", "typescript"):
        return _lint_js(file_path, code, language)
    else:
        return {"error": f"非対応の言語: {language}"}


def _lint_python(file_path: str = None, code: str = None) -> dict:
    if not shutil.which("ruff"):
        return {"error": "ruff がインストールされていません。`pip install ruff` を実行してください"}

    tmp_path = None
    try:
        if code:
            tmp = tempfile.NamedTemporaryFile(
                suffix=".py", dir=str(ALLOWED_WORK_DIR), delete=False, mode="w", encoding="utf-8"
            )
            tmp.write(code)
            tmp.close()
            tmp_path = Path(tmp.name)
            target_path = str(tmp_path)
        else:
            target_path = str(_resolve_safe_path(file_path))

        result = subprocess.run(
            ["ruff", "check", target_path, "--output-format=json"],
            capture_output=True, text=True, timeout=30, shell=False,
        )

        issues = []
        if result.stdout.strip():
            raw = json.loads(result.stdout)
            for item in raw:
                issues.append({
                    "line": item.get("location", {}).get("row"),
                    "column": item.get("location", {}).get("column"),
                    "severity": "error" if item.get("fix") is None else "warning",
                    "code": item.get("code"),
                    "message": item.get("message"),
                })

        errors = sum(1 for i in issues if i["severity"] == "error")
        warnings = sum(1 for i in issues if i["severity"] == "warning")
        return {
            "issues": issues,
            "summary": f"{errors} errors, {warnings} warnings",
            "tool_used": "ruff",
            "passed": len(issues) == 0,
        }
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"lint エラー: {e}"}
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()


def _lint_js(file_path: str = None, code: str = None, language: str = "javascript") -> dict:
    if not shutil.which("eslint"):
        return {"error": "eslint がインストールされていません。`npm install -g eslint` を実行してください"}

    tmp_path = None
    ext = ".ts" if language == "typescript" else ".js"
    try:
        if code:
            tmp = tempfile.NamedTemporaryFile(
                suffix=ext, dir=str(ALLOWED_WORK_DIR), delete=False, mode="w", encoding="utf-8"
            )
            tmp.write(code)
            tmp.close()
            tmp_path = Path(tmp.name)
            target_path = str(tmp_path)
        else:
            target_path = str(_resolve_safe_path(file_path))

        result = subprocess.run(
            ["eslint", "--format=json", target_path],
            capture_output=True, text=True, timeout=30, shell=False,
        )

        issues = []
        if result.stdout.strip():
            raw = json.loads(result.stdout)
            for file_result in raw:
                for msg in file_result.get("messages", []):
                    issues.append({
                        "line": msg.get("line"),
                        "column": msg.get("column"),
                        "severity": "error" if msg.get("severity") == 2 else "warning",
                        "code": msg.get("ruleId"),
                        "message": msg.get("message"),
                    })

        errors = sum(1 for i in issues if i["severity"] == "error")
        warnings = sum(1 for i in issues if i["severity"] == "warning")
        return {
            "issues": issues,
            "summary": f"{errors} errors, {warnings} warnings",
            "tool_used": "eslint",
            "passed": errors == 0,
        }
    except Exception as e:
        return {"error": f"lint エラー: {e}"}
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()
