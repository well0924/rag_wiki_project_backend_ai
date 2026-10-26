import asyncio
import tiktoken
from openai import AsyncOpenAI
from app.config import settings
from app.database import AsyncSessionLocal, DocumentChunkEntity
from sqlalchemy import text
from app.utile.embedding import generate_embeddings

# OpenAI 클라이언트는 운영 분기에서 최초 사용 시점에 생성
# (로컬 개발 모드는 OPENAI_API_KEY 없이도 서버가 기동되어야 하므로 import 시점 생성 금지)
_openai_client = None

def get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        # API Key는 Pydantic settings로부터 안전하게 획득
        _openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai_client

def split_text_by_tokens(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> list[str]:
    tokenizer = tiktoken.get_encoding("cl100k_base")
    tokens = tokenizer.encode(text)
    chunks = []
    start = 0
    while start < len(tokens):
        end = start + chunk_size
        chunk_tokens = tokens[start:end]
        chunks.append(tokenizer.decode(chunk_tokens))
        start += (chunk_size - chunk_overlap)
        if start >= len(tokens) or chunk_size <= chunk_overlap:
            break
    return chunks

async def get_embedding_vector(texts: list[str]) -> list[list[float]]:
    """[핵심 통합 함수] 환경에 따라 로컬 모델/OpenAI 자동 전환"""
    if not settings.IS_PRODUCTION:
        print("🛠 [Development] 로컬 임베딩 모델 실행 중...")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, generate_embeddings, texts)
    
    print("🚀 [Production] OpenAI Embedding API 호출 중...")
    response = await get_openai_client().embeddings.create(
        input=texts,
        model="text-embedding-3-small"
    )
    return [data.embedding for data in response.data]

async def process_and_store_document(document_id: int, content: str):
    """수정된 인덱싱 파이프라인 (세션 커밋 안정성 확보)"""
    chunks = split_text_by_tokens(content, chunk_size=500, chunk_overlap=50)
    if not chunks:
        return

    embeddings = await get_embedding_vector(chunks)

    async with AsyncSessionLocal() as session:
        async with session.begin():
            for i, (chunk_text, emb_vector) in enumerate(zip(chunks, embeddings)):
                chunk_entity = DocumentChunkEntity(
                    document_id=document_id,
                    chunk_index=i,
                    content=chunk_text,
                    embedding=emb_vector
                )
                session.add(chunk_entity)
        # 💡 비동기 컨텍스트 블록(session.begin)이 안전하게 종료되어 커밋된 후 로그 출력
        print(f"💾 [pgvector 적재 완료] Document ID: {document_id}, 총 {len(chunks)}개 청크 저장 성공.")

async def search_relevant_chunks_with_permission(query: str, user_group_ids: list[int], is_admin: bool = False, limit: int = 3) -> list[dict]:
    """
    [안전성 극대화] tuple 인덱스로 확실하게 발라내어 'slice' 관련 에러 원천 차단
    - Soft Delete된 문서(documents.is_deleted)는 배치 파기 전이라도 검색 대상에서 즉시 제외
    - 권한 확인은 EXISTS로 수행하여, 여러 허용 그룹에 걸친 문서의 청크가 중복 반환되지 않도록 방지
    - 관리자(is_admin)는 그룹 권한 필터 없이 전사 문서 검색 (Spring의 문서 목록 조회 규칙과 동일)
    """
    query_vector = await get_embedding_vector([query])
    vector_str = f"[{','.join(map(str, query_vector[0]))}]"

    async with AsyncSessionLocal() as session:
        raw_query = text("""
            SELECT dc.id, dc.content
            FROM public.document_chunks dc
            INNER JOIN public.documents d ON d.id = dc.document_id
            WHERE d.is_deleted = false
              AND (
                  CAST(:is_admin AS boolean)
                  OR EXISTS (
                      SELECT 1
                      FROM public.document_permissions dp
                      WHERE dp.document_id = dc.document_id
                        AND dp.group_id = ANY(:group_ids)
                  )
              )
            ORDER BY dc.embedding <=> :query_vector
            LIMIT :limit
        """)

        bind_groups = user_group_ids if user_group_ids else [-1]

        result = await session.execute(
            raw_query,
            {
                "is_admin": is_admin,
                "group_ids": bind_groups,
                "query_vector": vector_str,
                "limit": limit
            }
        )
        
        # 💡 fetchall() 결과를 한 판 순수한 튜플 리스트로 받아서 확실하게 인덱스로 매핑
        rows = result.fetchall()
        return [{"id": int(row[0]), "content": str(row[1])} for row in rows]

async def clear_vector_chunks(document_id: int):
    """새벽 스프링 배치 진영의 원격 파기 신호를 처리하는 코어 엔드포인트 서비스 로직"""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            delete_query = text("""
                DELETE FROM public.document_chunks 
                WHERE document_id = :document_id
            """)
            await session.execute(delete_query, {"document_id": document_id})
    print(f"🧹 [Vector DB 클린징 완료] Document ID: {document_id} 관련 전체 청크 영구 삭제 성공.")

async def generate_rag_answer(query: str, context_chunks: list[str]) -> str:
    """
    [LLM 생성 분기] 
    - 로컬(Development): GPT를 쓰지 않으므로 추출된 청크 기반의 Mock 데이터 반환
    - 운영(Production): OpenAI gpt-4o-mini API 호출
    """
    if not context_chunks:
        return "죄송합니다. 질문하신 내용에 대해 접근 권한이 있는 문서가 존재하지 않거나 관련 정보를 찾을 수 없습니다."

    # 1. 로컬 개발 환경: GPT API 호출 없이 로컬용 Mock 답변 서빙
    if not settings.IS_PRODUCTION:
        print("🛠 [Development] 로컬 환경 - GPT API 호출을 건너뛰고 Mock 답변을 생성합니다.")
        
        mock_response = (
            "🤖 [로컬 개발 모드 안내]\n"
            "현재 로컬 환경이므로 OpenAI API를 호출하지 않고, 검색된 컨텍스트를 반환합니다.\n\n"
            "📝 [검색된 사내 문서 청크]\n"
        )
        for i, chunk in enumerate(context_chunks, 1):
            mock_response += f"[{i}] {chunk[:100]}...\n"
            
        return mock_response

    # 2. 운영 환경: 실제 OpenAI GPT-4o-mini 모델로 정밀 답변 생성
    print("[Production] OpenAI Chat Completion API 호출 중...")
    
    context_text = "\n\n".join(context_chunks)
    system_prompt = (
        "너는 사내 위키 지능형 어시스턴트야. 주어진 [사내 가이드 문서] 내용만을 바탕으로 유저의 질문에 친절하게 답해줘.\n"
        "만약 문서 내용으로 답변을 유추할 수 없다면, 모른다고 정직하게 답해야 해.\n\n"
        f"[사내 가이드 문서]\n{context_text}"
    )

    try:
        response = await get_openai_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query}
            ],
            temperature=0.2
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"❌ [LLM Generation 에러]: {str(e)}")
        return "AI 답변 생성 중 오류가 발생했습니다."