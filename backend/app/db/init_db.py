from sqlalchemy import text
from datetime import datetime, timezone

import app.models  # noqa: F401
from app.db.session import Base, engine
from app.services.search.task_rules import TASKS


def main() -> None:
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    # 공지별 세부 제목이 공유 KnowledgeTask 이름을 덮던 이전 데이터도
    # 서버 시작 시 canonical 업무명으로 안전하게 복구한다.
    with engine.begin() as connection:
        for task in TASKS:
            connection.execute(text(
                "UPDATE knowledge_tasks SET name = :name, parent_key = :parent_key, category = :category "
                "WHERE task_key = :task_key"
            ), {
                "name": task.name, "parent_key": task.parent,
                "category": task.category, "task_key": task.key,
            })
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            statements = [
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS contact_person VARCHAR(100)",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS application_location TEXT",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS eligibility_notes JSON DEFAULT '[]'::json",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS fee_information TEXT",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS capacity TEXT",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS selection_method TEXT",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS result_announcement TEXT",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS cancellation_policy TEXT",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS benefits JSON DEFAULT '[]'::json",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS credits_or_hours TEXT",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS important_dates JSON DEFAULT '[]'::json",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS additional_facts JSON DEFAULT '[]'::json",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS evidence_map JSON DEFAULT '{}'::json",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS contact_role VARCHAR(100)",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS department_email VARCHAR(200)",
                "ALTER TABLE notice_metadata ADD COLUMN IF NOT EXISTS department_office_location VARCHAR(300)",
                "ALTER TABLE notices ADD COLUMN IF NOT EXISTS content_links JSON DEFAULT '[]'::json",
                "ALTER TABLE notices ADD COLUMN IF NOT EXISTS attachment_manifest JSON DEFAULT '[]'::json",
                "ALTER TABLE notices ADD COLUMN IF NOT EXISTS source_snapshot TEXT DEFAULT ''",
                "ALTER TABLE notices ADD COLUMN IF NOT EXISTS source_metadata JSON DEFAULT '{}'::json",
                "ALTER TABLE notices ADD COLUMN IF NOT EXISTS source_type VARCHAR(40) DEFAULT 'official_notice'",
                "ALTER TABLE notices ADD COLUMN IF NOT EXISTS source_priority INTEGER DEFAULT 50",
                "ALTER TABLE notices ADD COLUMN IF NOT EXISTS extraction_status VARCHAR(30) DEFAULT 'not_required'",
                "ALTER TABLE crawl_history ADD COLUMN IF NOT EXISTS processed_count INTEGER DEFAULT 0",
                "ALTER TABLE crawl_history ADD COLUMN IF NOT EXISTS phase VARCHAR(40) DEFAULT 'queued'",
                "ALTER TABLE crawl_history ADD COLUMN IF NOT EXISTS phase_current INTEGER DEFAULT 0",
                "ALTER TABLE crawl_history ADD COLUMN IF NOT EXISTS phase_total INTEGER",
                "ALTER TABLE task_units ADD COLUMN IF NOT EXISTS document_submission_start TIMESTAMPTZ",
                "ALTER TABLE task_units ADD COLUMN IF NOT EXISTS document_submission_end TIMESTAMPTZ",
                "ALTER TABLE task_units ADD COLUMN IF NOT EXISTS result_announcement_start TIMESTAMPTZ",
                "ALTER TABLE task_units ADD COLUMN IF NOT EXISTS result_announcement_end TIMESTAMPTZ",
                "ALTER TABLE task_facts ADD COLUMN IF NOT EXISTS source_type VARCHAR(40) DEFAULT 'html'",
                "ALTER TABLE task_facts ADD COLUMN IF NOT EXISTS student_actionable BOOLEAN DEFAULT FALSE",
                "CREATE INDEX IF NOT EXISTS ix_notices_source_type ON notices (source_type)",
                "CREATE INDEX IF NOT EXISTS ix_notices_source_priority ON notices (source_priority)",
                "ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS recovery_triggered BOOLEAN DEFAULT FALSE",
                "ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS recovery_reason VARCHAR(120)",
                "ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS requested_missing_fields JSON DEFAULT '[]'::json",
                "ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS recovery_result VARCHAR(30)",
                "ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS checked_attachment_count INTEGER DEFAULT 0",
                "ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS checked_page_count INTEGER DEFAULT 0",
                "ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS recovery_duration_ms DOUBLE PRECISION",
                "ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS recovery_cache_hit BOOLEAN DEFAULT FALSE",
                "ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS persisted_fact_count INTEGER DEFAULT 0",
                "ALTER TABLE query_metrics ADD COLUMN IF NOT EXISTS persisted_step_count INTEGER DEFAULT 0",
            ]
            for statement in statements:
                connection.execute(text(statement))
    # 프로세스 재시작으로 끊긴 작업이 새 스케줄을 영구 차단하지 않게 종료 처리한다.
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE crawl_history SET finished_at = :finished_at, phase = 'failed', "
            "error_message = COALESCE(error_message, '서버 재시작으로 작업이 중단되었습니다.') "
            "WHERE finished_at IS NULL"
        ), {"finished_at": datetime.now(timezone.utc)})


if __name__ == "__main__":
    main()
