from sqlalchemy import text
from datetime import datetime, timezone

import app.models  # noqa: F401
from app.db.session import Base, engine


def main() -> None:
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
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
                "CREATE INDEX IF NOT EXISTS ix_notices_source_type ON notices (source_type)",
                "CREATE INDEX IF NOT EXISTS ix_notices_source_priority ON notices (source_priority)",
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
