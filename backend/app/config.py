from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

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
    # 즉시 변환(작업 88-1): 대기열 맨 앞으로 올릴 때 부여하는 우선순위 값(기본 0 보다 커야 함) / 워커 생존 신호를 쓰는 간격(초)
    # / 마지막 신호가 이 시간(초)보다 오래되면 워커 응답 없음(workerAlive=false). 간격보다 충분히 커야 한다
    # 회의 일시(작업 88-4): 현재 시각보다 이만큼(분)까지는 시계 오차로 보고 허용한다. 그보다 미래면 거부(422)
    meeting_at_future_tolerance_minutes: int = Field(10, ge=0)
    queue_priority_immediate: int = Field(100, ge=1)
    worker_heartbeat_interval_sec: float = Field(5.0, gt=0)
    worker_heartbeat_stale_sec: int = Field(30, ge=1)
    # 안건 분할 추론(작업 75-2): 청크 최대 글자 수 / 앞 청크와 겹치는 글자 수 / 교정 프롬프트에 담는 원본 응답 앞부분 글자 수
    # / LLM 호출 제한 시간(초) / 파싱 실패 때 교정 재시도 횟수(코드로 최대 1회 강제) / 5개 항목 칸당 최대 글자 수 / 안건 표지 추가 문구(쉼표 구분)
    extract_chunk_max_chars: int = Field(6000, gt=0)
    extract_chunk_overlap_chars: int = Field(500, gt=0)
    extract_response_max_chars_for_repair: int = Field(4000, gt=0)
    extract_llm_timeout_seconds: float = Field(300, gt=0)
    extract_parse_retry_max: int = Field(1, ge=1)
    extract_minutes_field_max_chars: int = Field(4000, gt=0)
    extract_agenda_markers: str = ""
    # 하드웨어 감지·권장 모델 프로파일(작업 77-1, 도구 scripts/detect_hardware.py 에서만 사용. 현행 처리 흐름은 바꾸지 않는다)
    # llm_profile: "auto"(GPU 를 감지해 선택) 또는 프로파일 id 로 고정(고정하면 감지하지 않는다)
    # hardware_profiles_path: 프로파일 파일(backend 폴더 기준 상대 경로 가능) / hardware_min_free_ratio: 여유 VRAM 이 총량의 이 비율 미만이면 한 단계 낮춘다
    llm_profile: str = "auto"
    hardware_profiles_path: Path = Path("config/model_profiles.json")
    hardware_min_free_ratio: float = Field(0.7, gt=0, le=1)
    # 추출(LLM) 엔진(작업 81-1): auto = STT_PROVIDER 를 따른다(현행 동작) / gemini / local(로컬 OpenAI 호환 서버) / fake
    # 로컬 LLM: base_url 은 ".../v1" 까지 포함. 키는 보통 불필요("not-needed"). JSON 모드를 서버가 거부하면 자동으로 끄고 1회 재요청한다.
    # allow_external_host 가 false(기본)면 루프백·사설망 호스트만 허용(보안 원칙: 외부망 연동 기본 OFF, 변경은 시스템 관리자만)
    llm_provider: Literal["auto", "gemini", "local", "fake"] = "auto"
    local_llm_base_url: str = ""
    local_llm_model: str = ""
    local_llm_api_key: SecretStr = SecretStr("not-needed")
    local_llm_json_mode: bool = True
    local_llm_allow_external_host: bool = False
    local_llm_max_output_tokens: int = Field(4096, ge=1)
    # 시스템 상태 점검(작업 A-1): 디스크 여유 공간 기준(MB). 여유 < DISK_MIN_FREE_MB 면 업로드를 거절하고, < DISK_WARN_FREE_MB 면 경고 상태.
    disk_warn_free_mb: int = Field(5120, ge=0)
    disk_min_free_mb: int = Field(1024, ge=0)
    # nvidia-smi 조회 제한 시간(초). 읽지 못하면 "확인 불가"
    gpu_status_timeout_seconds: float = Field(3.0, gt=0, le=30)
    # 워커 자동 재기동(worker_supervisor.py): 비정상 종료 시 이 횟수까지만 다시 띄운다(창 안의 횟수, 무한 재시도 금지).
    # 창(WORKER_RESTART_WINDOW_SEC)을 넘겨 안정적으로 돈 뒤의 종료는 횟수를 새로 센다. 한도를 넘기면 중단하고 상태 파일에 기록한다.
    worker_restart_max: int = Field(3, ge=0, le=20)
    worker_restart_window_sec: int = Field(600, ge=1)
    worker_restart_delay_sec: float = Field(5.0, ge=0, le=300)
    worker_supervisor_state_path: Path = Path("data/worker_supervisor.json")
    # 엔진 시험 모드(작업 91-1, 시험 전용): 기본 꺼짐(납품 설정). 켜면 관리자 이상이 회의 등록·재처리 때 엔진을 고르고,
    # "OpenAI 호환 HTTP(원격·시험용)" 엔진 설정·로딩 테스트를 쓸 수 있다. 꺼져 있으면 관련 요청은 서버가 거부한다(화면 숨김만이 아님).
    # 원격 서버 값(주소·모델·키)은 여기 기본값이고, 화면에서 저장한 값은 Git 제외 파일(engine_test_settings_path)이 덮어쓴다.
    # 자동 엔진 전환은 없다: 원격 연결 실패는 Gemini 로 넘어가지 않고 실패(remote_unavailable)로 끝난다.
    engine_test_mode: bool = False
    # 시험 모드에서 엔진을 고르지 않았을 때의 기본: default(기존 설정 엔진) / remote(원격·시험용)
    engine_test_default: Literal["default", "remote"] = "default"
    engine_test_settings_path: Path = Path("data/engine_test.json")
    remote_llm_base_url: str = ""
    remote_llm_model: str = ""
    remote_llm_api_key: SecretStr = SecretStr("")
    remote_llm_timeout_seconds: float = Field(300, gt=0, le=3600)
    # 원격 엔진의 청크 한도(글자). 0 이면 EXTRACT_CHUNK_MAX_CHARS 를 쓴다
    remote_llm_chunk_max_chars: int = Field(0, ge=0)
    # 원격 모델 서버의 맥락 길이(n_ctx, 토큰). 구간 글자 수 상한을 (맥락 - 출력 예약 - 고정 지시) 기준으로 보수적으로 자동 계산한다(작업 B-1d)
    remote_llm_context_tokens: int = Field(8192, ge=1024)
    # 내부 전용 작업 수 확인(/internal/queue-status, 작업 B-1c-1): 요청의 실제 연결 주소가 루프백일 때만 응답한다. 기본 켬
    internal_queue_status_enabled: bool = True
    # 원격 엔진 프로파일 나머지 항목(app/pipeline/engine_profile.py): JSON 응답 형식 지원·글자당 토큰(보수적, 한국어는 크게)·
    # 서버 토크나이저 사용(옵션, 기본 꺼짐, 아직 값만)·요청에 맥락 길이 명시(향후 Ollama num_ctx, 아직 값만)
    remote_llm_json_format: bool = True
    remote_llm_tokens_per_char: float = Field(1.3, ge=0.5, le=4.0)
    remote_llm_use_server_tokenizer: bool = False
    remote_llm_send_context_option: bool = False
    # 오디오 전처리(작업 75-1 파트 B, FFmpeg 단일 체인): 기본 꺼짐. 켜면 STT 입력 직전에 16kHz 모노 wav 로 전처리한다
    # ffmpeg_path: 기본 "ffmpeg"(PATH 검색, backend/bin 에 번들 파일이 있으면 그것을 우선) / 시간 초과(초)
    audio_preprocess_enabled: bool = False
    # true 면 전처리 실패를 처리 실패로 본다. false 면 경고만 남기고 원본 파일로 진행
    audio_preprocess_strict: bool = False
    ffmpeg_path: str = "ffmpeg"
    ffmpeg_timeout_seconds: float = Field(900, gt=0)
    # 라이선스(사용 허가, 작업 80-1): .lic 파일 / 검증용 공개키 PEM(저장소에는 넣지 않음, 없으면 public_key_missing)
    # / 만료 임박 경고 일수 / 켜면 발급 시 핑거프린트가 들어 있는 라이선스를 이 장비와 대조(기본 꺼짐). 상대 경로는 backend 폴더 기준
    license_path: Path = Path("data/license.lic")
    license_public_key_path: Path = Path("config/license_public.pem")
    license_warn_days: int = Field(30, ge=0)
    license_require_hardware_match: bool = False
    # 외부 연동 허용 정책(작업 82-1 파트 C): 기본 false 는 현행 동작(개발 호환). true 면 Gemini 등 외부 LLM 은 시스템 관리자의
    # external_llm_enabled 가 참일 때만 실행하고, 로컬 LLM 의 외부 주소는 local_llm_external_host_allowed 가 참일 때만 허용한다.
    # external_llm_enabled 는 시스템 관리자 설정 행이 없을 때 쓰는 초기값(기본 거짓)이다.
    external_policy_enforced: bool = False
    external_llm_enabled: bool = False
    # 지식(KMS) 연동 선반영(작업 83-1): 기본 꺼짐. 시스템 관리자 설정(system_settings)이 없을 때 쓰는 초기값과 서비스 토큰·호출 제한 시간.
    # 서비스 토큰은 시스템 간 호출용이며 값은 어디에도 출력하지 않는다(사용자 토큰은 전달하지 않는다). 실제 호출 구현은 아직 없다.
    knowledge_integration_enabled: bool = False
    knowledge_api_endpoint: str = ""
    knowledge_service_token: SecretStr = SecretStr("")
    knowledge_timeout_seconds: float = Field(3.0, gt=0, le=30)
    # 관리자가 설정하는 비밀번호(계정 생성·재설정·본인 변경)의 최소 글자 수(작업 82-1)
    password_min_length: int = Field(10, ge=8)
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

    @property
    def effective_llm_provider(self) -> str:
        """실제 추출 엔진. auto 면 STT_PROVIDER 를 따른다(fake → fake, gemini → gemini)."""
        return self.stt_provider if self.llm_provider == "auto" else self.llm_provider

    @model_validator(mode="after")
    def _check_local_llm(self):
        url = self.local_llm_base_url.strip()
        if url:
            try:
                parts = urlsplit(url)
                valid = parts.scheme in ("http", "https") and bool(parts.hostname) and not parts.query and not parts.fragment
                parts.port  # noqa: B018 — 잘못된 포트는 여기서 ValueError
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("LOCAL_LLM_BASE_URL 은 http:// 또는 https:// 로 시작하는 올바른 주소여야 합니다.")
            self.local_llm_base_url = url.rstrip("/")
        if self.effective_llm_provider == "local" and not (self.local_llm_base_url and self.local_llm_model.strip()):
            raise ValueError("LLM_PROVIDER=local 이면 LOCAL_LLM_BASE_URL 과 LOCAL_LLM_MODEL 이 필요합니다.")
        return self

    @model_validator(mode="after")
    def _check_disk_thresholds(self):
        if self.disk_min_free_mb > self.disk_warn_free_mb:
            raise ValueError("DISK_MIN_FREE_MB 는 DISK_WARN_FREE_MB 보다 클 수 없습니다.")
        return self

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
