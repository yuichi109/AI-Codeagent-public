"""
RAG実績DB ツール

ChromaDB を使って3種類の知見を蓄積・検索する。
  - success   : 動いた手順・解決策
  - prohibited: やってはいけない操作
  - caution   : 間違えやすい・ハマりやすい罠

DBは ~/AI-Codeagent/.rag_db/ に永続化。GitLabで全PC同期する想定。
"""

import uuid
from datetime import date
from pathlib import Path

import chromadb
from chromadb.config import Settings

_DB_DIR = Path(__file__).parent.parent / ".rag_db"
_COLLECTION_NAME = "knowledge"

_VALID_TYPES = {"success", "prohibited", "caution"}
_VALID_STATUSES = {"active", "deprecated"}


def _get_collection():
    client = chromadb.PersistentClient(
        path=str(_DB_DIR),
        settings=Settings(anonymized_telemetry=False),
    )
    return client.get_or_create_collection(
        name=_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def rag_save(summary: str, record_type: str, tags: list = None) -> dict:
    """
    知見をRAGデータベースに保存します。

    エージェントがタスク完了・エラー解決・問題発見時に「記録しますか？」と
    ユーザーに確認を取ってから呼び出してください。

    record_type:
      - success   : 動いた手順・解決策
      - prohibited: やってはいけない操作（絶対禁止）
      - caution   : 間違えやすい・ハマりやすい罠（注意事項）
    """
    if record_type not in _VALID_TYPES:
        return {"error": f"record_type は {_VALID_TYPES} のいずれかを指定してください"}
    if not summary or not summary.strip():
        return {"error": "summary が空です"}

    tags = tags or []
    record_id = str(uuid.uuid4())
    today = date.today().isoformat()

    col = _get_collection()
    col.add(
        ids=[record_id],
        documents=[summary],
        metadatas=[{
            "type": record_type,
            "status": "active",
            "tags": ",".join(tags),
            "date": today,
            "last_verified": today,
        }],
    )

    type_label = {"success": "成功実績", "prohibited": "禁止事項", "caution": "注意事例"}[record_type]
    return {
        "saved": True,
        "id": record_id,
        "type": record_type,
        "type_label": type_label,
        "summary": summary,
        "tags": tags,
        "date": today,
    }


def rag_search(query: str, record_type: str = None, n_results: int = 5) -> dict:
    """
    RAGデータベースから関連する知見を検索します。

    タスク開始前に prohibited を検索して禁止事項を確認し、
    caution で注意点を、success で参考手順を取得するために使います。

    record_type を省略すると全タイプを横断検索します（active のみ）。
    """
    if not query or not query.strip():
        return {"error": "query が空です"}
    if record_type and record_type not in _VALID_TYPES:
        return {"error": f"record_type は {_VALID_TYPES} のいずれか、または省略してください"}

    col = _get_collection()
    total = col.count()
    if total == 0:
        return {"results": [], "total_in_db": 0, "message": "DBにまだ記録がありません"}

    where = {"status": {"$eq": "active"}}
    if record_type:
        where = {"$and": [{"status": {"$eq": "active"}}, {"type": {"$eq": record_type}}]}

    n = min(n_results, total)
    res = col.query(
        query_texts=[query],
        n_results=n,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    results = []
    for doc, meta, dist in zip(
        res["documents"][0],
        res["metadatas"][0],
        res["distances"][0],
    ):
        results.append({
            "id": res["ids"][0][results.__len__()],  # index trick
            "summary": doc,
            "type": meta.get("type"),
            "tags": [t for t in meta.get("tags", "").split(",") if t],
            "date": meta.get("date"),
            "last_verified": meta.get("last_verified"),
            "relevance": round(1 - dist, 3),
        })

    # id を正しく取得し直す
    for i, item in enumerate(results):
        item["id"] = res["ids"][0][i]

    return {
        "results": results,
        "query": query,
        "record_type_filter": record_type,
        "total_in_db": total,
    }


def rag_update_status(record_id: str, new_status: str, reason: str = "") -> dict:
    """
    記録のステータスを更新します。

    古くなった・無効になった記録を deprecated に変更します。
    削除はせず deprecated として残します（履歴保持のため）。

    new_status: "active" | "deprecated"
    """
    if new_status not in _VALID_STATUSES:
        return {"error": f"new_status は {_VALID_STATUSES} のいずれかを指定してください"}

    col = _get_collection()
    existing = col.get(ids=[record_id], include=["metadatas", "documents"])
    if not existing["ids"]:
        return {"error": f"ID '{record_id}' の記録が見つかりません"}

    meta = existing["metadatas"][0]
    meta["status"] = new_status
    meta["last_verified"] = date.today().isoformat()
    if reason:
        meta["deprecation_reason"] = reason

    col.update(ids=[record_id], metadatas=[meta])

    return {
        "updated": True,
        "id": record_id,
        "new_status": new_status,
        "reason": reason,
        "summary": existing["documents"][0],
    }


def rag_list(record_type: str = None, status: str = "active") -> dict:
    """
    RAGデータベースの記録一覧を取得します。

    /rag-review スキルでユーザーに記録を見せて古いものを整理するために使います。
    record_type を省略すると全タイプを返します。
    status: "active" | "deprecated" | "all"
    """
    col = _get_collection()
    total = col.count()
    if total == 0:
        return {"records": [], "total": 0, "message": "DBにまだ記録がありません"}

    if status == "all":
        where = None
    elif record_type:
        where = {"$and": [{"status": {"$eq": status}}, {"type": {"$eq": record_type}}]}
    else:
        where = {"status": {"$eq": status}}

    res = col.get(
        where=where,
        include=["documents", "metadatas"],
        limit=200,
    )

    records = []
    for rid, doc, meta in zip(res["ids"], res["documents"], res["metadatas"]):
        records.append({
            "id": rid,
            "summary": doc,
            "type": meta.get("type"),
            "status": meta.get("status"),
            "tags": [t for t in meta.get("tags", "").split(",") if t],
            "date": meta.get("date"),
            "last_verified": meta.get("last_verified"),
        })

    records.sort(key=lambda r: (r["type"], r["date"]), reverse=True)

    return {
        "records": records,
        "count": len(records),
        "total_in_db": total,
        "filter_type": record_type,
        "filter_status": status,
    }
