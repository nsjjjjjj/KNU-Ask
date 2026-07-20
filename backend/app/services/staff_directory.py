from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StaffDirectoryContact
from app.utils.text import normalize_text


@dataclass(frozen=True)
class ResolvedStaffContact:
    department_name: str
    contact_person: str | None
    duty: str | None
    phone: str
    source_url: str


def _compact(value: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value or "").lower()


def _parse_raw_contact(raw: dict) -> tuple[str | None, str | None, str | None]:
    metadata = raw.get("source_metadata") or {}
    person = normalize_text(str(metadata.get("contactPerson") or "")) or None
    duty = normalize_text(str(metadata.get("duty") or "")) or None
    phone = normalize_text(str(metadata.get("phone") or "")) or None
    if phone:
        return person, duty, phone

    content = normalize_text(raw.get("content") or "")
    match = re.search(
        r"담당자 및 업무:\s*(?P<person>[^.(]+?)(?:\s*\((?P<duty>[^)]*)\))?\.\s*문의 전화번호:\s*(?P<phone>0\d{1,2}-\d{3,4}-\d{4})",
        content,
    )
    if not match:
        representative = re.search(r"(0\d{1,2}-\d{3,4}-\d{4})", content)
        return None, "대표전화", representative.group(1) if representative else None
    return (
        normalize_text(match.group("person")) or None,
        normalize_text(match.group("duty") or "") or None,
        match.group("phone"),
    )


def sync_staff_directory(db: Session, records: list[dict]) -> int:
    """공식 연락처 수집 결과를 AI 호출 없이 업무별 사전으로 동기화한다."""
    seen: set[str] = set()
    count = 0
    now = datetime.now(timezone.utc)
    for raw in records:
        if raw.get("source_type") != "staff_directory":
            continue
        source_id = str(raw["source_id"])
        department = normalize_text(raw.get("department_name") or "")
        person, duty, phone = _parse_raw_contact(raw)
        if not department or not phone:
            continue
        contact = db.scalar(select(StaffDirectoryContact).where(StaffDirectoryContact.source_id == source_id))
        if not contact:
            contact = StaffDirectoryContact(
                source_id=source_id,
                department_name=department,
                phone=phone,
                source_url=raw["source_url"],
            )
            db.add(contact)
        contact.department_name = department
        contact.contact_person = person
        contact.duty = duty
        contact.phone = phone
        contact.source_url = raw["source_url"]
        contact.is_active = True
        contact.crawled_at = now
        seen.add(source_id)
        count += 1

    if seen:
        for stale in db.scalars(select(StaffDirectoryContact).where(
            StaffDirectoryContact.is_active.is_(True),
            StaffDirectoryContact.source_id.not_in(seen),
        )):
            stale.is_active = False
    db.flush()
    return count


def resolve_staff_contact(
    db: Session,
    department_name: str | None,
    context: str,
    preferred_person: str | None = None,
) -> ResolvedStaffContact | None:
    if not department_name:
        return None
    department_key = _compact(department_name)
    rows = db.scalars(select(StaffDirectoryContact).where(
        StaffDirectoryContact.is_active.is_(True),
    )).all()
    candidates = [row for row in rows if _compact(row.department_name) == department_key]
    if not candidates:
        return None

    context_key = _compact(context)
    terms = {
        _compact(token)
        for token in re.findall(r"[0-9A-Za-z가-힣]+", normalize_text(context))
        if len(_compact(token)) >= 2
    }
    generic = {
        "문의", "방법", "일정", "기간", "신청", "안내", "알려줘", "알려주세요",
        "담당", "담당자", "연락처", "전화번호", "부서", department_key,
    }
    terms -= generic
    duty_aliases = {
        "학적": ("휴학", "복학", "자퇴", "제적", "학적변동"),
        "성적": ("성적",),
        "학점교류": ("학점교류", "타대학수강"),
        "제증명": ("증명서", "제증명"),
        "수강신청": ("수강신청", "예비수강"),
        "강의평가": ("강의평가",),
        "수업": ("수업", "강의시간표"),
        "계절수업": ("계절학기", "계절수업"),
        "교육과정": ("교육과정", "전공", "다전공"),
        "i로드맵": ("i로드맵", "아이로드맵"),
    }
    for duty_term, aliases in duty_aliases.items():
        if any(_compact(alias) in context_key for alias in aliases):
            terms.add(_compact(duty_term))

    def score(row: StaffDirectoryContact) -> tuple[float, int]:
        person_key = _compact(row.contact_person)
        duty_key = _compact(row.duty)
        value = 0.0
        if preferred_person and person_key == _compact(preferred_person):
            value += 20.0
        for term in terms:
            if term and term in duty_key:
                value += 5.0 + min(len(term), 8) / 10
            elif term and term in person_key:
                value += 3.0
        if duty_key and duty_key in context_key:
            value += 6.0
        if "팀장" in duty_key:
            value += 0.5
        if not duty_key:
            value -= 0.5
        return value, -row.id

    best = max(candidates, key=score)
    return ResolvedStaffContact(
        department_name=best.department_name,
        contact_person=best.contact_person,
        duty=best.duty,
        phone=best.phone,
        source_url=best.source_url,
    )
