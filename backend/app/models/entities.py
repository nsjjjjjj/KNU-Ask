from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.session import Base


json_type = JSON
vector_type = Vector(settings.embedding_dimensions).with_variant(JSON, "sqlite")


class Notice(Base):
    __tablename__ = "notices"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500), index=True)
    content: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    department_name: Mapped[str | None] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    crawl_status: Mapped[str] = mapped_column(String(30), default="success")
    notice_status: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    ai_processed: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    embedding_version: Mapped[str] = mapped_column(String(20), default="1.0")
    attachment_names: Mapped[list] = mapped_column(json_type, default=list)
    attachment_urls: Mapped[list] = mapped_column(json_type, default=list)
    attachment_text: Mapped[str] = mapped_column(Text, default="")
    attachment_manifest: Mapped[list] = mapped_column(json_type, default=list)
    content_links: Mapped[list] = mapped_column(json_type, default=list)
    # 원문을 다시 요청하지 않고도 AI 후처리를 재현할 수 있도록 수집 당시
    # 게시물 HTML과 게시자/연락처 같은 원천 메타데이터를 그대로 보존한다.
    source_snapshot: Mapped[str] = mapped_column(Text, default="")
    source_metadata: Mapped[dict] = mapped_column(json_type, default=dict)
    source_type: Mapped[str] = mapped_column(String(40), default="official_notice", index=True)
    source_priority: Mapped[int] = mapped_column(Integer, default=50, index=True)
    extraction_status: Mapped[str] = mapped_column(String(30), default="not_required", index=True)
    crawled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    metadata_record: Mapped["NoticeMetadata | None"] = relationship(back_populates="notice", cascade="all, delete-orphan", uselist=False)
    embedding_record: Mapped["NoticeEmbedding | None"] = relationship(back_populates="notice", cascade="all, delete-orphan", uselist=False)
    chunks: Mapped[list["NoticeChunk"]] = relationship(back_populates="notice", cascade="all, delete-orphan", order_by="NoticeChunk.chunk_index")
    action_guide: Mapped["ActionGuide | None"] = relationship(back_populates="notice", cascade="all, delete-orphan", uselist=False)
    task_units: Mapped[list["TaskUnit"]] = relationship(back_populates="notice", cascade="all, delete-orphan")


class NoticeMetadata(Base):
    __tablename__ = "notice_metadata"
    id: Mapped[int] = mapped_column(primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    sub_category: Mapped[str | None] = mapped_column(String(100), index=True)
    academic_year: Mapped[int | None] = mapped_column(Integer, index=True)
    semester: Mapped[int | None] = mapped_column(Integer, index=True)
    application_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    application_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    event_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_student_types: Mapped[list] = mapped_column(json_type, default=list)
    target_grades: Mapped[list] = mapped_column(json_type, default=list)
    target_departments: Mapped[list] = mapped_column(json_type, default=list)
    target_campus: Mapped[list] = mapped_column(json_type, default=list)
    action_type: Mapped[str] = mapped_column(String(20), default="기타")
    application_method: Mapped[str | None] = mapped_column(Text)
    application_location: Mapped[str | None] = mapped_column(Text)
    required_documents: Mapped[list] = mapped_column(json_type, default=list)
    eligibility_notes: Mapped[list] = mapped_column(json_type, default=list)
    fee_information: Mapped[str | None] = mapped_column(Text)
    capacity: Mapped[str | None] = mapped_column(Text)
    selection_method: Mapped[str | None] = mapped_column(Text)
    result_announcement: Mapped[str | None] = mapped_column(Text)
    cancellation_policy: Mapped[str | None] = mapped_column(Text)
    benefits: Mapped[list] = mapped_column(json_type, default=list)
    credits_or_hours: Mapped[str | None] = mapped_column(Text)
    important_dates: Mapped[list] = mapped_column(json_type, default=list)
    additional_facts: Mapped[list] = mapped_column(json_type, default=list)
    evidence_map: Mapped[dict] = mapped_column(json_type, default=dict)
    department_name: Mapped[str | None] = mapped_column(String(200))
    contact_person: Mapped[str | None] = mapped_column(String(100))
    contact_role: Mapped[str | None] = mapped_column(String(100))
    department_phone: Mapped[str | None] = mapped_column(String(50))
    department_email: Mapped[str | None] = mapped_column(String(200))
    department_office_location: Mapped[str | None] = mapped_column(String(300))
    department_office_hours: Mapped[str | None] = mapped_column(String(100))
    keywords: Mapped[list] = mapped_column(json_type, default=list)
    synonyms: Mapped[list] = mapped_column(json_type, default=list)
    search_text: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    notice: Mapped[Notice] = relationship(back_populates="metadata_record")


class NoticeEmbedding(Base):
    __tablename__ = "notice_embeddings"
    id: Mapped[int] = mapped_column(primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), unique=True, index=True)
    embedding: Mapped[list] = mapped_column(vector_type)
    embedding_model: Mapped[str] = mapped_column(String(100))
    embedding_version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    notice: Mapped[Notice] = relationship(back_populates="embedding_record")


class NoticeChunk(Base):
    """긴 문서와 첨부파일을 검색 가능한 작은 근거 단위로 저장한다."""

    __tablename__ = "notice_chunks"
    __table_args__ = (UniqueConstraint("notice_id", "chunk_index", name="uq_notice_chunk_index"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(vector_type)
    embedding_model: Mapped[str] = mapped_column(String(100))
    embedding_version: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    notice: Mapped[Notice] = relationship(back_populates="chunks")


class ActionGuide(Base):
    """공지에서 한 번 추출한 신청 절차를 질문마다 재사용한다."""

    __tablename__ = "action_guides"
    id: Mapped[int] = mapped_column(primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), unique=True, index=True)
    task_name: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str | None] = mapped_column(Text)
    prerequisites: Mapped[list] = mapped_column(json_type, default=list)
    warnings: Mapped[list] = mapped_column(json_type, default=list)
    application_url: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    notice: Mapped[Notice] = relationship(back_populates="action_guide")
    steps: Mapped[list["ActionStep"]] = relationship(
        back_populates="guide", cascade="all, delete-orphan", order_by="ActionStep.step_order",
    )


class ActionStep(Base):
    __tablename__ = "action_steps"
    __table_args__ = (UniqueConstraint("action_guide_id", "step_order", name="uq_action_step_order"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    action_guide_id: Mapped[int] = mapped_column(ForeignKey("action_guides.id", ondelete="CASCADE"), index=True)
    step_order: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    action_type: Mapped[str] = mapped_column(String(30), default="other")
    action_url: Mapped[str | None] = mapped_column(Text)
    link_label: Mapped[str | None] = mapped_column(String(100))
    source_type: Mapped[str] = mapped_column(String(30), default="html")
    source_locator: Mapped[str | None] = mapped_column(String(300))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    guide: Mapped[ActionGuide] = relationship(back_populates="steps")


class KnowledgeTask(Base):
    """공지와 독립적으로 유지되는 학사업무 사전."""

    __tablename__ = "knowledge_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    parent_key: Mapped[str | None] = mapped_column(String(120), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    aliases: Mapped[list] = mapped_column(json_type, default=list)
    excluded_intents: Mapped[list] = mapped_column(json_type, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    units: Mapped[list["TaskUnit"]] = relationship(back_populates="task")


class TaskUnit(Base):
    """문서 전체가 아니라 학생이 수행하는 하나의 업무·근거 구간."""

    __tablename__ = "task_units"
    __table_args__ = (UniqueConstraint("notice_id", "unit_key", name="uq_notice_task_unit_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("knowledge_tasks.id", ondelete="CASCADE"), index=True)
    unit_key: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(500), index=True)
    section_title: Mapped[str | None] = mapped_column(String(300), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list] = mapped_column(json_type, default=list)
    excluded_intents: Mapped[list] = mapped_column(json_type, default=list)
    target_student_types: Mapped[list] = mapped_column(json_type, default=list)
    target_departments: Mapped[list] = mapped_column(json_type, default=list)
    admission_year_start: Mapped[int | None] = mapped_column(Integer, index=True)
    admission_year_end: Mapped[int | None] = mapped_column(Integer, index=True)
    academic_year: Mapped[int | None] = mapped_column(Integer, index=True)
    semester: Mapped[int | None] = mapped_column(Integer, index=True)
    application_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    application_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    event_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    document_submission_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    document_submission_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_announcement_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_announcement_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    notice: Mapped[Notice] = relationship(back_populates="task_units")
    task: Mapped[KnowledgeTask] = relationship(back_populates="units")
    facts: Mapped[list["TaskFact"]] = relationship(back_populates="unit", cascade="all, delete-orphan")
    evidence: Mapped[list["TaskEvidence"]] = relationship(back_populates="unit", cascade="all, delete-orphan")
    procedure: Mapped["TaskProcedure | None"] = relationship(back_populates="unit", cascade="all, delete-orphan", uselist=False)
    embedding_record: Mapped["TaskUnitEmbedding | None"] = relationship(back_populates="unit", cascade="all, delete-orphan", uselist=False)


class TaskFact(Base):
    __tablename__ = "task_facts"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_unit_id: Mapped[int] = mapped_column(ForeignKey("task_units.id", ondelete="CASCADE"), index=True)
    fact_type: Mapped[str] = mapped_column(String(100), index=True)
    label: Mapped[str] = mapped_column(String(200))
    value: Mapped[str] = mapped_column(Text)
    normalized_value: Mapped[str | None] = mapped_column(Text)
    applies_to: Mapped[list] = mapped_column(json_type, default=list)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    source_locator: Mapped[str | None] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(40), default="html")
    student_actionable: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[TaskUnit] = relationship(back_populates="facts")


class TaskEvidence(Base):
    __tablename__ = "task_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_unit_id: Mapped[int] = mapped_column(ForeignKey("task_units.id", ondelete="CASCADE"), index=True)
    field_name: Mapped[str] = mapped_column(String(120), index=True)
    excerpt: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(40), default="html")
    source_locator: Mapped[str | None] = mapped_column(String(500))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[TaskUnit] = relationship(back_populates="evidence")


class TaskProcedure(Base):
    __tablename__ = "task_procedures"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_unit_id: Mapped[int] = mapped_column(ForeignKey("task_units.id", ondelete="CASCADE"), unique=True, index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    application_url: Mapped[str | None] = mapped_column(Text)
    prerequisites: Mapped[list] = mapped_column(json_type, default=list)
    warnings: Mapped[list] = mapped_column(json_type, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    unit: Mapped[TaskUnit] = relationship(back_populates="procedure")
    steps: Mapped[list["TaskProcedureStep"]] = relationship(
        back_populates="procedure", cascade="all, delete-orphan", order_by="TaskProcedureStep.step_order",
    )


class TaskProcedureStep(Base):
    __tablename__ = "task_procedure_steps"
    __table_args__ = (UniqueConstraint("task_procedure_id", "step_order", name="uq_task_procedure_step_order"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    task_procedure_id: Mapped[int] = mapped_column(ForeignKey("task_procedures.id", ondelete="CASCADE"), index=True)
    step_order: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    action_type: Mapped[str] = mapped_column(String(30), default="other")
    action_url: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(40), default="html")
    source_locator: Mapped[str | None] = mapped_column(String(500))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    procedure: Mapped[TaskProcedure] = relationship(back_populates="steps")


class TaskUnitEmbedding(Base):
    __tablename__ = "task_unit_embeddings"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_unit_id: Mapped[int] = mapped_column(ForeignKey("task_units.id", ondelete="CASCADE"), unique=True, index=True)
    embedding: Mapped[list] = mapped_column(vector_type)
    embedding_model: Mapped[str] = mapped_column(String(100))
    embedding_version: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    unit: Mapped[TaskUnit] = relationship(back_populates="embedding_record")


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    office_hours: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StaffDirectoryContact(Base):
    """학교 공식 직원 연락처에서 수집한 업무별 연락처."""

    __tablename__ = "staff_directory_contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    department_name: Mapped[str] = mapped_column(String(200), index=True)
    contact_person: Mapped[str | None] = mapped_column(String(100))
    duty: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str] = mapped_column(String(50))
    source_url: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    crawled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FAQ(Base):
    __tablename__ = "faqs"
    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(String(500))
    answer: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    source_notice_id: Mapped[int | None] = mapped_column(ForeignKey("notices.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CrawlHistory(Base):
    __tablename__ = "crawl_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_found: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    phase: Mapped[str] = mapped_column(String(40), default="queued")
    phase_current: Mapped[int] = mapped_column(Integer, default=0)
    phase_total: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), index=True)
    job_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatSessionContext(Base):
    """질문 원문 없이 후속 질문에 필요한 최소 조건만 보존한다."""

    __tablename__ = "chat_session_contexts"
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_keys: Mapped[list] = mapped_column(json_type, default=list)
    academic_year: Mapped[int | None] = mapped_column(Integer)
    admission_year: Mapped[int | None] = mapped_column(Integer)
    semester: Mapped[int | None] = mapped_column(Integer)
    selected_notice_ids: Mapped[list] = mapped_column(json_type, default=list)
    department_name: Mapped[str | None] = mapped_column(String(200))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class VerifiedAnswerCache(Base):
    """질문 원문 없이 조건·사실·검증 근거를 재사용하는 답변 캐시."""

    __tablename__ = "verified_answer_cache"
    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    question_hashes: Mapped[list] = mapped_column(json_type, default=list)
    query_plan: Mapped[dict] = mapped_column(json_type, default=dict)
    answer: Mapped[str] = mapped_column(Text)
    response_payload: Mapped[dict] = mapped_column(json_type, default=dict)
    facts: Mapped[list] = mapped_column(json_type, default=list)
    sources: Mapped[list] = mapped_column(json_type, default=list)
    source_content_hashes: Mapped[dict] = mapped_column(json_type, default=dict)
    supported: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    missing_facts: Mapped[list] = mapped_column(json_type, default=list)
    verification_status: Mapped[str] = mapped_column(String(30), default="pending_review", index=True)
    gemini_model: Mapped[str | None] = mapped_column(String(120))
    codex_model: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(40))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class AnswerCacheAlias(Base):
    __tablename__ = "answer_cache_aliases"
    question_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    cache_id: Mapped[int] = mapped_column(
        ForeignKey("verified_answer_cache.id", ondelete="CASCADE"), index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QueryMetric(Base):
    """개인정보·질문·프롬프트 원문을 제외한 질문 단위 진단 기록."""

    __tablename__ = "query_metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    exact_cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    canonical_cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    gemini_called: Mapped[bool] = mapped_column(Boolean, default=False)
    codex_called: Mapped[bool] = mapped_column(Boolean, default=False)
    local_search_used: Mapped[bool] = mapped_column(Boolean, default=False)
    live_search_used: Mapped[bool] = mapped_column(Boolean, default=False)
    search_attempts: Mapped[int] = mapped_column(Integer, default=0)
    checked_url_count: Mapped[int] = mapped_column(Integer, default=0)
    gemini_input_tokens: Mapped[int | None] = mapped_column(Integer)
    gemini_output_tokens: Mapped[int | None] = mapped_column(Integer)
    codex_input_tokens: Mapped[int | None] = mapped_column(Integer)
    codex_output_tokens: Mapped[int | None] = mapped_column(Integer)
    stage_timings_ms: Mapped[dict] = mapped_column(json_type, default=dict)
    json_retried: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(120))
    final_source_urls: Mapped[list] = mapped_column(json_type, default=list)
    supported: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery_reason: Mapped[str | None] = mapped_column(String(120))
    requested_missing_fields: Mapped[list] = mapped_column(json_type, default=list)
    recovery_result: Mapped[str | None] = mapped_column(String(30))
    checked_attachment_count: Mapped[int] = mapped_column(Integer, default=0)
    checked_page_count: Mapped[int] = mapped_column(Integer, default=0)
    recovery_duration_ms: Mapped[float | None] = mapped_column(Float)
    recovery_cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    persisted_fact_count: Mapped[int] = mapped_column(Integer, default=0)
    persisted_step_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class EvidenceRecoveryRecord(Base):
    """질문 원문 없이 누락 필드의 재확인 결과와 원천 버전을 보존한다."""

    __tablename__ = "evidence_recovery_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    recovery_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    canonical_key: Mapped[str] = mapped_column(String(64), index=True)
    task_key: Mapped[str | None] = mapped_column(String(120), index=True)
    requested_fields: Mapped[list] = mapped_column(json_type, default=list)
    recovered_fields: Mapped[list] = mapped_column(json_type, default=list)
    remaining_missing_fields: Mapped[list] = mapped_column(json_type, default=list)
    notice_ids: Mapped[list] = mapped_column(json_type, default=list)
    source_hashes: Mapped[dict] = mapped_column(json_type, default=dict)
    status: Mapped[str] = mapped_column(String(30), index=True)
    reason: Mapped[str | None] = mapped_column(String(120))
    checked_urls: Mapped[list] = mapped_column(json_type, default=list)
    checked_attachments: Mapped[list] = mapped_column(json_type, default=list)
    checked_page_count: Mapped[int] = mapped_column(Integer, default=0)
    timings_ms: Mapped[dict] = mapped_column(json_type, default=dict)
    persisted_fact_count: Mapped[int] = mapped_column(Integer, default=0)
    persisted_step_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class OnDemandCodexJob(Base):
    """Mac의 Codex CLI가 처리하는 질문 시점 근거 검증 작업."""

    __tablename__ = "on_demand_codex_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    canonical_key: Mapped[str] = mapped_column(String(64), index=True)
    query_plan: Mapped[dict] = mapped_column(json_type, default=dict)
    sources: Mapped[list] = mapped_column(json_type, default=list)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    result_payload: Mapped[dict] = mapped_column(json_type, default=dict)
    error_message: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class EvidenceReviewQueue(Base):
    """검증 실패한 모델 출력은 공개 캐시와 분리해 점검만 가능하게 보존한다."""

    __tablename__ = "evidence_review_queue"
    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(64), index=True)
    query_plan: Mapped[dict] = mapped_column(json_type, default=dict)
    proposed_output: Mapped[dict] = mapped_column(json_type, default=dict)
    verification_status: Mapped[str] = mapped_column(String(30), default="pending_review", index=True)
    verification_error: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    answer_id: Mapped[str] = mapped_column(String(100), index=True)
    resolved: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(40))
    source_ids: Mapped[list] = mapped_column(json_type, default=list)
    response_status: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DataGap(Base):
    """질문 중 발견된 누락 필드와 품질 문제를 원문 질문 없이 집계한다."""

    __tablename__ = "data_gaps"
    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    notice_id: Mapped[int | None] = mapped_column(ForeignKey("notices.id", ondelete="SET NULL"), index=True)
    gap_type: Mapped[str] = mapped_column(String(60), index=True)
    field_name: Mapped[str | None] = mapped_column(String(100), index=True)
    category: Mapped[str | None] = mapped_column(String(40), index=True)
    query_intent: Mapped[str | None] = mapped_column(String(200))
    context: Mapped[dict] = mapped_column(json_type, default=dict)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    detected_automatically: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
