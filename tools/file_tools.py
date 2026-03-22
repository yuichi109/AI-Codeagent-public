import re
from pathlib import Path
from datetime import datetime
from config import ALLOWED_WORK_DIR


def _normalize_path(path: str) -> str:
    """'workspace' のように ALLOWED_WORK_DIR 自体を指す名前を '.' に正規化する。"""
    p = Path(path)
    # 絶対パスで ALLOWED_WORK_DIR と一致する場合も '.' 扱い
    if p.resolve() == ALLOWED_WORK_DIR:
        return "."
    # 先頭セグメントが ALLOWED_WORK_DIR の名前と一致する場合は除去
    parts = p.parts
    if parts and parts[0] == ALLOWED_WORK_DIR.name:
        remaining = Path(*parts[1:]) if len(parts) > 1 else Path(".")
        return str(remaining)
    return path


def _resolve_safe_path(path: str) -> Path:
    path = _normalize_path(path)
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
    # docker-compose ファイルでポート8000の使用を禁止
    import re
    if re.search(r'docker-compose', path) and re.search(r'["\']8000:', content):
        return {"error": "ポート8000はエージェントサーバー(uvicorn)が使用中のため使用禁止です。8080や8001など別のポートを使用してください。"}
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


def edit_file(path: str, old_str: str, new_str: str, expected_replacements: int = 1) -> dict:
    """ファイル内の特定文字列を置換します。old_str が一意でない場合はエラーを返します。"""
    try:
        target = _resolve_safe_path(path)
        if not target.exists():
            return {"error": f"ファイルが見つかりません: {path}"}
        content = target.read_text(encoding="utf-8")
        count = content.count(old_str)
        if count == 0:
            return {"error": f"置換対象の文字列が見つかりません。old_str を確認してください。"}
        if count != expected_replacements:
            return {
                "error": f"置換対象が {count} 箇所見つかりましたが、expected_replacements={expected_replacements} と一致しません。"
                         f" old_str をより具体的にするか、expected_replacements を {count} に設定してください。"
            }
        new_content = content.replace(old_str, new_str, expected_replacements)
        target.write_text(new_content, encoding="utf-8")
        return {
            "message": f"{path} を編集しました ({count} 箇所置換)",
            "path": str(target),
            "replacements": count,
        }
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"編集エラー: {e}"}


def glob_files(pattern: str, path: str = ".") -> dict:
    """glob パターンでファイルパスを検索します。** で再帰検索できます。"""
    try:
        base = _resolve_safe_path(path)
        if not base.is_dir():
            return {"error": f"ディレクトリが見つかりません: {path}"}
        matches = []
        for p in sorted(base.glob(pattern))[:500]:
            if p.is_file():
                matches.append(str(p.relative_to(ALLOWED_WORK_DIR)))
        return {"files": matches, "total": len(matches), "pattern": pattern}
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"glob エラー: {e}"}


def grep(pattern: str, path: str = ".", file_pattern: str = "**/*",
         case_sensitive: bool = True, max_results: int = 100) -> dict:
    """ファイル内容を正規表現で検索し、マッチした行をファイルパス・行番号付きで返します。"""
    try:
        base = _resolve_safe_path(path)
        if not base.is_dir():
            return {"error": f"ディレクトリが見つかりません: {path}"}
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return {"error": f"正規表現エラー: {e}"}

        results = []
        for p in sorted(base.glob(file_pattern)):
            if not p.is_file():
                continue
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for lineno, line in enumerate(lines, 1):
                if regex.search(line):
                    results.append({
                        "file": str(p.relative_to(ALLOWED_WORK_DIR)),
                        "line": lineno,
                        "content": line,
                    })
                    if len(results) >= max_results:
                        return {
                            "matches": results,
                            "total": len(results),
                            "truncated": True,
                            "message": f"結果が {max_results} 件を超えたため打ち切りました",
                        }
        return {"matches": results, "total": len(results), "truncated": False}
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"grep エラー: {e}"}


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
                })
            except OSError:
                continue

        lines = [str(target)]
        for i, item in enumerate(items):
            is_last = (i == len(items) - 1)
            prefix = "└── " if is_last else "├── "
            name = item["path"].split("/")[-1]
            if item["type"] == "directory":
                lines.append(f"{prefix}{name}/")
            else:
                size = f" ({item['size']:,}B)" if item['size'] is not None else ""
                lines.append(f"{prefix}{name}{size}")

        return "\n".join(lines)
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"一覧取得エラー: {e}"}
