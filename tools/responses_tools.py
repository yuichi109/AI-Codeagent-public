import httpx
from openai import OpenAI, AzureOpenAI


def call_responses_api(prompt: str) -> str:
    """Responses API でコードを生成して返す。write_file/edit_file の前に呼ぶ。"""
    import config

    if not config.RESPONSES_API_ENDPOINT or not config.RESPONSES_API_KEY:
        return "[ERROR] RESPONSES_API_ENDPOINT / RESPONSES_API_KEY が未設定です"

    if config.RESPONSES_API_VERSION:
        client = AzureOpenAI(
            azure_endpoint=config.RESPONSES_API_ENDPOINT,
            api_key=config.RESPONSES_API_KEY,
            api_version=config.RESPONSES_API_VERSION,
            http_client=httpx.Client(trust_env=False),
        )
    else:
        client = OpenAI(
            base_url=config.RESPONSES_API_ENDPOINT.rstrip("/"),
            api_key=config.RESPONSES_API_KEY,
            http_client=httpx.Client(trust_env=False),
        )

    try:
        response = client.responses.create(
            model=config.RESPONSES_API_MODEL,
            input=prompt,
        )
        return response.output_text
    except Exception as e:
        return f"[ERROR] Responses API 呼び出し失敗: {e}"
