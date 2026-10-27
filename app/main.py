from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI
from pydantic import BaseModel, Field
from app.config import settings
from app.database import init_db
from app.routers import search
from app.service.rag_service import clear_vector_chunks, generate_rag_answer, search_relevant_chunks_with_permission
from app.service.worker import consume_upload_stream

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 벡터 테이블 준비 (워커가 청크를 적재하기 전에 반드시 완료)
    await init_db()
    print(f"▶️ FastAPI Startup: document_chunks 준비 완료 (embedding={settings.EMBEDDING_PROVIDER}, llm={settings.LLM_PROVIDER})")

    # 백그라운드 Redis Streams 컨슈머 가동
    worker_task = asyncio.create_task(consume_upload_stream())
    print("▶️ FastAPI Startup: 백그라운드 Redis 컨슈머 태스크 가동 완료.")
    yield  # ─── 이 지점에서 서버가 정상 구동되어 코틀린의 REST API를 수신함 ───
    print("⏸️ FastAPI Shutdown: 백그라운드 태스크 종료 중...")
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        print("Redis Stream AI Worker가 안전하게 종료되었습니다(Graceful Shutdown).")

app = FastAPI(title="Wiki AI Service", lifespan=lifespan)

app.include_router(search.router)

@app.get("/")
def read_root():
    return {"status": "success", "message": "FastAPI AI 엔진 정상 작동 중"}