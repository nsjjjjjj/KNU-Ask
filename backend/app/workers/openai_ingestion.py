from __future__ import annotations

import argparse
import time

import requests

from app.core.config import settings
from app.services.ai.openai_enrichment import OpenAIEnrichmentService


API_BASE = "http://localhost:8000/api"
HEADERS = {"X-Admin-Token": settings.admin_api_token, "Content-Type": "application/json"}


def _request(path: str, method: str = "GET", payload: dict | None = None):
    response = requests.request(method, f"{API_BASE}{path}", headers=HEADERS, json=payload, timeout=900)
    if response.status_code == 204:
        return None
    response.raise_for_status()
    return response.json()


def run_once() -> bool:
    job = _request("/enrichment/jobs/next?worker=openai")
    if not job:
        return False
    job_id = int(job["jobId"])
    try:
        structured = OpenAIEnrichmentService().enrich(job)
        _request(
            f"/enrichment/jobs/{job_id}/complete", "POST",
            structured.model_dump(mode="json", by_alias=True),
        )
    except Exception as exc:
        _request(f"/enrichment/jobs/{job_id}/fail", "POST", {"error": str(exc)[:2000]})
        raise
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=15)
    args = parser.parse_args()
    while True:
        worked = run_once()
        if not args.watch:
            return 0
        time.sleep(1 if worked else max(args.interval, 5))


if __name__ == "__main__":
    raise SystemExit(main())
