# RAG 기반 사내 위키 검색 에이전트 (AI Server)
 
> Python · FastAPI · Redis Streams · pgvector · SentenceTransformers · OpenAI
 
---
 
## 📌 프로젝트 개요
 
사내 문서를 업로드하면 AI가 자동으로 임베딩·청킹하여 벡터 DB에 인덱싱하고,
임직원이 자연어로 질의하면 소속 부서 권한 기반으로 필터링된 문서만 참조하여 답변을 생성하는
RAG(Retrieval-Augmented Generation) 기반 사내 위키 검색 서버입니다.
 
- **Spring Boot 백엔드 저장소**: [rag_wiki_project_backend](https://github.com/well0924/rag_wiki_project_backend)
---
 
## 🛠️ 기술 스택
 
| 구분 | 기술 |
|---|---|
| Language | Python 3.11 |
| Framework | FastAPI |
| Message Queue | Redis Streams (Consumer Group) |
| Vector DB | pgvector (PostgreSQL) |
| Embedding (Local) | SentenceTransformers `distiluse-base-multilingual-cased-v1` (512차원) |
| Embedding (Production) | OpenAI `text-embedding-3-small` (1536차원) |
| LLM | OpenAI `gpt-4o-mini` |
| Tokenizer | tiktoken (`cl100k_base`) |
| ORM | SQLAlchemy (비동기) |
 
---

## 📁 패키지 구조
 
```
app/
├── config.py              ← Pydantic Settings (환경변수 관리)
├── database.py            ← 비동기 DB 엔진, DocumentChunkEntity
├── main.py                ← FastAPI 앱 진입점, Redis Streams 워커 시작
├── routers/
│   └── search.py          ← RAG 검색 엔드포인트, 벡터 청크 삭제 엔드포인트
├── service/
│   ├── rag_service.py     ← 청킹, 임베딩, pgvector 저장, RAG 검색, LLM 답변 생성
│   └── embedding.py       ← 로컬 SentenceTransformer 모델 관리
└── utile/
    └── embedding.py       ← 임베딩 유틸
```
 
---
 
## 📎 지원 파일 형식 (MVP)
 
| 형식 | 확장자 | 비고 |
|---|---|---|
| 텍스트 | `.txt` | 기본 텍스트 문서 |
| PDF | `.pdf` | 사내 가이드, 규정 문서 등 |
| Word | `.docx` | 기획서, 회의록 등 |
| Markdown | `.md` | README, ADR, 포스트모템 등 |
 
> Spring Boot에서 업로드 시 `ALLOWED_DOC_TYPES = setOf("TXT", "PDF", "DOCX", "MD")`로 검증 후 Redis Streams에 발행합니다.
> 허용되지 않은 형식은 업로드 단계에서 차단됩니다.
 
## 📁 패키지 구조
 
```
app/
├── config.py              ← Pydantic Settings (환경변수 관리)
├── database.py            ← 비동기 DB 엔진, DocumentChunkEntity
├── main.py                ← FastAPI 앱 진입점, Redis Streams 워커 시작
├── routers/
│   └── search.py          ← RAG 검색 엔드포인트, 벡터 청크 삭제 엔드포인트
├── service/
│   ├── rag_service.py     ← 청킹, 임베딩, pgvector 저장, RAG 검색, LLM 답변 생성
│   └── embedding.py       ← 로컬 SentenceTransformer 모델 관리
└── utile/
    └── embedding.py       ← 임베딩 유틸
```
 
---
 
## 📎 지원 파일 형식 (MVP)
 
| 형식 | 확장자 | 비고 |
|---|---|---|
| 텍스트 | `.txt` | 기본 텍스트 문서 |
| PDF | `.pdf` | 사내 가이드, 규정 문서 등 |
| Word | `.docx` | 기획서, 회의록 등 |
| Markdown | `.md` | README, ADR, 포스트모템 등 |
 
> Spring Boot에서 업로드 시 `ALLOWED_DOC_TYPES = setOf("TXT", "PDF", "DOCX", "MD")`로 검증 후 Redis Streams에 발행합니다.
> 허용되지 않은 형식은 업로드 단계에서 차단됩니다.
 
---
 
## 🔄 문서 인덱싱 파이프라인
 
Spring Boot 백엔드가 문서 업로드 시 Redis Streams에 이벤트를 발행하면,
FastAPI AI 워커가 이를 소비하여 벡터 인덱싱을 수행합니다.
 
```
1. Redis Streams에서 document_id, content 수신
2. tiktoken으로 텍스트 청킹 (chunk_size=500, overlap=50)
3. 임베딩 생성 (개발: 로컬 모델 / 운영: OpenAI API)
4. pgvector에 청크별 벡터 저장
5. ACK 송신으로 메시지 처리 완료 확인
```
 
### 동작 캡처
 
> FastAPI 워커 기동 및 문서 인덱싱 완료 로그
 
<img width="776" height="236" alt="Image" src="https://github.com/user-attachments/assets/1d1003f2-ffa9-4dc1-bd75-66ea877526ea" />
 
> Redis Streams 및 캐시 적재 확인
 
<img width="829" height="144" alt="Image" src="https://github.com/user-attachments/assets/a254cec0-91aa-46d9-b8cb-111d80cc5852" />
 
---
 
## 🔍 RAG 검색 파이프라인
 
```
1. 사용자 쿼리 수신 (query, userGroupIds, isAdmin)
2. 쿼리 임베딩 생성
3. pgvector 코사인 유사도 검색 (부서 권한 필터링 포함)
4. 상위 3개 청크 컨텍스트로 LLM 답변 생성
5. answer + referencedChunkIds 반환
```
 
### 권한 기반 필터링 쿼리
 
```sql
SELECT dc.id, dc.content
FROM public.document_chunks dc
INNER JOIN public.document_permissions dp ON dc.document_id = dp.document_id
WHERE dp.group_id = ANY(:group_ids)
ORDER BY dc.embedding <=> :query_vector
LIMIT 3
```
 
사용자의 `group_ids`로 접근 가능한 문서만 유사도 검색 대상에 포함시켜
타 부서 문서가 답변에 노출되지 않도록 합니다.
 
### 동작 캡처
 
> RAG 검색 요청/응답

<img width="1078" height="247" alt="Image" src="https://github.com/user-attachments/assets/29899cbb-60b9-44ba-b11a-a40562f10838" />

<img width="1314" height="233" alt="Image" src="https://github.com/user-attachments/assets/2e5afa34-0e8c-4ced-b5a9-4dd3a0e2f23e" /> 
 
## 🧹 벡터 청크 삭제
 
Spring Boot 백엔드(Spring Batch)가 삭제된 문서를 감지하여 아래 엔드포인트를 호출합니다.
Python은 해당 document_id에 속한 모든 벡터 청크를 pgvector에서 제거합니다.
 
```
Spring Batch → DELETE /api/v1/vector/clear/{document_id} → pgvector 청크 삭제
```
 
> 문서 상태 관리(UPLOADED → PROCESSING → COMPLETED) 및 배치 스케줄링은 [백엔드 저장소](https://github.com/well0924/rag_wiki_project_backend)를 참고하세요.
 
---
 
 
임베딩과 답변 생성(LLM)은 환경변수로 각각 따로 전환합니다.

| 환경변수 | 값 | 동작 |
|---|---|---|
| `EMBEDDING_PROVIDER` | `local` (기본) | SentenceTransformers `distiluse-base-multilingual-cased-v1` (512차원) |
| | `openai` | OpenAI text-embedding-3-small (1536차원) |
| `LLM_PROVIDER` | `mock` (기본) | 검색된 청크를 그대로 반환 (GPT 호출 없음) |
| | `openai` | OpenAI gpt-4o-mini |

예) 임베딩은 로컬 모델로 비용 없이 처리하고 답변만 GPT로 생성: `EMBEDDING_PROVIDER=local`, `LLM_PROVIDER=openai`
 
`document_chunks` 테이블은 FastAPI가 기동 시 생성합니다 (`CREATE EXTENSION vector` + `create_all`, 이미 있으면 건너뜀).
임베딩 모델에 따라 벡터 차원이 달라지므로, 기존 테이블의 `embedding` 차원이 현재 `EMBEDDING_PROVIDER`와 다르면
기동을 중단합니다. 모델을 바꿀 때는 테이블을 재생성하고 문서를 재인덱싱해야 합니다.
 
---


## 🔗 관련 저장소
 
- Spring Boot 백엔드: [rag_wiki_project_backend](https://github.com/well0924/rag_wiki_project_backend)