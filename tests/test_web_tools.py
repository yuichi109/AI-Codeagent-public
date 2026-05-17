"""tools/web_tools.py の単体テスト。外部ネットワーク呼び出しはすべてモックする。"""
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# _is_safe_url
# ---------------------------------------------------------------------------

class TestIsSafeUrl:
    def test_valid_https(self):
        from tools.web_tools import _is_safe_url
        ok, msg = _is_safe_url("https://example.com/page")
        assert ok

    def test_valid_http(self):
        from tools.web_tools import _is_safe_url
        ok, msg = _is_safe_url("http://example.com/")
        assert ok

    def test_ftp_rejected(self):
        from tools.web_tools import _is_safe_url
        ok, msg = _is_safe_url("ftp://example.com/file")
        assert not ok
        assert "スキーム" in msg

    def test_no_scheme_rejected(self):
        from tools.web_tools import _is_safe_url
        ok, msg = _is_safe_url("example.com")
        assert not ok

    def test_localhost_rejected(self):
        from tools.web_tools import _is_safe_url
        ok, msg = _is_safe_url("http://127.0.0.1/secret")
        assert not ok
        assert "プライベート" in msg

    def test_private_10_rejected(self):
        from tools.web_tools import _is_safe_url
        ok, msg = _is_safe_url("http://10.0.0.1/admin")
        assert not ok

    def test_private_192_rejected(self):
        from tools.web_tools import _is_safe_url
        ok, msg = _is_safe_url("http://192.168.1.1/")
        assert not ok

    def test_unresolvable_host(self):
        from tools.web_tools import _is_safe_url
        ok, msg = _is_safe_url("https://this-host-does-not-exist-xyz-abc.invalid/")
        assert not ok
        assert "解決" in msg


# ---------------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------------

class TestWebFetch:
    def _mock_response(self, text, content_type="text/html; charset=utf-8", status=200):
        mock = MagicMock()
        mock.text = text
        mock.headers = {"Content-Type": content_type}
        mock.raise_for_status = MagicMock()
        return mock

    def test_fetch_html_success(self):
        from tools.web_tools import web_fetch
        html = "<html><head><title>Test Page</title></head><body><p>Hello</p></body></html>"
        with patch("tools.web_tools.requests.get") as mock_get:
            mock_get.return_value = self._mock_response(html)
            result = web_fetch("https://example.com/")
        assert "error" not in result
        assert result["title"] == "Test Page"
        assert "Hello" in result["content"]

    def test_fetch_unsafe_url(self):
        from tools.web_tools import web_fetch
        result = web_fetch("http://127.0.0.1/secret")
        assert "error" in result

    def test_fetch_unsupported_content_type(self):
        from tools.web_tools import web_fetch
        with patch("tools.web_tools.requests.get") as mock_get:
            mock_get.return_value = self._mock_response(b"binary", content_type="application/octet-stream")
            result = web_fetch("https://example.com/file.bin")
        assert "error" in result
        assert "コンテンツタイプ" in result["error"]

    def test_fetch_request_exception(self):
        from tools.web_tools import web_fetch
        import requests
        with patch("tools.web_tools.requests.get", side_effect=requests.RequestException("timeout")):
            result = web_fetch("https://example.com/")
        assert "error" in result

    def test_max_chars_truncation(self):
        from tools.web_tools import web_fetch
        long_html = f"<html><body>{'x' * 50000}</body></html>"
        with patch("tools.web_tools.requests.get") as mock_get:
            mock_get.return_value = self._mock_response(long_html)
            result = web_fetch("https://example.com/", max_chars=100)
        assert result["truncated"] is True
        assert len(result["content"]) <= 100


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------

class TestWebSearch:
    def test_tavily_used_when_available(self):
        from tools.web_tools import web_search
        mock_results = {"results": [{"title": "T", "url": "https://t.com", "snippet": "s"}], "query": "q", "source": "tavily", "count": 1}
        with patch("tools.web_tools._search_tavily", return_value=mock_results) as mock_tav:
            result = web_search("pytest")
        assert result["source"] == "tavily"
        mock_tav.assert_called_once()

    def test_falls_back_to_ddgs(self):
        from tools.web_tools import web_search
        ddgs_results = {"results": [{"title": "D", "url": "https://d.com", "snippet": "ddgs"}], "query": "q", "source": "ddgs", "count": 1}
        with patch("tools.web_tools._search_tavily", return_value=None), \
             patch("tools.web_tools._search_ddgs", return_value=ddgs_results), \
             patch("tools.web_tools._results_look_relevant", return_value=True):
            result = web_search("pytest")
        assert result["source"] == "ddgs"

    def test_returns_error_on_all_failure(self):
        from tools.web_tools import web_search
        import requests
        with patch("tools.web_tools._search_tavily", return_value=None), \
             patch("tools.web_tools._search_ddgs", return_value=None), \
             patch("tools.web_tools._search_searxng", return_value=None), \
             patch("tools.web_tools.requests.get", side_effect=requests.RequestException("fail")):
            result = web_search("pytest")
        assert "error" in result or result.get("results") == []

    def test_max_results_capped_at_10(self):
        from tools.web_tools import web_search
        captured = {}
        def fake_tavily(query, max_results):
            captured["max"] = max_results
            return {"results": [], "query": query, "source": "tavily", "count": 0}
        with patch("tools.web_tools._search_tavily", side_effect=fake_tavily):
            web_search("test", max_results=99)
        assert captured["max"] <= 10


# ---------------------------------------------------------------------------
# web_research
# ---------------------------------------------------------------------------

class TestWebResearch:
    def test_returns_sources(self):
        from tools.web_tools import web_research
        search_result = {
            "results": [{"title": "Page", "url": "https://example.com/", "snippet": "text"}],
            "source": "tavily",
        }
        fetch_result = {"url": "https://example.com/", "title": "Page", "content": "body text", "truncated": False}
        with patch("tools.web_tools.web_search", return_value=search_result), \
             patch("tools.web_tools.web_fetch", return_value=fetch_result):
            result = web_research("query")
        assert result["source_count"] == 1
        assert result["sources"][0]["content"] == "body text"

    def test_handles_search_failure(self):
        from tools.web_tools import web_research
        with patch("tools.web_tools.web_search", return_value={"error": "fail", "results": []}):
            result = web_research("query")
        assert "error" in result
