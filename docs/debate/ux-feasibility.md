# 라운드 3 — UX 실현 가능성 평가

- 작성일: 2026-07-20
- 평가 역할: UX Architect
- 평가 대상: 라운드 2 합의안과 `docs/research/ux-recommendations.md`

## 1. 판정

`React + FastAPI + PostgreSQL + pgvector + 선택적 OpenAI`는 MVP 화면 흐름을 지원할 수 있다. Elasticsearch, 별도 Vector DB, Redis는 질문 입력, 근거 제시, 다음 행동, no-result 복구에 필요한 선행 조건이 아니다.

다만 성공 응답과 오류 응답만 있는 API로는 실제 화면을 안전하게 만들 수 없다. 서버는 검색 상태와 답변 모드, 해석한 조건, 다음 행동, 담당 부서, 근거 구간, 검색 범위를 구조화해 반환해야 한다. 공지 상태는 저장 당시 값이 아니라 질의 시점의 KST와 유효기간으로 재계산해야 한다.

## 2. 화면 흐름

```text
진입
  → 자유 질문 / 시기별 추천 질문 / 분야 / 검수 FAQ
  → 민감정보 패턴 검사
      → 발견: 외부 전송 전에 수정 요청
  → 규칙 기반 질문 분석
      → 답을 바꾸는 모호성만 1회 확인
  → 유효한 검수 FAQ 고신뢰 일치 확인
      → 일치: FAQ 반환
      → 불일치: 공지 검색
  → metadata + lexical + pgvector 후보 검색
  → 근거 충분성 판정
      → 충분: 결정적 답변 또는 선택적 생성 요약
      → 상충: 상충 경고와 문서별 근거
      → 과거뿐: 종료 경고와 담당 부서
      → 근거 부족/결과 없음: 범위 설명과 복구 행동
      → 장애: 재시도와 FAQ/공지 목록 fallback
  → 결론 → 다음 행동 → 담당 부서 → 근거/원문
  → 후속 질문 → 도움 여부 → 종료/새 질문
```

## 3. 기능별 실현 가능성

| 화면 요구 | 판정 | 서버/API 보완 |
|---|---|---|
| 자유 질문 | 가능 | `request_id`, 중복 제출 방지 |
| 분야 필터 칩 | 가능 | 선택 분야는 기본 soft boost |
| FAQ 진입 | 가능 | 유효기간·승인·출처 버전 필요 |
| 검색 진행 표시 | 가능 | 분석/검색/근거검증/생성을 분리 |
| 결론 우선 답변 | 가능 | 자연어 외 구조화된 핵심 필드 반환 |
| 다음 행동 CTA | 조건부 가능 | 검증된 공식 URL과 기한 필드 필요 |
| 담당 부서 | 조건부 가능 | 검수된 부서 기준 데이터 필요 |
| 근거 공지 최대 3개 | 가능 | parent 공지와 evidence chunk를 묶음 |
| 종료 공지 경고 | 조건부 가능 | 질의 시 유효 상태 재계산 |
| 결과 없음 복구 | 가능 | 원인별 상태 코드 필요 |
| 생성 장애 fallback | 가능 | 공지 목록·FAQ·부서 응답 분리 |
| 피드백 | 가능 | 질문 원문 없는 최소 이벤트 모델 |
| 자동 상담 티켓 | MVP 제외 | 부서 딥링크와 사용자가 복사할 문구만 제공 |

## 4. 필수 10개 쟁점의 UX 평가

1. **PostgreSQL + pgvector만으로 충분한가?** 화면 구현에는 충분하다. 판정 기준은 DB 제품이 아니라 Recall@5, 조건 위반율, 근거 없는 확정 답변율이다.
2. **Elasticsearch가 필요한가?** 아니다. 오히려 이중 색인 지연으로 수정 전 공지가 보일 수 있다. 한국어 재현율 또는 p95 실패가 측정된 뒤 재평가한다.
3. **Redis가 필요한가?** 아니다. 캐시된 오래된 날짜 답변은 UX 신뢰를 훼손한다. 도입 시 코퍼스·검색·모델 버전과 짧은 TTL, 변경 무효화가 필수다.
4. **전부 청킹해야 하는가?** 아니다. 짧은 공지는 전체, 긴 공지는 구조 경계로 분리하며 사용자는 parent 공지 카드와 실제 근거 구간을 본다.
5. **metadata와 vector 우선순위는?** 명시된 연도·학기·학년·상태는 검증된 metadata, 주제·구어체는 vector, 서식명·학수번호는 lexical 검색이 담당한다.
6. **질문 분석에 항상 LLM이 필요한가?** 아니다. 날짜·학기·학년·명령·민감정보는 규칙이 우선이고, 해석이 답을 바꾸는 모호함만 LLM 또는 재질문으로 처리한다.
7. **FAQ와 RAG 우선순위는?** 유효한 검수 FAQ → 구조화 필드 직접 답변 → RAG → 공지 목록/담당 부서 순이다.
8. **수정·삭제 감지는?** 원문/첨부 해시와 목록 대조, 버전 보존, 연속 누락 확인, 정정 관계를 사용한다. UI에는 수정·종료·대체 공지·마지막 확인 시각을 표시한다.
9. **결과 없음 판단은?** `no_result`, `insufficient_evidence`, `conflicting_evidence`, `stale_only`, `out_of_scope`, `clarification_required`, `service_error`를 구분한다.
10. **생성 없이 가능한 질문은?** 검수 FAQ, 날짜/상태/링크/부서 조회, 조건별 공지 목록, 원문 요청, 서비스 범위와 장애 안내다.

## 5. 프런트엔드 상태 모델

```ts
type ChatPhase =
  | "idle"
  | "validating_input"
  | "clarification_required"
  | "analyzing"
  | "searching_faq"
  | "searching_notices"
  | "validating_evidence"
  | "generating"
  | "success"
  | "no_result"
  | "insufficient_evidence"
  | "conflicting_evidence"
  | "stale_only"
  | "out_of_scope"
  | "service_error";
```

`generating`은 모든 요청의 필수 상태가 아니다. FAQ, 구조화 답변, 공지 목록은 `validating_evidence`에서 바로 완료될 수 있다.

## 6. 최소 응답 계약

```json
{
  "status": "success",
  "answer_mode": "deterministic",
  "verified_at": "2026-07-20T14:30:00+09:00",
  "corpus_version": "2026-07-20T14:25:13Z",
  "interpreted_as": {
    "academic_year": 2026,
    "term": "2",
    "grade": 3
  },
  "summary": "3학년 수강신청은 8월 10일 10:00부터입니다.",
  "warnings": [],
  "next_action": {
    "label": "수강신청 화면으로 이동",
    "url": "https://official.example.edu/...",
    "deadline": "2026-08-11T17:00:00+09:00",
    "official": true
  },
  "department": {
    "name": "학사지원팀",
    "phone": "000-0000-0000",
    "source_url": "https://official.example.edu/..."
  },
  "sources": [
    {
      "notice_id": "id",
      "title": "2026학년도 2학기 수강신청 안내",
      "effective_status": "upcoming",
      "evidence_excerpt": "3학년: 8.10. 10:00 ~ 8.11. 17:00",
      "url": "https://official.example.edu/..."
    }
  ],
  "search_scope": {
    "sources": ["official_notices", "approved_faq"],
    "backfill_from": "2025-03-01"
  }
}
```

`status`와 `answer_mode`는 분리한다. 예를 들어 `stale_only + search_results_only`, `insufficient_evidence + department_handoff`가 가능해야 한다.

## 7. 상태별 화면 계약

| 상태 | 사용자에게 설명할 내용 | 주요 행동 |
|---|---|---|
| `success` | 결론, 조건, 확인 기준일 | 다음 행동/원문 보기 |
| `clarification_required` | 답을 바꾸는 누락 조건 하나 | 조건 선택·입력 |
| `no_result` | 확인한 범위에 관련 자료 없음 | 질문 고쳐 쓰기 |
| `insufficient_evidence` | 관련 자료는 있으나 핵심 근거 없음 | 원문/담당 부서 |
| `conflicting_evidence` | 공식 자료 간 내용이 다름 | 원문 비교/부서 확인 |
| `stale_only` | 현재 유효 자료 없이 과거 자료만 존재 | 담당 부서/과거 공지 |
| `out_of_scope` | 수집·서비스 범위 밖 | 공식 통합검색/부서 찾기 |
| `service_error` | 자료 없음이 아닌 일시적 장애 | 다시 시도/FAQ |

## 8. MVP 범위와 출시 조건

포함 범위는 모바일 자유 질문, 분야/FAQ/추천 질문, 민감정보 사전 차단, 규칙 우선 질문 분석, PostgreSQL 검색, 선택적 청킹, 비생성 답변, 선택적 생성 요약, 다음 행동·부서·근거·원문, 실패 상태, 최소 피드백이다.

SSO와 개인 학사정보, 자동 티켓/CRM, 신청 완료 처리, 선제 알림, 전면 첨부 형식 지원, 자동 FAQ 승인, 다국어 생성은 제외한다.

출시 전에 다음을 확인한다.

1. 날짜·학기·대상 조건 위반과 종료 공지 오인율이 허용 기준 이하
2. 근거 구간이 실제 답을 지지하는지 담당자 평가 통과
3. no-result 사용자가 질문 수정 또는 부서 연결로 복구 가능
4. 시스템 장애와 데이터 없음이 명확히 구분됨
5. 민감정보가 외부 모델 호출 전에 차단됨
6. 375px, 200% 글자 확대, 키보드, 화면 판독기 과업 통과
7. 최소 1~2개 학년도와 현행 규정이 백필됨
8. 공지 수정·삭제 후 검색과 FAQ가 정해진 SLA 안에 갱신됨

