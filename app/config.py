from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field

class Settings(BaseSettings):
    # 1. AI 및 캐시 설정
    REDIS_URL: str = "redis://localhost:6379"
    OPENAI_API_KEY: str = ""
    IS_PRODUCTION: bool = False  # 운영 환경이면 True로 설정
    # 2. 자바(Spring Boot)와 일치시킨 데이터베이스 개별 환경 변수
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "root"
    POSTGRES_DB: str = "wiki"
    # 3. Spring Boot ↔ FastAPI 내부 통신 설정
    SPRING_BOOT_URL: str = "http://localhost:89"
    # 서버 간 공유 키 (로컬 기본값 / 운영에서는 반드시 환경변수로 교체, Spring의 internal.api-key와 동일해야 함)
    INTERNAL_API_KEY: str = "local-internal-api-key"

    # 4. 개별 변수들을 조합해서 완벽한 비동기 DB 주소 반환 (Property 활용)
    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # .env 파일이 존재할 경우 환경 변수를 우선적으로 로드하도록 설정
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        extra="ignore"  # 정의되지 않은 다른 변수가 .env에 있어도 에러 내지 않고 무시
    )

settings = Settings()