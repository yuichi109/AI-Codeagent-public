import ipaddress
import socket
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from config import SEARXNG_BASE_URL, SEARXNG_ENABLED, TAVILY_API_KEY

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CodeAgent/1.0)"}

try:
    from ddgs import DDGS as _DDGS
    _DDGS_AVAILABLE = True
except ImportError:
    _DDGS_AVAILABLE = False

try:
    from tavily import TavilyClient as _TavilyClient
    _TAVILY_AVAILABLE = True
except ImportError:
    _TAVILY_AVAILABLE = False


def _is_safe_url(url: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"スキーム '{parsed.scheme}' は許可されていません (http/https のみ)"
    if not parsed.hostname:
        return False, "ホスト名が不正です"
    try:
        ip = socket.gethostbyname(parsed.hostname)
        ip_obj = ipaddress.ip_address(ip)
        private_ranges = [
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
            ipaddress.ip_network("127.0.0.0/8"),
            ipaddress.ip_network("169.254.0.0/16"),
            ipaddress.ip_network("::1/128"),
        ]
        for r in private_ranges:
            if ip_obj in r:
                return False, f"プライベートIPへのアクセスは禁止されています: {ip}"
    except socket.gaierror:
        return False, f"ホスト名を解決できません: {parsed.hostname}"
    return True, ""


def _search_tavily(query: str, max_results: int = 5) -> dict | None:
    """Tavily Search API を使った検索。AIエージェント向け設計で高精度。"""
    if not TAVILY_API_KEY or not _TAVILY_AVAILABLE:
        return None
    try:
        client = _TavilyClient(api_key=TAVILY_API_KEY)
        resp = client.search(query, max_results=min(max_results, 10))
        results = []
        for item in resp.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", "")[:500],
            })
        if results:
            return {"results": results, "query": query, "source": "tavily", "count": len(results)}
    except Exception:
        pass
    return None


def _search_ddgs(query: str, max_results: int = 5) -> dict | None:
    """ddgs ライブラリを使った DuckDuckGo 検索。CAPTCHA を回避できる場合が多い。"""
    if not _DDGS_AVAILABLE:
        return None
    try:
        with _DDGS() as ddgs:
            raw = list(ddgs.text(query, region="jp-jp", max_results=max_results))
        results = []
        for item in raw:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("href", ""),
                "snippet": item.get("body", "")[:500],
            })
        if results:
            return {"results": results, "query": query, "source": "ddgs", "count": len(results)}
    except Exception:
        pass
    return None


def _search_searxng(query: str, max_results: int = 5) -> dict | None:
    """SearXNG JSON API を使った検索。失敗時は None を返す。"""
    try:
        resp = requests.get(
            f"{SEARXNG_BASE_URL}/search",
            params={"q": query, "format": "json", "language": "ja"},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        resp.encoding = "utf-8"
        data = resp.json()

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", "")[:500],
            })

        if results:
            return {"results": results, "query": query, "source": "searxng", "count": len(results)}
    except Exception:
        pass
    return None


def _has_japanese(text: str) -> bool:
    """テキストに日本語文字が含まれるかチェック。"""
    return any('\u3000' <= c <= '\u9fff' or '\uff00' <= c <= '\uffef' for c in text)


def _results_look_relevant(results: list, query: str) -> bool:
    """検索結果がクエリに関連していそうか簡易チェック。"""
    if not results:
        return False
    # クエリが日本語を含む場合、最低1件は日本語コンテンツを期待
    if _has_japanese(query):
        return any(
            _has_japanese(r.get("title", "") + r.get("snippet", ""))
            for r in results[:3]
        )
    return True


def web_search(query: str, max_results: int = 5) -> dict:
    max_results = min(max_results, 10)

    # --- 一次手段: Tavily Search API (設定時のみ・AIエージェント向け高精度) ---
    result = _search_tavily(query, max_results)
    if result:
        return result

    # --- 二次手段: ddgs ライブラリ (安定・CAPTCHA回避・スニペット充実) ---
    result = _search_ddgs(query, max_results)
    if result and _results_look_relevant(result.get("results", []), query):
        return result

    # --- 四次手段: SearXNG (有効時・関連性チェック付き) ---
    if SEARXNG_ENABLED:
        result = _search_searxng(query, max_results)
        if result and _results_look_relevant(result.get("results", []), query):
            return result

    # ddgs か SearXNG がとりあえず何か返していたら使う（関連性不問）
    if result:
        return result

    # --- 五次手段: DuckDuckGo Instant Answer API ---
    try:
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_redirect": "1", "no_html": "1"},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        if data.get("AbstractText") and data.get("AbstractURL"):
            results.append({
                "title": data.get("Heading", ""),
                "url": data["AbstractURL"],
                "snippet": data["AbstractText"][:500],
            })
        for topic in data.get("RelatedTopics", []):
            if len(results) >= max_results:
                break
            if isinstance(topic, dict) and topic.get("FirstURL"):
                results.append({
                    "title": topic.get("Text", "")[:100],
                    "url": topic["FirstURL"],
                    "snippet": topic.get("Text", "")[:500],
                })

        if results:
            return {"results": results, "query": query, "source": "duckduckgo-api", "count": len(results)}
    except Exception:
        pass

    # --- フォールバック: Wikipedia 検索 API (無料・APIキー不要・日本語対応) ---
    try:
        # 日本語 Wikipedia で検索
        resp = requests.get(
            "https://ja.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": query,
                "limit": max_results,
                "format": "json",
                "namespace": 0,
            },
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        # [query, [titles], [snippets], [urls]]
        titles = data[1] if len(data) > 1 else []
        snippets = data[2] if len(data) > 2 else []
        urls = data[3] if len(data) > 3 else []

        results = []
        for title, snippet, url in zip(titles, snippets, urls):
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet[:300] if snippet else f"Wikipedia: {title}",
            })

        if results:
            return {"results": results, "query": query, "source": "wikipedia-ja", "count": len(results)}

        # 日本語で見つからなければ英語 Wikipedia でも試す
        resp_en = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": query, "limit": max_results, "format": "json", "namespace": 0},
            headers=HEADERS,
            timeout=10,
        )
        resp_en.raise_for_status()
        data_en = resp_en.json()
        titles_en = data_en[1] if len(data_en) > 1 else []
        snippets_en = data_en[2] if len(data_en) > 2 else []
        urls_en = data_en[3] if len(data_en) > 3 else []

        results_en = []
        for title, snippet, url in zip(titles_en, snippets_en, urls_en):
            results_en.append({
                "title": title,
                "url": url,
                "snippet": snippet[:300] if snippet else f"Wikipedia: {title}",
            })

        return {"results": results_en, "query": query, "source": "wikipedia-en", "count": len(results_en)}

    except Exception as e:
        return {"error": f"検索エラー: {e}", "results": [], "query": query}


def web_fetch(url: str, extract_text: bool = True, max_chars: int = 8000) -> dict:
    max_chars = min(max_chars, 20000)
    ok, err = _is_safe_url(url)
    if not ok:
        return {"error": err, "url": url}

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, stream=True)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        if not any(ct in content_type for ct in ("text/html", "application/json", "text/plain")):
            return {"error": f"非対応のコンテンツタイプ: {content_type}", "url": url}

        raw = resp.text

        if extract_text and "html" in content_type:
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            title = soup.title.string.strip() if soup.title else ""
            text = soup.get_text(separator="\n", strip=True)
            # 連続する空行を圧縮
            import re
            text = re.sub(r"\n{3,}", "\n\n", text)
            content = text[:max_chars]
        else:
            title = ""
            content = raw[:max_chars]

        return {
            "url": url,
            "title": title,
            "content": content,
            "content_length": len(content),
            "truncated": len(raw) > max_chars,
        }
    except requests.RequestException as e:
        return {"error": f"取得エラー: {e}", "url": url}
    except Exception as e:
        return {"error": f"処理エラー: {e}", "url": url}


def web_research(query: str, max_sources: int = 3, max_chars_per_page: int = 3000) -> dict:
    """
    検索 → 上位ページを自動取得 → まとめて返す。
    AIが複数ソースを参照して比較・提案できるようにするための高レベルツール。
    """
    max_sources = min(max_sources, 5)
    max_chars_per_page = min(max_chars_per_page, 8000)

    # Step 1: 検索
    search_result = web_search(query, max_results=max_sources + 2)
    if "error" in search_result or not search_result.get("results"):
        return {"error": f"検索失敗: {search_result.get('error', '結果なし')}", "query": query, "sources": []}

    # Step 2: 各ページを取得
    sources = []
    for item in search_result["results"]:
        if len(sources) >= max_sources:
            break
        url = item.get("url", "")
        if not url:
            continue

        fetched = web_fetch(url, extract_text=True, max_chars=max_chars_per_page)
        if "error" in fetched:
            # 取得失敗したページはスニペットだけ使う
            sources.append({
                "url": url,
                "title": item.get("title", ""),
                "content": item.get("snippet", ""),
                "fetch_failed": True,
            })
        else:
            sources.append({
                "url": url,
                "title": fetched.get("title") or item.get("title", ""),
                "content": fetched.get("content", ""),
                "fetch_failed": False,
            })

    return {
        "query": query,
        "sources": sources,
        "source_count": len(sources),
        "search_backend": search_result.get("source", "unknown"),
    }
