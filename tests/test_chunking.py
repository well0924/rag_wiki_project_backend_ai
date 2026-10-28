import tiktoken
from app.service.rag_service import split_text_by_tokens

tokenizer = tiktoken.get_encoding("cl100k_base")


def test_empty_text_returns_no_chunks():
    assert split_text_by_tokens("") == []


def test_short_text_is_single_chunk():
    text = "연차 신청은 사내 포털에서 합니다."

    assert split_text_by_tokens(text) == [text]


def test_long_text_is_split_within_chunk_size_with_overlap():
    text = " ".join(f"word{i}" for i in range(1500))
    total_tokens = len(tokenizer.encode(text))

    chunks = split_text_by_tokens(text, chunk_size=500, chunk_overlap=50)
    chunk_tokens = [tokenizer.encode(c) for c in chunks]

    assert len(chunks) > 1
    assert all(len(t) <= 500 for t in chunk_tokens)
    # 이웃한 청크는 경계의 50토큰을 공유 → 문장이 청크 경계에서 잘려도 문맥 유지
    for prev, nxt in zip(chunk_tokens, chunk_tokens[1:]):
        assert prev[-50:] == nxt[:50]
    # 마지막 청크가 원문의 끝까지 포함 (누락되는 토큰 없음)
    assert chunks[-1].endswith("word1499")
    assert sum(len(t) for t in chunk_tokens) - 50 * (len(chunks) - 1) == total_tokens


def test_overlap_not_smaller_than_chunk_size_does_not_loop_forever():
    text = " ".join(f"word{i}" for i in range(300))

    chunks = split_text_by_tokens(text, chunk_size=50, chunk_overlap=50)

    assert len(chunks) == 1
