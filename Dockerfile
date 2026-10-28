# FastAPI AI 서버 운영 이미지 (EMBEDDING_PROVIDER=openai 기준, torch 미포함)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# 청크 분할용 토크나이저를 빌드 시점에 내려받아, 기동 후 첫 인덱싱 때 외부 다운로드가 없도록 함
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY app ./app

# root 권한 없이 실행
RUN useradd --create-home --uid 1000 appuser && chown -R appuser /opt/tiktoken-cache
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
