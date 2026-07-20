from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CATEGORIES = ["학사일정", "등록", "학사", "교직안내", "병무", "창업교육안내", "대학생활안내", "장학", "취업", "기타"]
NOTICE_STATUSES = ["upcoming", "active", "expired", "always", "unknown"]
ACTION_TYPES = ["신청", "제출", "납부", "확인", "참석", "수강", "발급", "문의", "기타"]
STEP_ACTION_TYPES = ["open_url", "navigate", "submit", "upload", "pay", "verify", "contact", "other"]
SOURCE_TYPES = ["html", "body_image", "attachment_image", "pdf", "attachment", "unknown"]
STEP_TITLE_PREFERRED_MAX = 100


def _compact_step_title(value: str, action_type: str) -> str:
    """긴 원문 문단 대신 화면에서 읽을 수 있는 짧은 행동 제목을 만든다."""
    action_labels = {
        "open_url": "신청 페이지 접속",
        "navigate": "신청 메뉴 이동",
        "submit": "신청 내용 제출",
        "upload": "필요 서류 업로드",
        "pay": "비용 납부",
        "verify": "처리 결과 확인",
        "contact": "담당 부서 문의",
    }
    if action_type in action_labels:
        return action_labels[action_type]
    cleaned = re.sub(r"^[\s\-–—•·■▶※]+", "", re.sub(r"\s+", " ", value)).strip()
    candidate = re.split(r"[.!?;]|\s+[|•■▶※]\s*", cleaned, maxsplit=1)[0].strip(" ,-:;")
    if len(candidate) > STEP_TITLE_PREFERRED_MAX:
        candidate = candidate[:STEP_TITLE_PREFERRED_MAX].rstrip(" ,-:;") + "…"
    return candidate or "신청 절차 확인"


class CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=lambda s: s.split("_")[0] + "".join(p.title() for p in s.split("_")[1:]))


class Period(CamelModel):
    start: datetime | None = None
    end: datetime | None = None


class Target(CamelModel):
    student_types: list[str] = Field(default_factory=list)
    grades: list[int] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    campus: list[str] = Field(default_factory=list)


class DepartmentInfo(CamelModel):
    name: str | None = None
    contact_person: str | None = Field(default=None, max_length=100)
    contact_role: str | None = Field(default=None, max_length=100)
    phone: str | None = None
    email: str | None = None
    office_location: str | None = None
    office_hours: str | None = None
    contact_duty: str | None = None
    contact_source: str | None = None
    source_url: str | None = None


class ImportantDate(CamelModel):
    label: str = Field(min_length=1, max_length=100)
    start: datetime | None = None
    end: datetime | None = None
    description: str | None = Field(default=None, max_length=1000)
    source_locator: str | None = Field(default=None, max_length=300)


class AdditionalFact(CamelModel):
    """전용 컬럼이 아직 없는 새로운 공지 정보를 손실 없이 보존한다."""

    fact_type: str = Field(default="other", min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=150)
    value: str = Field(min_length=1, max_length=2000)
    applies_to: list[str] = Field(default_factory=list)
    student_actionable: bool = False
    source_type: str = "unknown"
    source_locator: str | None = Field(default=None, max_length=300)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("source_type")
    @classmethod
    def source_type_allowed(cls, value: str) -> str:
        return value if value in SOURCE_TYPES else "unknown"


class StructuredActionStep(CamelModel):
    order: int = Field(ge=1, le=30)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=2000)
    action_type: str = "other"
    action_url: str | None = None
    link_label: str | None = Field(default=None, max_length=100)
    source_type: str = "html"
    source_locator: str | None = Field(default=None, max_length=300)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def compact_oversized_title(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip()
        if len(title) <= STEP_TITLE_PREFERRED_MAX:
            data["title"] = title
            return data

        description = re.sub(r"\s+", " ", str(data.get("description") or "")).strip()
        if title not in description:
            description = f"{title} {description}".strip()
        data["description"] = description[:2000].rstrip()
        data["title"] = _compact_step_title(title, str(data.get("actionType") or data.get("action_type") or "other"))
        data["confidence"] = min(float(data.get("confidence") or 0.0), 0.5)
        return data

    @field_validator("action_type")
    @classmethod
    def step_action_allowed(cls, value: str) -> str:
        return value if value in STEP_ACTION_TYPES else "other"

    @field_validator("source_type")
    @classmethod
    def source_type_allowed(cls, value: str) -> str:
        return value if value in SOURCE_TYPES else "unknown"


class StructuredActionGuide(CamelModel):
    task_name: str = Field(min_length=1, max_length=500)
    summary: str | None = Field(default=None, max_length=2000)
    prerequisites: list[str] = Field(default_factory=list)
    steps: list[StructuredActionStep] = Field(default_factory=list, max_length=30)
    warnings: list[str] = Field(default_factory=list)
    application_url: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False

    @model_validator(mode="after")
    def mark_low_confidence_steps_for_review(self):
        if any(step.confidence <= 0.5 for step in self.steps):
            self.needs_review = True
        return self


class StructuredNotice(CamelModel):
    category: str = "기타"
    sub_category: str | None = None
    academic_year: int | None = None
    semester: int | None = None
    published_at: datetime | None = None
    application_period: Period = Field(default_factory=Period)
    event_period: Period = Field(default_factory=Period)
    target: Target = Field(default_factory=Target)
    action_type: str = "기타"
    application_method: str | None = None
    application_location: str | None = None
    required_documents: list[str] = Field(default_factory=list)
    eligibility_notes: list[str] = Field(default_factory=list)
    fee_information: str | None = None
    capacity: str | None = None
    selection_method: str | None = None
    result_announcement: str | None = None
    cancellation_policy: str | None = None
    benefits: list[str] = Field(default_factory=list)
    credits_or_hours: str | None = None
    important_dates: list[ImportantDate] = Field(default_factory=list)
    additional_facts: list[AdditionalFact] = Field(default_factory=list, max_length=100)
    evidence_map: dict[str, str] = Field(default_factory=dict)
    department: DepartmentInfo = Field(default_factory=DepartmentInfo)
    keywords: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    search_text: str = ""
    notice_status: str = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False
    action_guide: StructuredActionGuide | None = None

    @field_validator("category")
    @classmethod
    def category_allowed(cls, value: str) -> str:
        return value if value in CATEGORIES else "기타"

    @field_validator("notice_status")
    @classmethod
    def status_allowed(cls, value: str) -> str:
        return value if value in NOTICE_STATUSES else "unknown"

    @field_validator("action_type")
    @classmethod
    def action_allowed(cls, value: str) -> str:
        return value if value in ACTION_TYPES else "기타"


class QueryFilters(CamelModel):
    intent: str | None = None
    category: str | None = None
    sub_category: str | None = None
    academic_year: int | None = None
    semester: int | None = None
    grade: int | None = None
    department: str | None = None
    student_status: str | None = None
    time_scope: str | None = None
    keywords: list[str] = Field(default_factory=list)


class CodexJobFailure(CamelModel):
    error: str = Field(min_length=1, max_length=2000)


class NoticeSummary(CamelModel):
    id: int
    title: str
    category: str | None = None
    published_at: datetime
    notice_status: str
    status_label: str | None = None
    source_url: str
    score: float | None = None


class ChatRequest(CamelModel):
    message: str = Field(min_length=1, max_length=1000)
    session_id: str | None = None
    selected_category: str | None = None


class NextAction(CamelModel):
    label: str
    description: str | None = None
    url: str | None = None
    deadline: datetime | None = None
    official: bool = True


class AnswerFact(CamelModel):
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=1000)


class ActionGuideResponse(CamelModel):
    task_name: str
    summary: str | None = None
    targets: list[str] = Field(default_factory=list)
    period: Period = Field(default_factory=Period)
    prerequisites: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)
    eligibility_notes: list[str] = Field(default_factory=list)
    application_location: str | None = None
    fee_information: str | None = None
    capacity: str | None = None
    selection_method: str | None = None
    result_announcement: str | None = None
    cancellation_policy: str | None = None
    benefits: list[str] = Field(default_factory=list)
    credits_or_hours: str | None = None
    important_dates: list[ImportantDate] = Field(default_factory=list)
    steps: list[StructuredActionStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    application_url: str | None = None
    source_url: str
    department: DepartmentInfo = Field(default_factory=DepartmentInfo)
    confidence: float = 0.0
    needs_review: bool = False


class SourceEvidence(CamelModel):
    notice_id: int
    title: str
    published_at: datetime
    effective_status: str
    evidence_excerpt: str
    url: str


class SearchScope(CamelModel):
    sources: list[str] = Field(default_factory=lambda: [
        "academic_guides", "academic_calendar", "official_faq", "official_notices",
        "staff_directory", "event_guides",
    ])
    notice_count: int = 0
    description: str = "현재 수집된 강남대학교 공식 학사안내·FAQ·공지·담당자·행사안내"


class ChatResponse(CamelModel):
    answer_id: str
    answer: str
    status: Literal[
        "success", "no_result", "constraint_mismatch", "insufficient_evidence",
        "conflicting_evidence", "stale_only", "out_of_scope",
        "clarification_required", "service_error",
    ] = "success"
    answer_mode: Literal["faq", "action_guide", "deterministic", "generated", "search_results_only", "department_handoff"] = "generated"
    answer_facts: list[AnswerFact] = Field(default_factory=list)
    answer_notes: list[str] = Field(default_factory=list)
    matched_notices: list[NoticeSummary] = Field(default_factory=list)
    sources: list[SourceEvidence] = Field(default_factory=list)
    department: DepartmentInfo = Field(default_factory=DepartmentInfo)
    next_action: NextAction | None = None
    action_guide: ActionGuideResponse | None = None
    warnings: list[str] = Field(default_factory=list)
    original_url: str | None = None
    has_data: bool
    session_id: str
    query: QueryFilters | None = None
    verified_at: datetime
    search_scope: SearchScope = Field(default_factory=SearchScope)


class CategoryResponse(CamelModel):
    category: str
    notices: list[NoticeSummary] = Field(default_factory=list)
    message: str


class FAQResponse(CamelModel):
    id: int
    question: str
    category: str


class FeedbackRequest(CamelModel):
    answer_id: str = Field(min_length=8, max_length=100)
    resolved: bool
    reason: Literal["resolved", "incorrect", "outdated", "misunderstood", "insufficient", "needs_staff"]
    source_ids: list[int] = Field(default_factory=list)
    response_status: str


class FeedbackResponse(CamelModel):
    status: Literal["accepted"] = "accepted"


class DataGapUpdate(CamelModel):
    status: Literal["open", "resolved", "ignored"]
    resolution_note: str | None = Field(default=None, max_length=1000)


class CrawlerStatus(CamelModel):
    id: int | None = None
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    total_found: int = 0
    new_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    processed_count: int = 0
    phase: str = "idle"
    phase_current: int = 0
    phase_total: int | None = None
    error_message: str | None = None
