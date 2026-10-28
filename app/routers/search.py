# app/routers/search.py
import traceback
from fastapi import APIRouter, Depends, status, HTTPException
from pydantic import BaseModel, Field
from app.config import settings
from app.security import verify_internal_api_key
from app.service.rag_service import clear_vector_chunks, search_relevant_chunks_with_permission, generate_rag_answer

# 모든 엔드포인트는 Spring Boot 서버만 호출 가능 (X-Internal-Api-Key 검증)
router = APIRouter(prefix="/api/v1/rag", tags=["Search"], dependencies=[Depends(verify_internal_api_key)])

# Spring Boot 검색 요청 DTO
class PythonSearchRequest(BaseModel):
    query: str
    user_group_ids: list[int] = Field(..., alias="userGroupIds")
    is_admin: bool = Field(..., alias="isAdmin")

    class Config:
        populate_by_name = True  # 스네이크/카멜 케이스 양방향 매핑 허용

@router.post("/search")
async def rag_search(request: PythonSearchRequest):
    """
    [RAG 검색 엔드포인트]
    Spring Boot가 인증한 사용자의 그룹 권한 범위 안에서 근거 청크를 찾아 답변 생성
    """
    try:
        # 1. 권한 범위 안에서 유사도 검색
        relevant_chunks = await search_relevant_chunks_with_permission(
            query=request.query,
            user_group_ids=request.user_group_ids,
            is_admin=request.is_admin,
            limit=settings.SEARCH_TOP_K
        )

        # Spring search_history 에 저장할 참조 청크 ID 목록
        referenced_chunk_ids = [chunk["id"] for chunk in relevant_chunks]

        # 2. 답변 생성 (답변 + 근거 문서)
        result = await generate_rag_answer(query=request.query, context_chunks=relevant_chunks)

        # Spring PythonSearchResponse 필드명(camelCase)과 일치
        return {
            "answer": result["answer"],
            "referencedChunkIds": referenced_chunk_ids,
            "sources": result["sources"]
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
    [영구 삭제 배치 연동 엔드포인트]
    Soft Delete된 문서의 벡터 청크를 영구 삭제
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
