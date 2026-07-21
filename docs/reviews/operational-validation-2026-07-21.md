# KNU-Ask 크롤링 확대 및 운영 검증 보고서

검증일: 2026-07-21 (Asia/Seoul)
환경: Docker Compose, PostgreSQL/pgvector, BGE-M3(Ollama), Gemini Flash, Codex 외부 구조화 큐

## 1. 기존에 수집되지 않았던 원인

- 행사/안내와 FAQ 코드는 일부 있었지만 운영 DB에서 전체 프로필을 실행하지 않았고 스케줄러도 꺼져 있어 행사 0건 상태였다.
- 학사안내는 고정 시작점만 읽어 하위 탭과 FAQ 탭 일부를 놓쳤다.
- 국제교류·봉사·장애학생 하위 사이트는 마지막 페이지 이후에도 필독 행을 반복해 기존 날짜 종료 조건만으로는 같은 페이지를 최대 200회 재수집했다.
- 첨부에 포함된 NUL(0x00) 문자 7건이 PostgreSQL 저장 단계에서 거부됐다.
- 설치된 LaunchAgent가 오래된 개발 토큰을 사용해 403을 냈다.
- 이미지 주소가 `.do`이고 Content-Type이 `application/octet-stream`인 경우 실제 PNG/JPEG를 `.bin`으로 넘겨 Codex 비전 입력이 실패했다.

## 2. 추가한 크롤링 대상

- 최근 12개월 공식 공지와 행사/안내 전체 본문
- 학사안내 18개 루트 및 발견한 하위 탭, 휴복학·졸업·장학 FAQ
- 학사일정, 장학·학자금대출, 2024~2026 대학요람, 공개 현행 규정
- 대학일자리플러스센터, 대외교류센터, 심전생활관, 사회봉사, 장애학생지원
- 상담·심리검사, IT 서비스, 무료셔틀, 후생·동아리·도서관 상시 안내
- 통합전화번호부 373건

## 3. 주요 변경 파일

- `backend/app/services/crawler/knu.py`: 수집 프로필, 전체 출처, 하위 탭, 반복 페이지 차단, 요청 시작 간격 진행률
- `backend/app/services/crawler/attachments.py`: PDF 페이지 근거, OCR 검토, 해시 및 24시간 다운로드 캐시
- `backend/app/services/crawler/jobs.py`: 원문 수집과 안전한 증분/전체 처리
- `backend/app/services/processing.py`: TaskUnit 기준 저장, 외부 AI 큐, NUL 재귀 정리, 검증 후 공개
- `backend/app/services/search/hybrid.py`, `task_rules.py`: 강한 조건 검색, 행사 정확 제목, 졸업/조기졸업·현장실습/창업대체학점 분리
- `backend/app/services/chat.py`: 복수 업무, 최소 세션 문맥, 근거 기반 직접 답변
- `frontend/src/pages/AdminPage.tsx`, `ChatPage.tsx`, `frontend/src/components/*`: 관리자 예상량, 상태 색상, 모바일 접기 흐름
- `nginx/default.conf`: 공개 API 속도 제한과 외부 관리자 경로 차단
- `scripts/codex_ingestion_worker.py`, `install_codex_launch_agent.py`: 비전 구조화, 실패 격리, 토큰 비노출 LaunchAgent
- `backend/scripts/validate_operational_queries.py`: 17개 질문 진단

## 4. 시험 수집 건수

- profile `pilot`, history 7
- 발견 670건, 신규 297건, 갱신 373건, 동일 0건, 실패 0건
- 행사/학사/FAQ/장학/대학요람/취업/상시 안내/전화번호 표본 검증 후 전체 수집으로 확대했다.

## 5. 전체 확대 수집 건수

- profile `full`, history 11
- 발견 3,219건, 신규 1,771건, 갱신 1,307건, 동일 134건
- 최초 저장 실패 7건은 모두 NUL 제거 후 해당 원문 URL만 재수집해 복구했다.
- 활성 검색 문서 2,843건과 전화번호부 373건이다. 발견 건수와의 차이는 중복 sourceId 병합이다.

## 6. 출처별 성공·실패 건수

| 출처 | 활성 건수 |
|---|---:|
| official_notice | 751 |
| event | 1,845 |
| academic_guide | 55 |
| official_faq | 62 |
| scholarship_guide | 4 |
| university_catalog | 12 |
| university_regulation | 6 |
| career_program | 50 |
| international_notice / guide | 13 / 1 |
| dormitory_notice / guide | 10 / 1 |
| student_service_notice / guide | 19 / 11 |
| library_guide | 3 |
| staff_directory | 373 |

원문 단계의 최종 미복구 실패는 0건이다. 첨부 추출 상태는 success 755, partial 190, failed 28, not_required 325, 행사 저우선 처리 deferred 1,545건이다. 첨부 URL 보유 문서 2,047건 중 추출 본문 945건, OCR 적용 문서 920건이다.

## 7. 실패 URL과 실패 이유

다음 7개 공식 공지는 원문/첨부 NUL 때문에 최초 저장이 실패했으나 모두 재수집에 성공했다.

- `36270298438b88cb3074fce50dba6b21` 산학재단 장학금
- `6c9c680e16e6099cdab64c5a0a3013bd` 리스타트
- `dd84092bc84d8a0226a4c0298b23d601` 더체인지
- `71927f1f53d660b0b32621d9b710eba9` 리스타트
- `5570a2b7e2a7f91ee3b59359d8544c35` 선원가족 장학
- `f46eb2c7d0847a1d679b425d3d2866c1` 부산진구장학회
- `837925065af86366dac4bb029e149783` 지역기반 기업분석 경진대회

최종 원문 실패 URL은 없다. `failed` 첨부 28건은 비지원 바이너리, OCR 실패, 크기 제한 등을 문서별 manifest에 보존하며 HTML 본문과 기존 공개 데이터는 유지한다.

## 8. 질문별 검색 검증 결과

- 크래프톤 캠프 정확 제목: 공지 1078 / `event.camp` 1위
- 크래프톤 신청 방법: 같은 공지를 찾되 원문에 절차가 없어 추측 없이 `insufficient_evidence`
- 현재 교외 캠프: ABC캠프 신청 기간 정상
- 2026-2 수강신청: 공지 24, 2026-08-05 10:00~08-06 23:59
- 2024 입학생 졸업요건: 2024 대학요람, 130학점과 제2전공 근거
- 졸업요건/조기졸업 비교: 공지 885와 126의 서로 다른 근거
- 휴학/후속 질문/복학 연락처: 상시 휴학, 세션 문맥, 교무팀 전화 정상
- 국가·성적우수 장학: 상시 기준과 신청 필요 여부 분리
- 교환학생/기숙사/상담/셔틀: 각 전용 TaskUnit 정상
- 현장실습: 창업 대체학점을 제외하고 공지 8의 현장실습학기제 선택
- 졸업요건 확인 위치: 대학요람 공식 원문 선택

17개 회귀 질문 중 원문에 신청 절차가 없는 크래프톤 신청 질문만 의도적으로 근거 부족이며, 나머지는 `success`다.

## 9. AI 호출 수와 예상 토큰

- 전체 신규·변경 Codex 구조화 큐 2,709건
- 완료 50건, 안전하게 대기 중 2,659건
- 전체 실행 전 입력 상한은 약 18.4M tokens로 표시했다. 실제 호출은 문서 길이 제한과 변경 감지로 상한보다 작다.
- 현재 Codex workspace가 `out of credits`를 반환해 큐를 중지했다. 규칙 기반 결과로 대체하지 않았다.
- 활성 문서 2,843건 중 검증된 공개 문서 904건, 새 문서 대기 1,939건이다.

## 10. 자동 갱신 상태

- 매시간 공지·행사 증분, 매일 학사일정, 매주 정적 안내·전화번호·요람·규정 전체 확인 코드가 구현됐다.
- LaunchAgent는 토큰을 plist/Git에 저장하지 않고 권한 600 파일만 읽는다.
- 크레딧 소진 상태에서 실패를 반복하지 않도록 LaunchAgent와 스케줄러는 현재 중지했다.

## 11. Docker 반영 상태

- backend, frontend, nginx 최신 이미지/설정 반영
- PostgreSQL 및 첨부 캐시 볼륨 보존
- health 200, Nginx `nginx -t` 성공
- 외부 Host의 관리자 API 404
- 백엔드 132개, 프런트엔드 11개 테스트와 production build 성공
- 390×844 모바일 브라우저에서 현장실습 절차를 10단계에서 학생 행동 5단계로 축약하고 내부 승인·합격 확인을 제거했으며 가로 넘침 0, 공식 근거 기본 접힘을 확인
- tracked secret 검사와 `git diff --check` 성공

## 12. 남은 데이터 부족

- 공식 대학요람 페이지에서 공개 확인 가능한 파일은 2024~2026학년도이며, 2017~2023 공개 파일은 찾지 못했다.
- Codex 크레딧 때문에 2,659건의 새 구조화·임베딩 공개 전환이 대기 중이다.
- 비지원 HWP 등 실패 첨부 28건과 저우선 일반 행사 첨부 1,545건은 원문 HTML/제목 검색은 가능하지만 첨부 내부 사실을 답변 근거로 사용하지 않는다.
- 크래프톤 캠프 공지에는 신청 기간·방법 자체가 없어 추가 공식 출처가 없으면 안내할 수 없다.

## 13. 운영 전에 추가로 필요한 작업

1. Codex 앱을 크레딧이 남은 두 번째 계정으로 전환하거나 학교 OpenAI API를 설정한다.
2. `python3 scripts/install_codex_launch_agent.py` 후 8개 drain worker로 2,659건을 완료한다.
3. pending/running/failed가 0인지, 스키마·임베딩 누락이 0인지 확인한다.
4. 17개 질문과 모바일 브라우저를 한 번 더 검증한다.
5. 그 뒤에만 `CRAWLER_SCHEDULE_ENABLED=true`로 켜고 고정 Cloudflare Tunnel과 운영 알림을 연결한다.
