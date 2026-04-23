"""
browser_search: システムの Edge/Chrome を使って Bing 検索し結果を返す。
Google は bot 判定が厳しいため Bing を使用。JavaScript で DOM を直接解析。
"""

import json


def browser_search(query: str, max_results: int = 8) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return json.dumps({"error": "playwright がインストールされていません。`pip install playwright` を実行してください。"}, ensure_ascii=False)

    try:
        with sync_playwright() as p:
            browser = None
            for channel in ("msedge", "chrome"):
                try:
                    browser = p.chromium.launch(
                        channel=channel,
                        headless=False,
                        args=["--window-position=-32000,-32000"],  # 画面外に配置
                    )
                    break
                except Exception:
                    continue
            if browser is None:
                return json.dumps({"error": "Edge または Chrome が見つかりません。"}, ensure_ascii=False)

            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
            )
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            page.set_extra_http_headers({"Accept-Language": "ja,en;q=0.9"})

            # Bing で検索（Google より bot 判定が格段に緩い）
            page.goto(f"https://www.bing.com/search?q={query}&setlang=ja&count={max_results}", timeout=20000)
            page.wait_for_load_state("domcontentloaded")

            results = page.evaluate(f"""
() => {{
    const results = [];
    const seen = new Set();
    const items = document.querySelectorAll('li.b_algo');
    for (const item of items) {{
        if (results.length >= {max_results}) break;
        const a = item.querySelector('h2 a');
        if (!a) continue;
        const href = a.href || '';
        if (!href.startsWith('http') || seen.has(href)) continue;
        seen.add(href);
        const title = a.innerText.trim();
        const snippetEl = item.querySelector('.b_caption p, p');
        const snippet = snippetEl ? snippetEl.innerText.trim().substring(0, 250) : '';
        if (title) results.push({{ title, url: href, snippet }});
    }}
    return results;
}}
""")
            browser.close()

    except Exception as e:
        return json.dumps({"error": f"browser_search エラー: {e}"}, ensure_ascii=False)

    if not results:
        return json.dumps({
            "warning": "検索結果を取得できませんでした。",
            "query": query,
        }, ensure_ascii=False)

    return json.dumps({"query": query, "source": "Bing (browser)", "results": results}, ensure_ascii=False, indent=2)
