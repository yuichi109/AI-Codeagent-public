import json
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path
from config import ALLOWED_WORK_DIR

BACKUP_DIR = Path.home() / "Backups"

PROTECTED_LIST_FILE = ALLOWED_WORK_DIR / ".protected.json"

# 常に保護するエントリ（ユーザーが保護リストに書かなくても削除されない）
ALWAYS_PROTECTED = {".protected.json", ".git", ".agent_todos.json"}


def protected_list_read() -> dict:
    """ワークスペースの保護リストを読み取る"""
    if not PROTECTED_LIST_FILE.exists():
        return {"paths": [], "message": "保護リストはまだ作成されていません。protected_list_update で作成できます。"}
    try:
        data = json.loads(PROTECTED_LIST_FILE.read_text(encoding="utf-8"))
        paths = data.get("paths", [])
        return {"paths": paths, "count": len(paths)}
    except Exception as e:
        return {"error": str(e), "paths": []}


def protected_list_update(paths: list) -> dict:
    """
    ワークスペースの保護リストに paths を追加する（既存エントリは保持）。
    paths には workspace 直下のファイル名 / ディレクトリ名を指定する。
    例: ["myproject/", "important.txt", "data/"]
    完全に置き換えたい場合は protected_list_replace を使う。
    """
    try:
        # 既存リストを読み込んでマージ
        existing = []
        if PROTECTED_LIST_FILE.exists():
            try:
                existing = json.loads(PROTECTED_LIST_FILE.read_text(encoding="utf-8")).get("paths", [])
            except Exception:
                existing = []
        # 重複除去・順序保持（既存 → 新規の順）
        merged = list(dict.fromkeys(existing + [str(p) for p in paths]))
        PROTECTED_LIST_FILE.write_text(
            json.dumps({"paths": merged}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "paths": merged,
            "count": len(merged),
            "message": f"保護リストを更新しました（{len(merged)}件）",
        }
    except Exception as e:
        return {"error": str(e)}


def protected_list_replace(paths: list) -> dict:
    """
    ワークスペースの保護リストを完全に置き換える。
    既存エントリをすべて削除して paths で上書きしたい場合のみ使う。
    """
    try:
        clean_paths = list(dict.fromkeys(str(p) for p in paths))
        PROTECTED_LIST_FILE.write_text(
            json.dumps({"paths": clean_paths}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "paths": clean_paths,
            "count": len(clean_paths),
            "message": f"保護リストを置き換えました（{len(clean_paths)}件）",
        }
    except Exception as e:
        return {"error": str(e)}


def workspace_cleanup_preview() -> dict:
    """
    workspace 直下のファイル/ディレクトリを走査し、
    保護リストに含まれないものを削除対象として返す。
    実際の削除は行わない（ユーザー確認後に /workspace/cleanup で実行）。
    """
    try:
        # 保護リストを読み込む
        if PROTECTED_LIST_FILE.exists():
            data = json.loads(PROTECTED_LIST_FILE.read_text(encoding="utf-8"))
            user_protected = set(data.get("paths", []))
        else:
            user_protected = set()

        # 常時保護 + ユーザー指定保護をマージ
        protected_names = ALWAYS_PROTECTED | user_protected

        to_delete = []
        protected_found = []

        for entry in sorted(ALLOWED_WORK_DIR.iterdir()):
            name = entry.name
            is_dir = entry.is_dir()

            # 保護判定（末尾スラッシュあり・なし両方を照合）
            if name in protected_names or (name + "/") in protected_names:
                protected_found.append({
                    "name": name,
                    "type": "dir" if is_dir else "file",
                })
            else:
                size = _calc_size(entry)
                to_delete.append({
                    "name": name,
                    "type": "dir" if is_dir else "file",
                    "size_str": _fmt_size(size),
                })

        return {
            "to_delete": to_delete,
            "protected": protected_found,
            "to_delete_count": len(to_delete),
            "message": (
                f"{len(to_delete)}個のアイテムが削除対象です"
                f"（保護済み: {len(protected_found)}個）"
            ),
        }
    except Exception as e:
        return {"error": str(e)}


def workspace_backup() -> dict:
    """
    ワークスペースの内容を ~/Backups/YYYYMMDD.tar.gz にバックアップする。
    同日に複数回実行した場合は YYYYMMDD_1.tar.gz のように連番を付ける。
    """
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        # 同日の連番処理
        dest = BACKUP_DIR / f"{date_str}.tar.gz"
        counter = 1
        while dest.exists():
            dest = BACKUP_DIR / f"{date_str}_{counter}.tar.gz"
            counter += 1

        with tarfile.open(dest, "w:gz") as tar:
            tar.add(ALLOWED_WORK_DIR, arcname="workspace")

        size = dest.stat().st_size
        return {
            "path": str(dest),
            "size_str": _fmt_size(size),
            "message": f"バックアップを作成しました: {dest.name} ({_fmt_size(size)})",
        }
    except Exception as e:
        return {"error": str(e)}


def _calc_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    except Exception:
        pass
    return total


def _fmt_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
