from types import SimpleNamespace
import pytest
from app.config import settings
from app.service import rag_service
from app.service.rag_service import (
    NO_ANSWER_MESSAGE,
    _collect_sources,
    filter_by_similarity,
    generate_rag_answer,
)


def chunk(chunk_id: int, document_id: int, title: str, similarity: float, content: str = "본문") -> dict:
    return {"id": chunk_id, "document_id": document_id, "title": title, "content": content, "similarity": similarity}


# 검색 결과 순서: [1] 휴가 규정(문서 10), [2] 휴가 규정(문서 10), [3] 출장 규정(문서 20)
CHUNKS = [
    chunk(1, 10, "휴가 규정", 0.81),
    chunk(2, 10, "휴가 규정", 0.62),
    chunk(3, 20, "출장 규정", 0.55),
]


class FakeOpenAI:
    """OpenAI 호출 없이 정해진 답변을 돌려주고, 요청 내용을 기록"""

    def __init__(self, answer=None, error=None):
        self.calls = []
        self._answer = answer
        self._error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self._answer))])


@pytest.fixture
def fake_llm(monkeypatch):
    """LLM_PROVIDER=openai 분기를 가짜 클라이언트로 실행"""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")

    def install(answer=None, error=None) -> FakeOpenAI:
        client = FakeOpenAI(answer, error)
        monkeypatch.setattr(rag_service, "get_openai_client", lambda: client)
        return client

    return install


# ---------- 유사도 하한 ----------

def test_filter_by_similarity_drops_chunks_below_threshold():
    chunks = [chunk(1, 10, "a", 0.5), chunk(2, 10, "a", 0.3), chunk(3, 20, "b", 0.29)]

    assert [c["id"] for c in filter_by_similarity(chunks, 0.3)] == [1, 2]


# ---------- 출처 정리 ----------

def test_collect_sources_merges_chunks_of_same_document():
    sources, number_map = _collect_sources(CHUNKS)

    assert sources == [
        {"documentId": 10, "title": "휴가 규정", "similarity": 0.81},
        {"documentId": 20, "title": "출장 규정", "similarity": 0.55},
    ]
    # 청크 번호 → 출처 번호 (같은 문서의 청크 1, 2는 모두 출처 [1])
    assert number_map == {1: 1, 2: 1, 3: 2}


def test_collect_sources_keeps_only_cited_chunks():
    sources, number_map = _collect_sources(CHUNKS, cited_numbers={3})

    assert sources == [{"documentId": 20, "title": "출장 규정", "similarity": 0.55}]
    assert number_map == {3: 1}


# ---------- 답변 생성 ----------

async def test_no_chunks_returns_fixed_message_without_calling_llm(fake_llm):
    client = fake_llm(answer="지어낸 답변")

    result = await generate_rag_answer("법인카드 한도는?", [])

    assert result == {"answer": NO_ANSWER_MESSAGE, "sources": []}
    assert client.calls == []


async def test_llm_request_uses_grounding_prompt_and_temperature_zero(fake_llm):
    client = fake_llm(answer="연차는 15일입니다 [1].")

    await generate_rag_answer("연차는 며칠?", CHUNKS)

    request = client.calls[0]
    assert request["temperature"] == 0
    assert request["messages"][0]["role"] == "system"
    user_message = request["messages"][1]["content"]
    assert '<source id="1" title="휴가 규정">' in user_message
    assert '<source id="3" title="출장 규정">' in user_message
    assert user_message.endswith("질문: 연차는 며칠?")


async def test_no_answer_signal_is_replaced_with_fixed_message(fake_llm):
    fake_llm(answer="NO_ANSWER")

    result = await generate_rag_answer("점심 메뉴 추천해줘", CHUNKS)

    assert result == {"answer": NO_ANSWER_MESSAGE, "sources": []}


async def test_citations_are_renumbered_to_cited_sources_only(fake_llm):
    # 모델은 청크 번호 [3]만 인용 → 출처에는 출장 규정만 남고 번호는 [1]로 다시 매김
    fake_llm(answer="출장비는 실비 정산합니다 [3].")

    result = await generate_rag_answer("출장비는?", CHUNKS)

    assert result["answer"] == "출장비는 실비 정산합니다 [1]."
    assert result["sources"] == [{"documentId": 20, "title": "출장 규정", "similarity": 0.55}]


async def test_consecutive_citations_of_same_document_collapse(fake_llm):
    fake_llm(answer="연차는 15일이며 이월됩니다 [1][2]. 연차는 사전 신청 [1] [2]. 출장은 사전 승인 [3].")

    result = await generate_rag_answer("연차와 출장", CHUNKS)

    assert result["answer"] == "연차는 15일이며 이월됩니다 [1]. 연차는 사전 신청 [1]. 출장은 사전 승인 [2]."
    assert [s["documentId"] for s in result["sources"]] == [10, 20]


async def test_invented_citation_number_is_removed(fake_llm):
    # 존재하지 않는 문서 번호 [7]은 근거가 없으므로 표기 자체를 제거
    fake_llm(answer="연차는 15일입니다 [1]. 반차도 가능합니다 [7].")

    result = await generate_rag_answer("연차", CHUNKS)

    assert result["answer"] == "연차는 15일입니다 [1]. 반차도 가능합니다."
    assert [s["documentId"] for s in result["sources"]] == [10]


async def test_answer_without_citation_exposes_all_given_sources(fake_llm):
    fake_llm(answer="연차는 15일입니다.")

    result = await generate_rag_answer("연차", CHUNKS)

    assert result["answer"] == "연차는 15일입니다."
    assert [s["documentId"] for s in result["sources"]] == [10, 20]


async def test_llm_error_returns_error_message_without_sources(fake_llm):
    fake_llm(error=RuntimeError("boom"))

    result = await generate_rag_answer("연차", CHUNKS)

    assert result == {"answer": "AI 답변 생성 중 오류가 발생했습니다.", "sources": []}
