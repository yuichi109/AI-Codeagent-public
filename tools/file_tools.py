from pathlib import Path
from datetime import datetime
from config import ALLOWED_WORK_DIR


def _resolve_safe_path(path: str) -> Path:
    target = (ALLOWED_WORK_DIR / path).resolve()
    if not str(target).startswith(str(ALLOWED_WORK_DIR)):
        raise PermissionError(f"アクセス禁止: '{path}' は作業ディレクトリ外です")
    return target


def read_file(path: str, encoding: str = "utf-8") -> dict:
    try:
        target = _resolve_safe_path(path)
        content = target.read_text(encoding=encoding)
        return {"content": content, "path": str(target), "size": len(content)}
    except PermissionError as e:
        return {"error": str(e)}
    except FileNotFoundError:
        return {"error": f"ファイルが見つかりません: {path}"}
    except Exception as e:
        return {"error": f"読み込みエラー: {e}"}


def write_file(path: str, content: str, mode: str = "overwrite") -> dict:
    try:
        target = _resolve_safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_mode = "a" if mode == "append" else "w"
        target.write_text(content, encoding="utf-8") if write_mode == "w" else open(target, "a", encoding="utf-8").write(content)
        return {"message": f"{path} に書き込みました", "path": str(target), "size": len(content)}
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"書き込みエラー: {e}"}


def list_files(path: str = ".", pattern: str = "*") -> dict:
    try:
        target = _resolve_safe_path(path)
        if not target.is_dir():
            return {"error": f"ディレクトリが見つかりません: {path}"}

        items = []
        for p in sorted(target.glob(pattern))[:200]:
            try:
                stat = p.stat()
                items.append({
                    "path": str(p.relative_to(ALLOWED_WORK_DIR)),
                    "type": "directory" if p.is_dir() else "file",
                    "size": stat.st_size if p.is_file() else None,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
            except OSError:
                continue

        return {"files": items, "total": len(items), "root": str(target)}
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"一覧取得エラー: {e}"}
