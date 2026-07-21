"""선별 질문으로 검색·절차·연락처 품질을 반복 검증한다."""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests


QUESTION_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "validation_questions.json"
API_BASE = os.getenv("KNUASK_API_BASE", "http://127.0.0.1:8080/api").rstrip("/")


def field_present(field: str, payload: dict) -> bool:
    guide = payload.get("actionGuide") or {}
    checks = {
        "application_period": lambda: bool((guide.get("period") or {}).get("start") or (guide.get("period") or {}).get("end")),
        "application_method": lambda: bool(guide.get("applicationMethod") or guide.get("steps")),
        "action_guide": lambda: bool(guide.get("steps")),
        "department_phone": lambda: bool((payload.get("department") or {}).get("phone")),
        "required_documents": lambda: bool(guide.get("requiredDocuments")),
        "eligibility_notes": lambda: bool(guide.get("eligibilityNotes")),
        "fee_information": lambda: bool(guide.get("feeInformation")),
    }
    return checks.get(field, lambda: False)()


def main() -> None:
    questions = json.loads(QUESTION_PATH.read_text(encoding="utf-8"))
    report = []
    for case in questions:
        response = requests.post(
            f"{API_BASE}/chat", json={"message": case["question"]}, timeout=90,
        )
        response.raise_for_status()
        payload = response.json()
        top_title = (payload.get("matchedNotices") or [{}])[0].get("title", "")
        missing = [field for field in case["requiredFields"] if not field_present(field, payload)]
        report.append({
            "id": case["id"],
            "passed": (
                case["expectedTitleContains"] in top_title
                and payload.get("answerMode") in case["expectedModes"]
                and not missing
                and not any(token in payload.get("answer", "") for token in ("**", "KNUASKLINEBREAKTOKEN"))
            ),
            "status": payload.get("status"),
            "answerMode": payload.get("answerMode"),
            "topTitle": top_title,
            "missingFields": missing,
            "answer": payload.get("answer"),
            "manualQualityChecks": case["qualityChecks"],
        })
    print(json.dumps({
        "summary": {
            "total": len(report),
            "passed": sum(item["passed"] for item in report),
            "failed": sum(not item["passed"] for item in report),
        },
        "cases": report,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
