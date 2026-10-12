from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    # SQLite 전용 설정(SQLite 가 아닌 DATABASE_URL 이면 쓰이지 않는다).
    # 잠금 대기 시간(밀리초): 다른 연결이 쓰는 중이면 이 시간까지 기다렸다가 "database is locked" 로 실패한다
    sqlite_busy_timeout_ms: int = Field(default=10000, ge=0)
    # 저널 모드: WAL(기본, 읽기와 쓰기가 서로 막지 않음) / DELETE(예전 방식으로 되돌릴 때)
    sqlite_journal_mode: Literal["WAL", "DELETE"] = "WAL"
    # 유사 업무 검색(작업 66-4): 이 점수(0~100) 미만은 제외 / 돌려줄 최대 건수 / 후보 업무를 훑는 최대 건수(최신 회의 순)
    similar_min_score: int = Field(default=50, ge=0, le=100)
    similar_max_results: int = Field(default=10, ge=1)
    similar_scan_max: int = Field(default=300, ge=1)
    # 업무 처리 현황(작업 69-3): 전일 통계 자동 집계(켬/끔), 집계 시각(APP_TIMEZONE 기준 HH:MM, 이 시각 이후 전일 집계가 없으면 실행),
    # 미완료(진행 중+지연) 과다 주의 임계값, 집중 관리 목록 최대 건수(계정·부서 행마다)
    workload_snapshot_enabled: bool = True
    workload_snapshot_time: str = Field(default="00:10", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    workload_overload_threshold: int = Field(default=5, ge=1)
    workload_urgent_max: int = Field(default=50, ge=1)
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
    # 일시 오류(HTTP 500·502·503·504·접속 오류) 자동 재시도: generate_content 호출 하나하나에 적용. 0 이면 끔.
    # 타임아웃·429·기타 4xx·키 오류는 재시도하지 않는다. 대기는 재시도마다 2배(최대 60초). SDK 자체 재시도는 쓰지 않는다
    gemini_retry_max: int = Field(2, ge=0)
    gemini_retry_backoff_sec: float = Field(5.0, ge=0)

    # ---- 음성 업로드·처리 ----
    # 전사 엔진: fake(가짜 대본, 네트워크 없음) / gemini. 업무 추출기도 같은 값을 따른다
    stt_provider: Literal["fake", "gemini"] = "fake"
    # 업로드 음성 보관 폴더(실행 위치 기준 상대 경로 가능, data/ 는 커밋 제외). 하위에 {tenant_id}/{uuid}.{ext}
    upload_dir: Path = Path("data/uploads")
    max_upload_mb: int = 200
    # 왼쪽 메뉴 "처리 현황"이 따라가는 기간(시간): 내가 올린 회의록 중 이 시간 안에 처리가 시작된 것
    processing_track_hours: int = Field(24, ge=1)
    # 허용 확장자(쉼표 구분, 점 없이)
    allowed_audio_ext: str = "m4a,mp3,wav,mp4,webm,ogg"
    # 근거 시각 대조 기준(0~100): 근거 인용문이 전사문 구간과 이 비율(%) 이상 맞아야 서버 대조 성공으로 본다. 완전 일치는 항상 성공
    evidence_match_min: int = Field(80, ge=0, le=100)
    # 수정 회의록 업로드(엑셀) 상한: 파일 크기(KB)와 업무 행 수
    update_max_file_kb: int = 2048
    update_max_rows: int = 1000
    # 자료 파일(txt) 등록(작업 73-1): 파일 크기 상한(바이트)과 글자 수 상한. 넘으면 422
    transcript_upload_max_bytes: int = Field(default=2 * 1024 * 1024, ge=1)
    transcript_max_chars: int = Field(default=300000, ge=1)
    # 처리 방식(작업 75-1): inline = 서버가 BackgroundTasks 로 직접 처리(현행) / queue = task_queue 에 쌓고 단독 worker 프로세스가 처리
    processing_mode: Literal["inline", "queue"] = "inline"
    # 워커: 대기 작업 폴링 간격(초), 처리 중 작업을 멈춘 것으로 보는 시간(초), 멈춘 작업의 최대 처리 시도 수, 종료 신호 뒤 현재 작업을 기다리는 시간(초)
    worker_poll_seconds: float = Field(1.5, gt=0)
    worker_stale_seconds: int = Field(1800, ge=1)
    worker_max_attempts: int = Field(3, ge=1)
    worker_shutdown_grace: int = Field(30, ge=0)
    # 안건 분할 추론(작업 75-2): 청크 최대 글자 수 / 앞 청크와 겹치는 글자 수 / 교정 프롬프트에 담는 원본 응답 앞부분 글자 수
    # / LLM 호출 제한 시간(초) / 파싱 실패 때 교정 재시도 횟수(코드로 최대 1회 강제) / 5개 항목 칸당 최대 글자 수 / 안건 표지 추가 문구(쉼표 구분)
    extract_chunk_max_chars: int = Field(6000, gt=0)
    extract_chunk_overlap_chars: int = Field(500, gt=0)
    extract_response_max_chars_for_repair: int = Field(4000, gt=0)
    extract_llm_timeout_seconds: float = Field(300, gt=0)
    extract_parse_retry_max: int = Field(1, ge=1)
    extract_minutes_field_max_chars: int = Field(4000, gt=0)
    extract_agenda_markers: str = ""
    # 오디오 전처리(작업 75-1 파트 B, FFmpeg 단일 체인): 기본 꺼짐. 켜면 STT 입력 직전에 16kHz 모노 wav 로 전처리한다
    # ffmpeg_path: 기본 "ffmpeg"(PATH 검색, backend/bin 에 번들 파일이 있으면 그것을 우선) / 시간 초과(초)
    audio_preprocess_enabled: bool = False
    # true 면 전처리 실패를 처리 실패로 본다. false 면 경고만 남기고 원본 파일로 진행
    audio_preprocess_strict: bool = False
    ffmpeg_path: str = "ffmpeg"
    ffmpeg_timeout_seconds: float = Field(900, gt=0)
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
    def _check_chunk_sizes(self):
        if self.extract_chunk_overlap_chars >= self.extract_chunk_max_chars:
            raise ValueError("EXTRACT_CHUNK_OVERLAP_CHARS 는 EXTRACT_CHUNK_MAX_CHARS 보다 작아야 합니다.")
        return self

    @property
    def extract_agenda_marker_list(self) -> list[str]:
        return [m.strip() for m in self.extract_agenda_markers.split(",") if m.strip()]

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
