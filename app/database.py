# app/database.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, Text
from pgvector.sqlalchemy import Vector
from app.config import settings

# 1. 완벽한 논블로킹 처리를 위한 비동기 DB 엔진 인프라 구성
engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

class DocumentChunkEntity(Base):
    """
    RAG 아키텍처의 핵심: 쪼개진 문서 텍스트 청크와 고차원 벡터 임베딩을 보관하는 테이블.
    코틀린 RDB 진영의 public 스키마 내 documents 테이블과 조인할 수 있도록 물리 구조 일치화.
    """
    __tablename__ = "document_chunks"
    __table_args__ = {"schema": "public"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=False, index=True)  # 권한 필터링용 핵심 외래 식별 키
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    
    # 로컬 SentenceTransformer 모델 기준 384차원 / 운영 전환 시 OpenAI text-embedding-3-small(1536차원)으로 교체
    VECTOR_DIM = 1536 if settings.IS_PRODUCTION else 512
    embedding = Column(Vector(VECTOR_DIM), nullable=False)
    
