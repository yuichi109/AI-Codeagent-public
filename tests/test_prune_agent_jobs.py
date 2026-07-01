"""合成テスト: _prune_agent_jobs が直近 keep(=20) 件だけ残し古いジョブを消すか確認。
実LLM・トークン不要。テスト専用スコープ _PRUNETEST を使い、終了時に丸ごと掃除する。"""
import os
import sys
import time
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server
from config import ALLOWED_WORK_DIR

SCOPE = "_PRUNETEST"
base = ALLOWED_WORK_DIR / ".agent-jobs" / SCOPE


def setup_dirs(n: int):
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    now = time.time()
    ids = []
    for i in range(n):
        d = base / f"job{i:02d}"
        d.mkdir()
        (d / "tasks.json").write_text("{}", encoding="utf-8")
        # 古い→新しい順に mtime を付与（i が大きいほど新しい）
        mt = now - (n - i) * 100
        os.utime(d, (mt, mt))
        ids.append(d.name)
    return ids


def main():
    try:
        ids = setup_dirs(25)
        assert len(list(base.iterdir())) == 25, "前提: 25件作成"

        server._prune_agent_jobs(SCOPE)  # keep=20

        remaining = sorted(p.name for p in base.iterdir() if p.is_dir())
        expected = sorted(ids[-20:])  # 新しい順の直近20件
        assert len(remaining) == 20, f"件数NG: {len(remaining)} != 20"
        assert remaining == expected, f"残った中身NG\n残: {remaining}\n期待: {expected}"
        # 一番古い5件が消えていること
        for old in ids[:5]:
            assert old not in remaining, f"古い {old} が残っている"

        print("PASS: 25件 -> 20件、古い5件(job00..job04)が削除、新しい20件が残存")

        # keep=0 のとき全削除
        setup_dirs(3)
        server._prune_agent_jobs(SCOPE, keep=0)
        assert not any(base.iterdir()), "keep=0 で全削除されていない"
        print("PASS: keep=0 で全削除")

        # 存在しないスコープでも例外を出さない
        server._prune_agent_jobs("_NO_SUCH_SCOPE_XYZ")
        print("PASS: 存在しないスコープでも安全（例外なし）")
    finally:
        shutil.rmtree(base, ignore_errors=True)
        print("cleanup: テストスコープ削除")


if __name__ == "__main__":
    main()
