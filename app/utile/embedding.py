# app/utile/embedding.py
from app.config import settings

# 모델을 미리 로드하여 재사용
_local_model = None

def get_model():
    global _local_model
    if settings.EMBEDDING_PROVIDER == "local" and _local_model is None:
        # torch 기반 무거운 패키지이므로 local 모드에서만 import
        # (운영 이미지는 EMBEDDING_PROVIDER=openai 로 sentence-transformers 없이 동작)
        from sentence_transformers import SentenceTransformer
        print("📥 [Embedding] 다국어(한글 지원) 로컬 임베딩 모델 로드 중...")
        _local_model = SentenceTransformer('distiluse-base-multilingual-cased-v1')
    return _local_model

def warm_up_model() -> bool:
    """
    서버 시작 시 로컬 모델을 미리 로드
    - 첫 검색 요청에서 모델을 로드하면 응답이 Spring의 요청 제한 시간을 넘길 수 있어, 요청을 받기 전에 로드해 둠
    - local 모드가 아니면(openai) 아무것도 하지 않고 False 반환
    """
    if settings.EMBEDDING_PROVIDER != "local":
        return False
    get_model()
    return True

def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """로컬 모델을 이용해 임베딩 생성"""
    model = get_model()
    return model.encode(texts).tolist()
