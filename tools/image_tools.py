import base64
import io
import time
from pathlib import Path

from config import (
    ALLOWED_WORK_DIR,
    IMAGE_PROVIDER, IMAGE_MODEL, IMAGE_QUALITY, IMAGE_SIZE, IMAGE_INHERIT,
    OPENAI_API_KEY, IMAGE_OPENAI_API_KEY,
    GEMINI_API_KEY, IMAGE_GEMINI_API_KEY,
    AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION,
    IMAGE_AZURE_API_KEY, IMAGE_AZURE_ENDPOINT, IMAGE_AZURE_API_VERSION,
    FOUNDRY_API_KEY, FOUNDRY_ENDPOINT, FOUNDRY_API_VERSION,
    IMAGE_FOUNDRY_API_KEY, IMAGE_FOUNDRY_ENDPOINT, IMAGE_FOUNDRY_API_VERSION,
    WATERMARK_ENABLED, WATERMARK_TEXT, WATERMARK_POSITION, WATERMARK_COLOR, WATERMARK_OPACITY, WATERMARK_FONT_SIZE,
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
        key = OPENAI_API_KEY if IMAGE_INHERIT else (IMAGE_OPENAI_API_KEY or OPENAI_API_KEY)
        return OpenAI(api_key=key)
    elif provider == "gemini":
        key = GEMINI_API_KEY if IMAGE_INHERIT else (IMAGE_GEMINI_API_KEY or GEMINI_API_KEY)
        return OpenAI(
            api_key=key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    elif provider == "azure":
        key      = AZURE_OPENAI_API_KEY     if IMAGE_INHERIT else (IMAGE_AZURE_API_KEY     or AZURE_OPENAI_API_KEY)
        endpoint = AZURE_OPENAI_ENDPOINT    if IMAGE_INHERIT else (IMAGE_AZURE_ENDPOINT    or AZURE_OPENAI_ENDPOINT)
        version  = AZURE_OPENAI_API_VERSION if IMAGE_INHERIT else (IMAGE_AZURE_API_VERSION or AZURE_OPENAI_API_VERSION or "2024-02-01")
        # 引き継ぎOFF（専用エンドポイント）はGlobal Standard形式でBearer認証が必要
        if IMAGE_INHERIT:
            return AzureOpenAI(api_key=key, azure_endpoint=endpoint, api_version=version, max_retries=0)
        return AzureOpenAI(azure_ad_token=key, azure_endpoint=endpoint, api_version=version, max_retries=0)
    elif provider == "foundry":
        key      = FOUNDRY_API_KEY     if IMAGE_INHERIT else (IMAGE_FOUNDRY_API_KEY     or FOUNDRY_API_KEY)
        endpoint = FOUNDRY_ENDPOINT    if IMAGE_INHERIT else (IMAGE_FOUNDRY_ENDPOINT    or FOUNDRY_ENDPOINT)
        version  = FOUNDRY_API_VERSION if IMAGE_INHERIT else (IMAGE_FOUNDRY_API_VERSION or FOUNDRY_API_VERSION or "2024-12-01-preview")
        return AzureOpenAI(azure_ad_token=key, azure_endpoint=endpoint, api_version=version, max_retries=0)
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


def _save_to_workspace(b64: str, prefix: str = "generated", workspace_scope: str = "") -> str:
    """base64画像をワークスペースに保存してファイル名を返す。スコープが設定されている場合はその配下に保存。"""
    filename = f"{prefix}_{int(time.time())}.png"
    base_dir = (ALLOWED_WORK_DIR / workspace_scope) if workspace_scope else ALLOWED_WORK_DIR
    save_dir = base_dir / "AI_Output_Images"
    save_dir.mkdir(parents=True, exist_ok=True)
    (save_dir / filename).write_bytes(base64.b64decode(b64))
    rel = f"{workspace_scope}/AI_Output_Images/{filename}" if workspace_scope else f"AI_Output_Images/{filename}"
    return rel


def generate_image(prompt: str, size: str = None, quality: str = None, _workspace_scope: str = "") -> dict:
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
        saved_path = _save_to_workspace(b64, "generated", _workspace_scope)
        return {
            "image_base64": b64,
            "mime": "image/png",
            "provider": provider,
            "model": model,
            "saved_path": saved_path,
            "prompt": prompt,
            "message": f"画像を生成しました。ワークスペースに保存済み: {saved_path}",
        }
    except Exception as e:
        return {"error": f"画像生成エラー: {e}"}


def _hex_to_rgb(hex_color: str) -> tuple:
    """#rrggbb を (r, g, b) に変換する。"""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _apply_watermark_to_b64(b64: str, text: str, position: str, color: str, opacity: float, font_size: int = 0) -> str:
    """base64 PNG にウォーターマークを重畳して base64 を返す。"""
    from PIL import Image, ImageDraw, ImageFont

    img_bytes = base64.b64decode(b64)
    base = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    w, h = base.size

    # font_size が 0 または小さすぎる（< 12）場合は自動（短辺の4%）
    font_size = font_size if font_size >= 12 else max(20, int(min(w, h) * 0.04))
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    dummy = Image.new("RGBA", (1, 1))
    bbox = ImageDraw.Draw(dummy).textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    margin = int(font_size * 0.5)
    positions = {
        "topleft":     (margin, margin),
        "topright":    (w - tw - margin, margin),
        "bottomleft":  (margin, h - th - margin),
        "bottomright": (w - tw - margin, h - th - margin),
        "center":      ((w - tw) // 2, (h - th) // 2),
    }
    xy = positions.get(position, positions["bottomright"])

    alpha = int(255 * max(0.0, min(1.0, opacity)))
    rgb = _hex_to_rgb(color)
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    # 影（可読性向上）
    shadow_rgb = (0, 0, 0) if sum(rgb) > 382 else (255, 255, 255)
    draw.text((xy[0] + 2, xy[1] + 2), text, font=font, fill=(*shadow_rgb, alpha))
    draw.text(xy, text, font=font, fill=(*rgb, alpha))

    result = Image.alpha_composite(base, overlay).convert("RGB")
    buf = io.BytesIO()
    result.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def apply_auto_watermark(b64: str, workspace_scope: str = "") -> tuple[str, str]:
    """設定に基づき自動ウォーターマークを適用。(watermarked_b64, saved_path) を返す。WATERMARK_ENABLED=False なら変更なし。"""
    if not WATERMARK_ENABLED:
        return b64, ""
    new_b64 = _apply_watermark_to_b64(b64, WATERMARK_TEXT, WATERMARK_POSITION, WATERMARK_COLOR, WATERMARK_OPACITY, WATERMARK_FONT_SIZE)
    saved_path = _save_to_workspace(new_b64, "watermarked", workspace_scope)
    return new_b64, saved_path


def watermark_image(
    image_path: str,
    text: str,
    position: str = "bottomright",
    color: str = "#ffffff",
    opacity: float = 0.6,
    font_size: int = 0,
    _workspace_scope: str = "",
) -> dict:
    """ワークスペース内の画像にテキストウォーターマークを追加します。"""
    target = (ALLOWED_WORK_DIR / image_path).resolve()
    if not str(target).startswith(str(ALLOWED_WORK_DIR)):
        return {"error": "作業ディレクトリ外のファイルにはアクセスできません"}
    if not target.exists():
        return {"error": f"ファイルが見つかりません: {image_path}"}
    if position not in ("topleft", "topright", "bottomleft", "bottomright", "center"):
        return {"error": "position は topleft/topright/bottomleft/bottomright/center のいずれか"}

    try:
        src_b64 = base64.b64encode(target.read_bytes()).decode()
        b64 = _apply_watermark_to_b64(src_b64, text, position, color, opacity, font_size)
        saved_path = _save_to_workspace(b64, "watermarked", _workspace_scope)
        return {
            "image_base64": b64,
            "mime": "image/png",
            "source_path": image_path,
            "text": text,
            "position": position,
            "color": color,
            "opacity": opacity,
            "font_size": font_size,
            "saved_path": saved_path,
            "message": f"ウォーターマークを追加しました。保存先: {saved_path}",
        }
    except Exception as e:
        return {"error": f"ウォーターマーク処理エラー: {e}"}


def edit_image(image_path: str, prompt: str, size: str = None, _workspace_scope: str = "") -> dict:
    """ワークスペース内の画像を編集・清書します（img2img）。OpenAI または Gemini が必要です。"""
    provider = IMAGE_PROVIDER
    model = IMAGE_MODEL

    if provider not in ("openai", "gemini", "azure", "foundry"):
        return {"error": f"img2img は未対応のプロバイダーです（現在: {provider}）"}

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
        saved_path = _save_to_workspace(b64, "edited", _workspace_scope)
        return {
            "image_base64": b64,
            "mime": "image/png",
            "provider": provider,
            "model": model,
            "saved_path": saved_path,
            "source_path": image_path,
            "prompt": prompt,
            "message": f"画像を編集しました。ワークスペースに保存済み: {saved_path}",
        }
    except Exception as e:
        return {"error": f"画像編集エラー: {e}"}
