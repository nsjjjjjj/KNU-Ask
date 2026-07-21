from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta
from sqlalchemy import desc, select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import CrawlHistory, Notice
from app.services.ai import AIService
from app.services.crawler.knu import KnuNoticeCrawler
from app.services.processing import NoticeProcessor
from app.services.staff_directory import sync_staff_directory


_crawl_lock = threading.Lock()


def create_crawl_history(phase: str = "queued") -> int | None:
    """수집과 재인덱싱을 합쳐 동시에 한 작업만 생성한다."""
    with SessionLocal() as db:
        running = db.scalar(select(CrawlHistory).where(CrawlHistory.finished_at.is_(None)).order_by(desc(CrawlHistory.id)))
        if running:
            return None
        history = CrawlHistory(phase=phase)
        db.add(history)
        db.commit()
        db.refresh(history)
        return history.id


def run_scheduled_crawl() -> None:
    history_id = create_crawl_history()
    if history_id is not None:
        run_crawler(history_id, incremental=True, profile="incremental")


def run_scheduled_daily_crawl() -> None:
    history_id = create_crawl_history(phase="daily_queued")
    if history_id is not None:
        run_crawler(history_id, profile="daily")


def run_scheduled_full_crawl() -> None:
    history_id = create_crawl_history(phase="weekly_queued")
    if history_id is not None:
        run_crawler(history_id, incremental=False, profile="full")


def _update_progress(history_id: int, phase: str, current: int, total: int | None) -> None:
    with SessionLocal() as progress_db:
        history = progress_db.get(CrawlHistory, history_id)
        if not history or history.finished_at:
            return
        history.phase = phase
        history.phase_current = current
        history.phase_total = total
        if phase == "processing":
            history.processed_count = current
            if total is not None:
                history.total_found = total
        progress_db.commit()


def run_crawler(history_id: int, incremental: bool = False, profile: str | None = None) -> None:
    if not _crawl_lock.acquire(blocking=False):
        with SessionLocal() as db:
            history = db.get(CrawlHistory, history_id)
            if history:
                history.error_message = "다른 크롤링 작업이 이미 실행 중입니다."
                history.phase = "failed"
                history.finished_at = datetime.now(timezone.utc)
                db.commit()
        return
    try:
        with SessionLocal() as db:
            history = db.get(CrawlHistory, history_id)
            try:
                crawler = KnuNoticeCrawler(
                    progress_callback=lambda phase, current, total: _update_progress(
                        history_id, phase, current, total,
                    ), incremental=incremental, profile=profile,
                )
                raw_notices = crawler.crawl()
                db.refresh(history)
                history.total_found = len(raw_notices)
                history.phase = "processing"
                history.phase_current = 0
                history.phase_total = len(raw_notices)
                db.commit()
                processor = NoticeProcessor(db)
                directory_ids = {
                    str(raw["source_id"])
                    for raw in raw_notices
                    if raw.get("source_type") == "staff_directory"
                }
                if directory_ids:
                    sync_staff_directory(db, [
                        raw for raw in raw_notices if raw.get("source_type") == "staff_directory"
                    ])
                seen: set[str] = set()
                processing_failures: list[dict] = []
                for index, raw in enumerate(raw_notices, start=1):
                    try:
                        with db.begin_nested():
                            if raw.get("source_type") == "staff_directory":
                                # 전체 동기화 뒤 정적 원문 보관 정책이 연락처 원문을
                                # 누락 자료로 오인해 archive하지 않도록 수집 ID도 기록한다.
                                seen.add(str(raw["source_id"]))
                                history.updated_count += 1
                            else:
                                notice, state = processor.upsert(
                                    raw, allow_external_enqueue=settings.codex_enrichment_enabled,
                                )
                                seen.add(notice.source_id)
                                if state == "new":
                                    history.new_count += 1
                                elif state == "updated":
                                    history.updated_count += 1
                                else:
                                    history.skipped_count += 1
                    except Exception as exc:
                        history.failed_count += 1
                        processing_failures.append({
                            "sourceId": str(raw.get("source_id") or "unknown"),
                            "reason": type(exc).__name__, "message": str(exc)[:300],
                        })
                    history.processed_count = index
                    history.phase_current = index
                    if index % 10 == 0:
                        db.commit()
                        db.refresh(history)
                if seen and crawler.listing_complete and crawler.profile == "full":
                    cutoff = datetime.now(timezone.utc) - relativedelta(months=settings.crawler_months)
                    for notice in db.scalars(select(Notice).where(
                        Notice.source_id.not_in(seen), Notice.is_archived.is_(False),
                        Notice.published_at >= cutoff, Notice.source_type == "official_notice",
                    )):
                        notice.is_archived = True
                    if not crawler.failures:
                        for notice in db.scalars(select(Notice).where(
                            Notice.source_id.not_in(seen), Notice.is_archived.is_(False),
                            Notice.source_type.in_({
                                "academic_guide", "official_faq", "staff_directory", "scholarship_guide",
                                "student_service", "university_catalog", "dormitory_guide", "international_guide",
                                "university_regulation",
                                "library_guide",
                            }),
                        )):
                            notice.is_archived = True
                source_failures = list(crawler.failures)
                history.failed_count += len(source_failures)
                all_failures = [*source_failures, *processing_failures]
                if all_failures:
                    history.error_message = json.dumps(all_failures[:50], ensure_ascii=False)
                    history.phase = "completed_with_failures"
                else:
                    history.phase = "completed"
                history.phase_current = history.total_found
                history.phase_total = history.total_found
                history.finished_at = datetime.now(timezone.utc)
                db.commit()
            except Exception as exc:
                db.rollback()
                history = db.get(CrawlHistory, history_id)
                if history:
                    history.error_message = str(exc)[:2000]
                    history.phase = "failed"
                    history.finished_at = datetime.now(timezone.utc)
                    db.commit()
    finally:
        _crawl_lock.release()


def run_reindex(history_id: int) -> None:
    """웹사이트를 다시 읽지 않고 저장된 원문으로 구조화·임베딩만 갱신한다."""
    if not _crawl_lock.acquire(blocking=False):
        with SessionLocal() as db:
            history = db.get(CrawlHistory, history_id)
            if history:
                history.error_message = "다른 수집 또는 재인덱싱 작업이 이미 실행 중입니다."
                history.phase = "failed"
                history.finished_at = datetime.now(timezone.utc)
                db.commit()
        return
    try:
        with SessionLocal() as db:
            notice_ids = list(db.scalars(
                select(Notice.id).where(Notice.is_archived.is_(False)).order_by(Notice.id)
            ))
            history = db.get(CrawlHistory, history_id)
            history.total_found = len(notice_ids)
            history.phase = "reindexing"
            history.phase_current = 0
            history.phase_total = len(notice_ids)
            db.commit()

        external_provider = settings.notice_structuring_provider.lower() in {"codex", "openai"}
        if external_provider:
            queued = 0
            failed = 0
            for index, notice_id in enumerate(notice_ids, start=1):
                with SessionLocal() as item_db:
                    try:
                        notice = item_db.get(Notice, notice_id)
                        if notice:
                            NoticeProcessor(item_db).enqueue_codex_enrichment(notice, force=False)
                            item_db.commit()
                            queued += 1
                    except Exception:
                        item_db.rollback()
                        failed += 1
                with SessionLocal() as progress_db:
                    history = progress_db.get(CrawlHistory, history_id)
                    history.phase = "enrichment_queueing"
                    history.phase_current = index
                    history.processed_count = index
                    progress_db.commit()
            with SessionLocal() as finish_db:
                history = finish_db.get(CrawlHistory, history_id)
                history.updated_count = queued
                history.failed_count = failed
                history.phase = "enrichment_queued"
                history.finished_at = datetime.now(timezone.utc)
                finish_db.commit()
            return

        # 한 문서 실패가 다음 문서의 SQLAlchemy 세션 상태를 오염시키지 않도록
        # 문서마다 짧은 트랜잭션을 사용한다.
        shared_ai = AIService()
        for index, notice_id in enumerate(notice_ids, start=1):
            succeeded = False
            with SessionLocal() as item_db:
                try:
                    notice = item_db.get(Notice, notice_id)
                    if notice:
                        NoticeProcessor(item_db, ai=shared_ai).process(notice, notice.content_links)
                        item_db.commit()
                        succeeded = True
                except Exception:
                    item_db.rollback()
            with SessionLocal() as progress_db:
                history = progress_db.get(CrawlHistory, history_id)
                if succeeded:
                    history.updated_count += 1
                else:
                    history.failed_count += 1
                history.processed_count = index
                history.phase_current = index
                progress_db.commit()

        with SessionLocal() as db:
            history = db.get(CrawlHistory, history_id)
            history.phase = "completed"
            history.finished_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            history = db.get(CrawlHistory, history_id)
            if history:
                history.error_message = str(exc)[:2000]
                history.phase = "failed"
                history.finished_at = datetime.now(timezone.utc)
                db.commit()
    finally:
        _crawl_lock.release()
