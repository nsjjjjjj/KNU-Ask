from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    EvidenceRecoveryRecord,
    Notice,
    StaffDirectoryContact,
    TaskEvidence,
    TaskFact,
    TaskProcedure,
    TaskProcedureStep,
)
from app.schemas import QueryPlan
from app.services.crawler.attachments import AttachmentExtractionError, AttachmentExtractor
from app.services.on_demand.cache import canonical_cache_key
from app.services.on_demand.sources import is_allowed_school_url
from app.services.search.task_rules import visible_student_step
from app.utils.text import normalize_text


RECOVERABLE_FIELDS = {
    "procedure",
    "required_documents",
    "department_contact",
    "application_url",
}
ACTION_WORDS = ("접속", "로그인", "선택", "작성", "제출", "방문", "업로드", "납부", "확인", "신청", "클릭")
DOCUMENT_LABELS = ("제출 서류", "제출서류", "구비서류", "준비 서류", "준비서류", "필요 서류")
CONTACT_WORDS = ("담당자", "연락처", "전화번호", "담당 부서", "문의처", "어디에 문의")
LINK_WORDS = ("신청 링크", "신청 페이지", "접수 링크", "바로가기", "신청 장소", "신청 어디")


@dataclass
class RecoveryOutcome:
    triggered: bool = False
    cache_hit: bool = False
    status: str = "not_needed"
    reason: str | None = None
    requested_missing_fields: list[str] = field(default_factory=list)
    recovered_fields: list[str] = field(default_factory=list)
    remaining_missing_fields: list[str] = field(default_factory=list)
    checked_attachment_count: int = 0
    checked_page_count: int = 0
    duration_ms: float = 0.0
    persisted_fact_count: int = 0
    persisted_step_count: int = 0
    timings_ms: dict[str, float] = field(default_factory=dict)


_flight_guard = threading.Lock()
_flights: dict[str, threading.Lock] = {}


def _flight(key: str) -> threading.Lock:
    with _flight_guard:
        return _flights.setdefault(key, threading.Lock())


def _normalized_field(value: str) -> str | None:
    compact = re.sub(r"[^a-z0-9가-힣]", "", value.casefold())
    if any(term in compact for term in ("procedure", "actionguide", "applicationmethod", "신청방법", "절차", "수행방법")):
        return "procedure"
    if any(term in compact for term in ("requireddocument", "documents", "제출서류", "준비서류", "구비서류")):
        return "required_documents"
    if any(term in compact for term in ("departmentcontact", "contact", "phone", "담당자", "연락처", "전화번호")):
        return "department_contact"
    if any(term in compact for term in ("applicationurl", "applicationlocation", "신청링크", "신청페이지", "신청장소")):
        return "application_url"
    return None


def _requested_fields(message: str, plan: QueryPlan) -> set[str]:
    requested = {
        field_name
        for raw in [*plan.requested_fields, *plan.required_facts]
        if (field_name := _normalized_field(raw))
    }
    text = normalize_text(message)
    compact = re.sub(r"\s+", "", text)
    if any(term in text for term in ("방법", "절차", "순서", "단계", "어떻게")) or any(
        term in compact for term in ("하는법", "신청법", "접수법", "제출법")
    ):
        requested.add("procedure")
    if any(term in text for term in DOCUMENT_LABELS):
        requested.add("required_documents")
    if any(term in text for term in CONTACT_WORDS):
        requested.add("department_contact")
    if any(term in text for term in LINK_WORDS):
        requested.add("application_url")
    return requested & RECOVERABLE_FIELDS


def _has_documents(unit, metadata) -> bool:
    if getattr(metadata, "required_documents", None):
        return True
    return any(
        _normalized_field(getattr(fact, "fact_type", "")) == "required_documents"
        or any(label in normalize_text(f"{fact.label} {fact.value}") for label in DOCUMENT_LABELS)
        for fact in getattr(unit, "facts", [])
    )


def _has_procedure(unit, metadata) -> bool:
    procedure = getattr(unit, "procedure", None)
    if procedure and any(visible_student_step(step) for step in procedure.steps):
        return True
    return bool(normalize_text(getattr(metadata, "application_method", "")))


def _has_application_url(unit, notice) -> bool:
    procedure = getattr(unit, "procedure", None)
    if procedure and is_allowed_school_url(procedure.application_url or ""):
        return True
    return any(is_allowed_school_url(str(item.get("url") or "")) for item in (notice.content_links or []))


def _has_contact(metadata) -> bool:
    return bool(normalize_text(getattr(metadata, "department_phone", "")))


def _missing_fields(requested: set[str], match: dict) -> list[str]:
    notice, metadata, unit = match["notice"], match["metadata"], match.get("task_unit")
    if unit is None:
        return []
    checks = {
        "procedure": _has_procedure(unit, metadata),
        "required_documents": _has_documents(unit, metadata),
        "department_contact": _has_contact(metadata),
        "application_url": _has_application_url(unit, notice),
    }
    return sorted(field_name for field_name in requested if not checks[field_name])


def _source_hashes(notices: list[Notice]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for notice in notices:
        manifest = [
            {
                "url": item.get("url"),
                "sha256": item.get("sha256"),
                "status": item.get("extractionStatus") or item.get("extraction_status"),
                "needsReview": item.get("needsReview") or item.get("needs_review"),
            }
            for item in (notice.attachment_manifest or [])
        ]
        hashes[str(notice.id)] = hashlib.sha256(json.dumps(
            {"content": notice.content_hash, "attachments": manifest},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
    return hashes


def _locator_for(source: str, start: int) -> tuple[str, str]:
    prefix = source[:start]
    attachment_matches = list(re.finditer(r"\[첨부파일:\s*([^\]]+)\]", prefix))
    page_matches = list(re.finditer(r"\[PDF page\s+(\d+)\]", prefix, re.I))
    if attachment_matches:
        name = normalize_text(attachment_matches[-1].group(1))
        page = page_matches[-1].group(1) if page_matches and page_matches[-1].start() > attachment_matches[-1].start() else None
        return "pdf" if page else "attachment", f"{name} PDF {page}페이지" if page else name
    return "html", "공지 본문"


def _procedure_steps(source: str) -> list[dict]:
    normalized = normalize_text(source)
    explicit = list(re.finditer(r"(?:Step|단계)\s*(\d+)\s*[.:：)\-]?\s*", normalized, re.I))
    numbered = []
    if len(explicit) < 2 and re.search(r"신청\s*(?:방법|절차)|진행\s*순서|이용\s*방법", normalized):
        numbered = list(re.finditer(r"(?<!\d)(\d{1,2})\s*[.)]\s*", normalized))
    markers = explicit if len(explicit) >= 2 else numbered
    if len(markers) < 2:
        return []
    result = []
    for index, marker in enumerate(markers[:8]):
        end = markers[index + 1].start() if index + 1 < len(markers) else min(len(normalized), marker.end() + 350)
        description = normalize_text(normalized[marker.end():end]).strip(" -:;·")
        description = re.split(r"(?:문의처|제출\s*서류|구비서류|유의사항)\s*[:：]", description)[0].strip()
        if not description or not any(word in description for word in ACTION_WORDS):
            continue
        source_type, locator = _locator_for(normalized, marker.start())
        urls = re.findall(r"https://[^\s<>\]\)]+", description)
        result.append({
            "title": description[:80],
            "description": description[:500],
            "action_url": next((url.rstrip(".,") for url in urls if is_allowed_school_url(url.rstrip(".,"))), None),
            "source_type": source_type,
            "source_locator": locator,
            "confidence": 0.94 if explicit else 0.86,
        })
    return result if len(result) >= 2 else []


def _document_facts(source: str) -> list[dict]:
    normalized = normalize_text(source)
    result: list[dict] = []
    pattern = re.compile(r"(?:제출\s*서류|구비서류|준비\s*서류|필요\s*서류)\s*[:：]\s*(.{1,350})")
    for match in pattern.finditer(normalized):
        raw = re.split(r"(?:문의처|신청\s*(?:방법|기간)|유의사항|지원\s*자격)\s*[:：]", match.group(1))[0]
        raw = raw.strip(" .;·")
        if not raw:
            continue
        values = [normalize_text(item).strip(" -•·,.;") for item in re.split(r"\s*(?:[,;]|[①②③④⑤⑥⑦⑧⑨]|\s[-•·]\s)\s*", raw)]
        values = [value for value in values if 1 < len(value) <= 160]
        source_type, locator = _locator_for(normalized, match.start())
        for value in values[:10]:
            result.append({"value": value, "excerpt": normalize_text(match.group(0)), "source_type": source_type, "source_locator": locator})
    return result


def _application_url(source: str, notice: Notice) -> str | None:
    for url in re.findall(r"https://[^\s<>\]\)]+", source):
        clean = url.rstrip(".,;')\"")
        context = source[max(0, source.find(url) - 80):source.find(url) + len(url) + 40]
        if any(term in context for term in ("신청", "접수", "지원", "로그인")) and is_allowed_school_url(clean):
            return clean
    for item in notice.content_links or []:
        url = str(item.get("url") or "")
        label = normalize_text(str(item.get("label") or item.get("text") or ""))
        if is_allowed_school_url(url) and any(term in label for term in ("신청", "접수", "지원")):
            return url
    return None


def _strict_staff_contact(db: Session, department_name: str | None, context: str):
    if not department_name:
        return None
    compact = lambda value: re.sub(r"[^0-9A-Za-z가-힣]", "", value or "").casefold()
    department_key = compact(department_name)
    context_key = compact(context)
    aliases = {
        "학적": ("휴학", "복학", "자퇴", "제적", "학적변동"),
        "수강신청": ("수강신청", "예비수강", "수강"),
        "성적": ("성적",), "제증명": ("증명서", "제증명"),
        "학점교류": ("학점교류", "타대학수강"), "교육과정": ("교육과정", "전공", "다전공"),
    }
    expected = {compact(key) for key, values in aliases.items() if any(compact(value) in context_key for value in values)}
    if not expected:
        return None
    candidates = []
    for row in db.scalars(select(StaffDirectoryContact).where(StaffDirectoryContact.is_active.is_(True))).all():
        duty = compact(row.duty)
        if compact(row.department_name) != department_key or "팀장" in duty:
            continue
        score = sum(1 for term in expected if term and term in duty)
        if score:
            candidates.append((score, row))
    return max(candidates, key=lambda item: (item[0], -item[1].id))[1] if candidates else None


class MissingEvidenceRecovery:
    def __init__(self, db: Session, extractor: AttachmentExtractor | None = None) -> None:
        self.db = db
        self.extractor = extractor or AttachmentExtractor(requests.Session())
        self.extractor.request_timeout_seconds = min(30.0, settings.missing_evidence_recovery_timeout_seconds)
        self.extractor.max_pdf_pages = settings.missing_evidence_max_pdf_pages
        self.extractor.max_ocr_pages = settings.missing_evidence_max_pdf_pages

    def recover(self, message: str, plan: QueryPlan, matches: list[dict], *, deadline: float | None = None) -> RecoveryOutcome:
        started = time.perf_counter()
        detection_started = time.perf_counter()
        requested = _requested_fields(message, plan)
        if not settings.missing_evidence_recovery_enabled or not requested or not matches:
            return RecoveryOutcome()
        top = matches[0]
        missing = _missing_fields(requested, top)
        if not missing:
            return RecoveryOutcome()

        notices = list({item["notice"].id: item["notice"] for item in matches[:3]}.values())
        source_hashes = _source_hashes(notices)
        canonical = canonical_cache_key(plan)
        encoded = json.dumps({"canonical": canonical, "fields": missing, "hashes": source_hashes}, sort_keys=True)
        recovery_key = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        outcome = RecoveryOutcome(
            triggered=True,
            reason="requested_structured_field_missing",
            requested_missing_fields=missing,
            timings_ms={"missingDetection": round((time.perf_counter() - detection_started) * 1000, 1)},
        )

        with _flight(recovery_key):
            cached = self.db.scalar(select(EvidenceRecoveryRecord).where(
                EvidenceRecoveryRecord.recovery_key == recovery_key,
            ))
            if cached and cached.status == "found":
                return self._from_record(cached, started)
            if cached and cached.status == "verified_absent" and self._not_expired(cached.expires_at):
                return self._from_record(cached, started)

            extraction_started = time.perf_counter()
            extraction_failed = False
            low_confidence = False
            checked_attachments: list[str] = []
            checked_urls: list[str] = []
            checked_pages = 0
            ocr_used = False
            source_parts = []
            for notice in notices:
                source_parts.append(f"[공지 본문]\n{notice.content}\n{notice.source_snapshot or ''}\n{notice.attachment_text or ''}")
                for index, item in enumerate(notice.attachment_manifest or []):
                    status = str(item.get("extractionStatus") or item.get("extraction_status") or "unprocessed")
                    confidence = item.get("ocrConfidence") if item.get("ocrConfidence") is not None else item.get("ocr_confidence")
                    if item.get("needsReview") or item.get("needs_review") or (confidence is not None and float(confidence) < 0.65):
                        low_confidence = True
                        continue
                    name = normalize_text(str(item.get("name") or f"첨부파일 {index + 1}"))
                    checked_attachments.append(name)
                    if status in {"success", "complete"}:
                        checked_pages += min(int(item.get("pageCount") or item.get("page_count") or 0), settings.missing_evidence_max_pdf_pages)
                        continue
                    url = str(item.get("url") or "")
                    if not url or not is_allowed_school_url(url):
                        continue
                    if deadline and time.perf_counter() >= deadline:
                        extraction_failed = True
                        break
                    checked_urls.append(url)
                    try:
                        text = self.extractor.extract(url, name)
                    except (AttachmentExtractionError, requests.RequestException, OSError):
                        extraction_failed = True
                        continue
                    manifest = self.extractor.manifest([name], [url])[0]
                    ocr_used = ocr_used or "ocr" in str(manifest.get("extractionMethod") or "").casefold()
                    updated_manifest = list(notice.attachment_manifest or [])
                    updated_manifest[index] = manifest
                    notice.attachment_manifest = updated_manifest
                    pages = min(int(manifest.get("pageCount") or 0), settings.missing_evidence_max_pdf_pages)
                    checked_pages += pages
                    if manifest.get("needsReview") or (
                        manifest.get("ocrConfidence") is not None and float(manifest["ocrConfidence"]) < 0.65
                    ):
                        low_confidence = True
                        continue
                    if not text:
                        extraction_failed = True
                        continue
                    block = f"[첨부파일: {name}]\n{text}"
                    notice.attachment_text = normalize_text(f"{notice.attachment_text}\n{block}")
                    source_parts.append(block)
            outcome.checked_attachment_count = len(checked_attachments)
            outcome.checked_page_count = checked_pages
            extraction_ms = round((time.perf_counter() - extraction_started) * 1000, 1)
            outcome.timings_ms["attachmentExtraction"] = extraction_ms
            outcome.timings_ms["pdfDownloadExtraction"] = extraction_ms if checked_attachments else 0.0
            outcome.timings_ms["ocr"] = extraction_ms if ocr_used else 0.0
            outcome.timings_ms["codexVerification"] = 0.0

            source = normalize_text("\n".join(source_parts))
            unit, metadata, notice = top.get("task_unit"), top["metadata"], top["notice"]
            db_started = time.perf_counter()
            recovered: list[str] = []
            if "procedure" in missing:
                steps = _procedure_steps(source)
                if steps and all(step["description"] in source for step in steps):
                    procedure = unit.procedure or TaskProcedure(task_unit_id=unit.id, prerequisites=[], warnings=[])
                    if unit.procedure is None:
                        self.db.add(procedure)
                        unit.procedure = procedure
                    procedure.summary = procedure.summary or f"{unit.title} 진행 절차"
                    procedure.confidence = min(step["confidence"] for step in steps)
                    procedure.needs_review = False
                    procedure.steps.clear()
                    self.db.flush()
                    for order, step in enumerate(steps, start=1):
                        procedure.steps.append(TaskProcedureStep(step_order=order, action_type="link" if step["action_url"] else "other", **step))
                    recovered.append("procedure")
                    outcome.persisted_step_count = len(steps)
            if "required_documents" in missing:
                documents = _document_facts(source)
                for document in documents:
                    if document["excerpt"] not in source or document["value"] not in document["excerpt"]:
                        continue
                    if any(fact.fact_type == "required_documents" and fact.value == document["value"] for fact in unit.facts):
                        continue
                    unit.facts.append(TaskFact(
                        fact_type="required_documents", label="제출 서류", value=document["value"],
                        source_locator=document["source_locator"], source_type=document["source_type"],
                        student_actionable=True, confidence=0.94,
                    ))
                    unit.evidence.append(TaskEvidence(
                        field_name="required_documents", excerpt=document["excerpt"],
                        source_locator=document["source_locator"], source_type=document["source_type"], confidence=0.94,
                    ))
                    outcome.persisted_fact_count += 1
                if outcome.persisted_fact_count:
                    recovered.append("required_documents")
            if "application_url" in missing:
                url = _application_url(source, notice)
                if url:
                    procedure = unit.procedure or TaskProcedure(task_unit_id=unit.id, prerequisites=[], warnings=[], confidence=0.9, needs_review=False)
                    if unit.procedure is None:
                        self.db.add(procedure)
                        unit.procedure = procedure
                    procedure.application_url = url
                    url_start = source.find(url)
                    source_type, locator = _locator_for(source, max(0, url_start))
                    evidence_source = next((
                        normalize_text(candidate)
                        for candidate in (notice.content, notice.attachment_text, notice.source_snapshot)
                        if url in normalize_text(candidate)
                    ), source)
                    evidence_start = evidence_source.find(url)
                    excerpt = normalize_text(evidence_source[
                        max(0, evidence_start - 80):evidence_start + len(url) + 80
                    ])
                    if url in excerpt and not procedure.steps:
                        procedure.steps.append(TaskProcedureStep(
                            step_order=1,
                            title="신청 페이지 열기",
                            description=excerpt,
                            action_type="link",
                            action_url=url,
                            source_type=source_type,
                            source_locator=locator,
                            confidence=0.9,
                        ))
                        outcome.persisted_step_count += 1
                    if url in excerpt and not any(
                        evidence.field_name == "application_url" and evidence.excerpt == excerpt
                        for evidence in unit.evidence
                    ):
                        unit.evidence.append(TaskEvidence(
                            field_name="application_url",
                            excerpt=excerpt,
                            source_type=source_type,
                            source_locator=locator,
                            confidence=0.9,
                        ))
                    recovered.append("application_url")
            if "department_contact" in missing:
                contact = _strict_staff_contact(self.db, metadata.department_name, f"{message} {notice.title} {unit.title}")
                if contact:
                    metadata.department_phone = contact.phone
                    metadata.contact_person = metadata.contact_person or contact.contact_person
                    metadata.contact_role = metadata.contact_role or contact.duty
                    recovered.append("department_contact")

            self.db.flush()
            outcome.persisted_fact_count = max(outcome.persisted_fact_count, 0)
            outcome.recovered_fields = sorted(set(recovered))
            outcome.remaining_missing_fields = sorted(set(missing) - set(recovered))
            outcome.timings_ms["dbPersist"] = round((time.perf_counter() - db_started) * 1000, 1)
            if recovered:
                outcome.status = "found"
                outcome.reason = "verified_evidence_recovered"
            elif extraction_failed:
                outcome.status = "failed"
                outcome.reason = "attachment_or_network_failure"
            elif low_confidence:
                outcome.status = "low_confidence"
                outcome.reason = "ocr_confidence_below_threshold"
            else:
                outcome.status = "verified_absent"
                outcome.reason = "official_sources_checked"
            outcome.duration_ms = round((time.perf_counter() - started) * 1000, 1)

            final_source_hashes = _source_hashes(notices)
            final_encoded = json.dumps({"canonical": canonical, "fields": missing, "hashes": final_source_hashes}, sort_keys=True)
            final_recovery_key = hashlib.sha256(final_encoded.encode("utf-8")).hexdigest()
            record = cached if cached and cached.recovery_key == final_recovery_key else self.db.scalar(
                select(EvidenceRecoveryRecord).where(EvidenceRecoveryRecord.recovery_key == final_recovery_key)
            )
            record = record or EvidenceRecoveryRecord(recovery_key=final_recovery_key, canonical_key=canonical)
            if record.id is None:
                self.db.add(record)
            record.task_key = plan.task_key
            record.requested_fields = missing
            record.recovered_fields = outcome.recovered_fields
            record.remaining_missing_fields = outcome.remaining_missing_fields
            record.notice_ids = [notice.id for notice in notices]
            record.source_hashes = final_source_hashes
            record.status = outcome.status
            record.reason = outcome.reason
            record.checked_urls = checked_urls
            record.checked_attachments = checked_attachments
            record.checked_page_count = checked_pages
            record.timings_ms = outcome.timings_ms
            record.persisted_fact_count = outcome.persisted_fact_count
            record.persisted_step_count = outcome.persisted_step_count
            ttl = settings.missing_evidence_negative_ttl_seconds if outcome.status == "verified_absent" else 7 * 86400
            record.expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(1, ttl))
            self.db.flush()
            return outcome

    @staticmethod
    def _not_expired(value: datetime) -> bool:
        comparable = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return comparable > datetime.now(timezone.utc)

    @staticmethod
    def _from_record(record: EvidenceRecoveryRecord, started: float) -> RecoveryOutcome:
        return RecoveryOutcome(
            triggered=True, cache_hit=True, status=record.status, reason=record.reason,
            requested_missing_fields=list(record.requested_fields or []),
            recovered_fields=list(record.recovered_fields or []),
            remaining_missing_fields=list(record.remaining_missing_fields or []),
            checked_attachment_count=len(record.checked_attachments or []),
            checked_page_count=record.checked_page_count,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            persisted_fact_count=record.persisted_fact_count,
            persisted_step_count=record.persisted_step_count,
            timings_ms={"recoveryCacheLookup": round((time.perf_counter() - started) * 1000, 1)},
        )
