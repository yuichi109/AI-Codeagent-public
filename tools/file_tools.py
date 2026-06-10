import re
from pathlib import Path
from datetime import datetime
from config import ALLOWED_WORK_DIR, ALLOWED_WORK_DIRS, APP_PORT


def _is_under_allowed_dir(target: Path) -> bool:
    """target がいずれかの許可ディレクトリの配下にあるか判定する。"""
    target_str = str(target)
    return any(target_str.startswith(str(d)) for d in ALLOWED_WORK_DIRS)


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
    p = Path(path)
    if p.is_absolute():
        target = p.resolve()
    else:
        # 相対パスはデフォルト作業ディレクトリ基準で解決
        normalized = _normalize_path(path)
        target = (ALLOWED_WORK_DIR / normalized).resolve()
    if not _is_under_allowed_dir(target):
        dirs = ", ".join(str(d) for d in ALLOWED_WORK_DIRS)
        raise PermissionError(f"アクセス禁止: '{path}' は許可された作業ディレクトリ外です\n許可: {dirs}")
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
    # docker-compose ファイルでエージェントサーバーのポート使用を禁止
    import re
    if re.search(r'docker-compose', path) and re.search(rf'["\']{APP_PORT}:', content):
        return {"error": f"ポート{APP_PORT}はエージェントサーバー(uvicorn)が使用中のため使用禁止です。8080など別のポートを使用してください。"}
    try:
        target = _resolve_safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_mode = "a" if mode == "append" else "w"
        # ヌルバイト等の不正な制御文字を除去（\n \r \t は保持）
        clean_content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', content)
        target.write_text(clean_content, encoding="utf-8") if write_mode == "w" else open(target, "a", encoding="utf-8").write(clean_content)
        removed = len(content) - len(clean_content)
        note = f"（制御文字 {removed} バイトを除去）" if removed > 0 else ""
        return {"message": f"{path} に書き込みました{note}", "path": str(target), "size": len(clean_content)}
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


def _count_files(directory: Path) -> int:
    """ディレクトリ配下のファイル数を再帰的に数える。"""
    return sum(1 for p in directory.rglob("*") if p.is_file())


def copy_file(src: str, dst: str) -> dict:
    """ファイル/ディレクトリをコピーします。コピー先に同名がある場合は上書き（ディレクトリはマージ）します。"""
    import shutil as _shutil
    try:
        src_path = _resolve_safe_path(src)
        dst_path = _resolve_safe_path(dst)
        if not src_path.exists():
            return {"error": f"コピー元が見つかりません: {src}"}
        if src_path.is_dir():
            # ディレクトリ丸ごとコピー（既存ディレクトリにはマージ・同名ファイル上書き）
            existed = dst_path.exists()
            n = _count_files(src_path)
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            _shutil.copytree(str(src_path), str(dst_path), dirs_exist_ok=True)
            if existed:
                msg = f"ディレクトリ {src} → {dst} に{n}個のファイルをマージコピーしました（同名は上書き）"
            else:
                msg = f"ディレクトリ {src} → {dst} に{n}個のファイルをコピーしました"
            return {"message": msg, "src": str(src_path), "dst": str(dst_path),
                    "is_dir": True, "file_count": n, "overwritten": existed}
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        overwritten = dst_path.exists()
        _shutil.copy2(str(src_path), str(dst_path))
        msg = f"{src} → {dst} に上書きコピーしました" if overwritten else f"{src} → {dst} にコピーしました"
        return {"message": msg, "src": str(src_path), "dst": str(dst_path), "overwritten": overwritten}
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"コピーエラー: {e}"}


def move_file(src: str, dst: str) -> dict:
    """ファイル/ディレクトリを移動（リネーム）します。同名ファイルは上書き。ディレクトリは移動先が空きの場合のみ。"""
    import shutil as _shutil
    try:
        src_path = _resolve_safe_path(src)
        dst_path = _resolve_safe_path(dst)
        if not src_path.exists():
            return {"error": f"移動元が見つかりません: {src}"}
        if src_path.is_dir():
            # ディレクトリ丸ごと移動。マージ未対応なので移動先が既存ならエラー（誤ったネスト・破壊を防ぐ）
            if dst_path.exists():
                return {"error": f"移動先に同名のディレクトリ/ファイルが既に存在します: {dst}\n"
                                 f"ディレクトリのマージ移動は未対応です。別名を指定するか、先に移動先を削除してください。"}
            n = _count_files(src_path)
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            _shutil.move(str(src_path), str(dst_path))
            msg = f"ディレクトリ {src} → {dst} に{n}個のファイルを移動しました"
            return {"message": msg, "src": str(src_path), "dst": str(dst_path),
                    "is_dir": True, "file_count": n, "overwritten": False}
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        overwritten = dst_path.exists()
        _shutil.move(str(src_path), str(dst_path))
        msg = f"{src} → {dst} に上書き移動しました" if overwritten else f"{src} → {dst} に移動しました"
        return {"message": msg, "src": str(src_path), "dst": str(dst_path), "overwritten": overwritten}
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"移動エラー: {e}"}


def delete_file(path: str) -> dict:
    """ファイル/ディレクトリを削除します。ディレクトリは配下を再帰的に削除します。承認フロー対象。"""
    import shutil as _shutil
    try:
        target = _resolve_safe_path(path)
        if not target.exists():
            return {"error": f"削除対象が見つかりません: {path}"}
        # 許可ディレクトリのルート自体（workspace 等）の削除は禁止
        if any(target == d for d in ALLOWED_WORK_DIRS):
            return {"error": f"作業ディレクトリのルート自体は削除できません: {path}"}
        if target.is_dir():
            n = _count_files(target)
            _shutil.rmtree(str(target))
            return {"message": f"ディレクトリ {path} を削除しました（{n}個のファイル）",
                    "path": str(target), "is_dir": True, "file_count": n}
        target.unlink()
        return {"message": f"{path} を削除しました", "path": str(target), "is_dir": False}
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"削除エラー: {e}"}


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
