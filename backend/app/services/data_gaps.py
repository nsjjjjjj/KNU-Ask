from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataGap, Notice
from app.schemas import QueryFilters


FIELD_TERMS = {
    "department_phone": ("전화", "전화번호", "연락처", "문의처"),
    "contact_person": ("담당자", "누구에게", "누구한테"),
    "department_email": ("이메일", "메일"),
    "department_office_location": ("사무실", "방문", "어디로 문의"),
    "application_period": ("언제", "기간", "마감", "몇 일", "며칠"),
    "application_method": ("어떻게", "방법", "절차", "하는 법", "어디서"),
    "application_url": ("링크", "바로가기", "신청 페이지", "사이트"),
    "required_documents": ("서류", "준비물", "뭐 필요"),
    "eligibility_notes": ("대상", "자격", "신청 가능", "제외"),
    "fee_information": ("비용", "얼마", "참가비", "환불"),
    "capacity": ("몇 명", "인원", "정원"),
    "selection_method": ("선발", "뽑", "선정 기준"),
    "result_announcement": ("결과", "합격 발표", "선발 발표"),
    "cancellation_policy": ("취소", "철회", "변경"),
    "benefits": ("혜택", "지원금", "활동비", "수료증", "무엇을 받"),
    "credits_or_hours": ("몇 학점", "인정 학점", "학점 인정", "봉사시간", "비교과 시간", "활동시간"),
}


def requested_fields(message: str) -> set[str]:
    return {
        field for field, terms in FIELD_TERMS.items()
        if any(term in message for term in terms)
    }


def missing_fields(message: str, notice: Notice) -> set[str]:
    meta = notice.metadata_record
    if not meta:
        return requested_fields(message)
    values = {
        "department_phone": meta.department_phone,
        "contact_person": meta.contact_person,
        "department_email": meta.department_email,
        "department_office_location": meta.department_office_location,
        "application_period": meta.application_start or meta.application_end,
        "application_method": meta.application_method,
        "application_url": notice.action_guide and notice.action_guide.application_url,
        "required_documents": meta.required_documents,
        "eligibility_notes": meta.eligibility_notes or meta.target_student_types or meta.target_grades,
        "fee_information": meta.fee_information,
        "capacity": meta.capacity,
        "selection_method": meta.selection_method,
        "result_announcement": meta.result_announcement,
        "cancellation_policy": meta.cancellation_policy,
        "benefits": meta.benefits,
        "credits_or_hours": meta.credits_or_hours,
    }
    return {field for field in requested_fields(message) if not values.get(field)}


def record_gap(
    db: Session, *, gap_type: str, field_name: str | None = None,
    notice: Notice | None = None, query: QueryFilters | None = None,
    context: dict | None = None, detected_automatically: bool = True,
) -> DataGap:
    category = query.category if query else None
    intent = query.intent if query else None
    identity = {
        "notice": notice.source_id if notice else None,
        "gapType": gap_type,
        "field": field_name,
        "category": category,
        "intent": intent,
    }
    fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    row = db.scalar(select(DataGap).where(DataGap.fingerprint == fingerprint))
    if row:
        row.occurrence_count += 1
        row.last_seen_at = datetime.now(timezone.utc)
        if row.status == "resolved":
            row.status = "reopened"
            row.resolved_at = None
    else:
        row = DataGap(
            fingerprint=fingerprint,
            notice_id=notice.id if notice else None,
            gap_type=gap_type,
            field_name=field_name,
            category=category,
            query_intent=intent,
            context=context or {},
            detected_automatically=detected_automatically,
        )
        db.add(row)
    db.commit()
    return row


def collect_answer_gaps(
    db: Session, message: str, query: QueryFilters, notice: Notice | None,
    *, no_result: bool = False, insufficient_procedure: bool = False,
    resolved_fields: set[str] | None = None,
) -> None:
    if no_result:
        record_gap(
            db, gap_type="missing_search_evidence", field_name="search_evidence", query=query,
            context={"requestedFields": sorted(requested_fields(message))},
        )
        return
    if not notice:
        return
    for field in sorted(missing_fields(message, notice) - (resolved_fields or set())):
        record_gap(
            db, gap_type="missing_requested_field", field_name=field, notice=notice, query=query,
            context={"sourceType": notice.source_type, "extractionStatus": notice.extraction_status},
        )
    if insufficient_procedure:
        record_gap(db, gap_type="insufficient_procedure", field_name="action_guide", notice=notice, query=query)
    if notice.attachment_urls and notice.extraction_status in {"partial", "failed"}:
        record_gap(
            db, gap_type="attachment_extraction_incomplete", field_name="attachment_text",
            notice=notice, query=query, context={"extractionStatus": notice.extraction_status},
        )
    if notice.metadata_record and notice.metadata_record.needs_review:
        record_gap(db, gap_type="low_confidence_structure", notice=notice, query=query)
