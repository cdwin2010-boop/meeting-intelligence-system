"""Gemini 클라이언트 만들기 (STT와 추출기가 함께 쓴다). google-genai 는 여기서만 불러온다."""
from app.config import Settings, settings as default_settings


def make_gemini_client(settings: Settings | None = None):
    """GEMINI_KEY_MODE 로 고른 키로만 만든다. 키가 비어 있으면 GeminiKeyMissingError(다른 모드로 넘어가지 않음)."""
    from google import genai
    from google.genai import types

    cfg = settings or default_settings
    api_key = cfg.selected_gemini_key()  # 먼저 키 검사(없으면 여기서 오류)
    # HttpOptions.timeout 단위는 밀리초
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=int(cfg.gemini_timeout_sec * 1000)),
    )
