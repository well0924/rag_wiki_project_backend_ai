import asyncio
import time
import traceback
from datetime import datetime, timezone
import openai
import redis.asyncio as aioredis
import httpx
from sqlalchemy.exc import InterfaceError, OperationalError
from app.config import settings
from app.security import internal_auth_headers
from app.service.rag_service import process_and_store_document

# Spring Boot 내부 전용(Internal API Key 인증) 문서 상태 변경 엔드포인트
STATUS_CALLBACK_URL = f"{settings.SPRING_BOOT_URL}/api/v1/internal/documents"

STREAM_KEY = "document:upload:stream"
# 재시도해도 성공할 수 없는 메시지를 격리하는 스트림 (원본 필드 + 실패 사유 보관)
DLQ_STREAM_KEY = "document:upload:dlq"
DLQ_MAX_LEN = 1000
GROUP_NAME = "fastapi-ai-group"
CONSUMER_NAME = "fastapi-worker-1"

# 최초 처리 포함 최대 처리 횟수 (Redis가 세는 delivery count 기준)
MAX_DELIVERIES = 3
# 이 시간 이상 ACK 되지 않은 메시지를 실패(또는 처리 중 워커 종료)로 보고 다시 가져옴
RECLAIM_MIN_IDLE_MS = 60_000
RECLAIM_INTERVAL_SECONDS = 30

# 잠시 후 다시 시도하면 성공할 수 있는 일시적 오류 (네트워크, 타임아웃, 요청 한도, 외부 서버 5xx, DB 연결)
# 그 외 오류(잘못된 입력, 차원 불일치 등)는 몇 번을 재시도해도 같은 결과이므로 바로 DLQ로 보냄
RETRYABLE_ERRORS = (
    openai.APIConnectionError,  # APITimeoutError 포함
    openai.RateLimitError,
    openai.InternalServerError,
    httpx.TransportError,
    OperationalError,
    InterfaceError,
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
)


def is_retryable(error: Exception) -> bool:
    return isinstance(error, RETRYABLE_ERRORS)


def _to_str(value) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def decode_fields(message_data: dict) -> dict[str, str]:
    """바이트 가드 해제 및 디코딩 연산"""
    return {_to_str(k): _to_str(v) for k, v in message_data.items()}


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


async def move_to_dlq(redis_client, message_id, fields: dict[str, str], reason: str):
    """원본 메시지를 DLQ 스트림에 보관한 뒤 ACK → 본 스트림의 PEL에서 제거 (무한 재처리 / PEL 누적 방지)"""
    await redis_client.xadd(
        DLQ_STREAM_KEY,
        {
            **fields,
            "originalMessageId": _to_str(message_id),
            "reason": reason[:500],
            "failedAt": datetime.now(timezone.utc).isoformat(),
        },
        maxlen=DLQ_MAX_LEN,
        approximate=True
    )
    await redis_client.xack(STREAM_KEY, GROUP_NAME, message_id)
    print(f"🪦 [DLQ 이동] Message ID: {_to_str(message_id)} / 사유: {reason}")


async def handle_message(redis_client, http_client: httpx.AsyncClient, message_id, message_data: dict, delivery_count: int = 1):
    """
    메시지 1건 처리 (PROCESSING → 청킹/임베딩/적재 → COMPLETED → ACK)
    - 실패 시 FAILED 콜백 후,
      일시적 오류이고 처리 횟수가 남았으면 ACK 하지 않음 → PEL에 남아 reclaim_pending 이 다시 처리
      재시도 불가 오류이거나 횟수를 다 썼으면 DLQ로 이동
    """
    fields = decode_fields(message_data)
    document_id_raw = fields.get("documentId", "")
    content = fields.get("content")  # Spring 이 추출한 문서 본문 텍스트

    if not document_id_raw.isdigit() or not content:
        print(f" [유효하지 않은 페이로드] Message ID: {_to_str(message_id)}")
        await move_to_dlq(redis_client, message_id, fields, "유효하지 않은 페이로드 (documentId/content 누락)")
        return

    document_id = int(document_id_raw)

    # 처리 도중 워커가 종료되는 일이 반복된 메시지는 더 이상 시도하지 않음
    if delivery_count > MAX_DELIVERIES:
        await update_document_status(http_client, document_id, "FAILED")
        await move_to_dlq(redis_client, message_id, fields, f"최대 처리 횟수({MAX_DELIVERIES}회) 초과")
        return

    print(f"\n📥 [이벤트 가로채기 성공] Document ID: {document_id} (시도 {delivery_count}/{MAX_DELIVERIES})")

    # 메시지 단위로 예외를 격리하여, 실패한 바로 그 문서만 FAILED 처리
    try:
        await update_document_status(http_client, document_id, "PROCESSING")

        # 청킹 -> 임베딩 -> pgvector 적재
        await process_and_store_document(document_id, content)

        await update_document_status(http_client, document_id, "COMPLETED")

        # 처리 완료 ACK
        await redis_client.xack(STREAM_KEY, GROUP_NAME, message_id)
        print(f"✅ [이벤트 처리 완료] Message ID: {_to_str(message_id)} ACK 송신 완료.")

    except Exception as e:
        traceback.print_exc()
        print(f"❌ [AI Worker 에러 발생] Document ID: {document_id}: {str(e)}")

        # 재시도로 성공하면 Spring 쪽에서 FAILED → PROCESSING → COMPLETED 로 복구됨
        await update_document_status(http_client, document_id, "FAILED")

        error = f"{type(e).__name__}: {e}"
        if not is_retryable(e):
            await move_to_dlq(redis_client, message_id, fields, f"재시도 불가 오류 - {error}")
        elif delivery_count >= MAX_DELIVERIES:
            await move_to_dlq(redis_client, message_id, fields, f"{MAX_DELIVERIES}회 시도 모두 실패 - {error}")
        else:
            print(f"🔁 [재시도 대기] Document ID: {document_id} ({RECLAIM_MIN_IDLE_MS // 1000}초 후 다시 처리)")


async def reclaim_pending(redis_client, http_client: httpx.AsyncClient):
    """
    ACK 되지 않고 오래 머문 메시지(PEL)를 다시 가져와 처리
    - xreadgroup(">")은 새 메시지만 읽으므로, 실패한 메시지는 이 경로로만 재처리됨
    - 워커 재기동 전 처리 중이던 메시지도 같은 방식으로 회수
    """
    # XPENDING의 IDLE 옵션은 Redis 6.2+ 전용이라, 로컬(Redis 5)에서도 동작하도록 경과 시간은 직접 비교
    pending = await redis_client.xpending_range(STREAM_KEY, GROUP_NAME, min="-", max="+", count=100)
    for entry in pending:
        if entry["time_since_delivered"] < RECLAIM_MIN_IDLE_MS:
            continue
        # XCLAIM 시 delivery count 가 1 증가하므로 이번 처리는 (기존 횟수 + 1)번째
        claimed = await redis_client.xclaim(
            STREAM_KEY, GROUP_NAME, CONSUMER_NAME, RECLAIM_MIN_IDLE_MS, [entry["message_id"]]
        )
        # 다른 워커가 먼저 가져갔거나 스트림에서 이미 삭제된 메시지
        if not claimed or not claimed[0][1]:
            continue
        message_id, message_data = claimed[0]
        await handle_message(redis_client, http_client, message_id, message_data, entry["times_delivered"] + 1)


async def consume_upload_stream():
    """
    스프링 부트(코틀린) 진영에서 발행한 Redis Streams 이벤트를
    비동기로 컨슘(구독)하여 처리하는 백그라운드 워커
    """
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)

    # 1. 컨슈머 그룹이 없다면 최초 1회 자동 생성
    try:
        await redis_client.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
        print(f"Redis Streams Consumer Group [{GROUP_NAME}] 생성 완료.")
    except Exception:
        pass

    print(f"[AI Worker] {STREAM_KEY} 스트림 모니터링 및 구독 시작...")

    # 0으로 시작하여 기동 직후 이전 실행에서 남은 미처리 메시지부터 회수
    last_reclaim_at = 0.0

    # Spring 상태 콜백용 비동기 HTTP 클라이언트
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        while True:
            if time.monotonic() - last_reclaim_at >= RECLAIM_INTERVAL_SECONDS:
                last_reclaim_at = time.monotonic()
                try:
                    await reclaim_pending(redis_client, http_client)
                except Exception as e:
                    traceback.print_exc()
                    print(f"❌ [AI Worker 재처리 에러]: {str(e)}")

            try:
                response = await redis_client.xreadgroup(
                    groupname=GROUP_NAME,
                    consumername=CONSUMER_NAME,
                    streams={STREAM_KEY: ">"},
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
                    try:
                        await handle_message(redis_client, http_client, message_id, message_data)
                    except Exception as e:
                        # ACK/DLQ 중 Redis 오류 등 → 메시지는 PEL에 남아 다음 회수 때 재처리
                        traceback.print_exc()
                        print(f"❌ [AI Worker 메시지 처리 에러] Message ID: {_to_str(message_id)}: {str(e)}")
