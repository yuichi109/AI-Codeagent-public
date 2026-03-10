import ipaddress
import socket
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CodeAgent/1.0)"}


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


def web_search(query: str, max_results: int = 5) -> dict:
    max_results = min(max_results, 10)

    # --- 一次手段: Instant Answer API ---
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
                "snippet": data["AbstractText"][:300],
            })
        for topic in data.get("RelatedTopics", []):
            if len(results) >= max_results:
                break
            if isinstance(topic, dict) and topic.get("FirstURL"):
                results.append({
                    "title": topic.get("Text", "")[:100],
                    "url": topic["FirstURL"],
                    "snippet": topic.get("Text", "")[:300],
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
