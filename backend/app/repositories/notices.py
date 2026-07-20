from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import ActionGuide, Notice


class NoticeRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def by_source_id(self, source_id: str) -> Notice | None:
        return self.db.scalar(select(Notice).where(Notice.source_id == source_id))

    def by_id(self, notice_id: int) -> Notice | None:
        return self.db.scalar(
            select(Notice)
            .options(
                joinedload(Notice.metadata_record),
                joinedload(Notice.embedding_record),
                joinedload(Notice.action_guide).joinedload(ActionGuide.steps),
            )
            .where(Notice.id == notice_id)
        )

    def active_source_ids(self) -> set[str]:
        return set(self.db.scalars(select(Notice.source_id).where(Notice.is_archived.is_(False))))
