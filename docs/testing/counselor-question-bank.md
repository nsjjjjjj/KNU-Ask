# 상담 질문 뱅크 사용법

`backend/app/data/counselor_question_bank.jsonl`은 실제 대학 상담 창구에서 나올 법한 표현으로 답변 품질을 탐색하는 질문 모음이다. 기존 `validation_questions.json`의 9개 골든 케이스를 대체하지 않는다. 골든 케이스는 정답 회귀 검사용이고, 이 질문 뱅크는 아직 발견하지 못한 실패를 찾는 탐색 검사용이다.

각 줄은 독립된 JSON 객체다. 주요 필드는 다음과 같다.

| 필드 | 의미 |
|---|---|
| `category` | 상담 업무 영역 |
| `risk` | 오답이 학생에게 미칠 수 있는 영향 |
| `expectedOutcome` | 답변, 조건 확인, 담당자 이관, 안전 응답 등 기대 행동 |
| `question` | 챗봇에 그대로 입력할 학생 질문 |
| `reviewPoints` | 사람이 답변에서 확인할 항목 |
| `conversationId`, `turn` | 같은 세션으로 실행할 후속 질문 묶음 |
| `tags` | 오타, 상대 날짜, 복수 업무 등 실패 유형 |

## 질문 확인

기본 명령은 API를 호출하지 않고 앞의 20개만 보여준다.

```bash
cd backend
python scripts/probe_counselor_answers.py
python scripts/probe_counselor_answers.py --category 수강 --limit 0
python scripts/probe_counselor_answers.py --query 오타 --limit 0
```

## 실제 API 검증

실행 중인 서버에 질문을 보낼 때만 `--execute`를 붙인다. 외부 AI가 켜져 있으면 호출 비용이 발생할 수 있으므로 작은 범위부터 시작한다.

```bash
cd backend
python scripts/probe_counselor_answers.py --category 휴학·복학 --limit 10 --execute > /tmp/knu-ask-report.json
python scripts/probe_counselor_answers.py --risk critical --limit 0 --execute > /tmp/knu-ask-critical.json
python scripts/probe_counselor_answers.py --limit 0 --execute > /tmp/knu-ask-all.json
```

자동 판정은 개인정보 거부, 안전 안내, 조건 확인, 담당 부서 연결, 성공 답변의 출처 유무 같은 구조적 문제만 잡는다. 날짜·대상·서류·절차가 원문과 맞는지는 각 케이스의 `reviewPoints`를 기준으로 사람이 확인해야 한다.

## 권장 검증 순서

1. `critical`, `high` 질문부터 실행한다.
2. 실패가 많은 카테고리를 골라 전체 질문을 실행한다.
3. 답변의 날짜, 대상, 신청 경로, 제출 서류, 담당 부서, 원문 링크를 확인한다.
4. 실패를 검색 누락, 질문 이해 실패, 최신성 오류, 근거 없는 생성, 안전 이관 실패로 분류한다.
5. 수정 후 같은 ID로 다시 실행해 회귀 여부를 비교한다.

질문에 들어간 이름·학번·전화번호·계좌번호는 개인정보 차단 동작을 확인하기 위한 명백한 예시값이며 실제 학생 정보가 아니다.
