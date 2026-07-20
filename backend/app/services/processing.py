import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import ActionGuide, ActionStep, Department, Notice, NoticeChunk, NoticeEmbedding, NoticeMetadata, ProcessingJob
from app.repositories import NoticeRepository
from app.schemas import StructuredActionGuide, StructuredNotice
from app.services.ai import AIService
from app.utils.text import content_hash, normalize_text


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
        digest = hashlib.sha1(b"extractor-v2|chunker-v3|canonical-index-v1")
        for prompt in sorted(prompt_dir.glob("*.txt")):
            digest.update(prompt.read_bytes())
        return f"{settings.schema_version}-{digest.hexdigest()[:8]}"[:20]

    def upsert(self, raw: dict, force: bool = False) -> tuple[Notice, str]:
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
        published_key = "" if raw.get("source_type") in {
            "academic_guide", "official_faq", "staff_directory",
        } else published.isoformat()
        digest = content_hash(
            raw["title"], raw["content"], raw.get("attachment_text", ""),
            published_key, resource_manifest,
        )
        notice = self.repo.by_source_id(source_id)
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
        if external_ai:
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
        meta.needs_review = bool(
            structured.needs_review or structured.confidence < 0.55
            or actionable_without_period or extraction_incomplete
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
        if notice.source_type in {"academic_guide", "official_faq", "staff_directory"}:
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
    def ground_external_structured(notice: Notice, structured: StructuredNotice) -> StructuredNotice:
        """Codex가 원문에 없는 연락처를 만들지 못하도록 저장 직전에 제거한다."""
        result = structured.model_copy(deep=True)
        source = normalize_text(
            f"{notice.title}\n{notice.content}\n{notice.attachment_text}\n"
            f"{json.dumps(notice.source_metadata or {}, ensure_ascii=False)}"
        )
        compact_source = re.sub(r"\s+", "", source).lower()
        rejected = False

        phone = result.department.phone
        if phone:
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
    def _chunk_text(title: str, search_text: str, content: str, size: int = 1400, overlap: int = 180) -> list[str]:
        body = normalize_text(content)
        if not body:
            return [normalize_text(f"{title} {search_text}")]
        chunks: list[str] = []
        start = 0
        while start < len(body):
            end = min(start + size, len(body))
            if end < len(body):
                boundary = max(body.rfind(". ", start + size // 2, end), body.rfind(" ", start + size // 2, end))
                if boundary > start:
                    end = boundary + 1
            chunk = normalize_text(f"{title} {search_text} {body[start:end]}")
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
        for index, chunk_text in enumerate(self._chunk_text(notice.title, search_text, full_content)):
            notice.chunks.append(NoticeChunk(
                chunk_index=index,
                heading=notice.title if index == 0 else f"{notice.title} ({index + 1})",
                text=chunk_text,
                search_text=normalize_text(f"{notice.title} {search_text} {chunk_text}"),
                embedding=self.ai.embedding(chunk_text),
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
        if not structured or not structured.steps:
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
        for order, item in enumerate(sorted(structured.steps, key=lambda step: step.order), start=1):
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
