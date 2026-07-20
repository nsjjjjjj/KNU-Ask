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
