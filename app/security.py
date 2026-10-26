# app/security.py
import secrets
from typing import Optional
from fastapi import Header, HTTPException, status
from app.config import settings

INTERNAL_API_KEY_HEADER = "X-Internal-Api-Key"


def internal_auth_headers() -> dict:
    """FastAPI → Spring Boot 역호출 시 첨부할 내부 인증 헤더"""
    return {INTERNAL_API_KEY_HEADER: settings.INTERNAL_API_KEY}


async def verify_internal_api_key(x_internal_api_key: Optional[str] = Header(default=None)):
    """
    Spring Boot 서버에서 온 요청인지 공유 키로 검증하는 라우터 의존성
    - 외부에서 user_group_ids를 위조해 권한 밖 문서를 검색하는 경로 차단
    """
    if x_internal_api_key is None or not secrets.compare_digest(x_internal_api_key, settings.INTERNAL_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 내부 API 키입니다.")
