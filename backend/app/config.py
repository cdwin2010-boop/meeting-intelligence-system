from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 개발용 기본 서명 키. dev 가 아닌 환경에서 이 값 그대로면 서버 시작을 거부한다
DEV_SECRET_KEY = "dev-only-insecure-secret-key-change-me"
# dev 가 아닌 환경의 최소 키 길이(HS256 권장: 32바이트 이상)
MIN_SECRET_KEY_LENGTH = 32


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
