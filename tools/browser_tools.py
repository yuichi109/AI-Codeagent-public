"""
browser_search: システムの Edge/Chrome を使ってブラウザで Google 検索し結果を返す。
Playwright を使用。`pip install playwright` のみで動作（playwright install 不要）。
"""

import json
import sys


def browser_search(query: str, max_results: int = 8) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return json.dumps({"error": "playwright がインストールされていません。`pip install playwright` を実行してください。"}, ensure_ascii=False)

    results = []

    try:
        with sync_playwright() as p:
            # システムの Edge を優先、なければ Chrome、なければ Chromium をダウンロードなしで試みる
            browser = None
            for channel in ("msedge", "chrome"):
                try:
                    browser = p.chromium.launch(channel=channel, headless=True)
                    break
                except Exception:
                    continue
            if browser is None:
                return json.dumps({"error": "Edge または Chrome が見つかりません。インストールされているか確認してください。"}, ensure_ascii=False)

            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
            )
            page.set_extra_http_headers({"Accept-Language": "ja,en;q=0.9"})

            page.goto(f"https://www.google.com/search?q={query}&hl=ja&num={max_results}", timeout=15000)
            page.wait_for_load_state("domcontentloaded")

            # 検索結果を取得（各 .g ブロックのタイトル・URL・スニペット）
            items = page.query_selector_all("div.g")
            for item in items[:max_results]:
                title_el = item.query_selector("h3")
                link_el = item.query_selector("a")
                snippet_el = item.query_selector("div[data-sncf], div.VwiC3b, span.aCOpRe")

                title = title_el.inner_text().strip() if title_el else ""
                url = link_el.get_attribute("href") if link_el else ""
                snippet = snippet_el.inner_text().strip() if snippet_el else ""

                if title and url and url.startswith("http"):
                    results.append({"title": title, "url": url, "snippet": snippet})

            browser.close()

    except Exception as e:
        return json.dumps({"error": f"browser_search エラー: {e}"}, ensure_ascii=False)

    if not results:
        return json.dumps({"warning": "検索結果を取得できませんでした。Google の UI が変更された可能性があります。", "query": query}, ensure_ascii=False)

    return json.dumps({"query": query, "results": results}, ensure_ascii=False, indent=2)
