"""
browser_search: システムの Edge/Chrome を使って Google 検索し結果を返す。
JavaScript で DOM を直接解析するため Google の UI 変更に強い。
"""

import json
import base64
from pathlib import Path


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
                    # headless=False でウィンドウを表示して起動（bot 判定回避）
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
            # webdriver フラグを消して bot 判定を回避
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                window.chrome = {runtime: {}};
            """)
            page.set_extra_http_headers({"Accept-Language": "ja,en;q=0.9"})
            page.goto(f"https://www.google.com/search?q={query}&hl=ja&num={max_results}", timeout=20000)
            page.wait_for_load_state("domcontentloaded")

            # JavaScript で DOM を直接解析（セレクター変更に強い）
            results = page.evaluate(f"""
() => {{
    const results = [];
    const seen = new Set();
    const root = document.querySelector('#search') || document.querySelector('#rso') || document.body;

    // h3 を起点に結果を収集
    const h3s = root.querySelectorAll('h3');
    for (const h3 of h3s) {{
        if (results.length >= {max_results}) break;

        // 親要素から <a href> を探す
        let a = h3.closest('a');
        if (!a) a = h3.parentElement && h3.parentElement.querySelector('a');
        if (!a) continue;

        const href = a.href || '';
        if (!href.startsWith('http') || href.includes('google.com/search') || seen.has(href)) continue;
        seen.add(href);

        const title = h3.innerText.trim();
        if (!title) continue;

        // スニペット: h3 の近くのテキストを取得
        const block = h3.closest('[data-hveid]') || h3.closest('div[class]') || h3.parentElement;
        let snippet = '';
        if (block) {{
            const clone = block.cloneNode(true);
            // タイトル部分を除去してテキスト取得
            clone.querySelectorAll('h3, style, script').forEach(el => el.remove());
            snippet = clone.innerText.replace(/\\s+/g, ' ').trim().substring(0, 250);
        }}

        results.push({{ title, url: href, snippet }});
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
            "hint": "Google に bot 判定された可能性があります。しばらく待ってから再試行してください。"
        }, ensure_ascii=False)

    return json.dumps({"query": query, "source": "Google (browser)", "results": results}, ensure_ascii=False, indent=2)
