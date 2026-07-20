# KNU-Ask RAG 및 검색 아키텍처 제안

작성 역할: RAG and Search Architect  
작성일: 2026-07-20  
대상 저장소: `KNU-Ask`

## 1. 결론

현재 MVP의 방향은 타당하다. 공지 변경 감지, 구조화 메타데이터, pgvector, 질문 분석, 근거가 약할 때 답변 거부라는 핵심 골격이 이미 있다. 그러나 운영 서비스로 전환하려면 검색 단위를 **공지 1건**에서 **버전이 고정된 근거 청크**로 바꾸고, 검색을 **벡터 단일 후보군 + 애플리케이션 키워드 가점**에서 **독립적인 lexical/vector 후보군 → RRF 융합 → 재랭킹 → 근거 적합성 판정**으로 바꿔야 한다.

권장 목표 구조는 다음과 같다.

```text
공식 API/웹/첨부파일
  → 수집 원장(raw immutable snapshot)
  → 파일 검사·텍스트 추출·정규화
  → 문서 버전 + 구조 보존 청킹
  → 메타데이터 추출·임베딩·FTS 인덱싱
  → 품질 검증 후 검색 인덱스 원자적 활성화

학생 질문
  → 정규화·안전 검사·대화 맥락 해소
  → 검색 계획(query + filters + expansions)
  → lexical top 50 || vector top 50
  → RRF 융합 top 30
  → 메타데이터/기간 정책 적용
  → cross-encoder 또는 LLM 재랭킹 top 8
  → 근거 다양화·인접 청크 확장 top 4~6
  → 인용 가능한 답변 생성
  → 문장별 인용/수치 검증
  → 답변 또는 명시적 답변 거부
```

MVP 확장 시 외부 벡터 DB를 먼저 추가할 이유는 없다. PostgreSQL + pgvector로 메타데이터, 전문검색, 벡터 검색, 버전 활성화를 하나의 트랜잭션 경계 안에서 관리하는 편이 현 규모와 팀 운영에 적합하다. 데이터량 또는 검색 QPS가 실제 측정으로 한계를 보일 때에만 검색 전용 계층을 분리한다.

## 2. 저장소 현황과 코드 기준 진단

### 2.1 현재 구현

| 영역 | 현재 구현 | 확인 위치 |
|---|---|---|
| 수집 | 최근 2개월 공지 수집, 사라진 공지 `is_archived` 처리 | `backend/app/api/routes.py`, `backend/app/services/crawler/knu.py` |
| 변경 감지 | 제목·본문·첨부 텍스트·게시일 정규화 후 SHA-256 | `backend/app/services/processing.py`, `backend/app/utils/text.py` |
| 구조화 | 공지별 카테고리, 기간, 대상, 담당 부서, 키워드 등을 LLM/규칙으로 추출 | `backend/app/services/processing.py`, `backend/app/services/ai/client.py` |
| 임베딩 | 공지별 `search_text` 하나를 1536차원으로 임베딩 | `NoticeEmbedding`, `AIService.embedding()` |
| 검색 | 구조화 필터 후 pgvector cosine top 30, Python에서 키워드/메타데이터/상태 가중합 | `backend/app/services/search/hybrid.py` |
| 생성 | 상위 5개 공지를 프롬프트에 넣고 답변 | `backend/app/services/chat.py`, `answer_generation.txt` |
| 캐시 | 질문 문자열 기반 Redis 캐시 24시간 | `backend/app/services/cache.py` |
| 처리 이력 | `ProcessingJob`, `CrawlHistory` 존재 | `backend/app/models/entities.py` |
| 테스트 | 카테고리 필터, 대표 벡터 검색, no-data, 만료 경고 | `backend/tests/test_search_chat.py` |

### 2.2 핵심 갭과 영향

| 우선순위 | 갭 | 실제 위험 | 권장 조치 |
|---|---|---|---|
| P0 | 공지 하나당 임베딩 하나 | 긴 공지 중 한 문단이나 표의 날짜·요건이 벡터에서 희석됨 | 제목/섹션/표 구조를 보존한 청크 모델 도입 |
| P0 | 첨부파일 URL만 저장하고 추출 파이프라인 미통합 | 핵심 신청서·표·세부 기준이 PDF/HWP에만 있으면 답변 불가 | 파일 원장, 악성 파일 검사, PDF/HWPX/HWP/OCR 추출 워커 도입 |
| P0 | “hybrid” 검색의 lexical 후보군이 없음 | 벡터 top 30 밖의 정확한 학수번호·서식명·고유명사는 키워드 점수 기회조차 없음 | FTS와 vector를 각각 top-K 검색 후 RRF |
| P0 | 답변 근거가 공지 ID 수준 | 답변의 각 문장이 원문의 어느 문단/페이지에서 왔는지 검증 불가 | `citation_id`, 페이지/섹션/문자 오프셋을 가진 청크 인용 |
| P0 | 검색 결과 임계값 `0.22`가 수작업 고정 | 점수 분포가 모델/데이터 변화에 따라 달라져 오답 또는 과도한 거부 | 평가셋 기반 임계값 보정, top1/top2 margin과 근거 판정 병행 |
| P1 | 학년·학적·부서·기간 필드가 질문에서 추출되지만 일부는 검색에 미사용 | 개인 조건과 맞지 않는 공지가 상위 노출 | hard/soft filter 정책을 명시하고 전 필드 적용 |
| P1 | 동일 문자열 질문 캐시가 인덱스 버전을 포함하지 않음 | 공지가 갱신돼도 최대 24시간 오래된 답변 반환 | `index_generation`과 사용자 범위가 포함된 캐시 키, 변경 시 세대 전환 |
| P1 | 검색/생성 입력에 ORM 객체를 `default=str`로 직렬화 | 불필요한 내부 표현, 토큰 낭비, 근거 경계 불명확 | 허용된 citation DTO만 생성 모델에 전달 |
| P1 | `ProcessingJob`이 동기 API 프로세스에서 수행 | 대용량 파일, OCR, 재처리 시 장애 격리·재시도·처리량 제어 어려움 | durable queue + 독립 워커 + idempotency key |
| P1 | 스키마/임베딩 버전 필드는 있으나 활성 인덱스 세대와 원본 버전 부재 | 재임베딩 중 구·신 데이터 혼합, 롤백 어려움 | document version과 index generation 도입 |
| P1 | 관측 지표가 크롤 건수 위주 | 검색 실패가 수집, 추출, 후보 생성, 재랭킹 중 어디서 생겼는지 모름 | 단계별 trace와 품질/신선도/비용 지표 |
| P2 | SQLite fallback은 실제 pgvector/FTS 동작을 검증하지 않음 | 테스트 통과와 운영 SQL 품질이 불일치 | PostgreSQL 통합 테스트를 CI 필수 단계로 추가 |

추가로 `NoticeProcessor.process()`는 실패 시 동일 세션의 트랜잭션 상태와 재시도 경계가 모호하고, 기존 임베딩을 즉시 덮어쓴다. 운영형 파이프라인은 새 버전을 별도 작성하고 검증 완료 후 활성 포인터를 바꿔야 한다.

## 3. 제안하는 전체 아키텍처

### 3.1 오프라인 인덱싱 경로

1. **Source Registry**: 게시판/API/RSS/부서 디렉터리별 소유 부서, 수집 방식, 허용 범위, 우선순위를 등록한다.
2. **Fetcher**: 조건부 요청(`ETag`, `Last-Modified`)과 rate limit을 사용한다. 원문 HTML/JSON과 HTTP 메타데이터를 불변 스냅샷으로 저장한다.
3. **Attachment Intake**: 파일 크기/형식 allowlist, MIME과 magic-byte 일치 검사, 악성 파일 스캔 후 객체 저장소에 content-addressed key로 보관한다.
4. **Extractor**: HTML 본문, PDF 텍스트/표/페이지, HWPX XML, HWP 변환, 이미지 OCR을 형식별 워커에서 추출한다. 추출 결과에는 페이지와 블록 위치를 보존한다.
5. **Normalizer**: 메뉴/푸터/중복 공통 문구 제거, Unicode NFC, 공백·줄바꿈 정규화, 표를 행/열 의미가 남는 텍스트로 변환한다. 원문은 훼손하지 않는다.
6. **Versioner**: canonical content와 첨부 manifest의 SHA-256으로 문서 버전을 만든다. 변경이 없으면 이후 작업을 건너뛴다.
7. **Metadata Extractor**: 규칙으로 날짜·전화·학년도 등을 먼저 추출하고, LLM은 스키마 출력으로 보완한다. 충돌·낮은 신뢰도·종료일 역전은 검수 큐로 보낸다.
8. **Chunker**: 문서 구조와 의미 경계를 보존해 청크를 만들고 각 청크에 원문 locator를 부여한다.
9. **Indexer**: lexical `tsvector`와 임베딩을 생성하고 새 `index_generation`에 적재한다.
10. **Validator/Publisher**: 청크 수, 빈 텍스트, 벡터 차원, 인용 locator, 대표 질의 smoke test를 통과한 세대만 활성화한다. 실패 시 직전 세대를 유지한다.

### 3.2 온라인 질의 경로

```text
POST /api/v2/chat
  ├─ 입력 정규화, rate limit, 세션/권한 범위 확인
  ├─ 대화 맥락 해소: “그거 언제까지야?” → 독립 질의
  ├─ QueryPlan 생성
  │    ├─ lexical_query
  │    ├─ semantic_query
  │    ├─ hard_filters: 접근권한, archived=false 등
  │    ├─ soft_filters: 학년/학적/학기/부서/기간
  │    └─ expansions: 교내 용어/약어/동의어
  ├─ 병렬 후보 검색
  │    ├─ FTS/BM25 계열 top 50
  │    └─ pgvector cosine top 50
  ├─ RRF 융합 top 30
  ├─ 시간·대상·권위·문서 상태 정책 점수
  ├─ 재랭킹 top 8
  ├─ 동일 문서 과점유 제거 + 인접 청크 확장 top 4~6
  ├─ 충분한 근거인가?
  │    ├─ 아니오: no-data/명확화/담당부서 안내
  │    └─ 예: 구조화된 답변 + citation IDs 생성
  ├─ 문장별 인용·숫자·날짜 entailment 검사
  └─ 응답 + 검색 trace 저장
```

질문 분석 LLM은 검색을 보조할 뿐 검색 실패의 단일 지점이 되어서는 안 된다. 분석이 실패하거나 timeout이면 원 질문으로 lexical/vector 검색을 수행하는 fallback이 필요하다.

## 4. 수집, 정제, 청킹 설계

### 4.1 원본과 파생 데이터 분리

- 원본 스냅샷은 감사와 재처리를 위해 불변으로 보존한다.
- 검색 가능한 문서 버전은 원본 스냅샷을 참조하는 파생물이다.
- 전화번호·부서 디렉터리처럼 별도 권위 데이터는 공지 본문에서 추론하지 않고 독립 source로 관리한다.
- 개인정보나 인증 후 열람 자료는 공개 공지 인덱스와 물리적 또는 최소한 논리적으로 분리한다.
- `archived`는 삭제가 아니라 검색 정책 상태다. 기본 검색에서는 제외하되 “작년 기준” 같은 명시적 과거 질문에는 별도 범위로 검색할 수 있다.

### 4.2 첨부파일 처리

| 형식 | 1차 처리 | fallback | 필수 메타데이터 |
|---|---|---|---|
| PDF text | 페이지별 텍스트·표 추출 | 저품질 페이지 OCR | page number, bbox(가능 시) |
| scanned PDF/image | OCR | 수동 검수 | page/image, OCR confidence |
| HWPX | ZIP/XML 구조 파싱 | 변환 워커 | section/table locator |
| HWP | 격리된 변환기 | 수동 검수 | converter version |
| XLSX | sheet/표 범위별 직렬화 | 원문 링크 안내 | sheet, cell range |
| DOCX | heading/paragraph/table 추출 | 원문 링크 안내 | heading, paragraph/table index |

파일 본문은 공지 본문 끝에 단순 연결하지 않는다. `document_asset`와 별도의 `document_version`을 만들고, 부모 공지와 `parent_document_id`로 연결해야 검색 결과에서 “공지 본문”과 “첨부 2쪽”을 구분해 인용할 수 있다.

### 4.3 청킹 규칙

고정 글자 수만으로 자르지 말고 다음 우선순위를 사용한다.

1. 제목 → H1/H2/H3 → 번호 목록 → 표 → 문단 경계를 보존한다.
2. 각 청크에는 공지 제목과 상위 heading path를 짧은 prefix로 붙인다.
3. 목표 크기: 임베딩 토크나이저 기준 350~650 tokens, 최대 800 tokens.
4. 문단 경계 overlap은 60~100 tokens로 제한한다. 표는 행을 중간에서 자르지 않는다.
5. 날짜표, 자격요건, 제출서류, 문의처처럼 독립 질의 가능성이 높은 블록은 작은 atomic chunk로 별도 생성한다.
6. 지나치게 짧은 블록은 같은 heading의 다음 블록과 합친다.
7. 청크마다 `ordinal`, `heading_path`, `page_start/end`, `char_start/end`, `token_count`, `content_hash`를 저장한다.
8. 임베딩 텍스트와 표시/인용 텍스트를 분리한다. 임베딩에는 검색 보조 prefix를 허용하지만 인용은 원문 그대로 보여준다.

한국어에서는 조사 차이와 복합명사가 lexical recall을 떨어뜨릴 수 있다. 초기 버전은 PostgreSQL `simple` 구성의 unigram 성격 한계가 있으므로, 원문 토큰과 함께 정규화된 교내 용어/동의어 필드를 가중 인덱싱한다. 평가 결과가 부족하면 PGroonga 또는 형태소 기반 analyzer를 실험하되, 운영 의존성 추가는 오프라인 nDCG/Recall 개선이 입증된 뒤 결정한다.

## 5. 데이터 모델

기존 `notices`, `notice_metadata`, `notice_embeddings`를 즉시 삭제하지 않고 아래 테이블을 추가해 점진 이행한다.

```sql
source(
  id, source_type, base_url, owner_department_id,
  access_scope, crawl_policy_json, enabled, created_at, updated_at
)

source_snapshot(
  id, source_id, external_id, canonical_url,
  fetched_at, http_status, etag, last_modified,
  raw_object_key, raw_sha256, fetch_trace_id
)

document(
  id, source_id, external_id, canonical_url, document_type,
  parent_document_id, visibility_scope, current_version_id,
  archived_at, created_at, updated_at,
  UNIQUE(source_id, external_id)
)

document_version(
  id, document_id, version_no, source_snapshot_id,
  title, normalized_text, published_at, effective_from, effective_to,
  content_sha256, extractor_name, extractor_version,
  processing_status, review_status, created_at,
  UNIQUE(document_id, version_no), UNIQUE(document_id, content_sha256)
)

document_asset(
  id, document_version_id, filename, media_type, byte_size,
  object_key, sha256, malware_scan_status, extraction_status,
  page_count, created_at
)

document_metadata(
  document_version_id PK/FK, category, sub_category,
  academic_year, semester, application_start, application_end,
  event_start, event_end, target_student_types[], target_grades[],
  target_departments[], target_campuses[], action_type,
  department_id, required_documents_json, synonyms[], keywords[],
  extraction_confidence, schema_version, needs_review
)

chunk(
  id UUID, document_version_id, index_generation_id,
  ordinal, chunk_type, heading_path[], display_text, embedding_text,
  page_start, page_end, char_start, char_end,
  token_count, content_sha256, language,
  search_vector TSVECTOR, embedding VECTOR(1536),
  embedding_model, embedding_version, created_at,
  UNIQUE(document_version_id, ordinal, content_sha256)
)

index_generation(
  id, status, schema_version, embedding_model, embedding_dimensions,
  chunker_version, metadata_version, started_at, validated_at, activated_at
)

retrieval_trace(
  id UUID, request_id, session_id_hash, index_generation_id,
  normalized_query, query_plan_json, lexical_ids_json, vector_ids_json,
  fused_ids_json, reranked_ids_json, selected_ids_json,
  timings_json, thresholds_json, outcome, created_at
)

answer_trace(
  id UUID, request_id, model, prompt_version,
  citation_ids UUID[], answer_hash, validation_json,
  input_tokens, output_tokens, latency_ms, created_at
)

evaluation_case(
  id, question, conversation_context_json, expected_chunk_ids UUID[],
  expected_document_ids[], expected_facts_json, expected_behavior,
  slice_tags[], dataset_version, reviewed_by, created_at
)
```

권장 인덱스:

```sql
CREATE INDEX chunk_fts_gin ON chunk USING gin (search_vector);
CREATE INDEX chunk_embedding_hnsw ON chunk
  USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunk_generation_doc ON chunk(index_generation_id, document_version_id);
CREATE INDEX metadata_period ON document_metadata(application_start, application_end);
CREATE INDEX document_active_scope ON document(visibility_scope, archived_at);
```

HNSW는 데이터가 매우 작을 때 필수가 아니다. 먼저 exact vector search로 품질 기준선을 만든 뒤 p95 지연과 데이터량에 따라 HNSW를 켜야 한다. approximate index 도입 후에는 `ef_search`별 recall/latency를 측정하고, 메타데이터 필터로 후보가 적어지는 질의에서 iterative scan 또는 exact fallback을 검토한다.

## 6. 검색 및 랭킹

### 6.1 QueryPlan

```json
{
  "standaloneQuery": "2026학년도 2학기 재학생 등록금 납부 마감일",
  "lexicalQuery": "2026 2학기 재학생 등록금 납부 마감",
  "semanticQuery": "2026학년도 2학기 재학생 등록금을 언제까지 납부해야 하는가",
  "hardFilters": {
    "visibilityScope": ["public"],
    "activeGeneration": true
  },
  "softFilters": {
    "category": "등록",
    "academicYear": 2026,
    "semester": 2,
    "studentStatus": "재학생",
    "timeScope": "current"
  },
  "expansions": ["등록금", "등록", "납부", "수납"]
}
```

권한/가시성, 활성 인덱스 세대는 hard filter다. 카테고리와 학기 등은 분석 오류 가능성이 있으므로 기본적으로 soft boost로 쓰고, 질문에 명시된 값만 hard filter로 승격한다. strict filter 결과가 0이면 권한 필터를 제외한 나머지를 단계적으로 완화하고 그 사실을 trace에 남긴다.

### 6.2 후보 생성과 RRF

- Lexical: 제목(A), heading/키워드(B), 본문(C)처럼 `setweight`한 `tsvector`와 `ts_rank_cd` 사용.
- Semantic: active generation과 visibility 범위에서 cosine distance top 50.
- 각 후보군은 독립적으로 생성한다. 이것이 현재 코드의 가장 중요한 변경점이다.
- RRF 기본값: `rrf_score(d) = Σ 1 / (60 + rank_i(d))`.
- lexical/vector 각 50개를 결합해 30개를 재랭커에 전달한다.
- 학년도·학기·대상 일치, 공식 source 우선순위, 최신성은 RRF 뒤의 설명 가능한 feature로 추가한다.
- 만료 공지는 무조건 제거하지 않는다. “지난 학기”, “예전 규정” 질문에서는 필요하다. 현재 절차 질문에서는 active/upcoming을 우선하고 expired만 남으면 강한 경고를 붙인다.

PostgreSQL은 `tsvector`/`tsquery`, `websearch_to_tsquery`, `ts_rank_cd`를 제공하고, pgvector 공식 문서도 PostgreSQL FTS와 함께 RRF 또는 cross-encoder를 사용하는 hybrid search를 권장한다. 관련 공식 문서는 문서 말미에 링크했다.

### 6.3 재랭킹과 컨텍스트 조립

재랭커 입력은 `(standalone query, chunk display_text, 핵심 메타데이터)`이며, 출력은 relevance 0~1과 간단한 reason code다.

- 1단계: 한국어를 지원하는 cross-encoder를 자체 호스팅하거나 외부 rerank API를 오프라인 비교한다.
- 초기 트래픽이 작으면 JSON 스키마를 강제한 소형 LLM 재랭킹으로 시작할 수 있으나 latency·비용·비결정성을 측정한다.
- top 8에서 최종 4~6개를 선택한다.
- 동일 문서 최대 3개, 동일 heading 최대 2개로 제한해 문서 다양성을 확보한다.
- 선택 청크의 앞/뒤 인접 청크는 날짜나 표 문맥이 끊긴 경우에만 확장한다.
- 생성 모델에 전달하는 총 근거 토큰 예산을 고정하고, 원문을 자르더라도 citation locator는 유지한다.

### 6.4 답변 거부와 명확화

단일 cosine/가중합 임계값 대신 다음 신호를 결합한다.

- 재랭커 top1 relevance가 검증 임계값 미만
- 상위 청크들이 서로 다른 날짜/정책을 말하며 최신 버전을 결정할 수 없음
- 질문의 필수 slot(학년도, 학기, 학생 유형)이 없고 답이 조건에 따라 달라짐
- 날짜·전화·신청 방법 같은 핵심 답에 대응하는 근거 span이 없음
- top1과 top2가 상충하고 점수 차가 작음
- 검색 결과가 모두 `needs_review=true` 또는 추출 품질 미달

행동은 세 종류다.

1. **명확화 질문**: “어느 학년도/학기인지 알려주세요.”
2. **답변 거부**: 현재 문서에서 확인할 수 없다고 명시하고 공식 원문/담당 부서 안내.
3. **제한적 답변**: 확인 가능한 부분만 답하고 불확실한 필드는 “확인되지 않음”으로 표시.

## 7. 인용과 답변 생성

### 7.1 생성 모델 입력 계약

ORM 객체 전체를 직렬화하지 않고 다음 DTO만 전달한다.

```json
{
  "citationId": "c_01J...",
  "documentId": 182,
  "documentVersion": 3,
  "title": "2026-2학기 등록금 납부 안내",
  "sourceUrl": "https://...",
  "publishedAt": "2026-07-15T00:00:00+09:00",
  "status": "active",
  "locator": {"section": "납부기간", "page": 2},
  "text": "재학생 등록금 납부기간은 ..."
}
```

모델 출력도 자유 텍스트 하나가 아니라 구조화한다.

```json
{
  "answerMarkdown": "... [1]",
  "claims": [
    {"text": "납부 마감은 8월 28일 16시입니다.", "citationIds": ["c_01J..."]}
  ],
  "uncertainties": [],
  "followUpQuestion": null,
  "abstained": false
}
```

### 7.2 인용 검증

- 모든 날짜, 금액, 전화번호, URL, 제출서류 문장에는 최소 한 개 citation을 요구한다.
- citation ID가 입력 근거 집합에 실제 존재하는지 서버가 검증한다.
- 숫자/날짜는 원문 span과 정규화 값이 일치하는지 규칙으로 검사한다.
- 문장-청크 entailment 검사에서 실패하면 한 번 재생성하고, 다시 실패하면 해당 문장을 제거하거나 답변을 거부한다.
- UI에는 `[1] 공지 제목 · 납부기간 · 2쪽`처럼 표시하고 클릭 시 원문 URL과 locator를 연다.
- 원문이 갱신돼도 과거 answer trace는 당시 `document_version`을 가리켜 재현 가능해야 한다.

## 8. API 흐름과 계약

### 8.1 학생 질의 API

`POST /api/v2/chat`

```json
{
  "message": "이번 학기 등록금 언제까지 내?",
  "sessionId": "...",
  "studentContext": {
    "academicYear": 2026,
    "semester": 2,
    "studentStatus": "재학생"
  }
}
```

응답:

```json
{
  "requestId": "...",
  "answer": "... [1]",
  "citations": [
    {
      "id": "c_01J...",
      "title": "2026-2학기 등록금 납부 안내",
      "url": "https://...",
      "locator": {"section": "납부기간", "page": 2},
      "excerpt": "...",
      "publishedAt": "2026-07-15T00:00:00+09:00"
    }
  ],
  "hasData": true,
  "confidence": "high",
  "followUpQuestion": null,
  "indexGeneration": 17
}
```

`studentContext`에는 민감한 학생 식별자를 넣지 않는다. 향후 개인화가 필요하면 서버가 인증 claim에서 최소 속성만 만들고, 캐시는 권한/컨텍스트 범위별로 분리한다.

### 8.2 내부 검색 API

`POST /internal/search`

- 입력: QueryPlan, generation, topK, debug flag.
- 출력: 각 청크의 lexical/vector rank, RRF, feature score, reranker score, filter relaxation 기록.
- 외부에 노출하지 않으며 평가 러너와 관리자 디버그 화면에서 사용한다.

### 8.3 인덱싱/운영 API

- `POST /internal/sources/{id}/sync`: durable job을 enqueue하고 `202 Accepted` 반환.
- `GET /internal/jobs/{id}`: 단계, 재시도, 오류, 처리 문서/청크 수.
- `POST /internal/documents/{id}/reindex`: 새 버전/세대에 idempotent 재처리.
- `POST /internal/index-generations/{id}/validate`: 품질 게이트 실행.
- `POST /internal/index-generations/{id}/activate`: 검증된 세대를 원자적으로 활성화.
- `POST /api/v2/feedback`: request ID, 도움 여부, 사유, 선택 인용을 저장.

## 9. 캐시와 신선도

현재 `question_key(message)`만으로는 부족하다. 다음을 캐시 키에 포함한다.

```text
sha256(
  normalized_standalone_query |
  index_generation |
  visibility_scope |
  locale |
  student_context_bucket |
  prompt_version |
  answer_model
)
```

- 공지 변경 시 개별 키 삭제보다 새 `index_generation`을 활성화해 자연스럽게 오래된 캐시를 무효화한다.
- 마감 임박/진행 상태 질문은 TTL을 짧게(예: 10~30분), 안정적인 규정 FAQ는 길게 둔다.
- query analysis, query embedding, retrieval result, final answer 캐시를 분리한다.
- 개인 정보나 인증 범위가 다른 답변은 절대 공용 캐시를 공유하지 않는다.

## 10. 평가 체계

### 10.1 골든셋

초기 300~500개 질문을 학사 담당자와 학생 표현을 섞어 구축한다. 최소 slice는 다음과 같다.

- 카테고리별: 등록, 장학, 휴복학, 수강, 졸업, 병무 등
- 질문 형태: 정확한 제목, 구어체, 오탈자, 약어, 다중 턴, 비교 질문
- 근거 위치: HTML 본문, PDF, HWP/HWPX, 표, 이미지 OCR
- 시간: 현재, 예정, 만료, 과거 학년도, 상충 공지
- 대상: 학년, 학적, 학과, 캠퍼스
- 실패 행동: 데이터 없음, 모호함, 권한 없음, 악의적 prompt injection

### 10.2 오프라인 지표

| 단계 | 지표 | 초기 출시 게이트 예시 |
|---|---|---|
| 수집 | source freshness, fetch success | 핵심 source 99%, 신선도 SLA 이내 |
| 추출 | non-empty ratio, OCR confidence, locator accuracy | 핵심 문서 빈 추출 0, locator 표본 98%+ |
| 후보 검색 | Recall@10/30, MRR@10 | Recall@30 95%+ |
| 최종 검색 | nDCG@5, Hit@5, 대상/기간 적합률 | nDCG@5 0.85+ |
| 답변 | claim precision, citation precision/recall, 날짜·금액 정확도 | 핵심 수치 정확도 99%+ |
| 거부 | unsupported-answer rate, abstention precision/recall | 근거 없는 답변 1% 미만 |
| 성능 | p50/p95 latency, token/cost per answer | 제품 SLA로 확정 |

수치는 초기 제안이며 실제 골든셋과 위험 허용도에 맞춰 확정한다. 평균만 보지 말고 slice별 최저 성능을 품질 게이트로 삼아야 한다.

### 10.3 비교 실험

반드시 다음 ablation을 같은 평가셋에서 비교한다.

1. 현재 단일 공지 임베딩 기준선
2. chunked vector only
3. lexical only
4. vector + lexical RRF
5. RRF + metadata policy
6. RRF + reranker
7. 인접 청크 확장 유무

모델/프롬프트/청커/동의어 사전을 바꿀 때마다 dataset version, index generation, 결과를 함께 저장한다. 온라인 클릭률만 최적화하면 그럴듯한 오답이 늘 수 있으므로 unsupported-answer rate와 함께 판단한다.

## 11. 관측성과 운영

### 11.1 요청 trace

모든 답변에 `request_id`를 부여하고 다음 span을 기록한다.

```text
chat.request
  query.rewrite
  query.embed
  retrieve.lexical
  retrieve.vector
  retrieve.fuse
  rerank
  context.build
  answer.generate
  citation.validate
```

각 span에는 latency, 후보 수, 모델/버전, token, 재시도, timeout을 기록하되 원 질문과 원문 전체는 기본 로그에 남기지 않는다. 분석이 필요하면 접근통제·보존기간이 적용된 별도 저장소에 비식별화해 보관한다.

### 11.2 대시보드와 알림

- **신선도**: source별 마지막 성공 수집 시각, 최신 원문 대비 lag, 연속 실패.
- **파이프라인**: queue depth, 처리시간, retry/dead-letter, 형식별 추출 실패율.
- **인덱스**: 활성 generation, 문서/청크 수, 임베딩 누락, 구버전 혼입.
- **검색**: zero-result, filter relaxation, lexical/vector overlap, reranker score 분포.
- **답변**: abstention, citation validation failure, 재생성, 사용자 negative feedback.
- **성능/비용**: 단계별 p50/p95/p99, 토큰, 임베딩/생성 비용, cache hit.

경보 예시는 핵심 source 신선도 SLA 초과, 15분간 zero-result 비율 급증, citation validation failure 1% 초과, dead-letter 증가, 활성 generation 검증 실패다.

### 11.3 재현성과 롤백

한 요청을 재현하는 최소 키는 `index_generation`, `document_version IDs`, `embedding model/version`, `reranker version`, `prompt version`, `answer model snapshot`, `query plan`이다. 새 세대 활성화는 단일 DB 포인터 변경으로 수행하고 이상 시 직전 세대로 되돌린다.

## 12. 단계별 구현 계획

### Phase 0 — 기준선과 안전망 (1주)

- 현재 검색 결과를 `retrieval_trace` 형태로 계측한다.
- 실제 질문 100개와 no-answer 30개로 작은 골든셋을 만든다.
- PostgreSQL/pgvector 통합 테스트를 CI에 추가한다.
- 캐시 키에 데이터/프롬프트 버전을 포함한다.
- 완료 조건: 현재 Recall@5, unsupported-answer rate, p95가 측정 가능.

### Phase 1 — 청크와 정확한 인용 (2주)

- `document`, `document_version`, `chunk`, `index_generation` 마이그레이션 추가.
- HTML 구조 보존 청커와 locator 생성.
- 기존 공지를 backfill하되 기존 검색 경로는 유지.
- 생성 입력을 citation DTO로 바꾸고 v2 응답에 인용 배열 추가.
- 완료 조건: 답변 핵심 claim의 citation coverage 100%, 기존 API 회귀 없음.

### Phase 2 — 진짜 하이브리드 검색 (1~2주)

- weighted `tsvector` GIN 인덱스와 lexical top 50 구현.
- vector top 50과 RRF 결합, 필터 완화 정책 구현.
- 현재 가중합과 오프라인 A/B 비교 후 shadow mode로 온라인 관찰.
- 완료 조건: 중요 slice에서 Recall@30 저하 없이 nDCG@5 개선.

### Phase 3 — 첨부파일 파이프라인 (2~4주)

- object storage, 파일 검사, PDF/HWPX 우선 추출.
- 저품질 PDF OCR fallback과 검수 큐.
- attachment 청크를 부모 공지와 연결하고 페이지 인용 UI 제공.
- 완료 조건: 표본 locator accuracy 98%+, 빈 추출 핵심 파일 0.

### Phase 4 — 재랭킹과 근거 검증 (2주)

- cross-encoder/LLM reranker 후보를 골든셋에서 비교.
- 문장별 인용, 숫자/날짜 검증, 재생성/거부 정책 적용.
- 완료 조건: p95·비용 예산 내 nDCG/claim precision 개선, 근거 없는 답변 목표 충족.

### Phase 5 — 운영화 (2주)

- FastAPI background task를 durable queue/worker로 분리.
- 인덱스 generation 검증/활성화/롤백 구현.
- OpenTelemetry trace, 대시보드, alert, dead-letter 운영 절차 추가.
- 관리자 문서 품질/검색 디버그 화면 제공.
- 완료 조건: 장애 주입 시 재처리·롤백 가능, 운영 runbook 승인.

## 13. 테스트 전략

- **단위 테스트**: 정규화, 해시, heading/table chunking, RRF 동률, filter relaxation, cache key.
- **계약 테스트**: extractor별 locator, QueryPlan/answer JSON schema, citation ID 무결성.
- **통합 테스트**: 실제 PostgreSQL에서 FTS + pgvector + metadata filter; Redis 세대 캐시.
- **회귀 테스트**: 모든 골든셋을 index/prompt/model 변경 PR에서 실행.
- **property test**: 청크를 순서대로 합치면 정규화 원문을 빠짐없이 덮는지, locator가 범위를 벗어나지 않는지.
- **보안 테스트**: 공지 본문의 prompt injection, 악성 파일, zip bomb, URL allowlist 우회.
- **부하 테스트**: 동시 chat, crawler/reindex 동시 실행, HNSW build 중 읽기 지연.
- **복구 테스트**: 중간 작업 실패, 동일 메시지 재전달, generation 활성화 직전 장애, 구세대 롤백.

현재 `test_search_chat.py`의 대표 성공 사례 외에 lexical-only로 찾아야 하는 학수번호/서식명, 첨부 2페이지의 표, 상충하는 구·신 공지, 모호한 “그거 언제까지야?”, 관련 없는 질문의 hard negative를 반드시 추가한다.

## 14. 기술 선택과 보류 사항

| 결정 | 권고 | 재검토 조건 |
|---|---|---|
| 저장/검색 | PostgreSQL + pgvector 유지 | 수백만 청크/QPS/SLA에서 측정된 병목 |
| lexical | PostgreSQL FTS로 시작 | 한국어 Recall이 골든셋 목표 미달 |
| 융합 | RRF | 학습 가능한 랭커가 충분한 판단 데이터로 유의미한 개선 |
| ANN | exact 기준선 후 HNSW | p95가 예산 초과할 때 |
| reranker | 오프라인 비교 후 선택 | 품질 이득이 latency/비용을 정당화할 때 |
| orchestration | durable queue + worker | 단일 프로세스 demo에만 한정하면 현행 유지 가능 |
| 외부 managed retrieval | 당장 도입하지 않음 | 운영 인력/규모상 직접 운영 비용이 더 커질 때 |

## 15. 즉시 수정할 코드 지점

1. `backend/app/models/entities.py`: chunk/version/generation/trace 모델 추가. `NoticeEmbedding` 단일행 제약은 새 모델로 대체.
2. `backend/app/services/processing.py`: 덮어쓰기 대신 새 document version 생성 → 청킹 → 임베딩 → 검증 → 활성화.
3. `backend/app/services/search/hybrid.py`: 독립 lexical/vector SQL, RRF, 전체 QueryFilters 적용, explain score 반환.
4. `backend/app/services/chat.py`: `index_generation` 포함 캐시, citation DTO, 검증/거부 흐름.
5. `backend/app/services/ai/client.py`: 길이 `text[:12000]` 절단 제거; 청크별 토큰 기반 제한, 구조화 출력, timeout/retry/fallback 명시.
6. `backend/app/api/routes.py`: 동기 background task를 durable job enqueue로 변경하고 v2 API 추가.
7. `backend/app/prompts/answer_generation.txt`: citation ID 기반 claim contract로 개편.
8. `backend/tests/`: PostgreSQL 통합 검색, chunk/attachment/citation/evaluation 회귀 테스트 추가.

## 16. 공식 참고 문서

- [pgvector 공식 저장소 — hybrid search, RRF/cross-encoder, HNSW 및 재랭킹](https://github.com/pgvector/pgvector)
- [pgvector-python 공식 예제 — PostgreSQL FTS와 vector 결과의 RRF 결합](https://github.com/pgvector/pgvector-python/blob/master/examples/hybrid_search/rrf.py)
- [PostgreSQL 공식 문서 — Text Search 제어, `websearch_to_tsquery`, `ts_rank_cd`](https://www.postgresql.org/docs/current/textsearch-controls.html)
- [PostgreSQL 공식 문서 — Text Search configuration과 synonym dictionary](https://www.postgresql.org/docs/current/textsearch-configuration.html)
- [OpenAI API 공식 문서 — Evals API](https://platform.openai.com/docs/api-reference/evals)
- [OpenAI API 공식 문서 — API 데이터 제어 및 보존 정책](https://platform.openai.com/docs/models/default-usage-policies-by-endpoint)

## 17. 최종 권고

가장 먼저 할 일은 모델 교체가 아니라 **근거 단위와 평가 단위의 정립**이다. 문서 버전·청크·locator·index generation을 만들면 첨부 처리, 진짜 hybrid retrieval, 재랭킹, 문장별 인용, 회귀 평가와 롤백이 같은 토대 위에 올라간다. 반대로 이 토대 없이 생성 모델만 개선하면 검색 누락과 출처 불명 문제를 측정하거나 재현하기 어렵다.

따라서 첫 운영 마일스톤은 “HTML 공지를 청크 단위로 검색하고, 모든 핵심 답변 문장을 정확한 원문 섹션에 인용하며, 골든셋에서 현재 기준선보다 개선됨을 증명하는 v2 경로”로 잡는 것이 적절하다. 이후 첨부 형식을 단계적으로 추가하고, 마지막에 재랭커와 ANN을 품질/성능 측정에 따라 선택한다.
