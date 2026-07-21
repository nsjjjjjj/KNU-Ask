from __future__ import annotations

import hashlib
import ipaddress
import io
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Notice
from app.schemas import QueryPlan
from app.utils.text import normalize_text


logger = logging.getLogger(__name__)
MAX_SOURCE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class OfficialSource:
    url: str
    title: str
    content: str
    content_hash: str
    fetched_at: datetime
    content_type: str = "text/html"
    extraction_status: str = "complete"

    def sanitized(self) -> dict:
        return {
            "url": self.url,
            "title": self.title[:300],
            "content": self.content[:18000],
            "contentHash": self.content_hash,
            "fetchedAt": self.fetched_at.isoformat(),
            "contentType": self.content_type,
            "extractionStatus": self.extraction_status,
        }


def is_allowed_school_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        host = parsed.hostname.rstrip(".").lower()
        allowed = any(host == domain or host.endswith(f".{domain}") for domain in settings.on_demand_allowed_domains)
        if not allowed:
            return False
        try:
            address = ipaddress.ip_address(host)
            return not (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved)
        except ValueError:
            pass
        # DNS rebinding 방어: 해석된 주소 중 하나라도 내부망이면 요청하지 않는다.
        for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
            address = ipaddress.ip_address(result[4][0])
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
                return False
        return True
    except (OSError, ValueError):
        return False


def _fact_terms(name: str) -> tuple[str, ...]:
    return {
        "totalCredits": ("졸업이수학점", "졸업 학점", "총 이수학점", "130학점"),
        "majorCredits": ("전공 이수", "전공학점", "전공 학점"),
        "generalEducationCredits": ("교양 이수", "교양학점", "교양 학점"),
        "applicationPeriod": ("신청 기간", "신청기간", "접수 기간", "마감"),
        "procedure": ("신청 방법", "신청방법", "신청 절차", "제출"),
        "requiredDocuments": ("제출 서류", "제출서류", "구비서류", "준비물"),
        "departmentContact": ("문의", "담당", "연락처", "전화"),
        "eligibility": ("신청 대상", "지원 대상", "자격", "조건"),
        "credits": ("학점",),
        "leaveDuration": ("최대 휴학", "휴학 가능 기간", "몇 학기", "몇 년", "휴학횟수"),
    }.get(name, (name,))


def missing_required_facts(plan: QueryPlan, sources: list[OfficialSource]) -> list[str]:
    text = normalize_text(" ".join(source.content for source in sources)).casefold()
    return [name for name in plan.required_facts if not any(term.casefold() in text for term in _fact_terms(name))]


class SchoolSourceGateway:
    """Codex에 노출할 수 있는 GET 전용 공식 학교 자료 경계."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.search_attempts = 0
        self.checked_urls = 0
        self.timed_out = False

    def _catalog_urls(self, plan: QueryPlan) -> list[str]:
        terms = [term for term in [
            plan.department, plan.college, plan.sub_category, *plan.search_terms, *plan.keywords[:4],
        ] if term]
        if not terms:
            return []
        clauses = []
        for term in terms[:8]:
            pattern = f"%{normalize_text(term)}%"
            clauses.extend((Notice.title.ilike(pattern), Notice.content.ilike(pattern)))
        rows = self.db.scalars(select(Notice).where(
            Notice.is_archived.is_(False), or_(*clauses),
        ).order_by(Notice.source_priority.desc(), Notice.published_at.desc()).limit(24)).all()
        return [row.source_url for row in rows if is_allowed_school_url(row.source_url)]

    def _search_listing(self, term: str) -> list[str]:
        if self.search_attempts >= settings.on_demand_max_searches:
            return []
        self.search_attempts += 1
        if not is_allowed_school_url(settings.notice_list_url):
            return []
        try:
            response = requests.get(
                settings.notice_list_url,
                params={"searchType": "ttl", "searchValue": term},
                headers={"User-Agent": "KNU-Ask-OnDemand/1.0"},
                timeout=min(settings.on_demand_page_timeout_seconds, 5.0),
            )
            response.raise_for_status()
            if not is_allowed_school_url(response.url):
                return []
            soup = BeautifulSoup(response.text, "html.parser")
            urls = []
            for anchor in soup.select("a[href]"):
                href = urljoin(response.url, anchor.get("href", ""))
                label = normalize_text(anchor.get_text(" ", strip=True))
                if href and is_allowed_school_url(href) and (not term or any(token in label for token in term.split())):
                    urls.append(href)
            return list(dict.fromkeys(urls))[:12]
        except requests.RequestException as exc:
            logger.info("official search listing unavailable attempt=%s error=%s", self.search_attempts, type(exc).__name__)
            return []

    def fetch_school_source(self, url: str) -> OfficialSource | None:
        if not is_allowed_school_url(url):
            return None
        self.checked_urls += 1
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "KNU-Ask-OnDemand/1.0", "Accept": "text/html,application/pdf"},
                timeout=min(settings.on_demand_page_timeout_seconds, 5.0),
            )
            if not response.ok or not is_allowed_school_url(response.url):
                return None
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            declared = int(response.headers.get("Content-Length", "0") or 0)
            if declared > MAX_SOURCE_BYTES or len(response.content) > MAX_SOURCE_BYTES:
                return None
            if content_type in {"text/html", "application/xhtml+xml", ""}:
                soup = BeautifulSoup(response.content, "html.parser")
                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()
                title = normalize_text(soup.title.get_text(" ") if soup.title else response.url)
                content = normalize_text(soup.get_text(" ", strip=True))
                status = "complete"
            elif content_type == "application/pdf" or response.url.lower().endswith(".pdf"):
                reader = PdfReader(io.BytesIO(response.content))
                content = normalize_text(" ".join((page.extract_text() or "") for page in reader.pages[:40]))
                title = response.url.rsplit("/", 1)[-1]
                status = "complete" if content else "deferred"
            else:
                return None
            if len(content) < 40:
                return None
            return OfficialSource(
                url=response.url,
                title=title[:300],
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                fetched_at=datetime.now(timezone.utc),
                content_type=content_type or "text/html",
                extraction_status=status,
            )
        except Exception as exc:
            logger.info("official source fetch failed error=%s", type(exc).__name__)
            return None

    def search_school_sources(
        self, plan: QueryPlan, *, timeout_seconds: float | None = None,
    ) -> list[OfficialSource]:
        started = time.monotonic()
        deadline = started + min(timeout_seconds or settings.on_demand_timeout_seconds, 30.0)
        fallback_term = normalize_text(" ".join(filter(None, [
            str(plan.admission_year or plan.academic_year or ""),
            plan.college, plan.department, plan.sub_category,
        ])))
        exact_terms = plan.search_terms[:1] or ([fallback_term] if fallback_term else [])
        expanded = normalize_text(" ".join(filter(None, [
            plan.sub_category, plan.college, plan.department, "대학요람 학사안내",
        ])))
        candidates = self._catalog_urls(plan)
        for term in [*exact_terms, expanded]:
            if time.monotonic() >= deadline or self.search_attempts >= settings.on_demand_max_searches:
                break
            if term:
                candidates.extend(self._search_listing(term))
        urls = list(dict.fromkeys(url for url in candidates if is_allowed_school_url(url)))
        urls.sort(key=lambda url: urlparse(url).path.lower().endswith((".pdf", ".png", ".jpg", ".jpeg")))
        urls = urls[:settings.on_demand_max_urls]
        collected: list[OfficialSource] = []
        for batch in (urls[:3], urls[3:6]):
            if not batch or time.monotonic() >= deadline:
                break
            with ThreadPoolExecutor(max_workers=min(3, len(batch))) as executor:
                futures = {executor.submit(self.fetch_school_source, url): url for url in batch}
                try:
                    for future in as_completed(futures, timeout=max(0.1, deadline - time.monotonic())):
                        source = future.result()
                        if source:
                            collected.append(source)
                except FuturesTimeoutError:
                    self.timed_out = True
                    for future in futures:
                        future.cancel()
            if collected and not missing_required_facts(plan, collected):
                break
        self.timed_out = time.monotonic() >= deadline
        return collected
