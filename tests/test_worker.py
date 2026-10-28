from unittest.mock import AsyncMock
import httpx
import pytest
from app.service import worker
from app.service.worker import (
    DLQ_STREAM_KEY,
    GROUP_NAME,
    MAX_DELIVERIES,
    RECLAIM_MIN_IDLE_MS,
    STREAM_KEY,
    handle_message,
    reclaim_pending,
)

MESSAGE_ID = b"1700000000000-0"
MESSAGE = {b"documentId": b"42", b"content": "연차 규정 본문".encode()}


@pytest.fixture
def redis_client():
    """xack / xadd 등 호출만 기록하는 가짜 Redis"""
    return AsyncMock()


@pytest.fixture
def statuses(monkeypatch):
    """Spring 상태 콜백 대신 전이된 상태만 기록"""
    recorded = []

    async def fake_update(http_client, document_id, status):
        recorded.append((document_id, status))

    monkeypatch.setattr(worker, "update_document_status", fake_update)
    return recorded


def indexing(monkeypatch, error=None):
    """청킹/임베딩/적재 대신 성공 또는 지정한 예외"""
    mock = AsyncMock(side_effect=error)
    monkeypatch.setattr(worker, "process_and_store_document", mock)
    return mock


def dlq_entry(redis_client) -> dict:
    stream, fields = redis_client.xadd.await_args.args
    assert stream == DLQ_STREAM_KEY
    return fields


async def test_success_marks_completed_and_acks(monkeypatch, redis_client, statuses):
    process = indexing(monkeypatch)

    await handle_message(redis_client, None, MESSAGE_ID, MESSAGE)

    process.assert_awaited_once_with(42, "연차 규정 본문")
    assert statuses == [(42, "PROCESSING"), (42, "COMPLETED")]
    redis_client.xack.assert_awaited_once_with(STREAM_KEY, GROUP_NAME, MESSAGE_ID)
    redis_client.xadd.assert_not_awaited()


async def test_retryable_error_keeps_message_pending_for_retry(monkeypatch, redis_client, statuses):
    indexing(monkeypatch, error=httpx.ConnectTimeout("timeout"))

    await handle_message(redis_client, None, MESSAGE_ID, MESSAGE, delivery_count=1)

    assert statuses == [(42, "PROCESSING"), (42, "FAILED")]
    # ACK 하지 않음 → PEL에 남아 reclaim_pending 이 다시 처리
    redis_client.xack.assert_not_awaited()
    redis_client.xadd.assert_not_awaited()


async def test_retryable_error_on_last_attempt_moves_to_dlq(monkeypatch, redis_client, statuses):
    indexing(monkeypatch, error=httpx.ConnectTimeout("timeout"))

    await handle_message(redis_client, None, MESSAGE_ID, MESSAGE, delivery_count=MAX_DELIVERIES)

    fields = dlq_entry(redis_client)
    assert fields["documentId"] == "42"
    assert fields["content"] == "연차 규정 본문"
    assert fields["originalMessageId"] == MESSAGE_ID.decode()
    assert "ConnectTimeout" in fields["reason"]
    redis_client.xack.assert_awaited_once_with(STREAM_KEY, GROUP_NAME, MESSAGE_ID)
    assert statuses[-1] == (42, "FAILED")


async def test_non_retryable_error_moves_to_dlq_immediately(monkeypatch, redis_client, statuses):
    indexing(monkeypatch, error=ValueError("벡터 차원 불일치"))

    await handle_message(redis_client, None, MESSAGE_ID, MESSAGE, delivery_count=1)

    assert "재시도 불가" in dlq_entry(redis_client)["reason"]
    redis_client.xack.assert_awaited_once_with(STREAM_KEY, GROUP_NAME, MESSAGE_ID)


async def test_message_over_max_deliveries_is_not_processed_again(monkeypatch, redis_client, statuses):
    process = indexing(monkeypatch)

    await handle_message(redis_client, None, MESSAGE_ID, MESSAGE, delivery_count=MAX_DELIVERIES + 1)

    process.assert_not_awaited()
    assert statuses == [(42, "FAILED")]
    assert "최대 처리 횟수" in dlq_entry(redis_client)["reason"]


@pytest.mark.parametrize("message", [{b"content": b"x"}, {b"documentId": b"abc", b"content": b"x"}, {b"documentId": b"1"}])
async def test_invalid_payload_moves_to_dlq_without_status_callback(monkeypatch, redis_client, statuses, message):
    process = indexing(monkeypatch)

    await handle_message(redis_client, None, MESSAGE_ID, message)

    process.assert_not_awaited()
    assert statuses == []
    assert "유효하지 않은 페이로드" in dlq_entry(redis_client)["reason"]
    redis_client.xack.assert_awaited_once()


def pending_entry(message_id=MESSAGE_ID, times_delivered=1, idle_ms=RECLAIM_MIN_IDLE_MS) -> dict:
    return {"message_id": message_id, "times_delivered": times_delivered, "time_since_delivered": idle_ms}


async def test_reclaim_processes_idle_message_with_next_delivery_count(monkeypatch, redis_client):
    redis_client.xpending_range.return_value = [pending_entry()]
    redis_client.xclaim.return_value = [(MESSAGE_ID, MESSAGE)]
    handled = AsyncMock()
    monkeypatch.setattr(worker, "handle_message", handled)

    await reclaim_pending(redis_client, None)

    handled.assert_awaited_once_with(redis_client, None, MESSAGE_ID, MESSAGE, 2)


async def test_reclaim_skips_recently_delivered_message(monkeypatch, redis_client):
    # 방금 전달된 메시지는 아직 처리 중일 수 있으므로 가져오지 않음
    redis_client.xpending_range.return_value = [pending_entry(idle_ms=RECLAIM_MIN_IDLE_MS - 1)]
    handled = AsyncMock()
    monkeypatch.setattr(worker, "handle_message", handled)

    await reclaim_pending(redis_client, None)

    redis_client.xclaim.assert_not_awaited()
    handled.assert_not_awaited()


async def test_reclaim_skips_message_taken_by_other_worker(monkeypatch, redis_client):
    redis_client.xpending_range.return_value = [pending_entry()]
    redis_client.xclaim.return_value = []
    handled = AsyncMock()
    monkeypatch.setattr(worker, "handle_message", handled)

    await reclaim_pending(redis_client, None)

    handled.assert_not_awaited()
