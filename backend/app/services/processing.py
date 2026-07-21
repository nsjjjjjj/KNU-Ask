import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    ActionGuide, ActionStep, Department, KnowledgeTask, Notice, NoticeChunk,
    NoticeEmbedding, NoticeMetadata, ProcessingJob, TaskEvidence, TaskFact,
    TaskProcedure, TaskProcedureStep, TaskUnit, TaskUnitEmbedding,
)
from app.repositories import NoticeRepository
from app.schemas import StructuredActionGuide, StructuredActionStep, StructuredNotice
from app.services.ai import AIService
from app.services.crawler.attachments import AttachmentExtractor
from app.services.search.task_rules import TASK_BY_KEY, detect_task, visible_student_step
from app.utils.text import content_hash, normalize_text, strip_nul


STATIC_SOURCE_TYPES = {
    "academic_guide", "official_faq", "staff_directory", "scholarship_guide",
    "student_service", "university_catalog", "dormitory_guide", "international_guide",
    "university_regulation",
    "library_guide",
}


class NoticeProcessor:
    def __init__(self, db: Session, ai: AIService | None = None) -> None:
        self.db = db
        self.ai = ai or AIService()
        self.repo = NoticeRepository(db)
        self.schema_version = self._pipeline_schema_version()
        model_digest = hashlib.sha1(self.ai.embedding_model_name.encode()).hexdigest()[:8]
        self.embedding_version = f"{settings.embedding_version}-{model_digest}"[:20]

    @staticmethod
    def _pipeline_schema_version() -> str:
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        digest = hashlib.sha1(b"extractor-v3|section-chunker-v1|task-unit-v1|canonical-index-v2")
        for prompt in sorted(prompt_dir.glob("*.txt")):
            digest.update(prompt.read_bytes())
        return f"{settings.schema_version}-{digest.hexdigest()[:8]}"[:20]

    def upsert(
        self, raw: dict, force: bool = False, *, allow_external_enqueue: bool | None = None,
    ) -> tuple[Notice, str]:
        raw = strip_nul(raw)
        source_id = str(raw["source_id"])
        published = raw["published_at"]
        if isinstance(published, str):
            published = datetime.fromisoformat(published)
        resource_manifest = json.dumps({
            "content_links": sorted(raw.get("content_links", [])),
            "attachment_names": raw.get("attachment_names", []),
            "attachment_urls": raw.get("attachment_urls", []),
            "attachment_manifest": raw.get("attachment_manifest", []),
            "image_hashes": sorted(raw.get("image_hashes", [])),
            "source_metadata": raw.get("source_metadata", {}),
            "source_type": raw.get("source_type", "official_notice"),
            "source_priority": raw.get("source_priority", 50),
            "extraction_status": raw.get("extraction_status", "not_required"),
        }, ensure_ascii=False, sort_keys=True)
        published_key = "" if raw.get("source_type") in STATIC_SOURCE_TYPES else published.isoformat()
        digest = content_hash(
            raw["title"], raw["content"], raw.get("attachment_text", ""),
            published_key, resource_manifest,
        )
        notice = self.repo.by_source_id(source_id)
        was_public = bool(notice and notice.ai_processed)
        if (
            notice
            and notice.content_hash == digest
            and notice.schema_version == self.schema_version
            and notice.embedding_version == self.embedding_version
            and not force
        ):
            notice.is_archived = False
            notice.crawled_at = datetime.now(timezone.utc)
            return notice, "skipped"
        state = "updated" if notice else "new"
        if not notice:
            notice = Notice(source_id=source_id, title="", content="", published_at=published, source_url="", content_hash=digest)
            self.db.add(notice)
        notice.title = normalize_text(raw["title"])
        notice.content = normalize_text(raw["content"])
        notice.published_at = published
        notice.source_url = raw["source_url"]
        notice.department_name = raw.get("department_name")
        notice.content_hash = digest
        notice.attachment_names = raw.get("attachment_names", [])
        notice.attachment_urls = raw.get("attachment_urls", [])
        notice.attachment_text = normalize_text(raw.get("attachment_text", ""))
        notice.attachment_manifest = raw.get("attachment_manifest", [])
        notice.content_links = list(dict.fromkeys(raw.get("content_links", [])))
        notice.source_snapshot = raw.get("source_snapshot", "")
        notice.source_metadata = raw.get("source_metadata", {})
        notice.source_type = raw.get("source_type", "official_notice")
        notice.source_priority = int(raw.get("source_priority", 50))
        notice.extraction_status = raw.get("extraction_status", "not_required")
        notice.crawled_at = datetime.now(timezone.utc)
        notice.is_archived = False
        notice.ai_processed = False
        self.db.flush()
        external_ai = settings.notice_structuring_provider.lower() in {"codex", "openai"}
        if allow_external_enqueue is not None:
            external_ai = external_ai and allow_external_enqueue
        if external_ai:
            # 기존 공개 세대는 외부 구조화·근거 검증·임베딩이 모두 성공할
            # 때까지 계속 사용한다. 새 공지만 비공개 상태로 대기한다.
            notice.ai_processed = was_public
            self.enqueue_codex_enrichment(notice)
        else:
            self.process(notice, notice.content_links)
        return notice, state

    def enqueue_codex_enrichment(self, notice: Notice, force: bool = False) -> ProcessingJob:
        existing = self.db.scalar(select(ProcessingJob).where(
            ProcessingJob.notice_id == notice.id,
            ProcessingJob.job_type == "codex_enrichment",
            ProcessingJob.status.in_(("pending", "running")),
        ).order_by(ProcessingJob.id.desc()))
        if existing and not force:
            return existing
        if existing and force:
            existing.status = "cancelled"
            existing.finished_at = datetime.now(timezone.utc)
        job = ProcessingJob(notice_id=notice.id, job_type="codex_enrichment", status="pending")
        self.db.add(job)
        self.db.flush()
        return job

    @staticmethod
    def refresh_attachments(notice: Notice) -> str:
        """저장된 첨부 URL만 다시 추출해 전체 공지 재수집 없이 원문을 보강한다."""
        if not notice.attachment_urls:
            notice.attachment_text = ""
            notice.attachment_manifest = []
            notice.extraction_status = "not_required"
            return notice.extraction_status

        session = requests.Session()
        session.headers.update({"User-Agent": "KNU-Ask-Crawler/1.0 (internal MVP demo)"})
        extractor = AttachmentExtractor(session)
        attachment_text, extraction_status = extractor.extract_many(
            list(notice.attachment_names or []), list(notice.attachment_urls or []),
        )
        notice.attachment_text = attachment_text
        notice.attachment_manifest = extractor.manifest(
            list(notice.attachment_names or []), list(notice.attachment_urls or []),
        )
        notice.extraction_status = extraction_status
        notice.ai_processed = False
        return extraction_status

    def process(self, notice: Notice, content_links: list[str] | None = None) -> None:
        job = ProcessingJob(notice_id=notice.id, job_type="structure_and_embed", status="running", started_at=datetime.now(timezone.utc))
        self.db.add(job)
        self.db.flush()
        try:
            department = self.db.scalar(select(Department).where(Department.name == notice.department_name)) if notice.department_name else None
            known_links = content_links if content_links is not None else self._existing_action_links(notice)
            full_content = normalize_text(f"{notice.content}\n{notice.attachment_text}")
            structured = self.ai.structure_notice(
                notice.title, full_content, notice.published_at,
                {"name": department.name, "phone": department.phone, "office_hours": department.office_hours} if department else {"name": notice.department_name},
                known_links,
            )
            self.persist_structured(notice, structured, known_links)
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.retry_count += 1
            job.error_message = str(exc)[:2000]
            raise
        finally:
            job.finished_at = datetime.now(timezone.utc)

    def persist_structured(self, notice: Notice, structured, known_links: list[str] | None = None) -> None:
        """검증된 구조화 결과를 저장하고 BGE 인덱스만 다시 계산한다."""
        known_links = known_links if known_links is not None else self._existing_action_links(notice)
        full_content = normalize_text(f"{notice.content}\n{notice.attachment_text}")
        canonical_search_text = self._canonical_search_text(notice, structured)
        embedding_vector = self.ai.embedding(canonical_search_text)
        meta = notice.metadata_record or NoticeMetadata(notice_id=notice.id, category="기타", search_text="")
        notice.metadata_record = meta
        self.db.add(meta)
        meta.category = structured.category
        meta.sub_category = structured.sub_category
        meta.academic_year = structured.academic_year
        meta.semester = structured.semester
        meta.application_start = structured.application_period.start
        meta.application_end = structured.application_period.end
        meta.event_start = structured.event_period.start
        meta.event_end = structured.event_period.end
        meta.target_student_types = structured.target.student_types
        meta.target_grades = structured.target.grades
        meta.target_departments = structured.target.departments
        meta.target_campus = structured.target.campus
        meta.action_type = structured.action_type
        meta.application_method = structured.application_method
        meta.application_location = structured.application_location
        meta.required_documents = structured.required_documents
        meta.eligibility_notes = structured.eligibility_notes
        meta.fee_information = structured.fee_information
        meta.capacity = structured.capacity
        meta.selection_method = structured.selection_method
        meta.result_announcement = structured.result_announcement
        meta.cancellation_policy = structured.cancellation_policy
        meta.benefits = structured.benefits
        meta.credits_or_hours = structured.credits_or_hours
        meta.important_dates = [item.model_dump(mode="json", by_alias=True) for item in structured.important_dates]
        meta.additional_facts = [item.model_dump(mode="json", by_alias=True) for item in structured.additional_facts]
        meta.evidence_map = structured.evidence_map
        meta.department_name = structured.department.name or notice.department_name
        meta.contact_person = structured.department.contact_person
        meta.contact_role = structured.department.contact_role
        meta.department_phone = structured.department.phone
        meta.department_email = structured.department.email
        meta.department_office_location = structured.department.office_location
        meta.department_office_hours = structured.department.office_hours
        meta.keywords = structured.keywords
        meta.synonyms = structured.synonyms
        meta.search_text = canonical_search_text
        meta.confidence = structured.confidence
        actionable_without_period = (
            structured.action_type in {"신청", "제출", "납부", "예약"}
            and notice.source_type in {"official_notice", "event"}
            and not structured.application_period.start and not structured.application_period.end
        )
        extraction_incomplete = bool(notice.attachment_urls) and notice.extraction_status not in {"success", "not_required"}
        attachment_needs_review = any(
            bool(item.get("needsReview")) or item.get("failureReason") in {
                "unsupported", "timeout", "size_limit", "ocr_failed", "download_failed",
            }
            for item in (notice.attachment_manifest or [])
        )
        meta.needs_review = bool(
            structured.needs_review or structured.confidence < 0.55
            or actionable_without_period or extraction_incomplete or attachment_needs_review
            or (len(full_content) < 80 and notice.source_type == "official_notice")
        )
        meta.schema_version = self.schema_version
        meta.processed_at = datetime.now(timezone.utc)
        self.db.flush()
        embedding = notice.embedding_record or NoticeEmbedding(
            notice_id=notice.id, embedding=[], embedding_model=self.ai.embedding_model_name,
            embedding_version=self.embedding_version,
        )
        notice.embedding_record = embedding
        self.db.add(embedding)
        embedding.embedding = embedding_vector
        embedding.embedding_model = self.ai.embedding_model_name
        embedding.embedding_version = self.embedding_version
        self._replace_chunks(notice, canonical_search_text, full_content)
        self._replace_task_units(notice, structured, full_content, known_links)
        if notice.source_type in STATIC_SOURCE_TYPES:
            meta.application_start = None
            meta.application_end = None
            notice.notice_status = "always"
        else:
            notice.notice_status = structured.notice_status
        notice.ai_processed = True
        notice.processed_at = datetime.now(timezone.utc)
        notice.schema_version = self.schema_version
        notice.embedding_version = self.embedding_version
        self.replace_action_guide(notice, structured.action_guide, known_links)

    @staticmethod
    def _attachment_part(attachment_text: str, name: str) -> str:
        if not attachment_text or not name:
            return ""
        pattern = re.compile(
            rf"\[첨부파일:\s*{re.escape(name)}\]\s*(.*?)(?=\[첨부파일:|$)",
            re.DOTALL,
        )
        match = pattern.search(attachment_text)
        return normalize_text(match.group(1)) if match else ""

    def _source_section_units(self, notice: Notice, structured) -> list[dict]:
        sections = (notice.source_metadata or {}).get("sections") or []
        units: list[dict] = []
        page_task = detect_task(notice.title)
        broad_page_tasks = {
            "graduation.requirements", "leave.general", "reserve.training", "scholarship.general",
        }
        for index, section in enumerate(sections, start=1):
            section_title = normalize_text(section.get("title") or "")
            section_content = normalize_text(section.get("content") or "")
            attachment_parts = []
            for attachment in section.get("attachments") or []:
                name = normalize_text(attachment.get("name") or "")
                extracted = self._attachment_part(notice.attachment_text, name)
                if extracted:
                    attachment_parts.append(f"첨부파일 {name}: {extracted}")
            content = normalize_text("\n".join([section_content, *attachment_parts]))
            # 특정 업무 전용 페이지(조기졸업·창업휴학 등)는 모든 구간을
            # 그 업무로 묶는다. 졸업·휴학·예비군처럼 여러 업무를 담는
            # 종합 페이지에서만 section 제목이 페이지 분류를 덮을 수 있다.
            section_task = detect_task(section_title)
            if page_task and page_task.key not in broad_page_tasks:
                task = page_task
            elif page_task:
                task = section_task or page_task
            else:
                # 계절수업 유의사항 속 "조기졸업대상자"처럼 맥락 일부를
                # 독립 업무로 오해할 위험이 커 Codex 분류 전에는 만들지 않는다.
                task = None
            if not task or not content:
                continue
            procedure = self._procedure_from_step_text(task.key, section_title, content)
            units.append({
                "task_key": task.key,
                "task_name": task.name,
                "parent_task": task.parent,
                "section_title": section_title or None,
                "summary": section_content[:500] or None,
                "content": content,
                "aliases": list(task.aliases),
                "excluded_intents": list(task.excluded_title_terms),
                "admission_year_start": None,
                "admission_year_end": None,
                # 상시 학사안내 본문의 첫 연도(예: 입학년도별 표의 2008)를
                # 공지 적용 학년도로 오인하지 않는다. 연도·학기 강제 조건은
                # 기한성 공지 또는 외부 구조화 TaskUnit에서만 보존한다.
                "academic_year": None if notice.source_type == "academic_guide" else structured.academic_year,
                "semester": None if notice.source_type == "academic_guide" else structured.semester,
                "target_student_types": [],
                "target_departments": [],
                "application_start": None,
                "application_end": None,
                "event_start": None,
                "event_end": None,
                "facts": [],
                "evidence": [{
                    "field_name": "source_section",
                    "excerpt": section_content[:3000],
                    "source_type": "html",
                    "source_locator": section.get("sourceLocator") or f"HTML section:{section_title}",
                    "confidence": 1.0,
                }] if section_content else [],
                "procedure": procedure,
                "confidence": max(structured.confidence, 0.8),
                "needs_review": structured.needs_review,
            })
        return units

    @staticmethod
    def _procedure_from_step_text(
        task_key: str, section_title: str, content: str,
    ) -> StructuredActionGuide | None:
        if "step 1" not in content.lower():
            return None
        source = content
        if task_key == "leave.general" and "1. 일반휴학" in source:
            source = source.split("1. 일반휴학", 1)[1].split("2. 입대휴학", 1)[0]
        matches = list(re.finditer(r"step\s*(\d+)\s*(.*?)(?=step\s*\d+|$)", source, re.IGNORECASE))
        if not matches:
            return None

        def title_for(description: str, order: int) -> str:
            if task_key == "leave.startup":
                if "결과" in description or "처리" in description and "확인" in description:
                    return "창업휴학 처리 결과 확인"
                if any(token in description for token in ("신청서", "증빙", "제출")):
                    return "신청서·증빙서류 준비 및 제출"
                return f"창업휴학 {order}단계"
            if "종합정보시스템" in description and "접속" in description:
                return "종합정보시스템 접속"
            if "신규휴학신청" in description:
                return "일반휴학 신규 신청 선택"
            if "휴학신청서" in description and "작성" in description:
                return "휴학신청서 작성"
            return f"{order}단계 진행"

        steps = []
        for fallback_order, match in enumerate(matches[:10], start=1):
            order = int(match.group(1) or fallback_order)
            description = normalize_text(match.group(2)).strip(" .")
            if not description:
                continue
            if not NoticeProcessor._student_action_step(description):
                continue
            steps.append(StructuredActionStep(
                order=len(steps) + 1,
                title=title_for(description, len(steps) + 1),
                description=description,
                action_type="navigate" if len(steps) <= 2 else "submit",
                source_type="html",
                source_locator=f"HTML section:{section_title}",
                confidence=0.9,
            ))
        if not steps:
            return None
        prerequisites = ["창업교육과정 교과목 6학점 이상 이수"] if task_key == "leave.startup" else []
        return StructuredActionGuide(
            task_name=TASK_BY_KEY[task_key].name if task_key in TASK_BY_KEY else section_title,
            summary=f"{section_title}의 공식 처리 순서입니다.",
            prerequisites=prerequisites,
            steps=steps,
            confidence=0.9,
        )

    @staticmethod
    def _student_action_step(step) -> bool:
        """학생이 직접 수행하지 않는 내부 승인·결재 단계를 화면 절차에서 제외한다."""
        return visible_student_step(step)

    def _structured_task_units(self, notice: Notice, structured, full_content: str) -> list[dict]:
        sections = (notice.source_metadata or {}).get("sections") or []
        by_title = {
            normalize_text(section.get("title") or "").replace(" ", ""): section
            for section in sections if section.get("title")
        }
        units: list[dict] = []
        for item in structured.task_units:
            section = by_title.get(normalize_text(item.section_title or "").replace(" ", ""))
            # TaskUnit 요약이 존재해도 원문을 대체하지 않는다. 특히 셔틀
            # 시간표·이미지 공지는 본문 요약이 한 줄뿐이고 실제 정류장과
            # 시간은 OCR 첨부에 있으므로 전체 근거를 보존해야 한다.
            content = normalize_text((section or {}).get("content") or full_content or item.summary)
            attachment_parts = []
            for attachment in (section or {}).get("attachments") or []:
                name = normalize_text(attachment.get("name") or "")
                extracted = self._attachment_part(notice.attachment_text, name)
                if extracted:
                    attachment_parts.append(f"첨부파일 {name}: {extracted}")
            content = normalize_text("\n".join([content, *attachment_parts]))
            # 외부 구조화 모델은 세부 업무를 잘 나누더라도
            # ``regular_tuition_payment``처럼 검색기가 모르는 임의 키를
            # 만들 수 있다. 세부 제목·별칭은 그대로 보존하되 검색 경계는
            # 서비스의 canonical task key로 정규화한다.
            rule_task = TASK_BY_KEY.get(item.task_key) or detect_task(
                " ".join(filter(None, [
                    item.task_name,
                    item.section_title,
                    " ".join(item.aliases or []),
                    item.parent_task,
                ]))
            )
            task_key = rule_task.key if rule_task else item.task_key
            units.append({
                "task_key": task_key,
                "task_name": item.task_name,
                "parent_task": rule_task.parent if rule_task else item.parent_task,
                "section_title": item.section_title,
                "summary": item.summary,
                "content": content,
                "aliases": list(dict.fromkeys([*(item.aliases or []), *(rule_task.aliases if rule_task else [])])),
                "excluded_intents": list(dict.fromkeys([*(item.excluded_intents or []), *(rule_task.excluded_title_terms if rule_task else [])])),
                "admission_year_start": item.admission_year_start,
                "admission_year_end": item.admission_year_end,
                "academic_year": item.academic_year,
                "semester": item.semester,
                "target_student_types": item.target.student_types,
                "target_departments": item.target.departments,
                "application_start": item.application_period.start,
                "application_end": item.application_period.end,
                "event_start": item.event_period.start,
                "event_end": item.event_period.end,
                "document_submission_start": item.document_submission_period.start,
                "document_submission_end": item.document_submission_period.end,
                "result_announcement_start": item.result_announcement_period.start,
                "result_announcement_end": item.result_announcement_period.end,
                "facts": [fact.model_dump() for fact in item.facts],
                "evidence": [evidence.model_dump() for evidence in item.evidence],
                "procedure": item.procedure,
                "confidence": item.confidence,
                "needs_review": item.needs_review,
            })
        return units

    def _fallback_task_unit(self, notice: Notice, structured, full_content: str) -> list[dict]:
        task = (
            detect_task(f"{notice.title} {structured.sub_category or ''}")
            or detect_task(f"{notice.title} {structured.sub_category or ''} {full_content[:500]}")
        )
        if not task:
            return []
        facts = [fact.model_dump() for fact in structured.additional_facts]
        evidence = [{
            "field_name": field_name,
            "excerpt": excerpt,
            "source_type": "html",
            "source_locator": "공지 본문",
            "confidence": structured.confidence,
        } for field_name, excerpt in (structured.evidence_map or {}).items() if excerpt]
        return [{
            "task_key": task.key, "task_name": task.name, "parent_task": task.parent,
            "section_title": notice.title, "summary": full_content[:500], "content": full_content,
            "aliases": list(task.aliases), "excluded_intents": list(task.excluded_title_terms),
            "admission_year_start": None, "admission_year_end": None,
            "academic_year": structured.academic_year, "semester": structured.semester,
            "target_student_types": structured.target.student_types,
            "target_departments": structured.target.departments,
            "application_start": structured.application_period.start,
            "application_end": structured.application_period.end,
            "event_start": structured.event_period.start, "event_end": structured.event_period.end,
            "document_submission_start": None, "document_submission_end": None,
            "result_announcement_start": None, "result_announcement_end": None,
            "facts": facts, "evidence": evidence, "procedure": structured.action_guide,
            "confidence": structured.confidence, "needs_review": structured.needs_review,
        }]

    def _replace_task_units(self, notice: Notice, structured, full_content: str, known_links: list[str]) -> None:
        for old_unit in list(notice.task_units):
            self.db.delete(old_unit)
        self.db.flush()

        units = self._structured_task_units(notice, structured, full_content)
        if not units:
            units = self._source_section_units(notice, structured)
        # section이 있는 학사안내에서 안전하게 분류된 구간이 하나도 없으면
        # 문서 전체 본문으로 다시 추측하지 않는다. 그 폴백이 계절수업의
        # 예외 문장을 조기졸업 업무로 만드는 원인이었다.
        has_source_sections = bool((notice.source_metadata or {}).get("sections"))
        if not units and not has_source_sections:
            units = self._fallback_task_unit(notice, structured, full_content)

        used_keys: set[str] = set()
        for index, item in enumerate(units, start=1):
            task_key = item["task_key"]
            task = self.db.scalar(select(KnowledgeTask).where(KnowledgeTask.task_key == task_key))
            rule_task = TASK_BY_KEY.get(task_key)
            if not task:
                task = KnowledgeTask(
                    task_key=task_key,
                    name=rule_task.name if rule_task else item["task_name"],
                    parent_key=rule_task.parent if rule_task else item.get("parent_task"),
                    category=rule_task.category if rule_task else structured.category,
                    aliases=item.get("aliases") or [],
                    excluded_intents=item.get("excluded_intents") or [],
                )
                self.db.add(task)
                self.db.flush()
            else:
                # KnowledgeTask는 여러 공지가 공유하는 canonical 분류다.
                # 공지별 세부 제목은 TaskUnit.title에만 저장하고 전역 이름을
                # 마지막 처리 공지의 표현으로 덮어쓰지 않는다.
                task.name = rule_task.name if rule_task else task.name
                task.parent_key = rule_task.parent if rule_task else task.parent_key
                task.category = rule_task.category if rule_task else task.category
                task.aliases = list(dict.fromkeys([*(task.aliases or []), *(item.get("aliases") or [])]))
                task.excluded_intents = list(dict.fromkeys([*(task.excluded_intents or []), *(item.get("excluded_intents") or [])]))

            raw_key = re.sub(r"[^a-z0-9._-]+", "-", f"{task_key}-{index}".lower()).strip("-")[:150]
            unit_key = raw_key or f"unit-{index}"
            while unit_key in used_keys:
                unit_key = f"{raw_key}-{len(used_keys) + 1}"[:160]
            used_keys.add(unit_key)
            fact_text = " ".join(f"{fact.get('label')}: {fact.get('value')}" for fact in item.get("facts") or [])
            procedure = item.get("procedure")
            procedure_text = ""
            if procedure:
                procedure_text = " ".join(
                    f"{step.order}. {step.title} {step.description}"
                    for step in sorted(procedure.steps, key=lambda value: value.order)
                )
            search_text = normalize_text(
                f"업무: {item['task_name']} 업무키: {task_key} 상위업무: {item.get('parent_task') or ''} "
                f"표현: {' '.join(item.get('aliases') or [])} 제외의도: {' '.join(item.get('excluded_intents') or [])} "
                f"구간: {item.get('section_title') or ''} 요약: {item.get('summary') or ''} "
                f"사실: {fact_text} 절차: {procedure_text} 근거: {item.get('content') or ''}"
            )[:12000]
            unit = TaskUnit(
                notice_id=notice.id, task_id=task.id, unit_key=unit_key,
                title=item["task_name"], section_title=item.get("section_title"),
                summary=item.get("summary"), content=item.get("content") or "", search_text=search_text,
                aliases=item.get("aliases") or [], excluded_intents=item.get("excluded_intents") or [],
                target_student_types=item.get("target_student_types") or [],
                target_departments=item.get("target_departments") or [],
                admission_year_start=item.get("admission_year_start"), admission_year_end=item.get("admission_year_end"),
                academic_year=item.get("academic_year"), semester=item.get("semester"),
                application_start=item.get("application_start"), application_end=item.get("application_end"),
                event_start=item.get("event_start"), event_end=item.get("event_end"),
                document_submission_start=item.get("document_submission_start"),
                document_submission_end=item.get("document_submission_end"),
                result_announcement_start=item.get("result_announcement_start"),
                result_announcement_end=item.get("result_announcement_end"),
                confidence=item.get("confidence") or 0.0, needs_review=bool(item.get("needs_review")),
                schema_version=self.schema_version,
            )
            notice.task_units.append(unit)
            self.db.add(unit)
            self.db.flush()
            unit.embedding_record = TaskUnitEmbedding(
                embedding=self.ai.embedding(search_text), embedding_model=self.ai.embedding_model_name,
                embedding_version=self.embedding_version,
            )
            for fact in item.get("facts") or []:
                if not fact.get("label") or not fact.get("value"):
                    continue
                unit.facts.append(TaskFact(
                    fact_type=fact.get("fact_type") or "other", label=fact["label"], value=fact["value"],
                    normalized_value=fact.get("normalized_value"), applies_to=fact.get("applies_to") or [],
                    valid_from=fact.get("valid_from"), valid_to=fact.get("valid_to"),
                    source_locator=fact.get("source_locator"),
                    source_type=fact.get("source_type") or "unknown",
                    student_actionable=bool(fact.get("student_actionable")),
                    confidence=fact.get("confidence") or 0.0,
                ))
            for evidence in item.get("evidence") or []:
                if not evidence.get("excerpt"):
                    continue
                unit.evidence.append(TaskEvidence(
                    field_name=evidence.get("field_name") or "other", excerpt=evidence["excerpt"],
                    source_type=evidence.get("source_type") or "unknown",
                    source_locator=evidence.get("source_locator"), confidence=evidence.get("confidence") or 0.0,
                ))
            if procedure and procedure.steps:
                saved_procedure = TaskProcedure(
                    summary=procedure.summary,
                    application_url=self._verified_url(procedure.application_url, known_links),
                    prerequisites=procedure.prerequisites, warnings=procedure.warnings,
                    confidence=procedure.confidence, needs_review=procedure.needs_review,
                )
                unit.procedure = saved_procedure
                student_steps = [
                    step for step in sorted(procedure.steps, key=lambda value: value.order)
                    if self._student_action_step(step)
                ]
                for order, step in enumerate(student_steps, start=1):
                    saved_procedure.steps.append(TaskProcedureStep(
                        step_order=order, title=step.title, description=step.description,
                        action_type=step.action_type,
                        action_url=self._verified_url(step.action_url, known_links),
                        source_type=step.source_type, source_locator=step.source_locator,
                        confidence=step.confidence,
                    ))

    @staticmethod
    def ground_external_structured(notice: Notice, structured: StructuredNotice) -> StructuredNotice:
        """Codex가 원문에 없는 연락처를 만들지 못하도록 저장 직전에 제거한다."""
        result = structured.model_copy(deep=True)
        source = normalize_text(
            f"{notice.title}\n{notice.content}\n{notice.attachment_text}\n"
            f"{json.dumps(notice.source_metadata or {}, ensure_ascii=False)}"
        )
        compact_source = re.sub(r"\s+", "", source).lower()
        date_source = normalize_text(f"{notice.title}\n{notice.content}\n{notice.attachment_text}")
        rejected = False

        def date_is_grounded(value: datetime | None) -> bool:
            if value is None:
                return True
            year, month, day = value.year, value.month, value.day
            patterns = (
                rf"{year}\s*년\s*0?{month}\s*월\s*0?{day}\s*일",
                rf"{year}\s*[./-]\s*0?{month}\s*[./-]\s*0?{day}",
                rf"(?<!\d)0?{month}\s*월\s*0?{day}\s*일",
                rf"(?<!\d)0?{month}\s*[./]\s*0?{day}(?!\d)",
            )
            return any(re.search(pattern, date_source) for pattern in patterns)

        for unit in result.task_units:
            grounded_evidence = []
            for evidence in unit.evidence:
                excerpt = re.sub(r"\s+", "", normalize_text(evidence.excerpt)).lower()
                if excerpt and excerpt in compact_source:
                    grounded_evidence.append(evidence)
                else:
                    unit.needs_review = True
                    unit.confidence = min(unit.confidence, 0.5)
                    rejected = True
            unit.evidence = grounded_evidence
            if not date_is_grounded(unit.application_period.start):
                unit.application_period.start = None
                unit.needs_review = True
                rejected = True
            if not date_is_grounded(unit.application_period.end):
                unit.application_period.end = None
                unit.needs_review = True
                rejected = True
            for fact in unit.facts:
                if not fact.source_locator:
                    fact.confidence = min(fact.confidence, 0.5)
                    unit.needs_review = True
                    rejected = True
            if unit.procedure:
                unit.procedure.steps = [
                    step for step in unit.procedure.steps
                    if NoticeProcessor._student_action_step(step)
                ]

        # 모델이 명확한 제목 분류를 기타로 남긴 경우에만 보정한다. 이미
        # 구체적으로 분류한 결과는 덮어쓰지 않는다.
        if result.category == "기타":
            title = normalize_text(notice.title)
            if any(token in title for token in ("국가장학금", "장학금", "장학생")):
                result.category = "장학"
            elif any(token in title for token in ("예비군", "병무")):
                result.category = "병무"
            elif any(token in title for token in (
                "수강신청", "휴학", "복학", "졸업", "학점", "학적", "전자출결", "증명서",
            )):
                result.category = "학사"

        # 빈 배열만으로는 '원문에 없음'과 '추출 실패'를 구분할 수 없다.
        # 대표 업무 주변에 명시적으로 서류 없음이 있을 때는 학생에게 그
        # 사실을 그대로 보여줄 수 있는 값으로 보존한다.
        if (
            not result.required_documents
            and "일반휴학" in notice.title
            and re.search(r"일반휴학.{0,500}(?:구비서류|제출서류)\s*없음", source)
        ):
            result.required_documents = ["별도 구비서류 없음"]

        phone = result.department.phone
        if phone:
            # 외부 구조화 모델이 여러 연락처와 설명을 phone 한 칸에 합쳐
            # DB 길이 제한을 넘기는 경우가 있다. 원문에 실제 존재하는 첫
            # 번째 정규 전화번호만 보존하고 나머지는 additionalFacts에 맡긴다.
            phone_matches = re.findall(r"0\d{1,2}[- )]?\d{3,4}[- ]?\d{4}", phone)
            result.department.phone = phone_matches[0] if phone_matches else phone[:50]
            phone = result.department.phone
            phone_digits = re.sub(r"\D", "", phone)
            source_digits = re.sub(r"\D", "", source)
            if len(phone_digits) < 7 or phone_digits not in source_digits:
                result.department.phone = None
                rejected = True

        email = result.department.email
        if email and email.lower() not in source.lower():
            result.department.email = None
            rejected = True

        person = result.department.contact_person
        if person:
            normalized_person = re.sub(r"\s+", "", person).lower()
            bare_person = re.sub(r"(주무관|교수|선생님|직원|팀장|센터장)$", "", normalized_person)
            if len(bare_person) < 2 or bare_person not in compact_source:
                result.department.contact_person = None
                result.department.contact_role = None
                rejected = True

        if rejected:
            result.needs_review = True
            result.confidence = min(result.confidence, 0.6)
            if result.action_guide:
                result.action_guide.needs_review = True
        return result

    @staticmethod
    def _existing_action_links(notice: Notice) -> list[str]:
        links = list(notice.content_links or [])
        if not notice.action_guide:
            return list(dict.fromkeys(links))
        links.append(notice.action_guide.application_url)
        links.extend(step.action_url for step in notice.action_guide.steps)
        return [link for link in links if link]

    @staticmethod
    def _canonical_search_text(notice: Notice, structured) -> str:
        def period_label(period) -> str:
            start = period.start.isoformat() if period.start else ""
            end = period.end.isoformat() if period.end else ""
            return f"{start} ~ {end}".strip(" ~")

        targets = [*structured.target.student_types]
        targets.extend(f"{grade}학년" for grade in structured.target.grades)
        targets.extend(structured.target.departments)
        targets.extend(structured.target.campus)
        guide = structured.action_guide
        steps = []
        if guide:
            for step in sorted(guide.steps, key=lambda item: item.order):
                steps.append(f"{step.order}. {step.title}: {normalize_text(step.description)[:240]}")
        important_dates_text = "; ".join(
            f"{item.label} {period_label(item)} {item.description or ''}"
            for item in structured.important_dates
        )
        additional_facts_text = "; ".join(
            f"{item.fact_type} {item.label}: {item.value} "
            f"대상={','.join(item.applies_to)} 근거={item.source_locator or ''}"
            for item in structured.additional_facts
        )
        lines = [
            f"제목: {notice.title}",
            f"문서종류: {notice.source_type}",
            f"분류: {structured.category} > {structured.sub_category or ''}",
            f"학년도: {structured.academic_year or ''}",
            f"학기: {structured.semester or ''}",
            f"신청기간: {period_label(structured.application_period)}",
            f"행사기간: {period_label(structured.event_period)}",
            f"대상: {', '.join(targets)}",
            f"행동: {structured.action_type}",
            f"신청방법: {structured.application_method or ''}",
            f"신청장소: {structured.application_location or ''}",
            f"준비서류: {', '.join(structured.required_documents)}",
            f"자격조건·제외대상: {', '.join(structured.eligibility_notes)}",
            f"비용·환불: {structured.fee_information or ''}",
            f"모집인원: {structured.capacity or ''}",
            f"선발방식: {structured.selection_method or ''}",
            f"결과발표: {structured.result_announcement or ''}",
            f"취소·변경: {structured.cancellation_policy or ''}",
            f"혜택·지원: {', '.join(structured.benefits)}",
            f"인정학점·활동시간: {structured.credits_or_hours or ''}",
            f"기타중요일정: {important_dates_text}",
            f"추가사실: {additional_facts_text}",
            f"담당부서: {structured.department.name or notice.department_name or ''}",
            f"담당자: {structured.department.contact_person or ''}",
            f"담당자직책: {structured.department.contact_role or ''}",
            f"전화번호: {structured.department.phone or ''}",
            f"이메일: {structured.department.email or ''}",
            f"사무실위치: {structured.department.office_location or ''}",
            f"운영시간: {structured.department.office_hours or ''}",
            f"키워드: {', '.join(structured.keywords)}",
            f"동의어: {', '.join(structured.synonyms)}",
            f"첨부파일: {', '.join(notice.attachment_names or [])}",
            f"필드근거: {'; '.join(f'{key}={value}' for key, value in structured.evidence_map.items())}",
            f"선행조건: {', '.join(guide.prerequisites if guide else [])}",
            f"신청단계: {' '.join(steps)}",
            f"주의사항: {', '.join(guide.warnings if guide else [])}",
        ]
        return normalize_text("\n".join(lines))[:5000]

    @staticmethod
    def _chunk_text(title: str, content: str, size: int = 1400, overlap: int = 180) -> list[str]:
        body = normalize_text(content)
        if not body:
            return [normalize_text(title)]
        chunks: list[str] = []
        start = 0
        while start < len(body):
            end = min(start + size, len(body))
            if end < len(body):
                boundary = max(body.rfind(". ", start + size // 2, end), body.rfind(" ", start + size // 2, end))
                if boundary > start:
                    end = boundary + 1
            chunk = normalize_text(body[start:end])
            if chunk:
                chunks.append(chunk)
            if end >= len(body):
                break
            start = max(end - overlap, start + 1)
        return chunks[:80]

    def _replace_chunks(self, notice: Notice, search_text: str, full_content: str) -> None:
        for old_chunk in list(notice.chunks):
            self.db.delete(old_chunk)
        self.db.flush()
        section_chunks: list[tuple[str, str]] = []
        for section in (notice.source_metadata or {}).get("sections") or []:
            title = normalize_text(section.get("title") or notice.title)
            content = normalize_text(section.get("content") or "")
            attachment_parts = []
            for attachment in section.get("attachments") or []:
                name = normalize_text(attachment.get("name") or "")
                extracted = self._attachment_part(notice.attachment_text, name)
                if extracted:
                    attachment_parts.append(f"첨부파일 {name}: {extracted}")
            combined = normalize_text("\n".join([content, *attachment_parts]))
            for part in self._chunk_text(title, combined):
                section_chunks.append((title, part))
        chunks = section_chunks or [
            (notice.title if index == 0 else f"{notice.title} ({index + 1})", chunk_text)
            for index, chunk_text in enumerate(self._chunk_text(notice.title, full_content))
        ]
        for index, (heading, chunk_text) in enumerate(chunks[:80]):
            # 잘라내기가 발생해도 실제 근거 문단이 보존되도록 청크를 구조화
            # 검색 텍스트보다 앞에 둔다.
            indexed_text = normalize_text(f"{notice.title} {chunk_text} {search_text}")
            notice.chunks.append(NoticeChunk(
                chunk_index=index,
                heading=heading,
                text=chunk_text,
                search_text=indexed_text,
                embedding=self.ai.embedding(indexed_text),
                embedding_model=self.ai.embedding_model_name,
                embedding_version=self.embedding_version,
            ))

    @staticmethod
    def _verified_url(value: str | None, known_links: list[str]) -> str | None:
        if not value:
            return None
        try:
            parsed = urlparse(value)
        except ValueError:
            return None
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return None
        return value if value in known_links else None

    def replace_action_guide(
        self,
        notice: Notice,
        structured: StructuredActionGuide | None,
        known_links: list[str] | None = None,
    ) -> ActionGuide | None:
        """검증된 절차만 저장하고 같은 공지의 이전 절차를 원자적으로 교체한다."""
        student_steps = [
            step for step in (structured.steps if structured else [])
            if self._student_action_step(step)
        ]
        if not structured or not student_steps:
            if notice.action_guide:
                self.db.delete(notice.action_guide)
                notice.action_guide = None
            return None

        known_links = list(dict.fromkeys(known_links or []))
        guide = notice.action_guide or ActionGuide(notice_id=notice.id, task_name=structured.task_name)
        notice.action_guide = guide
        self.db.add(guide)
        guide.task_name = structured.task_name
        guide.summary = structured.summary
        guide.prerequisites = structured.prerequisites
        guide.warnings = structured.warnings
        guide.application_url = self._verified_url(structured.application_url, known_links)
        guide.confidence = structured.confidence
        guide.needs_review = structured.needs_review or (
            bool(structured.application_url) and guide.application_url is None
        )
        guide.schema_version = self.schema_version
        # 기존 순번과 새 순번이 같은 경우 UNIQUE 제약이 충돌하지 않도록 먼저 삭제를 확정한다.
        self.db.flush()
        for old_step in list(guide.steps):
            self.db.delete(old_step)
        self.db.flush()
        for order, item in enumerate(sorted(student_steps, key=lambda step: step.order), start=1):
            action_url = self._verified_url(item.action_url, known_links)
            guide.steps.append(ActionStep(
                step_order=order,
                title=item.title,
                description=item.description,
                action_type=item.action_type,
                action_url=action_url,
                link_label=item.link_label if action_url else None,
                source_type=item.source_type,
                source_locator=item.source_locator,
                confidence=item.confidence,
            ))
            if item.action_url and action_url is None:
                guide.needs_review = True
        return guide
