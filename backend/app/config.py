from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 개발용 기본 서명 키. dev 가 아닌 환경에서 이 값 그대로면 서버 시작을 거부한다
DEV_SECRET_KEY = "dev-only-insecure-secret-key-change-me"
# dev 가 아닌 환경의 최소 키 길이(HS256 권장: 32바이트 이상)
MIN_SECRET_KEY_LENGTH = 32


class GeminiKeyMissingError(RuntimeError):
    """선택된 GEMINI_KEY_MODE 의 키가 비어 있음. 메시지에는 설정 이름만 넣고 키 값은 넣지 않는다."""


class Settings(BaseSettings):
    # backend/.env 를 읽는다(환경변수가 .env 보다 우선). 이 클래스에 없는 항목은 무시한다
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # DB 연결 주소. 기본은 backend/data/app.db (실행 위치 기준 상대 경로, data/ 는 커밋 제외)
    database_url: str = "sqlite:///./data/app.db"
    # 실행 환경 구분: dev / test / prod
    app_env: str = "dev"

    # 토큰 서명 키. SecretStr: 실수로 출력해도 '**********'로만 보인다
    secret_key: SecretStr = SecretStr(DEV_SECRET_KEY)
    # 로그인 토큰 유효 시간(분)
    access_token_minutes: int = 480

    # ---- Gemini (STT·업무 추출) ----
    # 어느 키를 쓸지는 이 값으로만 정한다. 무료 키가 429에 걸려도 유료 키로 자동 전환하지 않는다(모르는 새 비용 방지)
    gemini_key_mode: Literal["free", "paid"] = "free"
    gemini_api_key: SecretStr = SecretStr("")  # 무료 키
    gemini_paid_api_key: SecretStr = SecretStr("")  # 유료 키
    gemini_model: str = "gemini-3.8-flash"  # STT·추출 공통 모델
    gemini_timeout_sec: float = 900.0  # Gemini 요청 1건·파일 처리 대기의 기한(초)

    # ---- 음성 업로드·처리 ----
    # 전사 엔진: fake(가짜 대본, 네트워크 없음) / gemini. 업무 추출기도 같은 값을 따른다
    stt_provider: Literal["fake", "gemini"] = "fake"
    # 업로드 음성 보관 폴더(실행 위치 기준 상대 경로 가능, data/ 는 커밋 제외). 하위에 {tenant_id}/{uuid}.{ext}
    upload_dir: Path = Path("data/uploads")
    max_upload_mb: int = 200
    # 허용 확장자(쉼표 구분, 점 없이)
    allowed_audio_ext: str = "m4a,mp3,wav,mp4,webm,ogg"
    # 회의 일시를 현지 시각으로 바꿀 때 쓰는 시간대(추출기의 날짜·요일 기준)
    app_timezone: str = "Asia/Seoul"

    # ---- 메일 (기본 꺼짐) ----
    # false 면 발송 작업이 아무것도 보내지 않고 대기 메일을 skipped(mail_disabled)로 처리한다
    mail_enabled: bool = False
    # SMTP 값은 로그·예외·이벤트에 넣지 않는다
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    # 메일 본문의 로그인 링크
    app_base_url: str = "http://localhost:3000"

    # ---- CORS ----
    # 브라우저에서 이 API 를 부를 수 있는 프론트 주소(쉼표 구분). '*'(모든 출처 허용)는 시작 거부
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _refuse_wildcard_cors(self):
        if "*" in self.cors_origins:
            raise ValueError("CORS_ORIGINS 에 '*' 는 쓸 수 없습니다. 허용할 프론트 주소를 쉼표로 나열하세요.")
        return self

    @property
    def allowed_audio_extensions(self) -> set[str]:
        return {e.strip().lower().lstrip(".") for e in self.allowed_audio_ext.split(",") if e.strip()}

    def selected_gemini_key(self) -> str:
        """GEMINI_KEY_MODE 로 고른 키 값. 비어 있으면 오류(다른 모드의 키로 넘어가지 않음).
        클라이언트 생성에만 쓰고 출력·로그·예외 메시지에는 넣지 않는다."""
        if self.gemini_key_mode == "paid":
            key, name = self.gemini_paid_api_key, "GEMINI_PAID_API_KEY"
        else:
            key, name = self.gemini_api_key, "GEMINI_API_KEY"
        value = key.get_secret_value().strip()
        if not value:
            raise GeminiKeyMissingError(f"GEMINI_KEY_MODE={self.gemini_key_mode} 인데 {name} 가 비어 있습니다.")
        return value

    def all_gemini_keys(self) -> list[str]:
        """오류 문구 가리기(redact)용: 설정된 키 전부(무료·유료)"""
        keys = (self.gemini_api_key.get_secret_value().strip(), self.gemini_paid_api_key.get_secret_value().strip())
        return [k for k in keys if k]

    @model_validator(mode="after")
    def _refuse_default_secret_outside_dev(self):
        key = self.secret_key.get_secret_value()
        # .env 에 SECRET_KEY= 처럼 빈 값이면 기본값으로 본다(빈 키로 서명하지 않도록)
        if not key:
            self.secret_key = SecretStr(DEV_SECRET_KEY)
            key = DEV_SECRET_KEY
        if self.app_env != "dev" and (key == DEV_SECRET_KEY or len(key) < MIN_SECRET_KEY_LENGTH):
            raise ValueError(
                f"APP_ENV 가 dev 가 아니면 SECRET_KEY 를 .env 에 {MIN_SECRET_KEY_LENGTH}자 이상 무작위 값으로 설정해야 합니다."
            )
        return self


settings = Settings()
