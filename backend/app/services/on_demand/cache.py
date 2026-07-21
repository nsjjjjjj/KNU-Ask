from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AnswerCacheAlias, Notice, VerifiedAnswerCache
from app.schemas import ChatResponse, QueryPlan
from app.utils.text import normalize_text


def exact_question_hash(message: str) -> str:
    normalized = normalize_text(message).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _condition(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", "", normalize_text(value)).casefold() or None


def canonical_cache_key(plan: QueryPlan) -> str:
    task = plan.task_key or (plan.requested_tasks[0] if len(plan.requested_tasks) == 1 else None)
    # 행사·캠프는 같은 업무키 아래 서로 다른 프로그램이 다수 존재한다.
    # 질문의 고유명사를 버리면 크래프톤 캠프 답변을 다른 캠프 질문에
    # 재사용할 수 있으므로 행사 계열 캐시에만 검색 핵심어를 포함한다.
    event_keywords = (
        sorted({_condition(value) for value in plan.keywords if _condition(value)})
        if task and task.startswith("event.") else []
    )
    payload = {
        "task": task,
        "tasks": sorted(plan.requested_tasks),
        "academicYear": plan.academic_year,
        "admissionYear": plan.admission_year,
        "semester": plan.semester,
        "grade": plan.grade,
        "category": _condition(plan.category),
        "college": _condition(plan.college),
        "department": _condition(plan.department),
        "studentStatus": _condition(plan.student_status),
        "timeScope": _condition(plan.time_scope),
        "requestedFields": sorted(set(plan.requested_fields)),
        "requiredFacts": sorted(set(plan.required_facts)),
        "eventKeywords": event_keywords,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def ttl_for_plan(plan: QueryPlan, *, supported: bool) -> timedelta:
    if not supported:
        return timedelta(minutes=45)
    task = plan.task_key or ""
    fields = set(plan.required_facts) | set(plan.requested_fields)
    if task.startswith(("course.", "tuition.")) or "applicationPeriod" in fields or "application_period" in fields:
        return timedelta(hours=12)
    if task.startswith("event."):
        return timedelta(hours=6)
    if "department_contact" in fields:
        return timedelta(days=7)
    if task == "graduation.requirements":
        return timedelta(days=30)
    return timedelta(days=7)


class AnswerCacheStore:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _valid(self, row: VerifiedAnswerCache | None) -> bool:
        if not row or row.verification_status != "verified":
            return False
        if row.prompt_version != settings.on_demand_prompt_version:
            return False
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            return False
        for url, expected_hash in (row.source_content_hashes or {}).items():
            current_hash = self.db.scalar(select(Notice.content_hash).where(
                Notice.source_url == url, Notice.is_archived.is_(False),
            ).order_by(Notice.crawled_at.desc()).limit(1))
            if current_hash is not None and current_hash != expected_hash:
                return False
        return True

    def get_exact(self, message: str) -> VerifiedAnswerCache | None:
        alias = self.db.get(AnswerCacheAlias, exact_question_hash(message))
        row = self.db.get(VerifiedAnswerCache, alias.cache_id) if alias else None
        return row if self._valid(row) else None

    def get_canonical(self, plan: QueryPlan, message: str | None = None) -> VerifiedAnswerCache | None:
        row = self.db.scalar(select(VerifiedAnswerCache).where(
            VerifiedAnswerCache.canonical_key == canonical_cache_key(plan),
        ))
        if not self._valid(row):
            return None
        if message:
            self.add_alias(row, message)
        return row

    def add_alias(self, row: VerifiedAnswerCache, message: str) -> None:
        digest = exact_question_hash(message)
        alias = self.db.get(AnswerCacheAlias, digest)
        if alias is None:
            self.db.add(AnswerCacheAlias(question_hash=digest, cache_id=row.id))
        elif alias.cache_id != row.id:
            alias.cache_id = row.id
        hashes = list(row.question_hashes or [])
        if digest not in hashes:
            row.question_hashes = [*hashes[-19:], digest]
        self.db.flush()

    def response(self, row: VerifiedAnswerCache, *, session_id: str) -> ChatResponse:
        payload = dict(row.response_payload or {})
        payload["sessionId"] = session_id
        payload["answerId"] = str(uuid.uuid4())
        return ChatResponse.model_validate(payload)

    def put(
        self,
        *,
        message: str,
        plan: QueryPlan,
        response: ChatResponse,
        supported: bool,
        missing_facts: list[str] | None = None,
        source_hashes: dict[str, str] | None = None,
        verification_status: str = "verified",
        gemini_model: str | None = None,
        codex_model: str | None = None,
    ) -> VerifiedAnswerCache:
        key = canonical_cache_key(plan)
        row = self.db.scalar(select(VerifiedAnswerCache).where(
            VerifiedAnswerCache.canonical_key == key,
        )) or VerifiedAnswerCache(canonical_key=key)
        now = datetime.now(timezone.utc)
        row.query_plan = plan.model_dump(mode="json", by_alias=True)
        row.answer = response.answer
        row.response_payload = response.model_dump(mode="json", by_alias=True)
        row.facts = [item.model_dump(mode="json", by_alias=True) for item in response.answer_facts]
        row.sources = [item.model_dump(mode="json", by_alias=True) for item in response.sources]
        row.source_content_hashes = source_hashes or {}
        row.supported = supported
        row.missing_facts = list(missing_facts or [])
        row.verification_status = verification_status
        row.gemini_model = gemini_model
        row.codex_model = codex_model
        row.prompt_version = settings.on_demand_prompt_version
        row.fetched_at = now
        row.expires_at = now + ttl_for_plan(plan, supported=supported)
        self.db.add(row)
        self.db.flush()
        self.add_alias(row, message)
        return row
