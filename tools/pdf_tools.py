from pathlib import Path
from tools.file_tools import _resolve_safe_path


def read_pdf(path: str, pages: str = None, extract_tables: bool = False) -> str:
    """
    PDF ファイルのテキストを抽出して返す。

    path: workspace 相対パス (.pdf)
    pages: 抽出するページ範囲 (例: "1", "1-3", "2,4,6")。省略時は全ページ
    extract_tables: True の場合、テーブルも Markdown 形式で抽出する
    """
    try:
        import pdfplumber
    except ImportError:
        return "[ERROR] pdfplumber がインストールされていません。run_command('pip install pdfplumber') を実行してください。"

    resolved = _resolve_safe_path(path)
    if not resolved.exists():
        return f"[ERROR] ファイルが見つかりません: {path}"
    if resolved.suffix.lower() != ".pdf":
        return f"[ERROR] PDF ファイルではありません: {path}"

    # ページ指定をパース
    target_pages = _parse_pages(pages) if pages else None

    results = []
    try:
        with pdfplumber.open(resolved) as pdf:
            total = len(pdf.pages)
            results.append(f"📄 {resolved.name}（全 {total} ページ）\n")

            for i, page in enumerate(pdf.pages):
                page_num = i + 1
                if target_pages and page_num not in target_pages:
                    continue

                results.append(f"--- ページ {page_num} ---")

                text = page.extract_text()
                if text:
                    results.append(text.strip())
                else:
                    results.append("（テキストなし）")

                if extract_tables:
                    tables = page.extract_tables()
                    for t_idx, table in enumerate(tables):
                        if not table:
                            continue
                        results.append(f"\n[テーブル {t_idx + 1}]")
                        results.append(_table_to_markdown(table))

    except Exception as e:
        return f"[ERROR] PDF 読み取りに失敗しました: {e}"

    return "\n".join(results)


def _parse_pages(spec: str) -> set:
    """ページ指定文字列をページ番号のセットに変換 (例: "1-3,5" → {1,2,3,5})"""
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            pages.update(range(int(start), int(end) + 1))
        else:
            pages.add(int(part))
    return pages


def _table_to_markdown(table: list) -> str:
    """pdfplumber のテーブル（リストのリスト）を Markdown 形式に変換"""
    if not table:
        return ""
    rows = []
    header = [str(c or "") for c in table[0]]
    rows.append("| " + " | ".join(header) + " |")
    rows.append("| " + " | ".join(["---"] * len(header)) + " |")
    for row in table[1:]:
        rows.append("| " + " | ".join(str(c or "") for c in row) + " |")
    return "\n".join(rows)
