"""
RAG実績DB ツール

ChromaDB を使って3種類の知見を蓄積・検索する。
  - success   : 動いた手順・解決策
  - prohibited: やってはいけない操作
  - caution   : 間違えやすい・ハマりやすい罠

DBは ~/AI-Codeagent/.rag_db/ に永続化。GitLabで全PC同期する想定。
埋め込みモードは .env の RAG_EMBED_MODE で切り替え:
  - "default": ChromaDB 内蔵（all-MiniLM-L6-v2、ローカル・無料）
  - "azure"  : Azure OpenAI text-embedding モデル（高精度・日本語対応）

モードが変わった場合は _ensure_embed_mode_consistent() が自動で全件再変換する。
"""

import json
import uuid
from datetime import date
from pathlib import Path

import chromadb
from chromadb.config import Settings

_DB_DIR = Path(__file__).parent.parent / ".rag_db"
_COLLECTION_NAME = "knowledge"
_EMBED_MODE_FILE = _DB_DIR / "_embed_mode.json"

_VALID_TYPES = {"success", "prohibited", "caution"}
_VALID_STATUSES = {"active", "deprecated"}


def _current_mode() -> str:
    from config import RAG_EMBED_MODE
    return RAG_EMBED_MODE or "default"



class _AzureEmbeddingFunction:
    """trust_env=False の AzureOpenAI クライアントを使う埋め込み関数。
    社内プロキシ（407）をバイパスするため chromadb 組み込みの OpenAIEmbeddingFunction は使わない。"""

    @staticmethod
    def name() -> str:
        return "azure-openai"

    def __init__(self, endpoint: str, api_key: str, deployment: str, api_version: str):
        import httpx
        from openai import AzureOpenAI
        self._client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            http_client=httpx.Client(trust_env=False),
        )
        self._deployment = deployment

    def __call__(self, input: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self._deployment, input=input)
        return [item.embedding for item in response.data]

    def embed_query(self, input) -> list[float]:
        texts = [input] if isinstance(input, str) else input
        return self(texts)[0]


def _get_embedding_function(mode: str = None):
    """指定モード（省略時は設定値）の embedding function を返す。"""
    if mode is None:
        mode = _current_mode()

    if mode == "azure":
        from config import RAG_EMBED_ENDPOINT, RAG_EMBED_API_KEY, RAG_EMBED_DEPLOYMENT, RAG_EMBED_API_VERSION
        if RAG_EMBED_ENDPOINT and RAG_EMBED_API_KEY and RAG_EMBED_DEPLOYMENT:
            return _AzureEmbeddingFunction(
                endpoint=RAG_EMBED_ENDPOINT.rstrip("/"),
                api_key=RAG_EMBED_API_KEY,
                deployment=RAG_EMBED_DEPLOYMENT,
                api_version=RAG_EMBED_API_VERSION,
            )
    return None  # None = ChromaDB 内蔵 EF（onnxruntime）


def _get_client():
    return chromadb.PersistentClient(
        path=str(_DB_DIR),
        settings=Settings(anonymized_telemetry=False),
    )


def _reset_collection(client, kwargs):
    """既存データを新しい EF で再変換してコレクションを作り直す。データは保持される。"""
    # 既存データを取得（テキストだけ取り出す・embeddings は捨てる）
    existing_ids, existing_docs, existing_metas = [], [], []
    try:
        old_col = client.get_collection(_COLLECTION_NAME)
        result = old_col.get(include=["documents", "metadatas"])
        existing_ids  = result.get("ids", [])
        existing_docs = result.get("documents", [])
        existing_metas = result.get("metadatas", [])
    except Exception:
        pass

    # 旧コレクション削除
    try:
        client.delete_collection(_COLLECTION_NAME)
    except Exception:
        pass
    _EMBED_MODE_FILE.unlink(missing_ok=True)

    # 新 EF でコレクション再作成
    new_col = client.get_or_create_collection(**kwargs)

    # 既存データを新 EF で再投入（chromaDB が新しい embedding を自動生成）
    if existing_ids:
        new_col.add(ids=existing_ids, documents=existing_docs, metadatas=existing_metas)
        print(f"[rag] 次元不一致を検出 → {len(existing_ids)}件を新EFで再変換しました")

    return new_col


def _get_collection():
    """現在の埋め込みモードでコレクションを取得する。モード変更時は自動再変換。"""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_embed_mode_consistent()

    client = _get_client()
    ef = _get_embedding_function()
    kwargs = {"name": _COLLECTION_NAME, "metadata": {"hnsw:space": "cosine"}}
    if ef is not None:
        kwargs["embedding_function"] = ef
    try:
        return client.get_or_create_collection(**kwargs)
    except Exception as e:
        msg = str(e)
        if "onnxruntime" in msg.lower() or "onnx" in msg.lower():
            raise RuntimeError(
                "RAG初期化失敗: onnxruntime が見つかりません。\n"
                "pip install onnxruntime を実行してサーバーを再起動してください。\n"
                f"詳細: {msg}"
            ) from e
        # EF競合 or 次元不一致 → リセットして再作成
        if ("embedding function" in msg.lower() and "conflict" in msg.lower()) or \
           "dimension" in msg.lower():
            return _reset_collection(client, kwargs)
        raise


def _saved_mode() -> str | None:
    """前回保存した埋め込みモードを返す。未記録なら None。"""
    if _EMBED_MODE_FILE.exists():
        try:
            return json.loads(_EMBED_MODE_FILE.read_text(encoding="utf-8")).get("mode")
        except Exception:
            pass
    return None


def _save_mode(mode: str):
    _EMBED_MODE_FILE.write_text(json.dumps({"mode": mode}), encoding="utf-8")


def _ensure_embed_mode_consistent():
    """
    埋め込みモードが変わっていたら全記録を新モデルで再変換する。
    ユーザーは何もしなくてよい。
    """
    current = _current_mode()
    saved = _saved_mode()

    if saved is None:
        # 初回 or モードファイルなし → 現在のモードを記録するだけ
        _save_mode(current)
        return

    if saved == current:
        return  # モード変更なし

    # ---- モード変更検出: 全件取り出して再投入 ----
    print(f"[rag] 埋め込みモード変更を検出: {saved} → {current}。全記録を再変換します...")

    client = _get_client()

    # 旧コレクションから全件取得
    old_ef = _get_embedding_function(saved)
    old_kwargs = {"name": _COLLECTION_NAME, "metadata": {"hnsw:space": "cosine"}}
    if old_ef is not None:
        old_kwargs["embedding_function"] = old_ef

    try:
        old_col = client.get_collection(**old_kwargs)
        existing = old_col.get(include=["documents", "metadatas"])
    except Exception:
        # 旧コレクションが取得できなければモードファイルは更新せずスキップ
        # （次回も再変換を試みられるよう saved モードを保持する）
        return

    ids = existing.get("ids", [])
    docs = existing.get("documents", [])
    metas = existing.get("metadatas", [])

    # 新モードで仮コレクションに再投入してから旧コレクションを削除（失敗時にデータを保持）
    tmp_name = _COLLECTION_NAME + "_tmp"
    new_ef = _get_embedding_function(current)
    new_kwargs = {"name": tmp_name, "metadata": {"hnsw:space": "cosine"}}
    if new_ef is not None:
        new_kwargs["embedding_function"] = new_ef

    try:
        tmp_col = client.get_or_create_collection(**new_kwargs)
        if ids:
            tmp_col.add(ids=ids, documents=docs, metadatas=metas)
        print(f"[rag] 仮コレクションへの再変換完了（{len(ids)}件）")
    except Exception as e:
        # 再変換失敗 → 仮コレクション削除してモードファイルは更新しない
        try:
            client.delete_collection(tmp_name)
        except Exception:
            pass
        print(f"[rag] 再変換失敗（旧データ保持）: {e}")
        return

    # 成功後に旧コレクションを削除して本番名にリネーム
    client.delete_collection(_COLLECTION_NAME)
    client.delete_collection(tmp_name)
    final_ef = _get_embedding_function(current)
    final_kwargs = {"name": _COLLECTION_NAME, "metadata": {"hnsw:space": "cosine"}}
    if final_ef is not None:
        final_kwargs["embedding_function"] = final_ef
    final_col = client.get_or_create_collection(**final_kwargs)
    if ids:
        final_col.add(ids=ids, documents=docs, metadatas=metas)

    _save_mode(current)
    print(f"[rag] モード切替完了: {saved} → {current}（{len(ids)}件）")


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
    doc = {"type": record_type, "status": "active", "tags": ",".join(tags), "date": today, "last_verified": today}
    try:
        col.add(ids=[record_id], documents=[summary], metadatas=[doc])
    except Exception as e:
        if "dimension" in str(e).lower():
            # 次元不一致 → コレクションリセットして再試行
            client = _get_client()
            ef = _get_embedding_function()
            kwargs = {"name": _COLLECTION_NAME, "metadata": {"hnsw:space": "cosine"}}
            if ef is not None:
                kwargs["embedding_function"] = ef
            col = _reset_collection(client, kwargs)
            col.add(ids=[record_id], documents=[summary], metadatas=[doc])
        else:
            raise

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

    MIN_RELEVANCE = 0.3  # これ以下は無関係とみなす

    results = []
    for i, (doc, meta, dist) in enumerate(zip(
        res["documents"][0],
        res["metadatas"][0],
        res["distances"][0],
    )):
        relevance = round(1 - dist, 3)
        if relevance < MIN_RELEVANCE:
            continue
        results.append({
            "id": res["ids"][0][i],
            "summary": doc,
            "type": meta.get("type"),
            "tags": [t for t in meta.get("tags", "").split(",") if t],
            "date": meta.get("date"),
            "last_verified": meta.get("last_verified"),
            "relevance": relevance,
        })

    return {
        "results": results,
        "query": query,
        "record_type_filter": record_type,
        "total_in_db": total,
        "message": "該当する記録なし" if not results else None,
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

    for i, r in enumerate(records, 1):
        r["no"] = i
        r["short_id"] = r["id"][:8]

    return {
        "records": records,
        "count": len(records),
        "total_in_db": total,
        "filter_type": record_type,
        "filter_status": status,
    }
