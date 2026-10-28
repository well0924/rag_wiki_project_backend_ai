from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field

class Settings(BaseSettings):
    # 1. AI 및 캐시 설정
    REDIS_URL: str = "redis://localhost:6379"
    OPENAI_API_KEY: str = ""
    # 임베딩 / 답변 생성을 각각 독립적으로 전환 (예: 임베딩은 로컬 모델, 답변만 OpenAI)
    # - EMBEDDING_PROVIDER: local(distiluse, 512차원) | openai(text-embedding-3-small, 1536차원)
    # - LLM_PROVIDER: mock(검색 청크를 그대로 반환) | openai(gpt-4o-mini)
    EMBEDDING_PROVIDER: Literal["local", "openai"] = "local"
    LLM_PROVIDER: Literal["mock", "openai"] = "mock"
    # 2. 데이터베이스 접속 정보 (Spring Boot와 같은 DB 사용)
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "root"
    POSTGRES_DB: str = "wiki"
    # 3. Spring Boot ↔ FastAPI 내부 통신 설정
    SPRING_BOOT_URL: str = "http://localhost:89"
    # 서버 간 공유 키 (로컬 기본값 / 운영에서는 반드시 환경변수로 교체, Spring의 internal.api-key와 동일해야 함)
    INTERNAL_API_KEY: str = "local-internal-api-key"
    # 4. 검색 품질 / 환각 방지
    # - SEARCH_TOP_K: LLM에 근거로 넘길 최대 청크 수
    # - SEARCH_MIN_SIMILARITY: 코사인 유사도(1 - 거리) 하한. 이보다 낮은 청크는 "관련 없음"으로 보고 버림
    #   (관련 문서가 하나도 없으면 LLM을 호출하지 않고 "찾을 수 없음" 응답 → 억지 답변 차단)
    #   임베딩 모델마다 점수 분포가 다르므로 평가셋으로 보정 후 환경변수로 조정
    SEARCH_TOP_K: int = 3
    SEARCH_MIN_SIMILARITY: float = 0.3

    # 5. 개별 접속 정보를 조합한 비동기(asyncpg) DB URL
    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # 임베딩 모델별 벡터 차원 (document_chunks.embedding 컬럼 차원과 반드시 일치해야 함)
    @property
    def VECTOR_DIM(self) -> int:
        return 1536 if self.EMBEDDING_PROVIDER == "openai" else 512

    # .env 파일이 존재할 경우 환경 변수를 우선적으로 로드하도록 설정
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"  # 정의되지 않은 다른 변수가 .env에 있어도 에러 내지 않고 무시
    )

settings = Settings()
