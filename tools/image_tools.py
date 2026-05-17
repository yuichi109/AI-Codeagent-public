import base64
import io
from pathlib import Path

from config import (
    ALLOWED_WORK_DIR,
    IMAGE_PROVIDER, IMAGE_MODEL, IMAGE_QUALITY, IMAGE_SIZE,
    OPENAI_API_KEY,
    GEMINI_API_KEY,
    AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION,
    FOUNDRY_API_KEY, FOUNDRY_ENDPOINT, FOUNDRY_API_VERSION,
)

# プロバイダー別の代表的な画像生成モデル一覧（setup.html のプルダウン用）
IMAGE_MODELS_BY_PROVIDER = {
    "openai":  ["gpt-image-2", "gpt-image-1"],
    "gemini":  ["gemini-2.5-flash-image"],
    "azure":   ["gpt-image-1", "dall-e-3"],
    "foundry": ["gpt-image-1"],
}


def _make_client(provider: str):
    from openai import OpenAI, AzureOpenAI
    if provider == "openai":
        return OpenAI(api_key=OPENAI_API_KEY)
    elif provider == "gemini":
        return OpenAI(
            api_key=GEMINI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    elif provider == "azure":
        return AzureOpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=AZURE_OPENAI_API_VERSION or "2024-02-01",
        )
    elif provider == "foundry":
        return AzureOpenAI(
            api_key=FOUNDRY_API_KEY,
            azure_endpoint=FOUNDRY_ENDPOINT,
            api_version=FOUNDRY_API_VERSION or "2024-12-01-preview",
        )
    else:
        raise ValueError(f"未対応のプロバイダー: {provider}")


def _b64_from_response(data) -> str:
    if data.b64_json:
        return data.b64_json
    if data.url:
        import requests as _req
        r = _req.get(data.url, timeout=30)
        r.raise_for_status()
        return base64.b64encode(r.content).decode()
    raise ValueError("レスポンスに画像データがありません")


def generate_image(prompt: str, size: str = None, quality: str = None) -> dict:
    """テキストプロンプトから画像を生成します。セットアップ画面で設定したプロバイダー/モデルを使用します。"""
    provider = IMAGE_PROVIDER
    model = IMAGE_MODEL
    sz = size or IMAGE_SIZE or "1024x1024"
    ql = quality or IMAGE_QUALITY

    try:
        client = _make_client(provider)
        kwargs: dict = dict(model=model, prompt=prompt, n=1)
        if sz and sz != "auto":
            kwargs["size"] = sz
        # quality は OpenAI の gpt-image-* のみ対応
        if ql and provider == "openai" and "image" in model:
            kwargs["quality"] = ql

        resp = client.images.generate(**kwargs)
        b64 = _b64_from_response(resp.data[0])
        return {
            "image_base64": b64,
            "mime": "image/png",
            "prompt": prompt,
            "provider": provider,
            "model": model,
            "message": "画像を生成しました",
        }
    except Exception as e:
        return {"error": f"画像生成エラー: {e}"}


def edit_image(image_path: str, prompt: str, size: str = None) -> dict:
    """ワークスペース内の画像を編集・清書します（img2img）。OpenAI または Gemini が必要です。"""
    provider = IMAGE_PROVIDER
    model = IMAGE_MODEL

    if provider not in ("openai", "gemini"):
        return {"error": f"img2img は OpenAI / Gemini のみ対応しています（現在: {provider}）"}

    target = (ALLOWED_WORK_DIR / image_path).resolve()
    if not str(target).startswith(str(ALLOWED_WORK_DIR)):
        return {"error": "作業ディレクトリ外のファイルにはアクセスできません"}
    if not target.exists():
        return {"error": f"ファイルが見つかりません: {image_path}"}

    sz = size or IMAGE_SIZE or "1024x1024"

    try:
        client = _make_client(provider)
        with open(target, "rb") as f:
            image_data = f.read()

        buf = io.BytesIO(image_data)
        kwargs: dict = dict(
            model=model,
            image=(target.name, buf, "image/png"),
            prompt=prompt,
            n=1,
        )
        if sz and sz != "auto":
            kwargs["size"] = sz

        resp = client.images.edit(**kwargs)
        b64 = _b64_from_response(resp.data[0])
        return {
            "image_base64": b64,
            "mime": "image/png",
            "prompt": prompt,
            "source_path": image_path,
            "provider": provider,
            "model": model,
            "message": "画像を編集しました",
        }
    except Exception as e:
        return {"error": f"画像編集エラー: {e}"}
