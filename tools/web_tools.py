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
        # Abstract (即時回答)
        if data.get("AbstractText") and data.get("AbstractURL"):
            results.append({
                "title": data.get("Heading", ""),
                "url": data["AbstractURL"],
                "snippet": data["AbstractText"][:300],
            })

        # RelatedTopics
        for topic in data.get("RelatedTopics", []):
            if len(results) >= max_results:
                break
            if isinstance(topic, dict) and topic.get("FirstURL"):
                results.append({
                    "title": topic.get("Text", "")[:100],
                    "url": topic["FirstURL"],
                    "snippet": topic.get("Text", "")[:300],
                })

        return {"results": results, "query": query, "source": "duckduckgo", "count": len(results)}
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
