from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # backend/.env 를 읽는다(환경변수가 .env 보다 우선). 이 클래스에 없는 항목은 무시한다
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # DB 연결 주소. 기본은 backend/data/app.db (실행 위치 기준 상대 경로, data/ 는 커밋 제외)
    database_url: str = "sqlite:///./data/app.db"
    # 실행 환경 구분: dev / test / prod
    app_env: str = "dev"


settings = Settings()
