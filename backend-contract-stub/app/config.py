from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # .env 파일을 읽는다. extra="ignore": 이 클래스에 없는 항목이 .env에 있어도 오류를 내지 않는다
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 쉼표로 구분된 문자열로 받는다 (배열로 바로 받으면 JSON 형식을 요구해 초보자가 막히기 쉽다)
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origins(self) -> list[str]:
        # "a, b" -> ["a", "b"] (공백 제거, 빈 값 제외)
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


settings = Settings()
