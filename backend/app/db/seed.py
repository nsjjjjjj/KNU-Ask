import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.init_db import main as init_db
from app.db.session import SessionLocal
from app.models import Department, FAQ, Notice
from app.services.ai import AIService
from app.services.processing import NoticeProcessor


# 요구사항에 명시된 값으로, 실제 학교 전화번호가 아닌 명백한 더미 데이터입니다.
DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "sample_data.json"


def seed() -> None:
    init_db()
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        departments = {}
        for item in data["departments"]:
            department = db.scalar(select(Department).where(Department.name == item["name"]))
            if not department:
                department = Department(**item)
                db.add(department)
            departments[item["name"]] = department
        db.flush()

        if not settings.mock_crawler:
            sample_source_ids = [item["source_id"] for item in data["notices"]]
            for notice in db.scalars(select(Notice).where(Notice.source_id.in_(sample_source_ids))):
                notice.is_archived = True
            sample_questions = [item["question"] for item in data["faqs"]]
            for faq in db.scalars(select(FAQ).where(FAQ.question.in_(sample_questions))):
                faq.is_active = False
            db.commit()
            return

        processor = NoticeProcessor(db, AIService())
        notices = {}
        for item in data["notices"]:
            notice, _ = processor.upsert(item)
            meta = notice.metadata_record
            meta.category = item["category"]
            meta.sub_category = item["sub_category"]
            meta.academic_year = item["academic_year"]
            meta.semester = item["semester"]
            meta.application_start = datetime.fromisoformat(item["application_start"]) if item["application_start"] else None
            meta.application_end = datetime.fromisoformat(item["application_end"]) if item["application_end"] else None
            meta.action_type = item["action_type"]
            meta.application_method = item["application_method"]
            meta.required_documents = item["required_documents"]
            meta.keywords = item["keywords"]
            department = departments[item["department_name"]]
            meta.department_name = department.name
            meta.department_phone = department.phone
            meta.department_office_hours = department.office_hours
            meta.search_text = f"제목: {notice.title}\n분류: {meta.category} > {meta.sub_category}\n본문: {notice.content}\n키워드: {', '.join(meta.keywords)}"
            notice.notice_status = item["notice_status"]
            notice.embedding_record.embedding = processor.ai.embedding(meta.search_text)
            notices[item["source_id"]] = notice
        for item in data["faqs"]:
            source = notices.get(item.get("source_id"))
            faq = db.scalar(select(FAQ).where(FAQ.question == item["question"]))
            if not faq:
                faq = FAQ(question=item["question"])
                db.add(faq)
            faq.answer = item.get("answer")
            faq.category = item["category"]
            faq.source_notice_id = source.id if source else None
            faq.is_active = True
        db.commit()


if __name__ == "__main__":
    seed()
