import tempfile
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # .env 파일을 읽는다. extra="ignore": 이 클래스에 없는 항목이 .env에 있어도 오류를 내지 않는다
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 쉼표로 구분된 문자열로 받는다 (배열로 바로 받으면 JSON 형식을 요구해 초보자가 막히기 쉽다)
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ---- 음성 등록 (docs/API-CONTRACT.md "음성 등록 API") ----
    max_upload_mb: int = 500  # 이 크기를 넘으면 413
    gpu_guard_threshold_minutes: float = 20.0  # 로컬 Whisper 예상 시간이 이 값 이상이면 409 (프로젝트 지침: 20분)
    fake_worker_enabled: bool = True  # False 이면 업로드 작업이 queued 로 남는다 (테스트/시연용)
    fake_worker_step_seconds: float = 3.0  # 가짜 처리기가 단계마다 기다리는 시간(초)

    # ---- 처리 파이프라인 (v1.9, 4단계-A) ----
    # 둘 다 fake 이면 기존 가짜 처리기(fake_worker)가 그대로 돈다. 하나라도 gemini 이면 app/pipeline 이 처리한다.
    stt_provider: Literal["fake", "gemini"] = "fake"
    llm_provider: Literal["fake", "gemini"] = "fake"
    # SecretStr: 실수로 print/로그에 찍혀도 '**********'로만 보인다. 값은 get_secret_value()로만 꺼낸다.
    gemini_api_key: SecretStr = SecretStr("")
    gemini_stt_model: str = "gemini-3.8-flash"
    gemini_llm_model: str = "gemini-3.8-flash"
    gemini_timeout_seconds: float = 300.0  # Gemini 요청 1건의 기한(초)
    # 업로드한 음성을 처리 전까지 보관하는 폴더. completed 면 지우고, failed 면 Retry 를 위해 남긴다.
    upload_dir: Path = Path(tempfile.gettempdir()) / "meeting-uploads"

    @property
    def cors_origins(self) -> list[str]:
        # "a, b" -> ["a", "b"] (공백 제거, 빈 값 제외)
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def uses_gemini(self) -> bool:
        return self.stt_provider == "gemini" or self.llm_provider == "gemini"

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key.get_secret_value().strip())


settings = Settings()
