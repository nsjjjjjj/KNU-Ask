# 누락 정보 지연 재확인 검증 보고서

측정일: 2026-07-22
환경: Docker의 격리 SQLite pytest DB, 결정적 규칙 파서와 mock PDF extractor
운영 DB 변경: 없음

## 검증 범위

- 절차 3단계 복구 및 `TaskProcedure`/`TaskProcedureStep` 저장
- PDF 2페이지의 제출 서류만 `TaskFact`/`TaskEvidence`로 저장
- 업무가 일치하는 공식 직원 연락처 선택과 팀장·타 업무 담당자 제외
- 신청 URL의 공식 도메인 확인, 근거 구간 저장, 클릭 가능한 단계 생성
- `verified_absent` 단기 캐시와 비단정적 답변 문구
- 네트워크 실패와 낮은 OCR 신뢰도를 부재로 확정하지 않음
- 원문·첨부 해시 변경 시 검증 기록 무효화
- 위험 요청이 검색·재확인보다 먼저 차단됨
- Codex가 제안한 인용문이 원문에 없으면 `excerpt_not_in_source`로 거부되는 기존 검증 유지

## 결정적 mock 측정 결과

`time.perf_counter()`로 같은 fixture를 반복 측정했으며 절대 시간은 테스트 실패 기준으로 사용하지 않는다.

| 상태 | 중앙값/측정값 (ms) | 비고 |
|---|---:|---|
| baseline | 0.043 | 구조화 절차가 이미 존재 |
| cold recovery | 1.176 | 절차 누락 감지, 3단계 파싱·DB 반영 |
| warm after recovery | 0.044 | 저장된 구조화 데이터 사용 |
| baseline 대비 cold 증가 | 1.133 | 로컬 결정적 fixture |
| baseline 대비 warm 차이 | 0.001 | PDF/OCR/Codex 재호출 없음 |
| mock PDF cold | 2.762 | PDF 추출 결과를 반환하는 mock 1회 호출 포함 |
| mock PDF warm | 0.037 | extractor 호출 0회 |
| mock PDF DB 반영 | 2.100 | 서류 사실·근거 저장 |

mock extractor 자체가 즉시 결과를 반환해 `pdfDownloadExtraction`은 반올림 후 0.0ms였다. OCR과 Codex는 이 결정적 PDF fixture에서 필요하지 않아 각각 0.0ms이며, 실제 외부 호출 시간은 `QueryMetric.stage_timings_ms`와 recovery 전용 필드에 기록된다.

## 자동 테스트 결과

- 백엔드: 212 passed
- 프런트엔드: 20 passed
- 프런트엔드 production build: 성공
- warm 호출의 PDF 다운로드/OCR/Codex call count: 0

실제 PDF·네트워크 성능은 파일 크기, 학교 서버 응답, OCR 필요 여부에 영향을 받으므로 절대 시간으로 테스트를 실패시키지 않는다. 복구 다운로드는 공식 허용 도메인, 기존 파일 크기 제한, 12초 recovery 요청 제한, 최대 20페이지 설정을 사용한다.
