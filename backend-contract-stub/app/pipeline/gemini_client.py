"""Gemini 클라이언트 만들기 (STT와 추출기가 함께 쓴다). google-genai 는 gemini 모드에서만 불러온다."""
from app.config import settings


def make_gemini_client():
    from google import genai
    from google.genai import types

    # HttpOptions.timeout 단위는 밀리초 (설치된 google-genai 에서 확인)
    return genai.Client(
        api_key=settings.gemini_api_key.get_secret_value(),
        http_options=types.HttpOptions(timeout=int(settings.gemini_timeout_seconds * 1000)),
    )
