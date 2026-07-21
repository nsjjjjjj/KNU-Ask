from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import EvidenceReviewQueue, Notice
from app.schemas import (
    AnswerFact, ChatResponse, DepartmentInfo, NoticeSummary, QueryPlan,
    SearchScope, SourceEvidence,
)
from app.services.on_demand.codex import CodexEvidenceService, CodexEvidenceResult, verify_codex_result
from app.services.on_demand.cache import canonical_cache_key
from app.services.on_demand.sources import (
    OfficialSource, SchoolSourceGateway, is_allowed_school_url, missing_required_facts,
)
from app.services.search.task_rules import visible_student_step
from app.utils.text import normalize_text


logger = logging.getLogger(__name__)


def local_evidence_insufficient(plan: QueryPlan, matches: list[dict]) -> bool:
    if not matches:
        return True
    top = matches[0]
    notice = top["notice"]
    unit = top.get("task_unit")
    meta = top.get("metadata")
    required = set(plan.required_facts) | set(plan.requested_fields)
    satisfied: set[str] = set()
    if unit is not None:
        if unit.procedure and any(visible_student_step(step) for step in unit.procedure.steps):
            satisfied.update({"procedure", "actionGuide"})
        if unit.procedure and unit.procedure.application_url:
            satisfied.update({"applicationUrl", "application_url"})
        fact_types = {normalize_text(fact.fact_type).casefold() for fact in unit.facts}
        if fact_types.intersection({"required_document", "required_documents", "document", "documents"}):
            satisfied.update({"requiredDocuments", "required_documents"})
        if fact_types.intersection({"leaveduration", "leave_duration", "maximum_leave_duration", "leave_limit"}):
            satisfied.update({"leaveDuration", "leave_duration"})
        if fact_types.intersection({"eligibility", "requirement", "target"}):
            satisfied.add("eligibility")
        if unit.application_start or unit.application_end:
            satisfied.update({"applicationPeriod", "application_period"})
    if meta is not None:
        if meta.required_documents:
            satisfied.update({"requiredDocuments", "required_documents"})
        if meta.department_phone:
            satisfied.update({"departmentContact", "department_contact"})
        if meta.application_start or meta.application_end:
            satisfied.update({"applicationPeriod", "application_period"})
    if required and required.issubset(satisfied):
        return False
    if float(top.get("score") or 0) < settings.on_demand_top_score_threshold:
        return True
    if notice.extraction_status in {"partial", "failed", "deferred"}:
        return True
    text = normalize_text(" ".join(filter(None, [
        notice.title, notice.content, notice.attachment_text,
        top.get("chunk_text"), getattr(top.get("task_unit"), "content", None),
    ])))
    if plan.academic_year and str(plan.academic_year) not in text:
        return True
    if plan.admission_year and str(plan.admission_year) not in text:
        return True
    if plan.semester and f"{plan.semester}학기" not in text.replace(" ", ""):
        return True
    if plan.department and normalize_text(plan.department) not in text:
        return True
    local_source = OfficialSource(
        url=notice.source_url, title=notice.title, content=text,
        content_hash=notice.content_hash, fetched_at=notice.crawled_at,
        extraction_status=notice.extraction_status,
    )
    return bool(missing_required_facts(plan, [local_source]))


class OnDemandEvidenceResolver:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.gateway = SchoolSourceGateway(db)
        self.codex = CodexEvidenceService(db)
        self.verification_failure: str | None = None
        self.missing_facts: list[str] = []

    def _catalog_notice(self, source: OfficialSource) -> Notice:
        source_id = "ondemand:" + hashlib.sha256(source.url.encode("utf-8")).hexdigest()[:40]
        row = self.db.query(Notice).filter(Notice.source_id == source_id).one_or_none()
        if row is None:
            row = Notice(
                source_id=source_id,
                title=source.title,
                content=source.content,
                published_at=source.fetched_at,
                source_url=source.url,
                content_hash=source.content_hash,
                crawl_status="success",
                notice_status="unknown",
                is_archived=False,
                ai_processed=False,
                attachment_names=[], attachment_urls=[], attachment_text="",
                attachment_manifest=[], content_links=[], source_snapshot="",
                source_metadata={"catalogOnly": True}, source_type="on_demand_official",
                source_priority=80, extraction_status=source.extraction_status,
                crawled_at=source.fetched_at,
            )
            self.db.add(row)
        else:
            row.title = source.title
            row.content = source.content
            row.content_hash = source.content_hash
            row.crawled_at = source.fetched_at
            row.extraction_status = source.extraction_status
        self.db.flush()
        return row

    def resolve(
        self, plan: QueryPlan, *, session_id: str, answer_id: str,
        timeout_seconds: float | None = None,
    ) -> ChatResponse | None:
        if not settings.on_demand_live_search_enabled:
            return None
        deadline = time.monotonic() + min(timeout_seconds or settings.on_demand_timeout_seconds, 30.0)
        try:
            sources = self.gateway.search_school_sources(
                plan, timeout_seconds=max(0.5, deadline - time.monotonic()),
            )
        except Exception as exc:
            logger.warning("official homepage fallback error=%s", type(exc).__name__)
            self.verification_failure = "homepage_unavailable"
            return None
        if not sources or all(source.extraction_status != "complete" for source in sources):
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self.verification_failure = "live_search_timeout"
            return None
        try:
            result = self.codex.resolve(plan, sources, timeout_seconds=remaining)
        except Exception as exc:
            logger.warning("on-demand Codex fallback error=%s", type(exc).__name__)
            self.verification_failure = "codex_unavailable"
            return None
        verified, reason = verify_codex_result(plan, result, sources)
        if not verified:
            self.verification_failure = reason or "verification_failed"
            self.db.add(EvidenceReviewQueue(
                canonical_key=canonical_cache_key(plan),
                query_plan=plan.model_dump(mode="json", by_alias=True),
                proposed_output=result.model_dump(mode="json"),
                verification_error=self.verification_failure,
            ))
            self.db.flush()
            logger.warning("on-demand evidence rejected reason=%s", self.verification_failure)
            return None

        self.missing_facts = list(result.missingFacts)

        notices = {source.url: self._catalog_notice(source) for source in sources}
        facts = []
        source_rows = []
        summaries = []
        seen_urls = set()
        for fact in result.facts:
            notice = notices[fact.sourceUrl]
            facts.append(AnswerFact(
                label=fact.name, value=fact.value, source_notice_id=notice.id,
                source_locator=fact.sourceExcerpt[:500],
            ))
            if fact.sourceUrl in seen_urls:
                continue
            seen_urls.add(fact.sourceUrl)
            source = next(item for item in sources if item.url == fact.sourceUrl)
            summaries.append(NoticeSummary(
                id=notice.id, title=source.title, category=plan.category,
                published_at=source.fetched_at, notice_status="unknown",
                status_label="실시간 확인", source_url=source.url, score=1.0,
            ))
            excerpt = next(item.sourceExcerpt for item in result.facts if item.sourceUrl == source.url)
            source_rows.append(SourceEvidence(
                notice_id=notice.id, title=source.title, published_at=source.fetched_at,
                effective_status="unknown", evidence_excerpt=excerpt,
                url=source.url, task_key=plan.task_key,
            ))
        warnings = []
        status = "success"
        if result.missingFacts:
            status = "clarification_required" if plan.needs_clarification else "insufficient_evidence"
            warnings.append("확인하지 못한 사실: " + ", ".join(result.missingFacts))
        if plan.needs_clarification and plan.clarification_question:
            warnings.append(plan.clarification_question)
        return ChatResponse(
            answer_id=answer_id,
            answer=result.answer,
            status=status,
            answer_mode="deterministic",
            answer_facts=facts,
            matched_notices=summaries,
            sources=source_rows,
            department=DepartmentInfo(),
            warnings=warnings,
            original_url=source_rows[0].url if source_rows else None,
            has_data=True,
            session_id=session_id,
            query=plan,
            verified_at=datetime.now(timezone.utc),
            search_scope=SearchScope(
                sources=["official_live_search"], notice_count=len(sources),
                description="이번 질문에서 실시간 확인한 강남대학교 공식 자료",
            ),
        )
