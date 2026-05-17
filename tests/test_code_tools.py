"""tools/code_tools.py の単体テスト。"""
import pytest
import shutil


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def ruff_available():
    """ruff が使えるかチェック（使えない環境はスキップ）。"""
    from tools.code_tools import _find_ruff
    import subprocess
    try:
        subprocess.run(_find_ruff() + ["--version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# code_lint (Python)
# ---------------------------------------------------------------------------

class TestCodeLintPython:
    def test_no_input_returns_error(self, workspace):
        from tools.code_tools import code_lint
        result = code_lint()
        assert "error" in result

    def test_clean_code_passes(self, workspace):
        from tools.code_tools import code_lint
        if not ruff_available():
            pytest.skip("ruff not available")
        result = code_lint(code="x = 1\n", language="python")
        assert "error" not in result
        assert result["passed"] is True
        assert result["tool_used"] == "ruff"

    def test_lint_error_detected(self, workspace):
        from tools.code_tools import code_lint
        if not ruff_available():
            pytest.skip("ruff not available")
        # 未定義変数の使用（F821）はruffで検出される
        bad_code = "print(undefined_var)\n"
        result = code_lint(code=bad_code, language="python")
        assert "error" not in result
        assert isinstance(result["issues"], list)

    def test_file_path_lint(self, workspace):
        from tools.code_tools import code_lint
        if not ruff_available():
            pytest.skip("ruff not available")
        (workspace / "sample.py").write_text("import os\nx=1\n", encoding="utf-8")
        result = code_lint(file_path="sample.py")
        assert "error" not in result
        assert "issues" in result

    def test_language_detected_from_extension(self, workspace):
        from tools.code_tools import code_lint
        if not ruff_available():
            pytest.skip("ruff not available")
        (workspace / "script.py").write_text("x = 1\n", encoding="utf-8")
        result = code_lint(file_path="script.py")
        assert result.get("tool_used") == "ruff"

    def test_outside_workspace_rejected(self, workspace):
        from tools.code_tools import code_lint
        result = code_lint(file_path="/etc/passwd")
        assert "error" in result

    def test_summary_format(self, workspace):
        from tools.code_tools import code_lint
        if not ruff_available():
            pytest.skip("ruff not available")
        result = code_lint(code="x = 1\n", language="python")
        assert "errors" in result["summary"] and "warnings" in result["summary"]


# ---------------------------------------------------------------------------
# code_lint (JavaScript) — eslint がない環境はスキップ
# ---------------------------------------------------------------------------

class TestCodeLintJs:
    def test_eslint_not_installed_returns_error(self, workspace, monkeypatch):
        from tools.code_tools import code_lint
        # eslint がない状況をシミュレート
        monkeypatch.setattr(shutil, "which", lambda cmd: None if cmd == "eslint" else shutil.which(cmd))
        result = code_lint(code="var x = 1", language="javascript")
        assert "error" in result
        assert "eslint" in result["error"]

    @pytest.mark.skipif(not shutil.which("eslint"), reason="eslint not installed")
    def test_js_lint_runs(self, workspace):
        from tools.code_tools import code_lint
        result = code_lint(code="var x = 1;\n", language="javascript")
        assert "error" not in result
        assert "issues" in result


# ---------------------------------------------------------------------------
# 未対応言語
# ---------------------------------------------------------------------------

class TestUnsupportedLanguage:
    def test_unsupported_lang_error(self, workspace):
        from tools.code_tools import code_lint
        result = code_lint(code="hello", language="ruby")
        assert "error" in result
        assert "非対応" in result["error"]
