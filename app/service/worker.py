import asyncio
import traceback
import redis.asyncio as aioredis
import httpx
from app.config import settings
from app.service.rag_service import process_and_store_document  # 실제 인덱싱 서비스 주입

async def consume_upload_stream():
    """
    스프링 부트(코틀린) 진영에서 발행한 Redis Streams 이벤트를
    비동기로 컨슘(구독)하여 처리하는 백그라운드 워커
    """
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
    
    stream_key = "document:upload:stream"
    group_name = "fastapi-ai-group"
    consumer_name = "fastapi-worker-1"

    # 💡 [포트 정합성 조율] 네 기획 스펙에 맞춘 스프링 부트 로컬 포트 89 바인딩!
    SPRING_BOOT_URL = "http://localhost:89/api/v1/documents"

    # 1. 컨슈머 그룹이 없다면 최초 1회 자동 생성
    try:
        await redis_client.xgroup_create(stream_key, group_name, id="0", mkstream=True)
        print(f"Redis Streams Consumer Group [{group_name}] 생성 완료.")
    except Exception:
        pass

    print(f"[AI Worker] {stream_key} 스트림 모니터링 및 구독 시작...")

    # 스프링 부트 엔드포인트와 통신할 넌블로킹 HTTP 세션 가동
    async with httpx.AsyncClient() as http_client:
        while True:
            try:
                response = await redis_client.xreadgroup(
                    groupname=group_name,
                    consumername=consumer_name,
                    streams={stream_key: ">"},
                    count=1,
                    block=1000
                )

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

                        # 💡 [스프링 역호출 1단계] 포트 89번 경로로 상태 변경 전이 요청 실행
                        try:
                            await http_client.patch(f"{SPRING_BOOT_URL}/{document_id}/status?status=PROCESSING")
                        except Exception as ne:
                            print(f"⚠️ [스프링 통신 경고] PROCESSING 상태 전이 호출 실패: {str(ne)}")

                        # -------------------------------------------------------------
                        # 실전 RAG 인덱싱 비동기 가동 (청킹 -> 임베딩 -> pgvector 적재)
                        # -------------------------------------------------------------
                        await process_and_store_document(document_id, content)
                        
                        # 💡 [스프링 역호출 2단계] 인덱싱 완료 후 완료 상태창 피드백 전송
                        try:
                            await http_client.patch(f"{SPRING_BOOT_URL}/{document_id}/status?status=COMPLETED")
                        except Exception as ne:
                            print(f"⚠️ [스프링 통신 경고] COMPLETED 상태 전이 호출 실패: {str(ne)}")

                        # 3. 메시지 정상 처리 통보 (ACK 날리기)
                        await redis_client.xack(stream_key, group_name, message_id)
                        print(f"✅ [이벤트 처리 완료] Message ID: {message_id} ACK 송신 완료.")

            except Exception as e:
                traceback.print_exc()
                print(f"❌ [AI Worker 에러 발생]: {str(e)}")
                
                # 에러 발생 시 스프링에 FAILED 상태를 던져서 인프라 정합성 보호
                if 'document_id' in locals():
                    try:
                        await http_client.patch(f"{SPRING_BOOT_URL}/{document_id}/status?status=FAILED")
                    except Exception:
                        pass
                await asyncio.sleep(2)