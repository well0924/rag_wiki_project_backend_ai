# app/database.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import BigInteger, Column, Integer, Text, text
from pgvector.sqlalchemy import Vector
from app.config import settings

# 비동기 DB 엔진 / 세션 팩토리
engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

class DocumentChunkEntity(Base):
    """
    RAG 아키텍처의 핵심: 쪼개진 문서 텍스트 청크와 고차원 벡터 임베딩을 보관하는 테이블.
    코틀린 RDB 진영의 public 스키마 내 documents 테이블과 조인할 수 있도록 물리 구조 일치화.
    - 이 테이블의 스키마 소유자는 FastAPI (Spring은 documents 테이블만 관리)
    """
    __tablename__ = "document_chunks"
    __table_args__ = {"schema": "public"}

    # Spring documents.id(bigint)와 타입 일치
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    document_id = Column(BigInteger, nullable=False, index=True)  # documents.id 참조 (권한 필터 조인 / 문서 단위 삭제에 사용)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)

    # 로컬 SentenceTransformer 모델 기준 512차원 / OpenAI text-embedding-3-small 기준 1536차원
    embedding = Column(Vector(settings.VECTOR_DIM), nullable=False)


async def init_db():
    """
    기동 시 pgvector 확장과 document_chunks 테이블을 준비 (이미 있으면 건너뜀)
    - 기존 테이블의 벡터 차원이 현재 EMBEDDING_PROVIDER와 다르면 기동을 중단
      (차원이 다른 벡터는 저장/검색이 모두 실패하므로, 모델 교체 시 테이블 재생성 + 전체 재인덱싱 필요)
    """
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

        current_type = (await conn.execute(text("""
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            WHERE a.attrelid = 'public.document_chunks'::regclass
              AND a.attname = 'embedding'
        """))).scalar_one()

    expected_type = f"vector({settings.VECTOR_DIM})"
    if current_type != expected_type:
        raise RuntimeError(
            f"document_chunks.embedding 차원 불일치: DB={current_type}, "
            f"EMBEDDING_PROVIDER={settings.EMBEDDING_PROVIDER}({expected_type}). "
            "임베딩 모델을 바꿨다면 document_chunks 테이블을 재생성하고 문서를 재인덱싱해야 합니다."
        )
