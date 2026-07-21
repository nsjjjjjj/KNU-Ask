"""문제 업무 몇 건만 새 TaskUnit 구조로 수집·재색인한다.

전체 공지 재처리가 아니라 졸업·휴학·수강·등록금·예비군의 공식 학사
안내와 최신 공지 소수만 대상으로 삼는다. 운영의 신규 공지는 Codex
구조화 큐를 그대로 사용하고, 이 파일은 구조 검증용 선별 마이그레이션이다.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import Notice
from app.services.crawler.knu import KnuNoticeCrawler
from app.services.processing import NoticeProcessor


GUIDE_SLUGS = {"graduation", "leave-return", "classes", "refund", "reserve", "startup-leave"}
RECENT_TITLE_TERMS = ("수강신청", "등록금 납부", "학점등록", "창업휴학", "예비군")


def main() -> None:
    # 선별 마이그레이션은 현재 Codex가 검토한 업무 사전과 원문 section을
    # 사용한다. 신규·변경 공지의 운영 provider 설정은 바꾸지 않는다.
    original_provider = settings.notice_structuring_provider
    settings.notice_structuring_provider = "rules"
    db = SessionLocal()
    report = {"guides": [], "recentNotices": [], "failed": []}
    try:
        processor = NoticeProcessor(db)
        crawler = KnuNoticeCrawler(incremental=True)
        for raw in crawler._crawl_academic_guides(GUIDE_SLUGS):
            try:
                notice, state = processor.upsert(raw, force=True)
                db.commit()
                report["guides"].append({
                    "id": notice.id, "title": notice.title, "state": state,
                    "taskUnits": [unit.task.task_key for unit in notice.task_units],
                })
            except Exception as exc:
                db.rollback()
                report["failed"].append({"sourceId": raw.get("source_id"), "error": str(exc)[:500]})

        recent = []
        seen_ids = set()
        for term in RECENT_TITLE_TERMS:
            rows = db.scalars(
                select(Notice)
                .where(Notice.source_type == "official_notice", Notice.title.contains(term), Notice.is_archived.is_(False))
                .order_by(Notice.published_at.desc())
                .limit(3)
            ).all()
            for row in rows:
                if row.id not in seen_ids:
                    recent.append(row)
                    seen_ids.add(row.id)
        # 이전 시험 분류에서 "졸업생"이라는 단어만으로 생성된 잘못된
        # 졸업요건 단위를 같은 실행에서 제거한다.
        for row in db.scalars(select(Notice).where(Notice.title.contains("졸업생특화프로그램"))).all():
            if row.id not in seen_ids:
                recent.append(row)
                seen_ids.add(row.id)
        for notice in recent:
            try:
                processor.process(notice, list(notice.content_links or []))
                db.commit()
                report["recentNotices"].append({
                    "id": notice.id, "title": notice.title,
                    "taskUnits": [unit.task.task_key for unit in notice.task_units],
                })
            except Exception as exc:
                db.rollback()
                report["failed"].append({"noticeId": notice.id, "title": notice.title, "error": str(exc)[:500]})
    finally:
        settings.notice_structuring_provider = original_provider
        db.close()
    report["summary"] = {
        "guideCount": len(report["guides"]),
        "recentNoticeCount": len(report["recentNotices"]),
        "failedCount": len(report["failed"]),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
