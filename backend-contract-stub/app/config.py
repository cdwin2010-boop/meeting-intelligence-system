from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


# 저장소 안 데이터 폴더(backend-contract-stub/data). 실제 회의 내용이 들어가므로 .gitignore 로 커밋 제외
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


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
    gemini_api_key: SecretStr = SecretStr("")  # 무료 키
    gemini_paid_api_key: SecretStr = SecretStr("")  # 유료 키
    # 어느 키를 쓸지는 이 값으로만 정한다. 무료 키가 429에 걸려도 유료 키로 자동 전환하지 않는다(모르는 새 비용 방지).
    gemini_key_mode: Literal["free", "paid"] = "free"
    gemini_stt_model: str = "gemini-2.5-flash"
    gemini_llm_model: str = "gemini-2.5-flash"
    gemini_timeout_seconds: float = 300.0  # Gemini 요청 1건의 기한(초)
    # 업로드한 음성을 처리 전까지 보관하는 폴더. completed 면 지우고, failed 면 Retry 를 위해 남긴다.
    # (v1.9.10) 시스템 임시 폴더는 OS가 비울 수 있어 재시작 뒤 Retry 가 깨질 수 있다 → 저장소 안 data/uploads (.gitignore 제외)
    upload_dir: Path = _DATA_DIR / "uploads"
    # (v1.9.10) 등록 데이터(회의·전사 원문·액션아이템·작업)를 담는 SQLite 파일. 재시작해도 유지되고,
    # 초기화는 사용자가 `python -m scripts.reset_db --yes` 로 직접 실행할 때만 한다. 환경변수: STUB_DB_PATH
    stub_db_path: Path = _DATA_DIR / "stub.db"

    @property
    def cors_origins(self) -> list[str]:
        # "a, b" -> ["a", "b"] (공백 제거, 빈 값 제외)
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def uses_gemini(self) -> bool:
        return self.stt_provider == "gemini" or self.llm_provider == "gemini"

    @property
    def gemini_key_env_name(self) -> str:
        """현재 모드가 쓰는 키의 환경변수 이름 (안내 문구용. 값이 아님)"""
        return "GEMINI_PAID_API_KEY" if self.gemini_key_mode == "paid" else "GEMINI_API_KEY"

    def selected_gemini_key(self) -> str:
        """GEMINI_KEY_MODE 로 고른 키 값. 클라이언트 생성에만 쓰고 출력·로그에는 절대 쓰지 않는다."""
        key = self.gemini_paid_api_key if self.gemini_key_mode == "paid" else self.gemini_api_key
        return key.get_secret_value().strip()

    def all_gemini_keys(self) -> list[str]:
        """오류 문구 가리기(redact)용: 설정된 키 전부 (무료·유료)"""
        keys = (self.gemini_api_key.get_secret_value().strip(), self.gemini_paid_api_key.get_secret_value().strip())
        return [k for k in keys if k]

    @property
    def has_gemini_key(self) -> bool:
        """현재 모드의 키가 있는지 (다른 모드의 키는 보지 않는다)"""
        return bool(self.selected_gemini_key())


settings = Settings()
