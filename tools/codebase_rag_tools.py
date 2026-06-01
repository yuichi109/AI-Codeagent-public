"""
コードベース RAG ツール

workspace 内のコードファイルをベクトル化して意味検索できるようにする。
knowledge コレクション ("knowledge") とは分離した "codebase" コレクションを使用。

チャンク方式:
  - .py: AST でトップレベルのクラス・関数単位に分割
  - その他: 50行スライディングウィンドウ（オーバーラップ10行）
"""

import ast
import hashlib
import json
import uuid
from pathlib import Path

import chromadb
from chromadb.config import Settings

_DB_DIR = Path(__file__).parent.parent / ".rag_db"
_CODEBASE_COLLECTION = "codebase"
_INDEX_META_FILE = _DB_DIR / "_codebase_index_meta.json"

_DEFAULT_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
    ".cpp", ".c", ".h", ".cs", ".rb", ".php", ".sh", ".bash",
    ".yaml", ".yml", ".toml", ".md",
}

_CHUNK_SIZE = 50
_CHUNK_OVERLAP = 10


def _get_collection():
    from tools.rag_tools import _get_embedding_function
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(_DB_DIR),
        settings=Settings(anonymized_telemetry=False),
    )
    ef = _get_embedding_function()
    kwargs = {"name": _CODEBASE_COLLECTION, "metadata": {"hnsw:space": "cosine"}}
    if ef is not None:
        kwargs["embedding_function"] = ef
    return client, client.get_or_create_collection(**kwargs)


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _load_index_meta() -> dict:
    if _INDEX_META_FILE.exists():
        try:
            return json.loads(_INDEX_META_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_index_meta(meta: dict):
    _INDEX_META_FILE.write_text(json.dumps(meta), encoding="utf-8")


def _chunk_python(source: str, file_path: str) -> list[dict]:
    """AST でトップレベルのクラス・関数単位にチャンク分割する。"""
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [{"text": source, "chunk_type": "module", "chunk_name": file_path,
                 "start_line": 1, "end_line": len(lines)}]

    chunks = []
    processed_lines: set[int] = set()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno - 1
            end = node.end_lineno
            text = "\n".join(lines[start:end])
            chunk_type = "class" if isinstance(node, ast.ClassDef) else "function"
            chunks.append({
                "text": text,
                "chunk_type": chunk_type,
                "chunk_name": node.name,
                "start_line": node.lineno,
                "end_line": node.end_lineno,
            })
            processed_lines.update(range(start, end))

    # モジュールレベルのコード（クラス・関数以外）
    module_text = "\n".join(l for i, l in enumerate(lines) if i not in processed_lines)
    if module_text.strip():
        chunks.insert(0, {
            "text": module_text,
            "chunk_type": "module",
            "chunk_name": file_path,
            "start_line": 1,
            "end_line": len(lines),
        })

    return chunks or [{"text": source, "chunk_type": "module", "chunk_name": file_path,
                        "start_line": 1, "end_line": len(lines)}]


def _chunk_generic(source: str) -> list[dict]:
    """50行スライディングウィンドウでチャンク分割する。"""
    lines = source.splitlines()
    chunks = []
    step = _CHUNK_SIZE - _CHUNK_OVERLAP
    for i in range(0, max(1, len(lines)), step):
        chunk_lines = lines[i:i + _CHUNK_SIZE]
        text = "\n".join(chunk_lines)
        if text.strip():
            chunks.append({
                "text": text,
                "chunk_type": "chunk",
                "chunk_name": f"lines {i + 1}-{i + len(chunk_lines)}",
                "start_line": i + 1,
                "end_line": i + len(chunk_lines),
            })
    return chunks


def codebase_index(path: str = "", extensions: list = None, force: bool = False) -> dict:
    """
    指定ディレクトリ（省略時はworkspace全体）のコードファイルをインデックスします。
    変更がないファイルはスキップします（増分更新）。

    path: インデックス対象ディレクトリ（workspace相対パス、省略時はworkspace全体）
    extensions: 対象拡張子リスト（省略時はデフォルト）。例: [".py", ".js"]
    force: True にすると全ファイルを再インデックス
    """
    from config import ALLOWED_WORK_DIR
    workspace = Path(ALLOWED_WORK_DIR)
    target = workspace / path if path else workspace
    if not target.exists():
        return {"error": f"パスが存在しません: {target}"}

    exts = set(extensions) if extensions else _DEFAULT_EXTENSIONS
    _, col = _get_collection()
    meta = {} if force else _load_index_meta()

    indexed = skipped = 0
    errors = []

    code_files = [
        f for f in target.rglob("*")
        if f.is_file() and f.suffix in exts and ".git" not in f.parts
    ]

    for file_path in code_files:
        rel_path = str(file_path.relative_to(workspace))
        try:
            file_hash = _file_hash(file_path)
            if not force and meta.get(rel_path) == file_hash:
                skipped += 1
                continue

            source = file_path.read_text(encoding="utf-8", errors="ignore")
            if not source.strip():
                skipped += 1
                continue

            # 既存チャンクを削除
            existing = col.get(where={"file_path": {"$eq": rel_path}})
            if existing["ids"]:
                col.delete(ids=existing["ids"])

            chunks = _chunk_python(source, rel_path) if file_path.suffix == ".py" else _chunk_generic(source)

            # 各チャンクを8000文字以内に切り詰める（Azure embedding の8192トークン制限対策）
            for c in chunks:
                if len(c["text"]) > 8000:
                    c["text"] = c["text"][:8000]

            col.add(
                ids=[str(uuid.uuid4()) for _ in chunks],
                documents=[c["text"] for c in chunks],
                metadatas=[{
                    "file_path": rel_path,
                    "language": file_path.suffix.lstrip("."),
                    "chunk_type": c["chunk_type"],
                    "chunk_name": c["chunk_name"],
                    "start_line": c["start_line"],
                    "end_line": c["end_line"],
                } for c in chunks],
            )
            meta[rel_path] = file_hash
            indexed += 1

        except Exception as e:
            errors.append(f"{rel_path}: {e}")

    _save_index_meta(meta)

    return {
        "indexed": indexed,
        "skipped": skipped,
        "total_files": len(code_files),
        "errors": errors[:5] if errors else [],
        "message": f"{indexed}ファイルをインデックスしました（{skipped}件スキップ）",
    }


def codebase_search(query: str, n_results: int = 5, language: str = None) -> dict:
    """
    インデックス済みのコードベースから関連するコードを検索します。

    query: 検索クエリ（自然言語またはコードスニペット）
    n_results: 返す結果数（デフォルト5）
    language: 言語フィルタ（省略時は全言語）。例: "py", "js"
    """
    if not query.strip():
        return {"error": "query が空です"}

    _, col = _get_collection()
    total = col.count()
    if total == 0:
        return {
            "results": [],
            "message": "コードベースがまだインデックスされていません。codebase_index を実行してください。",
        }

    where = {"language": {"$eq": language}} if language else None
    n = min(n_results, total)

    res = col.query(
        query_texts=[query],
        n_results=n,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    MIN_RELEVANCE = 0.2
    results = []
    for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        relevance = round(1 - dist, 3)
        if relevance < MIN_RELEVANCE:
            continue
        results.append({
            "file_path": meta.get("file_path"),
            "chunk_name": meta.get("chunk_name"),
            "chunk_type": meta.get("chunk_type"),
            "language": meta.get("language"),
            "start_line": meta.get("start_line"),
            "end_line": meta.get("end_line"),
            "relevance": relevance,
            "snippet": doc[:500] + ("..." if len(doc) > 500 else ""),
        })

    return {
        "results": results,
        "query": query,
        "total_indexed": total,
        "message": "該当するコードが見つかりません" if not results else None,
    }


def codebase_clear(path: str = "") -> dict:
    """
    コードベースインデックスを削除します。
    path を指定すると該当パス配下のみ削除。省略時は全削除。
    """
    client, col = _get_collection()

    if path:
        all_docs = col.get(include=["metadatas"])
        ids_to_delete = [
            rid for rid, m in zip(all_docs["ids"], all_docs["metadatas"])
            if m.get("file_path", "").startswith(path)
        ]
        if ids_to_delete:
            col.delete(ids=ids_to_delete)
        meta = {k: v for k, v in _load_index_meta().items() if not k.startswith(path)}
        _save_index_meta(meta)
        return {"cleared": len(ids_to_delete), "path": path}

    count = col.count()
    client.delete_collection(_CODEBASE_COLLECTION)
    _INDEX_META_FILE.unlink(missing_ok=True)
    return {"cleared": count, "message": "コードベースインデックスを全削除しました"}
