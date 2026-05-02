import platform
from pathlib import Path
from tools.file_tools import _resolve_safe_path


# ---------------------------------------------------------------------------
# PDF 生成
# ---------------------------------------------------------------------------

def _find_cjk_font() -> str | None:
    """日本語対応 TTF フォントのパスを OS ごとに探す"""
    candidates = []
    if platform.system() == "Windows":
        win_fonts = Path(r"C:\Windows\Fonts")
        candidates = [
            win_fonts / "msgothic.ttc",
            win_fonts / "meiryo.ttc",
            win_fonts / "YuGothM.ttc",
        ]
    else:
        candidates = [
            Path("/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"),
            Path("/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def write_pdf(path: str, content: str, title: str = "", font_size: int = 11) -> dict:
    """
    Markdown 風テキストから PDF を生成します。

    # 見出し1 / ## 見出し2 / ### 見出し3 を大中小の見出しとして出力。
    - または * で始まる行は箇条書き。| で始まる行はテーブル行として整形。
    空行は段落区切り。日本語対応。

    path: workspace 相対パス (.pdf)
    content: Markdown 風テキスト
    title: PDF タイトル（表紙見出し、省略可）
    font_size: 本文フォントサイズ（デフォルト: 11）
    """
    try:
        from fpdf import FPDF
    except ImportError:
        return {"error": "fpdf2 がインストールされていません。run_command('pip install fpdf2') でインストールしてください。"}

    target = _resolve_safe_path(path)
    if target.suffix.lower() != ".pdf":
        return {"error": "出力パスは .pdf で終わる必要があります"}
    target.parent.mkdir(parents=True, exist_ok=True)

    font_path = _find_cjk_font()

    from fpdf.enums import XPos, YPos

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    if font_path:
        pdf.add_font("CJK", "", font_path)
        normal_font = "CJK"
    else:
        normal_font = "Helvetica"

    def mc(text: str, h: float = 6):
        """multi_cell のラッパー。呼び出し後に X をリセットして次行の先頭に戻す。"""
        pdf.multi_cell(pdf.epw, h, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def set_body():
        pdf.set_font(normal_font, size=font_size)
        pdf.set_text_color(40, 40, 40)

    def set_h1():
        pdf.set_font(normal_font, size=font_size + 8)
        pdf.set_text_color(20, 20, 20)

    def set_h2():
        pdf.set_font(normal_font, size=font_size + 4)
        pdf.set_text_color(30, 30, 30)

    def set_h3():
        pdf.set_font(normal_font, size=font_size + 2)
        pdf.set_text_color(40, 40, 40)

    if title:
        set_h1()
        mc(title, h=10)
        pdf.ln(4)

    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()

        if stripped.startswith("### "):
            set_h3()
            mc(stripped[4:], h=8)
            pdf.ln(1)
        elif stripped.startswith("## "):
            set_h2()
            mc(stripped[3:], h=9)
            pdf.ln(2)
        elif stripped.startswith("# "):
            set_h1()
            mc(stripped[2:], h=11)
            pdf.ln(3)
        elif stripped.startswith(("- ", "* ")):
            set_body()
            mc("  - " + stripped[2:])
        elif stripped.startswith("|"):
            # テーブル: 連続する | 行をまとめて処理
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row_text = lines[i].strip().strip("|")
                cells = [c.strip() for c in row_text.split("|")]
                table_lines.append(cells)
                i += 1
            # 区切り行（---|---）を除去
            table_lines = [r for r in table_lines if not all(set(c) <= set("-: ") for c in r)]
            if table_lines:
                col_count = max(len(r) for r in table_lines)
                col_w = pdf.epw / max(col_count, 1)
                set_body()
                for r_idx, row in enumerate(table_lines):
                    for c_idx in range(col_count):
                        cell_text = row[c_idx] if c_idx < len(row) else ""
                        pdf.cell(col_w, 7, cell_text, border=1)
                    pdf.ln()
                pdf.ln(2)
            continue  # i は内側ループで進んでいる
        elif stripped == "":
            pdf.ln(3)
        else:
            set_body()
            mc(stripped)

        i += 1

    try:
        pdf.output(str(target))
    except Exception as e:
        return {"error": f"PDF 書き出しエラー: {e}"}

    return {"path": str(target), "size_bytes": target.stat().st_size, "error": None}


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
