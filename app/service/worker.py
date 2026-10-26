import asyncio
import traceback
import redis.asyncio as aioredis
import httpx
from app.config import settings
from app.security import internal_auth_headers
from app.service.rag_service import process_and_store_document  # 실제 인덱싱 서비스 주입

# Spring Boot 내부 전용(Internal API Key 인증) 문서 상태 변경 엔드포인트
STATUS_CALLBACK_URL = f"{settings.SPRING_BOOT_URL}/api/v1/internal/documents"


async def update_document_status(http_client: httpx.AsyncClient, document_id: int, status: str):
    """
    스프링 부트에 문서 상태 전이를 역호출
    - 콜백 실패가 인덱싱 자체를 막지 않도록 예외는 로그만 남기고 삼킴
    - 401/404 등 HTTP 에러 응답도 놓치지 않도록 상태 코드를 확인
    """
    try:
        response = await http_client.patch(
            f"{STATUS_CALLBACK_URL}/{document_id}/status",
            params={"status": status},
            headers=internal_auth_headers()
        )
        response.raise_for_status()
    except Exception as e:
        print(f"⚠️ [스프링 통신 경고] Document ID: {document_id} {status} 상태 전이 호출 실패: {str(e)}")


async def consume_upload_stream():
    """
    스프링 부트(코틀린) 진영에서 발행한 Redis Streams 이벤트를
    비동기로 컨슘(구독)하여 처리하는 백그라운드 워커
    """
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)

    stream_key = "document:upload:stream"
    group_name = "fastapi-ai-group"
    consumer_name = "fastapi-worker-1"

    # 1. 컨슈머 그룹이 없다면 최초 1회 자동 생성
    try:
        await redis_client.xgroup_create(stream_key, group_name, id="0", mkstream=True)
        print(f"Redis Streams Consumer Group [{group_name}] 생성 완료.")
    except Exception:
        pass

    print(f"[AI Worker] {stream_key} 스트림 모니터링 및 구독 시작...")

    # 스프링 부트 엔드포인트와 통신할 넌블로킹 HTTP 세션 가동
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        while True:
            try:
                response = await redis_client.xreadgroup(
                    groupname=group_name,
                    consumername=consumer_name,
                    streams={stream_key: ">"},
                    count=1,
                    block=1000
                )
            except Exception as e:
                traceback.print_exc()
                print(f"❌ [AI Worker 스트림 수신 에러]: {str(e)}")
                await asyncio.sleep(2)
                continue

            if not response:
                await asyncio.sleep(0.1)
                continue

            for stream, messages in response:
                for message_id, message_data in messages:
                    # 바이트 가드 해제 및 디코딩 연산
                    decoded_data = {
                        k.decode('utf-8', errors='ignore'): v.decode('utf-8', errors='replace')
                        for k, v in message_data.items()
                    }

                    document_id_raw = decoded_data.get("documentId")
                    content = decoded_data.get("content")  # 스프링이 밀어넣어준 본문 알맹이 안착

                    if not document_id_raw or not content:
                        print(f" [유효하지 않은 페이로드 스킵] Message ID: {message_id}")
                        await redis_client.xack(stream_key, group_name, message_id)
                        continue

                    document_id = int(document_id_raw)
                    print(f"\n📥 [이벤트 가로채기 성공] Document ID: {document_id}")

                    # 메시지 단위로 예외를 격리하여, 실패한 바로 그 문서만 FAILED 처리
                    try:
                        await update_document_status(http_client, document_id, "PROCESSING")

                        # -------------------------------------------------------------
                        # 실전 RAG 인덱싱 비동기 가동 (청킹 -> 임베딩 -> pgvector 적재)
                        # -------------------------------------------------------------
                        await process_and_store_document(document_id, content)

                        await update_document_status(http_client, document_id, "COMPLETED")

                        # 3. 메시지 정상 처리 통보 (ACK 날리기)
                        await redis_client.xack(stream_key, group_name, message_id)
                        print(f"✅ [이벤트 처리 완료] Message ID: {message_id} ACK 송신 완료.")

                    except Exception as e:
                        traceback.print_exc()
                        print(f"❌ [AI Worker 에러 발생] Document ID: {document_id}: {str(e)}")

                        # 에러 발생 시 스프링에 FAILED 상태를 던져서 인프라 정합성 보호 (ACK 미송신 → PEL 보존)
                        await update_document_status(http_client, document_id, "FAILED")
                        await asyncio.sleep(2)
