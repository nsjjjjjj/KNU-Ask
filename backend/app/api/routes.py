from datetime import datetime, timedelta, timezone
import io
import secrets

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Response
from PIL import Image, UnidentifiedImageError
from sqlalchemy import desc, or_, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models import CrawlHistory, DataGap, FAQ, Feedback, Notice, NoticeMetadata, OnDemandCodexJob, ProcessingJob
from app.repositories import NoticeRepository
from app.schemas import (
    CATEGORIES, CategoryResponse, ChatRequest, ChatResponse, CrawlerStatus,
    CodexJobFailure, DataGapUpdate, FAQResponse, FeedbackRequest, FeedbackResponse,
    NoticeSummary, StructuredNotice,
)
from app.services.chat import ChatService, build_action_guide_response
from app.services.data_gaps import record_gap
from app.services.ai import AIService
from app.services.crawler.jobs import create_crawl_history, run_crawler as execute_crawler, run_reindex
from app.services.notice_status import effective_status, effective_status_label
from app.services.on_demand.sources import is_allowed_school_url
from app.services.on_demand.codex import CodexEvidenceResult, SYSTEM_PROMPT
from app.services.processing import NoticeProcessor
from app.utils.text import normalize_text, sensitive_input_types


router = APIRouter(prefix="/api")


def require_admin(x_admin_token: str | None = Header(None)) -> None:
    configured = (settings.admin_api_token or "").strip()
    insecure_example = (
        not configured
        or len(configured) < 32
        or configured.lower().startswith(("replace-", "change-", "example-", "your-"))
    )
    if insecure_example:
        raise HTTPException(503, "관리자 API 토큰이 안전하게 설정되지 않았습니다.")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, configured):
        raise HTTPException(403, "관리자 권한이 필요합니다.")


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    ai = AIService()
    return {
        "status": "ok", "service": settings.app_name, "mockAI": ai.chat_provider == "rules",
        "chatProvider": ai.chat_provider, "chatModel": ai.chat_model_name,
        "localAIComplexQueriesOnly": settings.local_ai_complex_queries_only,
        "chatContext": settings.ollama_chat_num_ctx,
        "structuringContext": settings.ollama_structuring_num_ctx,
        "noticeStructuringProvider": settings.notice_structuring_provider,
        "codexEnrichmentEnabled": settings.codex_enrichment_enabled,
        "onDemandSearchEnabled": settings.on_demand_search_enabled,
        "onDemandLiveSearchEnabled": settings.on_demand_live_search_enabled,
        "onDemandCodexEnabled": settings.on_demand_codex_enabled,
        "onDemandCodexProvider": settings.on_demand_codex_provider,
        "openAIEnrichmentModel": settings.openai_enrichment_model if settings.notice_structuring_provider == "openai" else None,
        "embeddingProvider": ai.embedding_provider, "embeddingModel": ai.embedding_model_name,
        "crawlerScheduleEnabled": settings.crawler_schedule_enabled,
        "crawlerScheduleMinutes": settings.crawler_schedule_minutes,
        "crawlerDailyScheduleHour": settings.crawler_daily_schedule_hour,
        "crawlerFullScheduleHour": settings.crawler_full_schedule_hour,
        "crawlerFullScheduleDay": settings.crawler_full_schedule_day,
    }


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db)):
    message = normalize_text(request.message)
    if not message or len(message) > settings.max_input_length:
        raise HTTPException(422, f"질문은 1자 이상 {settings.max_input_length}자 이하로 입력해주세요.")
    sensitive = sensitive_input_types(message)
    if sensitive:
        raise HTTPException(422, f"개인정보로 보이는 항목({', '.join(sensitive)})을 제거한 뒤 다시 질문해 주세요.")
    return ChatService(db).answer(message, request.session_id, request.selected_category)


@router.delete("/chat/sessions/{session_id}")
def end_chat(session_id: str, db: Session = Depends(get_db)):
    ChatService(db).end_session(session_id)
    return {"status": "success", "message": "문의가 종료되었습니다. 서버에 대화 원문은 저장되지 않습니다."}


@router.get("/categories")
def categories():
    return {"categories": CATEGORIES[:7]}


@router.get("/categories/{category}/notices", response_model=CategoryResponse)
def category_notices(category: str, db: Session = Depends(get_db)):
    if category not in CATEGORIES[:7]:
        raise HTTPException(404, "존재하지 않는 문의 분야입니다.")
    rows = db.scalars(
        select(Notice).join(NoticeMetadata).where(
            Notice.is_archived.is_(False), Notice.ai_processed.is_(True),
            NoticeMetadata.category == category,
        # 종료 공지를 건너뛴 뒤에도 최대 20건을 채울 수 있도록 후보는
        # 조금 넓게 가져온다.
        ).order_by(desc(Notice.published_at), desc(Notice.source_priority)).limit(100)
    ).all()
    notices = []
    for row in rows:
        current_status = effective_status(row)
        if current_status == "expired":
            continue
        notices.append(NoticeSummary(
            id=row.id, title=row.title, category=row.metadata_record.category,
            published_at=row.published_at, notice_status=current_status,
            status_label=effective_status_label(row, current_status), source_url=row.source_url,
        ))
        if len(notices) >= 20:
            break
    return CategoryResponse(
        category=category,
        notices=notices,
        message=f"{category} 분야 공지 {len(notices)}건을 찾았습니다." if notices else f"{category} 분야의 현재 공지를 찾지 못했습니다.",
    )


@router.get("/notices/search", response_model=CategoryResponse)
def search_notices(q: str = Query("", max_length=100), category: str | None = None, db: Session = Depends(get_db)):
    query = normalize_text(q)
    stmt = select(Notice).outerjoin(NoticeMetadata).where(
        Notice.is_archived.is_(False), Notice.ai_processed.is_(True),
    )
    if category:
        stmt = stmt.where(NoticeMetadata.category == category)
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(or_(
            Notice.title.ilike(pattern), Notice.content.ilike(pattern), Notice.attachment_text.ilike(pattern),
            NoticeMetadata.search_text.ilike(pattern),
        ))
    rows = db.scalars(stmt.order_by(desc(Notice.source_priority), desc(Notice.published_at)).limit(20)).all()
    notices = []
    for row in rows:
        metadata = row.metadata_record
        current_status = effective_status(row)
        notices.append(NoticeSummary(
            id=row.id, title=row.title, category=metadata.category if metadata else None,
            published_at=row.published_at, notice_status=current_status,
            status_label=effective_status_label(row, current_status), source_url=row.source_url,
        ))
    return CategoryResponse(
        category=category or "전체",
        notices=notices[:20],
        message=f"공지 {min(len(notices), 20)}건을 찾았습니다." if notices else "검색 조건에 맞는 공지를 찾지 못했습니다.",
    )


@router.get("/faqs", response_model=list[FAQResponse])
def faqs(db: Session = Depends(get_db)):
    rows = db.scalars(select(FAQ).where(FAQ.is_active.is_(True)).order_by(FAQ.id)).all()
    return [FAQResponse(id=row.id, question=row.question, category=row.category) for row in rows]


@router.post("/feedback", response_model=FeedbackResponse, status_code=202)
def feedback(request: FeedbackRequest, db: Session = Depends(get_db)):
    db.add(Feedback(
        answer_id=request.answer_id,
        resolved=request.resolved,
        reason=request.reason,
        source_ids=request.source_ids,
        response_status=request.response_status,
    ))
    db.commit()
    if not request.resolved:
        field_by_reason = {
            "incorrect": "structured_answer", "outdated": "freshness",
            "misunderstood": "query_understanding", "insufficient": "search_evidence",
            "needs_staff": "staff_escalation",
        }
        source_ids = request.source_ids or [None]
        for source_id in source_ids:
            notice = db.get(Notice, source_id) if source_id else None
            record_gap(
                db, gap_type=f"user_feedback_{request.reason}",
                field_name=field_by_reason.get(request.reason), notice=notice,
                context={"responseStatus": request.response_status}, detected_automatically=False,
            )
    return FeedbackResponse()


@router.get("/data-gaps", dependencies=[Depends(require_admin)])
def data_gaps(
    status: str = Query("open", pattern="^(open|reopened|resolved|ignored|all)$"),
    limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
):
    stmt = select(DataGap, Notice).outerjoin(Notice, Notice.id == DataGap.notice_id)
    if status != "all":
        stmt = stmt.where(DataGap.status == status)
    rows = db.execute(stmt.order_by(desc(DataGap.occurrence_count), desc(DataGap.last_seen_at)).limit(limit)).all()
    return {"gaps": [{
        "id": gap.id, "gapType": gap.gap_type, "fieldName": gap.field_name,
        "category": gap.category, "queryIntent": gap.query_intent,
        "occurrenceCount": gap.occurrence_count, "status": gap.status,
        "detectedAutomatically": gap.detected_automatically,
        "firstSeenAt": gap.first_seen_at, "lastSeenAt": gap.last_seen_at,
        "context": gap.context, "resolutionNote": gap.resolution_note,
        "notice": {"id": notice.id, "title": notice.title, "sourceUrl": notice.source_url} if notice else None,
    } for gap, notice in rows]}


@router.patch("/data-gaps/{gap_id}", dependencies=[Depends(require_admin)])
def update_data_gap(gap_id: int, request: DataGapUpdate, db: Session = Depends(get_db)):
    gap = db.get(DataGap, gap_id)
    if not gap:
        raise HTTPException(404, "데이터 누락 항목을 찾을 수 없습니다.")
    gap.status = request.status
    gap.resolution_note = request.resolution_note
    gap.resolved_at = datetime.now(timezone.utc) if request.status == "resolved" else None
    db.commit()
    return {"status": "success", "id": gap.id, "gapStatus": gap.status}


@router.post("/crawler/run", response_model=CrawlerStatus, dependencies=[Depends(require_admin)])
def run_crawler(
    background_tasks: BackgroundTasks,
    mode: str = Query(default="incremental", pattern="^(incremental|daily|pilot|full)$"),
    db: Session = Depends(get_db),
):
    history_id = create_crawl_history(phase=f"{mode}_queued")
    if history_id is None:
        raise HTTPException(409, "크롤링 작업이 이미 실행 중입니다.")
    history = db.get(CrawlHistory, history_id)
    background_tasks.add_task(
        execute_crawler, history_id, mode == "incremental", mode,
    )
    return CrawlerStatus(id=history_id, status="running", phase=f"{mode}_queued", started_at=history.started_at)


@router.get("/crawler/preview", dependencies=[Depends(require_admin)])
def crawler_preview(
    mode: str = Query(default="pilot", pattern="^(incremental|daily|pilot|full)$"),
    db: Session = Depends(get_db),
):
    active = db.scalars(select(Notice).where(Notice.is_archived.is_(False))).all()
    estimates = {
        "incremental": {"maximumDocuments": 200, "maximumAIJobs": 200, "sources": ["official_notice", "event"]},
        "daily": {"maximumDocuments": 5, "maximumAIJobs": 5, "sources": ["academic_calendar"]},
        "pilot": {
            # 전화번호부 373건과 최근 한 달 행사량을 포함한 보수적 상한이다.
            # 전화번호부는 별도 정규화 테이블로 동기화하므로 AI 작업에서 제외된다.
            "maximumDocuments": 800,
            "maximumAIJobs": 427,
            "sources": [
                "event", "academic_guide", "official_faq", "scholarship_guide",
                "university_catalog", "career_program", "dormitory_guide",
                "international_guide", "student_service", "staff_directory",
            ],
        },
        "full": {
            "maximumDocuments": 5000,
            "maximumAIJobs": 4600,
            "sources": [
                "official_notice", "event", "academic_guide", "official_faq",
                "scholarship_guide", "university_catalog", "career_program",
                "student_service", "dormitory_guide", "international_guide",
                "library_guide",
                "university_regulation", "international_notice", "dormitory_notice",
                "student_service_notice", "staff_directory",
            ],
        },
    }
    estimate = estimates[mode]
    # 실제 신규 여부는 원격 목록을 읽기 전에는 알 수 없다. 최대 건수와
    # 기존 공개 인덱스를 기준으로 보수적인 상한만 제공한다.
    return {
        "mode": mode,
        **estimate,
        "currentActiveDocuments": len(active),
        "estimatedNewOrChangedUpperBound": estimate["maximumDocuments"],
        "estimatedAIJobsUpperBound": estimate["maximumAIJobs"],
        "estimatedInputTokensUpperBound": estimate["maximumAIJobs"] * 4000,
        "unchangedDocumentsSkipAI": True,
        "publicIndexKeptUntilSuccess": True,
    }


@router.post("/index/rebuild", response_model=CrawlerStatus, dependencies=[Depends(require_admin)])
def rebuild_index(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """저장 원문은 그대로 두고 구조화 결과·문단·임베딩만 새 버전으로 재생성한다."""
    if (
        settings.notice_structuring_provider.lower() in {"codex", "openai"}
        and not settings.codex_enrichment_enabled
    ):
        raise HTTPException(409, "일괄 Codex enrichment가 비활성화되어 있습니다.")
    history_id = create_crawl_history(phase="reindex_queued")
    if history_id is None:
        raise HTTPException(409, "수집 또는 재인덱싱 작업이 이미 실행 중입니다.")
    history = db.get(CrawlHistory, history_id)
    background_tasks.add_task(run_reindex, history_id)
    return CrawlerStatus(id=history_id, status="running", phase="reindex_queued", started_at=history.started_at)


@router.get("/index/rebuild/preview", dependencies=[Depends(require_admin)])
def rebuild_index_preview(db: Session = Depends(get_db)):
    notices = db.scalars(select(Notice).where(Notice.is_archived.is_(False))).all()
    characters = sum(len(item.title or "") + len(item.content or "") + len(item.attachment_text or "") for item in notices)
    # 한국어·JSON 스키마 오버헤드를 포함한 보수적인 입력 토큰 근사치다.
    estimated_input_tokens = int(characters / 2.2) + len(notices) * 1800
    return {
        "provider": settings.notice_structuring_provider.lower(),
        "noticeCount": len(notices),
        "estimatedInputTokens": estimated_input_tokens,
        "willUseExternalQueue": settings.notice_structuring_provider.lower() in {"codex", "openai"},
        "publicIndexKeptUntilSuccess": True,
    }


@router.get("/crawler/status", response_model=CrawlerStatus, dependencies=[Depends(require_admin)])
def crawler_status(db: Session = Depends(get_db)):
    history = db.scalar(select(CrawlHistory).order_by(desc(CrawlHistory.id)).limit(1))
    if not history:
        return CrawlerStatus(status="idle")
    status = "failed" if history.error_message else ("completed" if history.finished_at else "running")
    return CrawlerStatus(
        id=history.id, status=status, started_at=history.started_at, finished_at=history.finished_at,
        total_found=history.total_found, new_count=history.new_count, updated_count=history.updated_count,
        skipped_count=history.skipped_count, failed_count=history.failed_count,
        processed_count=history.processed_count, phase=history.phase,
        phase_current=history.phase_current, phase_total=history.phase_total,
        error_message=history.error_message,
    )


@router.post("/notices/{notice_id}/reprocess", dependencies=[Depends(require_admin)])
def reprocess_notice(notice_id: int, db: Session = Depends(get_db)):
    notice = NoticeRepository(db).by_id(notice_id)
    if not notice:
        raise HTTPException(404, "공지를 찾을 수 없습니다.")
    processor = NoticeProcessor(db)
    processor.refresh_attachments(notice)
    if settings.codex_enrichment_enabled and settings.notice_structuring_provider.lower() in {"codex", "openai"}:
        job = processor.enqueue_codex_enrichment(notice, force=True)
        db.commit()
        return {"status": "queued", "noticeId": notice.id, "jobId": job.id}
    processor.process(notice)
    db.commit()
    return {"status": "success", "noticeId": notice.id, "processedAt": notice.processed_at}


@router.post("/notices/{notice_id}/codex-queue", dependencies=[Depends(require_admin)])
def queue_codex_notice(notice_id: int, db: Session = Depends(get_db)):
    notice = NoticeRepository(db).by_id(notice_id)
    if not notice:
        raise HTTPException(404, "공지를 찾을 수 없습니다.")
    processor = NoticeProcessor(db)
    processor.refresh_attachments(notice)
    job = processor.enqueue_codex_enrichment(notice, force=True)
    db.commit()
    return {"status": "queued", "jobId": job.id, "noticeId": notice.id}


@router.get("/enrichment/schema", dependencies=[Depends(require_admin)])
@router.get("/codex/schema", dependencies=[Depends(require_admin)])
def codex_schema():
    return StructuredNotice.model_json_schema(by_alias=True)


@router.get("/enrichment/jobs/next", dependencies=[Depends(require_admin)])
@router.get("/codex/jobs/next", dependencies=[Depends(require_admin)])
def next_codex_job(worker: str | None = None, db: Session = Depends(get_db)):
    if worker and not settings.codex_enrichment_enabled:
        # pending/running 레코드는 변경하지 않아 체크포인트와 큐를 보존한다.
        return Response(status_code=204)
    configured_provider = settings.notice_structuring_provider.lower()
    if worker and worker != configured_provider:
        return Response(status_code=204)
    stale_before = datetime.now(timezone.utc) - timedelta(minutes=30)
    stale_jobs = db.scalars(select(ProcessingJob).where(
        ProcessingJob.job_type == "codex_enrichment",
        ProcessingJob.status == "running",
        ProcessingJob.started_at < stale_before,
    )).all()
    for stale in stale_jobs:
        stale.status = "pending"
        stale.started_at = None
        stale.error_message = "stale job reclaimed"
    job = db.scalar(select(ProcessingJob).where(
        ProcessingJob.job_type == "codex_enrichment",
        ProcessingJob.status == "pending",
    ).order_by(ProcessingJob.id).with_for_update(skip_locked=True))
    if not job:
        db.commit()
        return Response(status_code=204)
    notice = NoticeRepository(db).by_id(job.notice_id)
    if not notice or notice.is_archived:
        job.status = "cancelled"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return Response(status_code=204)
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    job.error_message = None
    db.commit()
    meta = notice.metadata_record
    return {
        "jobId": job.id,
        "provider": configured_provider,
        "instructions": AIService.notice_structuring_prompt(),
        "notice": {
            "id": notice.id, "title": notice.title, "content": notice.content,
            "attachmentText": notice.attachment_text, "publishedAt": notice.published_at,
            "sourceUrl": notice.source_url, "sourceType": notice.source_type,
            "departmentName": notice.department_name,
            "sourceMetadata": notice.source_metadata or {},
            "verifiedContentLinks": notice.content_links or [],
            "attachments": notice.attachment_manifest or [],
            "localExtractionStatus": notice.extraction_status,
            "previousStructured": {
                "category": meta.category, "subCategory": meta.sub_category,
                "applicationStart": meta.application_start, "applicationEnd": meta.application_end,
            } if meta and notice.ai_processed else None,
        },
    }


@router.post("/enrichment/jobs/{job_id}/complete", dependencies=[Depends(require_admin)])
@router.post("/codex/jobs/{job_id}/complete", dependencies=[Depends(require_admin)])
def complete_codex_job(job_id: int, structured: StructuredNotice, db: Session = Depends(get_db)):
    job = db.get(ProcessingJob, job_id)
    if not job or job.job_type != "codex_enrichment":
        raise HTTPException(404, "Codex 보강 작업을 찾을 수 없습니다.")
    notice = NoticeRepository(db).by_id(job.notice_id)
    if not notice:
        raise HTTPException(404, "공지를 찾을 수 없습니다.")
    try:
        processor = NoticeProcessor(db)
        grounded = processor.ground_external_structured(notice, structured)
        processor.persist_structured(notice, grounded, notice.content_links or [])
        job.status = "completed"
        job.finished_at = datetime.now(timezone.utc)
        job.error_message = None
        db.commit()
    except Exception as exc:
        db.rollback()
        failed = db.get(ProcessingJob, job_id)
        failed.status = "failed"
        failed.retry_count += 1
        failed.finished_at = datetime.now(timezone.utc)
        failed.error_message = str(exc)[:2000]
        db.commit()
        raise HTTPException(500, "Codex 구조화 결과 저장에 실패했습니다.") from exc
    return {"status": "completed", "jobId": job.id, "noticeId": notice.id}


@router.post("/enrichment/jobs/{job_id}/fail", dependencies=[Depends(require_admin)])
@router.post("/codex/jobs/{job_id}/fail", dependencies=[Depends(require_admin)])
def fail_codex_job(job_id: int, request: CodexJobFailure, db: Session = Depends(get_db)):
    job = db.get(ProcessingJob, job_id)
    if not job or job.job_type != "codex_enrichment":
        raise HTTPException(404, "Codex 보강 작업을 찾을 수 없습니다.")
    job.status = "failed"
    job.retry_count += 1
    job.finished_at = datetime.now(timezone.utc)
    # complete 단계가 이미 구체적인 DB/임베딩 예외를 기록했다면 워커의
    # 포괄 오류로 덮어쓰지 않는다.
    if not job.error_message:
        job.error_message = request.error
    db.commit()
    return {"status": "failed", "jobId": job.id}


@router.get("/on-demand-codex/schema", dependencies=[Depends(require_admin)])
def on_demand_codex_schema():
    return CodexEvidenceResult.model_json_schema()


@router.get("/on-demand-codex/jobs/next", dependencies=[Depends(require_admin)])
def next_on_demand_codex_job(db: Session = Depends(get_db)):
    if not settings.on_demand_codex_enabled or settings.on_demand_codex_provider != "codex_exec":
        return Response(status_code=204)
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(minutes=5)
    for stale in db.scalars(select(OnDemandCodexJob).where(
        OnDemandCodexJob.status == "running",
        OnDemandCodexJob.started_at < stale_before,
    )):
        stale.status = "pending"
        stale.started_at = None
        stale.error_message = "stale job reclaimed"
    for expired in db.scalars(select(OnDemandCodexJob).where(
        OnDemandCodexJob.status == "pending",
        OnDemandCodexJob.expires_at <= now,
    )):
        expired.status = "cancelled"
        expired.finished_at = now
    job = db.scalar(select(OnDemandCodexJob).where(
        OnDemandCodexJob.status == "pending",
        OnDemandCodexJob.expires_at > now,
    ).order_by(OnDemandCodexJob.id).with_for_update(skip_locked=True))
    if not job:
        db.commit()
        return Response(status_code=204)
    job.status = "running"
    job.started_at = now
    job.error_message = None
    db.commit()
    return {
        "jobId": job.id,
        "instructions": SYSTEM_PROMPT,
        "queryPlan": job.query_plan,
        "sources": job.sources,
    }


@router.post("/on-demand-codex/jobs/{job_id}/complete", dependencies=[Depends(require_admin)])
def complete_on_demand_codex_job(
    job_id: int, result: CodexEvidenceResult, db: Session = Depends(get_db),
):
    job = db.get(OnDemandCodexJob, job_id)
    if not job:
        raise HTTPException(404, "온디맨드 Codex 작업을 찾을 수 없습니다.")
    job.result_payload = result.model_dump(mode="json")
    job.status = "completed"
    job.finished_at = datetime.now(timezone.utc)
    job.error_message = None
    db.commit()
    return {"status": "completed", "jobId": job.id}


@router.post("/on-demand-codex/jobs/{job_id}/fail", dependencies=[Depends(require_admin)])
def fail_on_demand_codex_job(
    job_id: int, request: CodexJobFailure, db: Session = Depends(get_db),
):
    job = db.get(OnDemandCodexJob, job_id)
    if not job:
        raise HTTPException(404, "온디맨드 Codex 작업을 찾을 수 없습니다.")
    job.status = "failed"
    job.finished_at = datetime.now(timezone.utc)
    job.error_message = request.error[:500]
    db.commit()
    return {"status": "failed", "jobId": job.id}


@router.get("/notices/{notice_id}/media/{media_index}")
def notice_media(notice_id: int, media_index: int, db: Session = Depends(get_db)):
    """학교 파일 서버의 세션 보호 이미지를 검증한 뒤 같은 출처로 중계한다."""
    notice = NoticeRepository(db).by_id(notice_id)
    if not notice or media_index < 0 or media_index >= len(notice.attachment_manifest or []):
        raise HTTPException(404, "이미지를 찾을 수 없습니다.")
    item = notice.attachment_manifest[media_index]
    media_url = str(item.get("url") or "")
    method = str(item.get("extractionMethod") or item.get("extraction_method") or "")
    name = normalize_text(str(item.get("name") or ""))
    if (
        not is_allowed_school_url(media_url)
        or not is_allowed_school_url(notice.source_url)
        or (method != "image_ocr" and "본문 이미지" not in name and "/image.do?" not in media_url)
    ):
        raise HTTPException(404, "이미지를 찾을 수 없습니다.")

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; KNU-Ask/1.0)"})
    try:
        source_response = session.get(notice.source_url, timeout=10)
        source_response.raise_for_status()
        if not is_allowed_school_url(source_response.url):
            raise HTTPException(502, "공식 공지 주소가 허용된 범위를 벗어났습니다.")
        upstream = session.get(
            media_url, timeout=15, headers={"Referer": notice.source_url}, stream=True,
        )
        upstream.raise_for_status()
        if not is_allowed_school_url(upstream.url):
            raise HTTPException(502, "공식 이미지 주소가 허용된 범위를 벗어났습니다.")
        limit = 8 * 1024 * 1024
        length = int(upstream.headers.get("content-length") or 0)
        if length > limit:
            raise HTTPException(413, "표시할 수 있는 이미지 크기를 초과했습니다.")
        chunks = bytearray()
        for chunk in upstream.iter_content(64 * 1024):
            chunks.extend(chunk)
            if len(chunks) > limit:
                raise HTTPException(413, "표시할 수 있는 이미지 크기를 초과했습니다.")
        body = bytes(chunks)
    except requests.RequestException as exc:
        raise HTTPException(502, "공식 시간표 이미지를 불러오지 못했습니다.") from exc

    try:
        image = Image.open(io.BytesIO(body))
        image.verify()
        media_type = Image.MIME.get(image.format or "")
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(502, "공식 파일이 올바른 이미지가 아닙니다.") from exc
    if media_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        raise HTTPException(415, "지원하지 않는 이미지 형식입니다.")
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/notices/{notice_id}")
def notice_detail(notice_id: int, db: Session = Depends(get_db)):
    notice = NoticeRepository(db).by_id(notice_id)
    if not notice:
        raise HTTPException(404, "공지를 찾을 수 없습니다.")
    meta = notice.metadata_record
    action_guide = build_action_guide_response(notice)
    current_status = effective_status(notice)
    return {
        "id": notice.id, "sourceId": notice.source_id, "title": notice.title,
        "content": notice.content, "publishedAt": notice.published_at,
        "sourceUrl": notice.source_url, "noticeStatus": current_status,
        "statusLabel": effective_status_label(notice, current_status),
        "isArchived": notice.is_archived,
        "attachments": notice.attachment_manifest,
        "actionGuide": action_guide.model_dump(by_alias=True) if action_guide else None,
        "metadata": {
            "category": meta.category, "subCategory": meta.sub_category,
            "academicYear": meta.academic_year, "semester": meta.semester,
            "applicationStart": meta.application_start, "applicationEnd": meta.application_end,
            "eventStart": meta.event_start, "eventEnd": meta.event_end,
            "actionType": meta.action_type,
            "applicationMethod": meta.application_method,
            "applicationLocation": meta.application_location,
            "targetStudentTypes": meta.target_student_types,
            "targetGrades": meta.target_grades,
            "targetDepartments": meta.target_departments,
            "targetCampus": meta.target_campus,
            "eligibilityNotes": meta.eligibility_notes,
            "feeInformation": meta.fee_information,
            "capacity": meta.capacity,
            "selectionMethod": meta.selection_method,
            "resultAnnouncement": meta.result_announcement,
            "cancellationPolicy": meta.cancellation_policy,
            "benefits": meta.benefits,
            "creditsOrHours": meta.credits_or_hours,
            "importantDates": meta.important_dates,
            "additionalFacts": meta.additional_facts,
            "evidenceMap": meta.evidence_map,
            "department": {"name": meta.department_name, "contactPerson": meta.contact_person, "contactRole": meta.contact_role, "phone": meta.department_phone, "email": meta.department_email, "officeLocation": meta.department_office_location, "officeHours": meta.department_office_hours},
            "keywords": meta.keywords, "requiredDocuments": meta.required_documents,
        } if meta else None,
    }
