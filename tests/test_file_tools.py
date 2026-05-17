"""tools/file_tools.py の単体テスト。"""
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

class TestReadFile:
    def test_success(self, workspace):
        from tools.file_tools import read_file
        (workspace / "hello.txt").write_text("world", encoding="utf-8")
        result = read_file("hello.txt")
        assert result["content"] == "world"
        assert result["size"] == 5

    def test_not_found(self, workspace):
        from tools.file_tools import read_file
        result = read_file("missing.txt")
        assert "error" in result
        assert "見つかりません" in result["error"]

    def test_outside_workspace_rejected(self, workspace):
        from tools.file_tools import read_file
        result = read_file("/etc/passwd")
        assert "error" in result
        assert "禁止" in result["error"] or "許可" in result["error"]


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

class TestWriteFile:
    def test_overwrite(self, workspace):
        from tools.file_tools import write_file, read_file
        write_file("out.txt", "first")
        write_file("out.txt", "second")
        assert read_file("out.txt")["content"] == "second"

    def test_append(self, workspace):
        from tools.file_tools import write_file, read_file
        write_file("log.txt", "line1\n")
        write_file("log.txt", "line2\n", mode="append")
        assert read_file("log.txt")["content"] == "line1\nline2\n"

    def test_creates_parent_dirs(self, workspace):
        from tools.file_tools import write_file
        result = write_file("sub/dir/file.txt", "hi")
        assert "error" not in result
        assert (workspace / "sub" / "dir" / "file.txt").exists()

    def test_control_chars_stripped(self, workspace):
        from tools.file_tools import write_file, read_file
        write_file("ctrl.txt", "hello\x00world\x07!")
        content = read_file("ctrl.txt")["content"]
        assert "\x00" not in content
        assert "\x07" not in content
        assert "hello" in content and "world" in content

    def test_newline_and_tab_preserved(self, workspace):
        from tools.file_tools import write_file, read_file
        write_file("nl.txt", "a\nb\tc")
        assert read_file("nl.txt")["content"] == "a\nb\tc"

    def test_docker_compose_port8000_blocked(self, workspace):
        from tools.file_tools import write_file
        result = write_file("docker-compose.yml", "ports:\n  - '8000:8000'")
        assert "error" in result
        assert "8000" in result["error"]


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

class TestEditFile:
    def test_success(self, workspace):
        from tools.file_tools import write_file, edit_file, read_file
        write_file("edit.txt", "foo bar foo")
        result = edit_file("edit.txt", "foo", "baz", expected_replacements=2)
        assert "error" not in result
        assert read_file("edit.txt")["content"] == "baz bar baz"

    def test_file_not_found(self, workspace):
        from tools.file_tools import edit_file
        result = edit_file("ghost.txt", "x", "y")
        assert "error" in result

    def test_old_str_not_found(self, workspace):
        from tools.file_tools import write_file, edit_file
        write_file("e.txt", "hello")
        result = edit_file("e.txt", "MISSING", "x")
        assert "error" in result
        assert "見つかりません" in result["error"]

    def test_ambiguous_replacement(self, workspace):
        from tools.file_tools import write_file, edit_file
        write_file("e.txt", "ab ab ab")
        # デフォルト expected_replacements=1 なのに3箇所ある
        result = edit_file("e.txt", "ab", "cd")
        assert "error" in result
        assert "3" in result["error"]


# ---------------------------------------------------------------------------
# glob_files
# ---------------------------------------------------------------------------

class TestGlobFiles:
    def test_finds_files(self, workspace):
        from tools.file_tools import glob_files
        (workspace / "a.py").write_text("")
        (workspace / "b.py").write_text("")
        (workspace / "c.txt").write_text("")
        result = glob_files("*.py")
        assert result["total"] == 2
        files = result["files"]
        assert any("a.py" in f for f in files)
        assert any("b.py" in f for f in files)

    def test_recursive_glob(self, workspace):
        from tools.file_tools import glob_files
        (workspace / "sub").mkdir()
        (workspace / "sub" / "deep.py").write_text("")
        result = glob_files("**/*.py")
        assert result["total"] >= 1

    def test_invalid_path(self, workspace):
        from tools.file_tools import glob_files
        result = glob_files("*.py", path="/etc")
        assert "error" in result


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------

class TestGrep:
    def test_finds_match(self, workspace):
        from tools.file_tools import grep
        (workspace / "src.py").write_text("def hello():\n    pass\n")
        result = grep("def hello")
        assert result["total"] == 1
        assert result["matches"][0]["line"] == 1

    def test_no_match(self, workspace):
        from tools.file_tools import grep
        (workspace / "src.py").write_text("x = 1\n")
        result = grep("NOTFOUND")
        assert result["total"] == 0

    def test_case_insensitive(self, workspace):
        from tools.file_tools import grep
        (workspace / "f.txt").write_text("Hello World\n")
        result = grep("hello world", case_sensitive=False)
        assert result["total"] == 1

    def test_invalid_regex(self, workspace):
        from tools.file_tools import grep
        result = grep("[invalid(")
        assert "error" in result

    def test_outside_workspace_rejected(self, workspace):
        from tools.file_tools import grep
        result = grep("root", path="/etc")
        assert "error" in result


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------

class TestListFiles:
    def test_returns_tree(self, workspace):
        from tools.file_tools import list_files
        (workspace / "a.txt").write_text("x")
        result = list_files()
        assert "a.txt" in result

    def test_invalid_path(self, workspace):
        from tools.file_tools import list_files
        result = list_files(path="/nonexistent")
        assert "error" in result
