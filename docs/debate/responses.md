# 라운드 5 — 비판 답변과 수정된 제안

- 작성일: 2026-07-20
- 답변 역할: RAG Architect, Infrastructure Architect
- 입력: `docs/debate/risk-challenges.md`

## 1. 답변 원칙

두 담당자는 후보 A를 유지하지만 “PostgreSQL이면 정확하다”, “단순하므로 안전하다”는 주장은 철회한다. 후보 A의 이점은 현재 코드 재사용과 적은 운영 대상이며, 정확도·보안·최신성은 출시 게이트로 별도 입증한다.

## 2. RAG 담당자의 답변

| 비판 | 판정 | 변경 전 | 변경 후 |
|---|---|---|---|
| 한국어 검색 미검증 | 전면 수용 | PG FTS+vector를 고정 기본안으로 평가 | ILIKE, trigram, FTS, vector, RRF를 동일 골든셋으로 비교 |
| hard filter false negative | 전면 수용 | 명시 조건을 곧바로 SQL 필터 | strict/relaxed 후보 병렬, 다중값·confidence·provenance 저장 |
| 최근 2개월 한계 | 전면 수용 | 최근 공지로 과거·현행 질의도 지원 | 1~2개 학년도 백필, 규정·편람·학사일정·부서 자료 별도 수집 |
| 무응답 기준 | 전면 수용 | 고정 점수와 결과 존재 여부 | 필수 span, 조건, 유효성, 충돌을 합친 증거 충분성 판정 |
| OpenAI 장애 | 부분 수용 | AI 중심 단일 경로 | lexical/metadata 검색과 템플릿 답변 fallback |
| FAQ 최신성 | 전면 수용 | 반복 질문을 FAQ gate로 승격 | 검수·버전·유효기간·승인자와 변경 시 자동 비활성화 |
| 인젝션·악성 첨부 | 부분 수용 | 문서를 바로 모델 근거로 전달 | 비신뢰 데이터 격리, 제한 스키마, 첨부 sandbox, 서버 검증 |
| 선택적 청킹 미검증 | 부분 수용 | 길이 기준 선택적 청킹 | parent+atomic child 병렬 검색과 세 방식 ablation |

### 수정된 RAG 흐름

```text
승인 source → 불변 원본/버전 → 안전한 정규화
→ 규칙 metadata + 조건부 LLM → parent + 선택적 구조 chunk
→ PostgreSQL lexical/metadata + pgvector 색인
→ 검증 후 활성 index generation 전환

질문 → PII 검사 → 규칙 QueryPlan → 검수 FAQ
→ lexical/vector + strict/relaxed metadata 후보
→ 효력·정정 관계 재정렬 → 근거 충분성 판정
→ 생성 또는 템플릿 → 인용·숫자·날짜·URL 서버 검증
```

### RAG 출시 게이트

- 실제 학생 표현 100~300개 골든셋에서 Recall@5와 조건 위반율 측정
- metadata 필터로 정답이 후보 전체에서 사라지는 사례 0건
- 무응답 정밀도 90% 이상, 근거 없는 확정 답변율 1% 미만
- 날짜·금액·대상 주장의 근거 coverage 100%
- 활성 FAQ의 유효 출처 연결 100%, 변경 시 자동 비활성화
- OpenAI 차단 시 검색·원문·부서·템플릿 응답 유지
- parent-only, full-chunk, parent+selective-chunk 비교 완료

## 3. 인프라 담당자의 답변

| 비판 | 판정 | 변경 전 | 변경 후 |
|---|---|---|---|
| 무인증 관리 API | 전면 수용 | 공개 라우터에 관리 기능 | 관리 경계 분리, 외부 차단, 인증·역할·감사·실행 잠금 |
| PII/로그/캐시 | 전면 수용 | 질문을 AI에 전달하고 응답 캐시 가능 | PII 사전 차단, 조건부 AI, Redis 제외, 본문 비로그 |
| SSRF·첨부 | 부분 수용 | 발견 URL의 향후 직접 처리 | 학교 host allowlist, redirect 재검증, 텍스트 PDF만 sandbox |
| 변경 감지 무결성 | 전면 수용 | 현재 레코드 갱신·1회 누락 처리 | 불변 버전, 첨부 hash, 연속 누락, 원자적 활성 포인터 |
| 비용 폭주 | 전면 수용 | 평균 호출과 캐시 절감 가정 | 호출/토큰/동시성/일·월 예산, circuit breaker, kill switch |
| 비밀·공급망 | 전면 수용 | 환경변수와 버전 고정 | secret store, digest·SBOM·scan, non-root·read-only |
| Mac mini 공개 | 전면 수용 | Compose 공개 파일럿 | 폐쇄 데모만 허용, 공개는 학교 승인 VM |
| 백업·롤백 | 전면 수용 | 매일 백업 권고 | RPO≤24h, RTO≤4h, 월별 실제 복원, 앱·인덱스 롤백 |
| Redis·큐 | 수용 | Redis+PG 큐+상시 워커 | 무캐시 + scheduler/cron 멱등 배치와 실행 원장 |
| 점수 편향 | 전면 수용 | 확정적 단일 점수 | 가설 점수·범위 공개, 실측과 독립 채점으로 갱신 |

### 수정된 인프라 흐름

```text
학생 → 학교 승인 프록시(TLS, rate limit)
     → React + 공개 FastAPI
     → PostgreSQL + pgvector
     → 조건부 OpenAI

관리자/학교 스케줄러 → 외부 차단 관리 경계
                       → advisory lock 멱등 배치
                       → 승인 대학 도메인만 수집
```

Redis, Elasticsearch, 별도 Vector DB, 상시 큐 워커는 MVP에서 제외한다. 배치는 `(notice_id, content_hash, processing_version)` 고유 제약으로 중복 과금을 막고, 실패는 제한된 운영자 재실행으로 처리한다.

### 인프라 출시 게이트

- 관리 API 인증·권한·감사와 외부망 차단
- PII 탐지, 외부 AI 처리 승인, 본문 비로그
- SSRF/redirect/DNS rebinding/대용량 파일 시험
- 수정·첨부교체·일시누락·삭제·복원 변경 감지 시험
- rate limit, 예산 차단, 비생성 fallback 시험
- secret scan, SBOM, 취약점·이미지 검사
- 학교 승인 VM과 운영 책임자
- 암호화 외부 백업과 RTO 내 복원 시험
- 배치 동시 실행·중간 실패·멱등 재실행 시험

## 4. 수정 후 공통 합의

최종 MVP는 **React + FastAPI + PostgreSQL + pgvector + 조건부 OpenAI** 하나다.

- PostgreSQL은 선택했지만 한국어 검색 방식은 실험으로 결정한다.
- metadata는 우선하되 미검수 값으로 정답을 제거하지 않는다.
- 공지는 parent를 항상 유지하고 긴·복합 블록만 선택적으로 청킹한다.
- LLM은 질의 분석과 답변에 항상 필요하지 않다.
- 검수 FAQ와 구조화 답변을 우선하고 RAG는 다음 경로다.
- 변경 감지는 불변 버전, 본문/첨부 해시, 반복 누락 확인으로 수행한다.
- 결과 없음은 다중 근거 상태로 판정한다.
- Redis·Elasticsearch·별도 Vector DB·전면 첨부·SSO는 MVP에서 제외한다.

## 5. 남아 있는 위험

1. 한국어 검색이 목표 품질에 도달할지는 아직 미측정이다.
2. PII 탐지는 완전하지 않고 외부 AI 공급자 위험을 제거하지 못한다.
3. 원천 공지가 틀리거나 늦게 수정되면 시스템도 정확성을 보장할 수 없다.
4. 배치 방식은 실시간성보다 단순성과 멱등성을 선택한 절충이다.
5. 학교의 외부 AI 승인과 운영 VM 제공은 외부 의존성이다.
6. 후보 A의 우위는 현재 Python 코드와 소규모 공개정보 MVP라는 조건에 한정된다.

