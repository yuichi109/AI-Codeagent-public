"""tools/command_tools.py の単体テスト。"""
import pytest


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------

class TestDecodeOutput:
    def test_utf8(self):
        from tools.command_tools import _decode_output
        assert _decode_output("日本語".encode("utf-8")) == "日本語"

    def test_cp932(self):
        from tools.command_tools import _decode_output
        assert _decode_output("テスト".encode("cp932")) == "テスト"

    def test_empty(self):
        from tools.command_tools import _decode_output
        assert _decode_output(b"") == ""


class TestTruncateOutput:
    def test_within_limit(self):
        from tools.command_tools import _truncate_output
        text = "a" * 100
        assert _truncate_output(text, limit=200) == text

    def test_truncated(self):
        from tools.command_tools import _truncate_output
        text = "a" * 10000
        result = _truncate_output(text, limit=100)
        assert len(result) < len(text)
        assert "中略" in result

    def test_preserves_tail(self):
        from tools.command_tools import _truncate_output
        text = "start" + "x" * 10000 + "end"
        result = _truncate_output(text, limit=100)
        assert "end" in result


class TestSplitShellChain:
    def test_single_command(self):
        from tools.command_tools import _split_shell_chain
        assert _split_shell_chain("echo hello") == ["echo hello"]

    def test_two_commands(self):
        from tools.command_tools import _split_shell_chain
        parts = _split_shell_chain("echo a && echo b")
        assert parts == ["echo a", "echo b"]

    def test_three_commands(self):
        from tools.command_tools import _split_shell_chain
        parts = _split_shell_chain("a && b && c")
        assert len(parts) == 3

    def test_quoted_ampersand_ignored(self):
        from tools.command_tools import _split_shell_chain
        # クォート内の && は分割しない
        parts = _split_shell_chain('echo "a && b"')
        assert len(parts) == 1

    def test_single_quoted_ampersand_ignored(self):
        from tools.command_tools import _split_shell_chain
        parts = _split_shell_chain("echo 'a && b'")
        assert len(parts) == 1


class TestIsPermissionError:
    def test_permission_denied(self):
        from tools.command_tools import _is_permission_error
        assert _is_permission_error("Permission denied")

    def test_are_you_root(self):
        from tools.command_tools import _is_permission_error
        assert _is_permission_error("Are you root?")

    def test_no_error(self):
        from tools.command_tools import _is_permission_error
        assert not _is_permission_error("Success")


# ---------------------------------------------------------------------------
# run_command
# ---------------------------------------------------------------------------

class TestRunCommand:
    def test_echo(self, workspace):
        from tools.command_tools import run_command
        result = run_command("echo hello")
        assert result["returncode"] == 0
        assert "hello" in result["stdout"]

    def test_empty_command(self, workspace):
        from tools.command_tools import run_command
        result = run_command("")
        assert "error" in result

    def test_blocked_command(self, workspace):
        from tools.command_tools import run_command
        result = run_command("mkfs /dev/null")
        assert "error" in result
        assert "mkfs" in result["error"]

    def test_nonexistent_command(self, workspace):
        from tools.command_tools import run_command
        result = run_command("_totally_nonexistent_cmd_xyz")
        assert "error" in result

    def test_and_chain_success(self, workspace):
        from tools.command_tools import run_command
        result = run_command("echo first && echo second")
        assert result["returncode"] == 0
        assert "first" in result["stdout"] or "second" in result["stdout"]

    def test_and_chain_stops_on_failure(self, workspace):
        from tools.command_tools import run_command
        result = run_command("false && echo should_not_appear")
        assert result["returncode"] != 0

    def test_work_dir_outside_workspace_rejected(self, workspace):
        from tools.command_tools import run_command
        result = run_command("echo hi", work_dir="/etc")
        assert "error" in result

    def test_python_version(self, workspace):
        from tools.command_tools import run_command
        result = run_command("python3 --version")
        assert result["returncode"] == 0
        assert "Python" in result["stdout"] or "Python" in result["stderr"]

    def test_bash_requires_sh_file(self, workspace):
        from tools.command_tools import run_command
        # bash -c "..." は禁止（.sh ファイルのみ許可）
        result = run_command('bash -c "echo hi"')
        assert "error" in result

    def test_bash_runs_sh_file(self, workspace):
        from tools.command_tools import run_command
        script = workspace / "test_script.sh"
        script.write_text("#!/bin/bash\necho from_script\n", encoding="utf-8")
        result = run_command("bash test_script.sh")
        # bubblewrap がない環境ではエラーになる場合があるのでスキップ
        if "bubblewrap" in (result.get("error") or ""):
            pytest.skip("bubblewrap not installed")
        assert result["returncode"] == 0
        assert "from_script" in result["stdout"]
