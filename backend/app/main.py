from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.core.config import settings
from app.db.init_db import main as init_db
from app.services.crawler.jobs import (
    run_scheduled_crawl, run_scheduled_daily_crawl, run_scheduled_full_crawl,
)


scheduler = BackgroundScheduler(timezone="Asia/Seoul")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if settings.crawler_schedule_enabled and not scheduler.running:
        scheduler.add_job(
            run_scheduled_crawl, "interval", minutes=settings.crawler_schedule_minutes,
            next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2),
            id="incremental-crawler", replace_existing=True, max_instances=1, coalesce=True,
        )
        scheduler.add_job(
            run_scheduled_daily_crawl, "cron", hour=settings.crawler_daily_schedule_hour, minute=15,
            id="daily-static-crawler", replace_existing=True, max_instances=1, coalesce=True,
        )
        scheduler.add_job(
            run_scheduled_full_crawl, "cron", day_of_week=settings.crawler_full_schedule_day,
            hour=settings.crawler_full_schedule_hour, minute=30,
            id="full-crawler", replace_existing=True, max_instances=1, coalesce=True,
        )
        scheduler.start()
    try:
        yield
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Token"],
)
app.include_router(router)
