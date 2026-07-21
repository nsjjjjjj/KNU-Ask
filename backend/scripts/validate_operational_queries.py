"""운영 전 대표 질문의 검색·근거·호출 진단을 JSON으로 출력한다.

전체 수집이나 재색인은 수행하지 않으며, 현재 공개 인덱스만 읽어 답변한다.
세션에는 서비스와 동일하게 업무키·연도·학기·선택 공지 등 최소 문맥만 저장된다.
"""

from __future__ import annotations

import argparse
import json
import time

from app.db.session import SessionLocal
from app.services.chat import ChatService


CASES = [
    {"id": "krafton-camp", "question": "경기대학교 크래프톤 정글 웹개발 집중 캠프 알려줘"},
    {"id": "krafton-apply", "question": "2026 AI-Powered 경기대 웹개발 캠프 신청 방법 알려줘"},
    {"id": "external-camps-open", "question": "현재 신청할 수 있는 교외 캠프 알려줘"},
    {"id": "course-2026-2", "question": "2026학년도 2학기 수강신청 일정 알려줘"},
    {"id": "graduation-2024", "question": "2024년도 입학생 졸업요건 알려줘"},
    {"id": "graduation-compare", "question": "2024년도 입학생 졸업요건과 조기졸업 요건 차이 알려줘"},
    {"id": "leave", "question": "휴학 신청 기간과 준비서류 알려줘"},
    {
        "id": "graduation-follow-up", "question": "그럼 어디서 확인해?",
        "sessionFrom": "graduation-2024",
    },
    {"id": "return-contact", "question": "복학 신청 방법과 담당자 전화번호 알려줘"},
    {"id": "national-scholarship", "question": "국가장학금 신청 기준 알려줘"},
    {"id": "merit-scholarship", "question": "성적우수장학금은 따로 신청해야 해?"},
    {"id": "exchange", "question": "교환학생 신청 자격과 절차 알려줘"},
    {"id": "dormitory", "question": "기숙사 신청은 어디서 해?"},
    {"id": "internship", "question": "현장실습은 어떻게 신청해?"},
    {"id": "counseling", "question": "심리상담은 어떻게 신청해?"},
    {"id": "shuttle", "question": "학교 무료셔틀은 어디서 타?"},
    {"id": "graduation-location", "question": "졸업요건은 어디에서 확인해?"},
]


def _missing_fields(question: str, payload: dict) -> list[str]:
    guides = [payload.get("actionGuide") or {}]
    guides.extend((item.get("actionGuide") or {}) for item in payload.get("taskResults") or [])
    departments = [payload.get("department") or {}]
    departments.extend((item.get("department") or {}) for item in payload.get("taskResults") or [])
    facts = payload.get("answerFacts") or []
    missing = []
    has_period_evidence = bool(
        facts
        or any(
            (guide.get("period") or {}).get("start")
            or (guide.get("period") or {}).get("end")
            or any(
                item.get("description") and "신청" in str(item.get("label") or "")
                for item in (guide.get("importantDates") or [])
            )
            for guide in guides
        )
    )
    if any(word in question for word in ("기간", "일정")) and not (
        has_period_evidence
    ):
        missing.append("date_or_period")
    if "서류" in question and not any(guide.get("requiredDocuments") for guide in guides):
        missing.append("required_documents")
    if any(word in question for word in ("방법", "순서대로", "제출")) and not any(
        guide.get("steps") or guide.get("applicationMethod") for guide in guides
    ):
        missing.append("verified_procedure")
    if any(word in question for word in ("담당자", "전화번호", "연락처")) and not any(
        department.get("phone") or department.get("name") for department in departments
    ):
        missing.append("department_contact")
    return missing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--summary", action="store_true",
        help="문항별 상태·선택 근거·누락 필드·AI 오류만 출력합니다.",
    )
    parser.add_argument(
        "--case", action="append", dest="case_ids",
        help="지정한 case id만 실행합니다. 여러 번 지정할 수 있습니다.",
    )
    args = parser.parse_args()
    sessions: dict[str, str] = {}
    rows = []
    with SessionLocal() as db:
        service = ChatService(db)
        selected_cases = [case for case in CASES if not args.case_ids or case["id"] in args.case_ids]
        for case in selected_cases:
            started = time.perf_counter()
            response = service.answer(case["question"], sessions.get(case.get("sessionFrom")))
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            payload = response.model_dump(mode="json", by_alias=True)
            sessions[case["id"]] = response.session_id
            selected = [
                {
                    "noticeId": source.notice_id,
                    "taskKey": source.task_key,
                    "taskUnitId": source.task_unit_id,
                    "title": source.title,
                }
                for source in response.sources
            ]
            selected_trace = sorted(
                (item for item in service.last_search_trace if item.get("decision") in {"selected", "kept"}),
                key=lambda item: item.get("score") or item.get("relevance") or 0,
                reverse=True,
            )
            excluded_trace = [
                item for item in service.last_search_trace if item.get("decision") == "excluded"
            ]
            rows.append({
                "id": case["id"],
                "question": case["question"],
                "queryPlan": payload["query"],
                "selectedTaskUnits": selected,
                "topCandidates": selected_trace[:8],
                "excludedCandidates": excluded_trace[:20],
                "sources": payload.get("sources") or [],
                "finalAnswer": payload.get("answer"),
                "answerFacts": payload.get("answerFacts") or [],
                "taskResults": payload.get("taskResults") or [],
                "wrongOrMissingFields": _missing_fields(case["question"], payload),
                "aiCalled": bool(service.ai.call_stats),
                "aiCalls": list(service.ai.call_stats),
                "elapsedMs": elapsed_ms,
                "status": payload.get("status"),
                "answerMode": payload.get("answerMode"),
            })
    output = {"cases": rows}
    if args.summary:
        output = {"cases": [{
            "id": row["id"],
            "status": row["status"],
            "answerMode": row["answerMode"],
            "selectedTaskUnits": row["selectedTaskUnits"],
            "wrongOrMissingFields": row["wrongOrMissingFields"],
            "finalAnswer": (row["finalAnswer"] or "")[:500],
            "elapsedMs": row["elapsedMs"],
            "failedAiCalls": [call for call in row["aiCalls"] if not call.get("succeeded")],
        } for row in rows]}
    print(json.dumps(
        output, ensure_ascii=False, indent=2,
        default=lambda value: value.item() if hasattr(value, "item") else str(value),
    ))


if __name__ == "__main__":
    main()
