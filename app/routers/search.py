# app/routers/search.py
import traceback
from fastapi import APIRouter, Depends, status, HTTPException
from pydantic import BaseModel, Field
from app.security import verify_internal_api_key
from app.service.rag_service import clear_vector_chunks, search_relevant_chunks_with_permission, generate_rag_answer

# 모든 엔드포인트는 Spring Boot 서버만 호출 가능 (X-Internal-Api-Key 검증)
router = APIRouter(prefix="/api/v1/rag", tags=["Search"], dependencies=[Depends(verify_internal_api_key)])

# 코틀린 서버에서 넘어오는 데이터 모델 (DTO)
class PythonSearchRequest(BaseModel):
    query: str
    user_group_ids: list[int] = Field(..., alias="userGroupIds")
    is_admin: bool = Field(..., alias="isAdmin")

    class Config:
        populate_by_name = True  # 스네이크/카멜 케이스 양방향 매핑 허용

@router.post("/search")
async def rag_search(request: PythonSearchRequest):
    """
    [RAG 코어 엔진 엔드포인트]
    코틀린 시큐리티 필터를 통과한 유저 권한 목록을 기반으로 컨텍스트 솎아낸 후 답변 반환
    """
    try:
        # 1. 권한 범위 내에서 고밀도 유사도 검색 실행
        relevant_chunks = await search_relevant_chunks_with_permission(
            query=request.query,
            user_group_ids=request.user_group_ids,
            is_admin=request.is_admin,
            limit=3
        )

        # 💡 순수 딕셔너리 구조에서 content 리스트 발라내기
        context_chunks = [chunk["content"] for chunk in relevant_chunks]
        # 💡 코틀린 search_history 적재용 ID 리스트 발라내기
        referenced_chunk_ids = [chunk["id"] for chunk in relevant_chunks]

        # 2. 답변 생성 엔진 가동
        ai_answer = await generate_rag_answer(query=request.query, context_chunks=context_chunks)

        # 코틀린 PythonSearchResponse 스펙과 자형 일치화
        return {
            "answer": ai_answer,
            "referencedChunkIds": referenced_chunk_ids
        }

    except Exception:
        # 내부 예외 메시지(DB 쿼리 등)는 응답에 노출하지 않고 서버 로그로만 남김
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="RAG 파이프라인 엔진 내부 장애"
        )

# 최종 경로: DELETE /api/v1/rag/vector/clear/{document_id} (라우터 prefix 포함)
@router.delete("/vector/clear/{document_id}")
async def clear_vector(document_id: int):
    """
    [새벽 스프링 배치 연동 엔드포인트]
    RDB에서 삭제 처리된 문서의 고차원 벡터 임베딩 데이터를 영구 파기
    """
    try:
        await clear_vector_chunks(document_id)
        return {"success": True, "message": f"Document ID: {document_id} 관련 벡터 데이터 영구 청소 성공."}
    except Exception:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="벡터 청크 파기 오퍼레이션 실패"
        )
