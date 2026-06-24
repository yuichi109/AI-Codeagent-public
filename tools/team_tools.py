"""
協調型マルチエージェント（チーム方式）の土台。
docs/multi-agent-team-design.md / [[project-multi-agent-team]] 参照。

パイプライン方式（tools/multi_agent_tools.py）は無改修で温存し、本モジュールで
「並列＋共有タスクリスト（self-claim/ロック/依存）＋mailbox（直接通信）」を提供する。

設計上のキモ:
- 新ツール（send_message/read_messages/claim_task/complete_task/list_tasks）は
  「呼び出し元 teammate 名」と「job_dir」を知らないと動かない**コンテキスト依存ツール**。
  グローバル登録（server.py/agent_core.py の TOOL_REGISTRY）はせず、teammate ごとに
  job_dir＋名前をクロージャで束ねた executor を作り run ループへ渡す（“専用便箋方式”）。
  → 同時並列で取り違えが起きない。チーム方式は本モジュール内で自己完結し
    [[project-dual-tool-registry]] の対象外（BG/定時から呼ばれない）。
"""
import asyncio
import json
import os
import random
import shutil
import time
from pathlib import Path
from typing import Awaitable, Callable

import config


def _is_rate_limit_error(e: Exception) -> bool:
    """例外が 429（レート制限）かどうかをプロバイダー非依存で判定する。"""
    if e.__class__.__name__ == "RateLimitError":
        return True
    code = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    if code == 429:
        return True
    s = str(e).lower()
    return "429" in s or "too many requests" in s or "rate limit" in s


async def _create_with_backoff(async_client, create_kwargs: dict):
    """chat.completions.create を 429 のとき指数バックオフ＋ジッターで再試行する。
    429 以外の例外、または上限到達時はそのまま送出（呼び出し側のタスクエラー扱いに任せる）。"""
    retries = config.MULTI_AGENT_RATELIMIT_RETRIES
    base = config.MULTI_AGENT_RATELIMIT_BASE_DELAY
    for attempt in range(retries + 1):
        try:
            return await async_client.chat.completions.create(**create_kwargs)
        except Exception as e:
            if not _is_rate_limit_error(e) or attempt == retries:
                raise
            # 並列ワーカーが同時に再試行して再衝突しないようジッターを足す
            delay = min(base * (2 ** attempt), 30.0) + random.uniform(0, base)
            await asyncio.sleep(delay)

# 役割 → teammate 名（mailbox の宛先・team.json のメンバー名に使う）
TEAM_MEMBER_NAMES: dict[str, str] = {
    "research": "researcher",
    "design":   "designer",
    "coding":   "coder",
    "infra":    "infra",
    "debug":    "debugger",
    "security": "security",
    "docs":     "docs",
}


# ----------------------------------------------------------------------------
# ファイルロック（同時 claim の競合防止）
# ----------------------------------------------------------------------------
def _acquire_lock(lock_path: Path, timeout: float = 10.0) -> int:
    """O_CREAT|O_EXCL でロックファイルを作る。取れるまで短くリトライ。"""
    deadline = time.time() + timeout
    while True:
        try:
            return os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.time() > deadline:
                # デッドロック回避: 古いロックを強制解放
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            time.sleep(0.05)


def _release_lock(fd: int, lock_path: Path) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        lock_path.unlink()
    except OSError:
        pass


# ----------------------------------------------------------------------------
# tasks.json（共有タスクリスト）
# ----------------------------------------------------------------------------
def _tasks_path(job_dir: Path) -> Path:
    return job_dir / "tasks.json"


def _read_tasks(job_dir: Path) -> list[dict]:
    p = _tasks_path(job_dir)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _write_tasks(job_dir: Path, tasks: list[dict]) -> None:
    _tasks_path(job_dir).write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def init_team_job(plan: dict, job_dir: Path) -> dict:
    """plan.json から tasks.json / team.json / mailbox ディレクトリを初期化する。"""
    tasks_in = plan.get("tasks", {})
    tasks: list[dict] = []
    for tid, t in tasks_in.items():
        tasks.append({
            "id": tid,
            "role": t.get("role", ""),
            "prompt": t.get("prompt", ""),
            "depends_on": t.get("depends_on", []),
            "files": t.get("files", []),
            "timeout_sec": t.get("timeout_sec"),
            "preset_id": t.get("preset_id"),
            "model": t.get("model"),
            "state": "pending",   # pending | in_progress | completed
            "owner": None,
            "summary": "",
        })
    _write_tasks(job_dir, tasks)

    # メンバー名簿（役割の集合から1役割1名）
    roles = []
    for t in tasks:
        if t["role"] and t["role"] not in roles:
            roles.append(t["role"])
    members = [{"name": TEAM_MEMBER_NAMES.get(r, r), "role": r} for r in roles]
    team = {"members": members, "max_parallel": plan.get("max_parallel", config.MULTI_AGENT_MAX_PARALLEL)}
    (job_dir / "team.json").write_text(json.dumps(team, ensure_ascii=False, indent=2), encoding="utf-8")

    (job_dir / "mailbox").mkdir(parents=True, exist_ok=True)
    return team


def _deps_satisfied(task: dict, by_id: dict[str, dict]) -> bool:
    return all(by_id.get(d, {}).get("state") == "completed" for d in task.get("depends_on", []))


def claim_task(job_dir: Path, member_name: str, role: str | None = None, task_id: str | None = None) -> dict:
    """次に実行可能なタスクを self-claim する（ロック下）。
    role=None なら役割を問わず取る（ワーカープール方式）。role 指定時はその役割のみ。
    返り値: 取れたタスク or {"none": True, "pending_remain": bool}
    """
    lock_path = _tasks_path(job_dir).with_suffix(".json.lock")
    fd = _acquire_lock(lock_path)
    try:
        tasks = _read_tasks(job_dir)
        by_id = {t["id"]: t for t in tasks}

        def _role_ok(t: dict) -> bool:
            return role is None or t.get("role") == role

        candidates = [
            t for t in tasks
            if t["state"] == "pending" and _role_ok(t) and _deps_satisfied(t, by_id)
        ]
        if task_id:
            candidates = [t for t in candidates if t["id"] == task_id]
        if not candidates:
            pending_remain = any(t["state"] == "pending" and _role_ok(t) for t in tasks)
            return {"none": True, "pending_remain": pending_remain}
        task = candidates[0]
        task["state"] = "in_progress"
        task["owner"] = member_name
        _write_tasks(job_dir, tasks)
        return dict(task)
    finally:
        _release_lock(fd, lock_path)


def verify_task_files(job_dir: Path, task: dict, work_dir: Path | None = None) -> list[str]:
    """タスクが宣言した files のうち、未達のもの（存在しない/空・空白のみ）を返す（リードの検収用）。
    「作ったフリ」（0バイト・空白だけのファイル）も未達として弾く。
    work_dir 指定時は相対パスを work_dir 基準で解決する（シングル型マルチ＝既存スコープ作業用。
    既定 None なら job_dir 基準＝従来挙動）。"""
    base = work_dir or job_dir
    missing: list[str] = []
    for f in task.get("files", []):
        p = Path(f)
        if not p.is_absolute():
            p = base / f
        if not p.exists():
            missing.append(f)
            continue
        try:
            if p.is_file() and not p.read_text(encoding="utf-8", errors="ignore").strip():
                missing.append(f)  # 空 or 空白のみ＝実質未作成
        except OSError:
            pass
    return missing


def _read_file_excerpt(path: Path, max_chars: int = 600) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""
    return text if len(text) <= max_chars else text[:max_chars] + "…（以下略）"


def reconcile_declared_files(job_dir: Path, task: dict, work_dir: Path | None = None) -> list[str]:
    """宣言ファイルが宣言パスに無い/空のとき、job_dir 内の同名・非空ファイルを探して宣言パスへ移動する。
    モデルが指定と違う場所（code/ 配下など）に書いてしまっても、機械的に正しい場所へ集約する＝
    **モデルの賢さ・従順さに一切依存しない**保証層。検収（verify_task）の直前に呼ぶ。

    安全策:
    - 既に中身のある宣言ファイルは触らない。
    - 候補は job_dir 内の同名・非空ファイルのうち、**どのタスクの宣言パスにも該当しない**「迷子ファイル」のみ
      （他タスクの正規成果物を奪わない）。
    - 候補が一意（ちょうど1個）のときだけ移動する。0個=正当な未達、複数=曖昧なので動かさない。
    返り値: 実施した移動の説明（"元 → 宣言パス"）リスト。
    work_dir 指定時は成果物の解決・探索を work_dir 基準で行う（tasks.json は job_dir=ctrl から読む）。"""
    wd = work_dir or job_dir
    def _abs(rel_or_abs: str) -> str:
        p = Path(rel_or_abs)
        return str((p if p.is_absolute() else wd / rel_or_abs).resolve())

    # 全タスクの宣言パス（＝正規の置き場所）を集めておき、候補から除外する
    all_declared_abs = set()
    for t in _read_tasks(job_dir):
        for f in t.get("files", []):
            all_declared_abs.add(_abs(f))

    moved: list[str] = []
    for f in task.get("files", []):
        dest = Path(_abs(f))
        if dest.exists() and dest.is_file():
            try:
                if dest.read_text(encoding="utf-8", errors="ignore").strip():
                    continue  # 既に正しい場所に中身あり
            except OSError:
                continue
        base = os.path.basename(f)
        candidates = []
        for cand in wd.rglob(base):
            if not cand.is_file() or "__pycache__" in cand.parts:
                continue
            cand_abs = str(cand.resolve())
            if cand_abs == str(dest.resolve()) or cand_abs in all_declared_abs:
                continue  # 自分自身 or 他タスクの正規成果物
            try:
                if not cand.read_text(encoding="utf-8", errors="ignore").strip():
                    continue
            except OSError:
                continue
            candidates.append(cand)
        if len(candidates) == 1:
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                _src_rel = candidates[0]
                shutil.move(str(candidates[0]), str(dest))
                try:
                    _rel = _src_rel.relative_to(wd)
                except ValueError:
                    _rel = _src_rel
                moved.append(f"{_rel} → {f}")
            except OSError:
                pass
    return moved


def verify_task(job_dir: Path, task: dict, work_dir: Path | None = None) -> list[str]:
    """タスクの検収。問題点を人間可読の文字列リストで返す（空＝合格）。
    ①宣言ファイルの有無/空チェック ②役割別の中身チェック（debug役のテスト合否など）。
    差し戻し時、返した文字列をそのまま teammate に渡して新情報（具体的なエラー）を与える。
    work_dir 指定時は成果物を work_dir 基準で検収する（既定 None なら job_dir 基準＝従来挙動）。"""
    base = work_dir or job_dir
    problems: list[str] = []

    missing = verify_task_files(job_dir, task, work_dir=work_dir)
    if missing:
        problems.append("未作成または空のファイル: " + ", ".join(missing))

    role = task.get("role", "")
    if role == "debug":
        # test-result.md がFAIL/不合格を示しているなら、その抜粋を添えて差し戻す
        tr = base / "test-result.md"
        if tr.exists():
            text = tr.read_text(encoding="utf-8", errors="ignore")
            low = text.lower()
            failed = ("fail" in low or "不合格" in text) and not (
                "pass" in low or "合格" in text or "成功" in text
            )
            if failed:
                problems.append(
                    "テストが不合格（test-result.md）。該当箇所:\n" + _read_file_excerpt(tr)
                )
    return problems


def find_entry_html(job_dir: Path) -> Path | None:
    """成果物の入口 index.html を探す（job直下を優先、無ければ最初に見つかったもの）。"""
    direct = job_dir / "index.html"
    if direct.exists():
        return direct
    for cand in sorted(job_dir.rglob("index.html")):
        if not any(seg in (".agent-jobs", ".team", "jobs", "__pycache__", ".git") for seg in cand.parts):
            return cand
    return None


def browser_smoke_test(html_path: Path, timeout_ms: int = 8000) -> list[str]:
    """index.html を実ブラウザ（Playwright/Chromium）で開いて致命的エラーを返す＝モデル非依存の検収。
    JSの構文エラー・モジュール不整合・グローバル衝突（already been declared）・読み込み失敗などを、
    実際にブラウザで実行して検出する。playwright/chromium が無い環境では検査をスキップ（[] を返す）。
    返り値: 問題点リスト（空＝ブラウザで正常に読み込めた）。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return []  # 検査不能な環境では誤検知で差し戻さない
    errs: list[str] = []

    def _on_console(m):
        # console.error を拾う（type=module + file:// の CORS ブロックや、jsの読み込み失敗はここに出る）。
        # favicon 等の無害なリソース404は除外して誤検知を防ぐ。
        try:
            if m.type == "error":
                t = m.text or ""
                if "favicon" not in t.lower():
                    errs.append(t)
        except Exception:
            pass

    def _on_requestfailed(req):
        # .js/.css/.html の読み込み失敗（パス間違い等）を検出。favicon は無視。
        try:
            url = req.url or ""
            if "favicon" in url.lower():
                return
            if url.split("?")[0].endswith((".js", ".css", ".mjs")):
                errs.append(f"リソース読み込み失敗: {url}")
        except Exception:
            pass

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception:
                return []  # ブラウザ未導入ならスキップ
            page = browser.new_page()
            # pageerror = 未捕捉のJS例外（SyntaxError/ReferenceError等）。console.error = CORS/モジュール読込失敗。
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.on("console", _on_console)
            page.on("requestfailed", _on_requestfailed)
            try:
                page.goto(html_path.resolve().as_uri(), timeout=timeout_ms)
                page.wait_for_timeout(600)
            finally:
                browser.close()
    except Exception as e:
        return [f"ブラウザでの読み込みに失敗: {type(e).__name__}: {e}"]
    uniq = list(dict.fromkeys(errs))
    if uniq:
        return ["ブラウザ実行時にエラー: " + " / ".join(uniq[:5])]
    return []


# 明らかに破壊的・システム変更・他ホスト接続のコマンド（チェックON でも実行せず静的検証に格下げ）
_DESTRUCTIVE_RE = __import__("re").compile(
    r"(?:^|[\s|;&])(?:sudo|su|apt|apt-get|yum|dnf|pacman|zypper|snap|systemctl|service|"
    r"mkfs\w*|dd|fdisk|parted|useradd|userdel|usermod|groupadd|passwd|reboot|shutdown|"
    r"poweroff|halt|mount|umount|iptables|nft|ufw|crontab|ssh|scp|rsync|chown|chpasswd|"
    r"mkswap|swapon|modprobe|insmod|update-grub|grub-install)\b"
    r"|rm\s+-[a-zA-Z]*[rf]"           # rm -rf / rm -f 等
    r"|\b(?:pip3?|npm|gem|cargo|pipx)\s+install\b.*(?:-g|--global)"
    r"|(?:curl|wget)\b[^|]*\|\s*(?:sudo\s+)?(?:bash|sh)"   # curl ... | bash
    r"|>\s*/(?:etc|usr|var|boot|sys|proc|dev|bin|sbin)\b", __import__("re").I,
)


def _is_destructive(cmd: str) -> bool:
    return bool(_DESTRUCTIVE_RE.search(cmd or ""))


def _script_is_destructive(args: list[str], job_dir: Path) -> bool:
    """コマンドが実行しようとしているスクリプト（.sh/.bash）の中身も走査する。
    `bash setup.sh` のようにコマンド行は無害でも、中身が apt/systemctl 等なら実行させない。"""
    for a in args:
        if a.endswith((".sh", ".bash")):
            p = Path(a) if os.path.isabs(a) else job_dir / a
            try:
                if _DESTRUCTIVE_RE.search(p.read_text(encoding="utf-8", errors="ignore")):
                    return True
            except OSError:
                pass
    return False


def _static_check(args: list[str], job_dir: Path) -> list[str]:
    """実行せず構文だけ確認する（拡張子で bash -n / py_compile / node --check を選択）。
    対象ファイルが見つからなければチェック不能としてスキップ（[]）。"""
    from tools.command_tools import _capture_run
    exts = {".sh": ["bash", "-n"], ".bash": ["bash", "-n"],
            ".py": ["python3", "-m", "py_compile"], ".js": ["node", "--check"], ".mjs": ["node", "--check"]}
    targets = [a for a in args if any(a.endswith(e) for e in exts)]
    problems: list[str] = []
    for tgt in targets:
        ext = "." + tgt.rsplit(".", 1)[-1]
        rc, out, err, _ = _capture_run(exts[ext] + [tgt], cwd=str(job_dir), timeout=20)
        if rc != 0:
            problems.append(f"構文エラー `{tgt}`:\n{(err or out).strip()[-400:]}")
    return problems


def _free_port() -> int:
    """OS に空きTCPポートを1つ選ばせて返す（衝突回避用）。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _avoid_port_conflict(args: list[str]) -> list[str]:
    """`python -m http.server <port>` 等の起動確認で、固定ポート（特に本体と同じ8000）が
    既に使われていると `Address already in use` で即死し「起動失敗」と誤判定する。
    http.server のポート引数を空きポートへ置換して自己衝突を防ぐ（起動確認はポート不問で
    「起動して即死しなければOK」なので、どのポートでも目的を満たす）。"""
    if "http.server" not in args:
        return args
    out = list(args)
    try:
        idx = out.index("http.server")
    except ValueError:
        return out
    # http.server の次にある数値トークン＝ポート。あれば置換、無ければ追加。
    if idx + 1 < len(out) and out[idx + 1].isdigit():
        out[idx + 1] = str(_free_port())
    else:
        out.insert(idx + 1, str(_free_port()))
    return out


def run_acceptance_checks(job_dir: Path, checks: list[dict]) -> list[str]:
    """プログラム成果物の受け入れ検収を機械的に実行する（debug役の自己申告 PASS に頼らない）。
    各 check:
      - cmd: 実行する単一コマンド（パイプ/&&不可・shell=False）
      - kind: "run"（完走して終了コード0を期待）/ "startup"（起動して即死しなければOK＝サーバー/ゲーム用）
      - expect_exit: run時の期待終了コード（既定0）
      - expect_contains: run時、標準出力に含むべき文字列（任意）
      - startup_sec: startup時、生存を確認する秒数（既定4）
      - timeout_sec: run時の最大待ち秒数（既定30）
    起動・実行の固まり対策は _capture_run（タイムアウト＋プロセスツリーkill）に委譲。
    返り値: 問題点リスト（空＝全チェック通過）。"""
    import shlex
    from tools.command_tools import _capture_run

    problems: list[str] = []
    for chk in checks or []:
        cmd = (chk.get("cmd") or "").strip()
        if not cmd:
            continue
        kind = chk.get("kind", "run")
        try:
            args = shlex.split(cmd)
        except ValueError:
            problems.append(f"受け入れコマンドを解釈できません: `{cmd}`")
            continue
        # 安全弁: 破壊的・システム変更・他ホスト接続コマンドは実行せず構文チェックに格下げ
        # （チェックON でもこの開発機を壊さない。kind="syntax" 指定時も同様に静的検証）。
        if kind == "syntax" or _is_destructive(cmd) or _script_is_destructive(args, job_dir):
            problems += _static_check(args, job_dir)
            continue
        # http.server の固定ポートが本体(8000)等と衝突して誤「起動失敗」になるのを防ぐ
        args = _avoid_port_conflict(args)
        if kind == "startup":
            timeout = chk.get("startup_sec", 4)
            rc, out, err, timed_out = _capture_run(args, cwd=str(job_dir), timeout=timeout)
            # タイムアウト=起動し続けている=OK / 即exitでもrc==0なら正当（すぐ終わる実行）
            if timed_out or rc == 0:
                continue
            tail = (err or out or "").strip()[-500:]
            problems.append(f"起動確認に失敗 `{cmd}`（終了コード {rc}）:\n{tail}")
        else:  # run
            timeout = chk.get("timeout_sec", 30)
            rc, out, err, timed_out = _capture_run(args, cwd=str(job_dir), timeout=timeout)
            if timed_out:
                problems.append(f"実行が時間内（{timeout}s）に終わりませんでした `{cmd}`")
                continue
            exp = chk.get("expect_exit", 0)
            if rc != exp:
                tail = (err or out or "").strip()[-500:]
                problems.append(f"実行に失敗 `{cmd}`（終了コード {rc}, 期待 {exp}）:\n{tail}")
                continue
            # expect_contains は補助的な検証。終了コード0が主たる合否基準なので、
            # 大文字小文字を無視して照合する（決め打ち文字列の大小ズレで動く物を誤判定しない）。
            need = chk.get("expect_contains")
            if need and need.lower() not in (out or "").lower():
                problems.append(f"出力に期待文字列が見つかりません `{cmd}`（期待: {need}）:\n{(out or '').strip()[-300:]}")
    return problems


def update_status_locked(job_dir: Path, role: str, status: str) -> None:
    """status.md の更新を team のファイルロックで保護する（並列ワーカーの lost update 防止）。
    記録ロジックはパイプライン方式と共通の multi_agent_tools._update_status を流用する。
    （パイプラインは逐次なので無防備でも問題なかったが、チーム方式は並列で同時書き込みが起きる）"""
    from tools.multi_agent_tools import _update_status
    lock_path = (job_dir / "status.md").with_suffix(".md.lock")
    fd = _acquire_lock(lock_path)
    try:
        _update_status(job_dir, role, status)
    finally:
        _release_lock(fd, lock_path)


def complete_task(job_dir: Path, task_id: str, summary: str = "") -> dict:
    lock_path = _tasks_path(job_dir).with_suffix(".json.lock")
    fd = _acquire_lock(lock_path)
    try:
        tasks = _read_tasks(job_dir)
        for t in tasks:
            if t["id"] == task_id:
                t["state"] = "completed"
                t["summary"] = summary
                _write_tasks(job_dir, tasks)
                return {"ok": True, "task_id": task_id}
        return {"ok": False, "error": f"タスク {task_id} が見つかりません"}
    finally:
        _release_lock(fd, lock_path)


def list_tasks(job_dir: Path) -> list[dict]:
    return [
        {"id": t["id"], "role": t.get("role"), "state": t["state"],
         "owner": t.get("owner"), "depends_on": t.get("depends_on", []),
         "summary": t.get("summary", "")}
        for t in _read_tasks(job_dir)
    ]


# ----------------------------------------------------------------------------
# 試行ログ（attempts.jsonl）— 誰が・何回目・どのモデルで・結果はどうだったかを後追いする
# ----------------------------------------------------------------------------
def _attempts_path(job_dir: Path) -> Path:
    return job_dir / "attempts.jsonl"


def log_attempt(job_dir: Path, **fields) -> None:
    """1トライ分の試行ログを attempts.jsonl に1行追記する（並列ワーカーの行混在を防ぐためロック保護）。
    記録例: worker / task_id / role / attempt（1始まり）/ preset_id / model / escalated / result / elapsed_sec / problems。
    どれが欠けても落とさない（ログ失敗で本処理を止めない）。"""
    entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **fields}
    p = _attempts_path(job_dir)
    lock_path = p.with_suffix(".jsonl.lock")
    fd = _acquire_lock(lock_path)
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    finally:
        _release_lock(fd, lock_path)


def read_attempts(job_dir: Path) -> list[dict]:
    """attempts.jsonl を読み出す（壊れた行はスキップ）。"""
    p = _attempts_path(job_dir)
    if not p.exists():
        return []
    out: list[dict] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return out


def summarize_attempts_md(job_dir: Path) -> str:
    """試行ログを人間可読の Markdown 表に整形する（空ならその旨）。"""
    rows = read_attempts(job_dir)
    if not rows:
        return ""
    out = "| タスク | 役割 | ワーカー | 試行 | モデル | 結果 | 秒 | 理由 |\n|---|---|---|---|---|---|---|---|\n"
    icon = {"ok": "✅", "ng": "⚠️", "error": "❌"}
    for r in rows:
        model = r.get("model", "")
        if r.get("escalated"):
            model += " ⬆️"
        res = icon.get(r.get("result", ""), r.get("result", ""))
        sec = r.get("elapsed_sec")
        sec_s = f"{sec:.0f}" if isinstance(sec, (int, float)) else ""
        # 成功以外は「なぜダメだったか」を理由欄に出す（pass/failしか分からない問題の解消）。
        reason = ""
        if r.get("result") != "ok":
            probs = r.get("problems") or []
            if isinstance(probs, str):
                probs = [probs]
            # 表セルを壊さないよう改行/パイプを除去し、長すぎる場合は丸める。
            joined = " / ".join(str(p).replace("|", "\\|").replace("\n", " ").strip() for p in probs if p)
            reason = (joined[:160] + "…") if len(joined) > 160 else joined
        out += (
            f"| `{r.get('task_id','')}` | {r.get('role','')} | {r.get('worker','')} "
            f"| {r.get('attempt','')} | `{model}` | {res} | {sec_s} | {reason} |\n"
        )
    return out


# ----------------------------------------------------------------------------
# mailbox（teammate 間メッセージング・宛先別 jsonl・既読オフセット）
# ----------------------------------------------------------------------------
def _mailbox_dir(job_dir: Path) -> Path:
    d = job_dir / "mailbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _count_messages(job_dir: Path) -> int:
    total = 0
    for f in _mailbox_dir(job_dir).glob("*.jsonl"):
        try:
            total += sum(1 for _ in f.open(encoding="utf-8"))
        except OSError:
            pass
    return total


def send_message(job_dir: Path, sender: str, to: str, subject: str, body: str) -> dict:
    """宛先 teammate の受信箱（mailbox/{to}.jsonl）に1行追記する。"""
    if not to:
        return {"ok": False, "error": "宛先(to)が空です"}
    if _count_messages(job_dir) >= config.MULTI_AGENT_MAX_MESSAGES:
        return {"ok": False, "error": f"このジョブのメッセージ総数が上限({config.MULTI_AGENT_MAX_MESSAGES})に達しました。これ以上の送信は控えてください。"}
    msg = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "from": sender, "to": to,
        "subject": subject or "", "body": body or "",
    }
    box = _mailbox_dir(job_dir) / f"{to}.jsonl"
    with box.open("a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return {"ok": True}


def read_messages(job_dir: Path, member_name: str) -> list[dict]:
    """自分宛の未読メッセージのみ返し、既読オフセットを更新する。"""
    box = _mailbox_dir(job_dir) / f"{member_name}.jsonl"
    if not box.exists():
        return []
    off_file = _mailbox_dir(job_dir) / f"{member_name}.offset"
    try:
        offset = int(off_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        offset = 0
    lines = box.read_text(encoding="utf-8").splitlines()
    unread = lines[offset:]
    out: list[dict] = []
    for ln in unread:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    off_file.write_text(str(len(lines)), encoding="utf-8")
    return out


# ----------------------------------------------------------------------------
# teammate 用ツール（クロージャで job_dir＋名前を束ねる＝“専用便箋方式”）
# ----------------------------------------------------------------------------
TEAM_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "チームの他メンバーにメッセージを送る。宛先名は team.json のメンバー名（例: designer/coder/debugger）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "宛先メンバー名"},
                    "subject": {"type": "string", "description": "件名（短く）"},
                    "body": {"type": "string", "description": "本文"},
                },
                "required": ["to", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_messages",
            "description": "自分宛の未読メッセージを取得する（引数なし）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "チームの共有タスクリストの現状を見る（引数なし）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def make_team_executor(
    base_executor: Callable[[str, dict], Awaitable[str]],
    job_dir: Path,
    member_name: str,
    work_dir: Path | None = None,
) -> Callable[[str, dict], Awaitable[str]]:
    """teammate 専用の executor。チームツール（send/read/list）は job_dir＝制御ディレクトリ＋
    自分の名前を埋めて処理し、それ以外は既存の execute_tool_async に委譲する。
    成果物の相対パスは work_dir 基準に正す（work_dir 未指定なら job_dir 基準＝従来挙動）。
    シングル型マルチでは work_dir=既存スコープ、job_dir=scope/.team/<id> に分離する。"""
    # teammate は work_dir を作業ディレクトリとみなす。相対パスは work_dir 基準に正す
    # （絶対パスはそのまま）。これで「code/x.py」が workspace ルートに落ちる事故を防ぐ。
    _work_base = work_dir or job_dir
    _PATH_KEYS = ("path", "file_path", "work_dir")

    def _rewrite_paths(args: dict) -> dict:
        out = dict(args)
        for k in _PATH_KEYS:
            v = out.get(k)
            if isinstance(v, str) and v and not os.path.isabs(v):
                out[k] = str(_work_base / v)
        return out

    async def _exec(name: str, args: dict) -> str:
        if name == "send_message":
            return json.dumps(
                send_message(job_dir, member_name, args.get("to", ""), args.get("subject", ""), args.get("body", "")),
                ensure_ascii=False,
            )
        if name == "read_messages":
            return json.dumps(read_messages(job_dir, member_name), ensure_ascii=False)
        if name == "list_tasks":
            return json.dumps(list_tasks(job_dir), ensure_ascii=False)
        return await base_executor(name, _rewrite_paths(args))
    return _exec


# ----------------------------------------------------------------------------
# teammate のエージェントループ（mailbox 自動読込みを最初から組み込む）
# ----------------------------------------------------------------------------
async def run_team_member(
    member_name: str,
    role: str,
    system_prompt: str,
    task_prompt: str,
    all_tools: list[dict],
    base_executor: Callable[[str, dict], Awaitable[str]],
    async_client,
    model: str,
    job_dir: Path,
    max_iterations: int | None = None,
    timeout_sec: int | None = None,
    work_dir: Path | None = None,
) -> str:
    """1タスクを実行する teammate のループ。ターン冒頭で自分宛 mailbox を機械的に注入する
    （弱いモデルが read_messages を呼び忘れても会話に取り込まれる）。
    work_dir 指定時は成果物の相対パスを work_dir 基準に解決する（既定は job_dir 基準）。"""
    from tools.multi_agent_tools import AGENT_ALLOWED_TOOLS, _filter_tools

    if max_iterations is None:
        max_iterations = config.MULTI_AGENT_MAX_ITERATIONS
    if timeout_sec is None:
        timeout_sec = config.MULTI_AGENT_TIMEOUT_SEC

    allowed = _filter_tools(all_tools, AGENT_ALLOWED_TOOLS.get(role, [])) + TEAM_TOOL_SCHEMAS
    executor = make_team_executor(base_executor, job_dir, member_name, work_dir=work_dir)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": task_prompt},
    ]

    start_time = time.time()
    iteration = 0
    final_text = ""
    used_any_tool = False
    nudged = False

    while iteration < max_iterations:
        # ターン冒頭: 自分宛の未読を機械的に注入（mailbox 自動配信の代替）
        unread = read_messages(job_dir, member_name)
        if unread:
            lines = [f"- {m.get('from')}「{m.get('subject','')}」: {m.get('body','')}" for m in unread]
            messages.append({
                "role": "user",
                "content": "📬 チームメンバーからの新着メッセージ:\n" + "\n".join(lines)
                + "\n（必要なら send_message で返信し、自分のタスクを続行してください）",
            })

        elapsed = time.time() - start_time
        if elapsed > timeout_sec and iteration > 0:
            messages.append({
                "role": "user",
                "content": "時間制限に達しました。現時点の成果をファイルに保存し、complete_task せず終了してよいので作業を締めてください。",
            })

        iteration += 1
        create_kwargs: dict = {"model": model, "messages": messages, "stream": False}
        if allowed:
            create_kwargs["tools"] = allowed

        response = await _create_with_backoff(async_client, create_kwargs)
        msg = response.choices[0].message
        messages.append(msg.model_dump(exclude_unset=True))

        if msg.tool_calls:
            used_any_tool = True
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                result = await executor(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result),
                })
        else:
            final_text = msg.content or ""
            # 弱いモデル対策: 一度もツールを使わずテキストだけで終わろうとしたら1回催促する
            # （「やります」と言うだけで実作業をしない典型パターンを救済）
            if not used_any_tool and not nudged and iteration < max_iterations:
                nudged = True
                messages.append({
                    "role": "user",
                    "content": "テキストの返答だけでは作業は完了になりません。"
                    "担当タスクを実際に遂行するため、必要なツール（write_file / run_command 等）を呼び出して成果をファイルに書き出してください。",
                })
                continue
            break

    return final_text


async def dispatch_team_task(user_message: str, async_client, model: str, job_dir: Path) -> dict:
    """チーム方式用のディスパッチャー。depends_on / files / role を持つ plan.json を生成する。"""
    from prompts import get_agent_system_prompt

    response = await async_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": get_agent_system_prompt("dispatcher_team", str(job_dir))},
            {"role": "user",   "content": user_message},
        ],
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content or "{}"
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError:
        plan = {"tasks": {}}
    plan["mode"] = "team"
    return plan
