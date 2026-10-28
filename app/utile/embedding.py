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

def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """로컬 모델을 이용해 임베딩 생성"""
    model = get_model()
    return model.encode(texts).tolist()
