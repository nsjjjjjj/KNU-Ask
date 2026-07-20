from __future__ import annotations

import json
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta

from app.core.config import settings
from app.services.crawler.attachments import AttachmentExtractor
from app.utils.text import normalize_text


SAMPLE_PATH = Path(__file__).resolve().parents[2] / "data" / "sample_data.json"
ACADEMIC_GUIDE_ROOTS = [
    ("calendar", "학사일정", "02be162adc07170ec7ee034097d627e9", "교무팀"),
    ("registration", "등록안내", "2af7d58d42bb566f3d177204bfacd937", "재무회계팀"),
    ("refund", "등록금 반환", "b044668e4cdff06c9e979c1e6ebf535c", "재무회계팀"),
    ("classes", "수업·수강신청", "fd8c126ac0e81458620beb18302bc271", "교무팀"),
    ("credit-exchange", "학점교류", "b6cdaa2d20ed253e51964ec6c6aeba1e", "교무팀"),
    ("major", "전공", "f18a64130de18975bfc2127ba53ee768", "교무팀"),
    ("multiple-major", "다전공", "b2d1211af4999ac7a3ae1e11ad581860", "교무팀"),
    ("leave-return", "휴학", "12d2ee44cc4e95562f84a01bf953a054", "교무팀"),
    ("graduation", "졸업", "c5dc4b1d7b4dd402e5e6a7a8471eb55c", "교무팀"),
    ("academic-status", "학적", "41c4ba211ab06cbc003455e07441b4f8", "교무팀"),
    ("attendance", "전자출결", "86b86ff51a4c7d33a2cea85a3f4d8d40", "교무팀"),
    ("certificate", "증명서 발급", "b46b6e20bc53a0234ac9fc9a238b113a", "교무팀"),
    ("military", "학생병사", "3b97657335d025de913a940dc19fa6b8", "예비군연대"),
    ("reserve", "예비군", "246d562e295a939edc605190e2b0221e", "예비군연대"),
    ("startup-credit", "창업대체학점인정제", "a1ed5a7cd101b044997be8a2ff63b979", "창업교육센터"),
    ("startup-leave", "창업휴학", "07e3888786f407dd1940d83642a5788f", "창업교육센터"),
    ("manual", "대학생활 가이드 및 편람", "3d1345da241450c621fe02936aeb96e7", "교무팀"),
]

FAQ_ROOT = "https://web.kangnam.ac.kr/menu/12d2ee44cc4e95562f84a01bf953a054.do?encMenuSeq=69fb0886aeb8aa9d6162c7145479945f"
DIRECTORY_URL = "https://web.kangnam.ac.kr/menu/d32b0ae4b98a62cad835c588275d3407.do"
EVENT_LIST_URL = "https://web.kangnam.ac.kr/menu/e4058249224f49ab163131ce104214fb.do"
ACTIONABLE_EVENT_TERMS = ("신청", "모집", "접수", "지원", "참여", "장학", "선발", "교육", "프로그램", "공모")


def parse_sample_html(html: str, source_url: str = "https://example.invalid/notice/1") -> dict:
    """사이트 구조 없이 파서 동작을 시험하기 위한 공개 함수."""
    soup = BeautifulSoup(html, "html.parser")
    attachments = soup.select("a.attachment[href]")
    content_links = [
        urljoin(source_url, link.get("href"))
        for link in soup.select("article a[href]:not(.attachment)")
    ]
    return {
        "source_id": soup.select_one("[data-source-id]").get("data-source-id", "sample") if soup.select_one("[data-source-id]") else "sample",
        "title": normalize_text((soup.select_one("h1") or soup.select_one("title")).get_text(" ", strip=True)),
        "content": normalize_text((soup.select_one("article") or soup.body or soup).get_text(" ", strip=True)),
        "published_at": (soup.select_one("time").get("datetime") if soup.select_one("time") else datetime.now().astimezone().isoformat()),
        "source_url": source_url,
        "department_name": normalize_text(soup.select_one(".department").get_text(" ", strip=True)) if soup.select_one(".department") else None,
        "attachment_names": [normalize_text(link.get_text(" ", strip=True)) for link in attachments],
        "attachment_urls": [urljoin(source_url, link.get("href")) for link in attachments],
        "attachment_manifest": [],
        "attachment_text": "",
        "content_links": list(dict.fromkeys(content_links)),
    }


class KnuNoticeCrawler:
    def __init__(
        self, progress_callback: Callable[[str, int, int | None], None] | None = None,
        incremental: bool = False,
    ) -> None:
        self.session = self._new_session()
        self.last_request = 0.0
        self.request_lock = threading.Lock()
        self.listing_complete = False
        self.progress_callback = progress_callback
        self.incremental = incremental
        self.attachments = AttachmentExtractor(self.session)

    @staticmethod
    def _new_session() -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": "KNU-Ask-Crawler/1.0 (internal MVP demo)"})
        return session

    def _progress(self, phase: str, current: int = 0, total: int | None = None) -> None:
        if self.progress_callback:
            self.progress_callback(phase, current, total)

    def _get(self, url: str, **kwargs) -> requests.Response:
        # 상세 파싱/OCR은 병렬화해도 학교 HTML 요청은 한 줄로 세워 기존 요청
        # 간격을 지킨다. 이 잠금은 requests.Session 동시 사용도 방지한다.
        with self.request_lock:
            elapsed = time.monotonic() - self.last_request
            if elapsed < settings.crawler_delay_seconds:
                time.sleep(settings.crawler_delay_seconds - elapsed)
            response = self.session.get(url, timeout=20, **kwargs)
            self.last_request = time.monotonic()
        response.raise_for_status()
        return response

    def crawl(self) -> list[dict]:
        if settings.mock_crawler:
            self.listing_complete = True
            data = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
            return [{
                **item,
                "attachment_names": [], "attachment_urls": [], "attachment_text": "", "attachment_manifest": [],
            } for item in data["notices"]]
        notices = self._crawl_real()
        notices.extend(self._crawl_academic_guides())
        notices.extend(self._crawl_academic_faqs())
        notices.extend(self._crawl_directory())
        if settings.crawler_include_events:
            notices.extend(self._crawl_events())
        self._progress("processing", 0, len(notices))
        return notices

    def _crawl_academic_guides(self) -> list[dict]:
        """학사 메뉴의 루트와 탭 하위 문서를 모두 독립 근거로 수집한다."""
        results = []
        seen_content: set[str] = set()
        for index, (slug, root_title, menu_hash, department) in enumerate(ACADEMIC_GUIDE_ROOTS, start=1):
            self._progress("academic_guides", index, len(ACADEMIC_GUIDE_ROOTS))
            root_url = f"{settings.notice_base_url}/menu/{menu_hash}.do"
            root_response = self._get(root_url)
            root_soup = BeautifulSoup(root_response.text, "html.parser")
            pages = [(root_title, root_response.url, root_soup)]
            for link in root_soup.select(f"a[href*='{menu_hash}.do?encMenuSeq=']"):
                title = normalize_text(link.get_text(" ", strip=True))
                absolute = urljoin(root_response.url, link.get("href"))
                if not title or "FAQ" in title.upper() or absolute == root_response.url:
                    continue
                response = self._get(absolute)
                pages.append((title, response.url, BeautifulSoup(response.text, "html.parser")))
            for page_title, page_url, soup in pages:
                content_node = soup.select_one(".contents")
                if not content_node:
                    continue
                content = normalize_text(content_node.get_text(" ", strip=True))
                digest = hashlib.sha256(content.encode()).hexdigest()
                if not content or digest in seen_content:
                    continue
                seen_content.add(digest)
                results.append(self._page_record(
                    source_id=f"academic-guide:{slug}:{urlparse(page_url).query or 'root'}",
                    title=f"[상시 학사안내] {page_title}",
                    response_url=page_url,
                    content_node=content_node,
                    department=department,
                    source_type="academic_guide",
                    source_priority=120 if slug == "calendar" else 110,
                ))
        return results

    def _page_record(
        self, *, source_id: str, title: str, response_url: str, content_node,
        department: str | None, source_type: str, source_priority: int,
    ) -> dict:
        download_links = list(content_node.select("a[href*='download.do']"))
        names = [normalize_text(link.get_text(" ", strip=True)) or f"첨부파일 {i}" for i, link in enumerate(download_links, 1)]
        urls = [urljoin(response_url, link.get("href")) for link in download_links]
        for image_index, image in enumerate(content_node.select("img[src]"), start=1):
            src = urljoin(response_url, image.get("src"))
            if src.startswith("https://") and src not in urls:
                names.append(image.get("alt") or f"본문 이미지 {image_index}")
                urls.append(src)
        content_links = []
        for link in content_node.select("a[href]"):
            href = link.get("href", "")
            if not href or "download.do" in href:
                continue
            absolute = urljoin(response_url, href)
            if absolute.startswith("https://"):
                content_links.append(absolute)
        attachment_text, extraction_status = self.attachments.extract_many(names, urls)
        attachment_manifest = self.attachments.manifest(names, urls)
        return {
            "source_id": source_id,
            "title": title,
            "content": normalize_text(content_node.get_text(" ", strip=True)),
            "published_at": date.today().isoformat(),
            "source_url": response_url,
            "department_name": department,
            "attachment_names": names,
            "attachment_urls": urls,
            "attachment_text": attachment_text,
            "attachment_manifest": attachment_manifest,
            "content_links": list(dict.fromkeys(content_links)),
            "source_type": source_type,
            "source_priority": source_priority,
            "extraction_status": extraction_status,
        }

    def _crawl_academic_faqs(self) -> list[dict]:
        results = []
        for page in range(1, 10):
            self._progress("academic_faq", page, 4)
            response = self._get(FAQ_ROOT, params={"paginationInfo.currentPageNo": page})
            soup = BeautifulSoup(response.text, "html.parser")
            questions = soup.select(".togg_list.faq dt.togg_tit")
            answers = soup.select(".togg_list.faq dd.togg_ol")
            if not questions:
                break
            for question_node, answer_node in zip(questions, answers):
                number = normalize_text((question_node.select_one(".faq_num") or question_node).get_text(" ", strip=True))
                question = normalize_text((question_node.select_one(".togg_txt") or question_node).get_text(" ", strip=True))
                answer = normalize_text(answer_node.get_text(" ", strip=True))
                if question and answer:
                    results.append({
                        "source_id": f"academic-faq:{number}:{hashlib.sha1(question.encode()).hexdigest()[:10]}",
                        "title": f"[공식 학사 FAQ] {question}", "content": answer,
                        "published_at": date.today().isoformat(), "source_url": response.url,
                        "department_name": "교무팀", "attachment_names": [], "attachment_urls": [],
                        "attachment_text": "", "attachment_manifest": [], "content_links": [], "source_type": "official_faq",
                        "source_priority": 105, "extraction_status": "not_required",
                    })
            pagination = soup.select_one(".pagination[data-params]")
            max_page = int(json.loads(pagination.get("data-params"))["max"]) if pagination else page
            if page >= max_page:
                break
        return results

    def _crawl_directory(self) -> list[dict]:
        self._progress("directory", 0, None)
        response = self._get(DIRECTORY_URL)
        soup = BeautifulSoup(response.text, "html.parser")
        results = [{
            "source_id": "directory:representative", "title": "[교내 담당자] 강남대학교 대표 전화번호",
            "content": "강남대학교 대표전화 031-280-3114, 031-280-3500. 세부 업무는 담당 부서별 내선번호를 확인하세요.",
            "published_at": date.today().isoformat(), "source_url": response.url, "department_name": "강남대학교",
            "attachment_names": [], "attachment_urls": [], "attachment_text": "", "content_links": [],
            "attachment_manifest": [],
            "source_metadata": {"contactPerson": None, "duty": "대표전화", "phone": "031-280-3114"},
            "source_type": "staff_directory", "source_priority": 115, "extraction_status": "not_required",
        }]
        for row in soup.select(".phone_loop"):
            department = normalize_text((row.select_one(".areaGame") or row).get_text(" ", strip=True))
            person_duty = normalize_text((row.select_one(".areaName") or row).get_text(" ", strip=True))
            extension = normalize_text((row.select_one(".teleOffi") or row).get_text(" ", strip=True))
            if not department or not extension or "FAX" in person_duty.upper():
                continue
            full_phone = f"031-280-{extension}" if extension.isdigit() and len(extension) == 4 else extension
            person_match = __import__("re").match(r"(?P<person>[^()]+?)(?:\s*\((?P<duty>.*)\))?$", person_duty)
            contact_person = normalize_text(person_match.group("person")) if person_match else person_duty
            duty = normalize_text(person_match.group("duty") or "") if person_match else ""
            entry_key = hashlib.sha1(f"{department}|{person_duty}|{extension}".encode()).hexdigest()[:16]
            results.append({
                "source_id": f"directory:{entry_key}", "title": f"[교내 담당자] {department} - {person_duty}",
                "content": f"부서: {department}. 담당자 및 업무: {person_duty}. 문의 전화번호: {full_phone}.",
                "published_at": date.today().isoformat(), "source_url": response.url, "department_name": department,
                "attachment_names": [], "attachment_urls": [], "attachment_text": "", "content_links": [],
                "attachment_manifest": [],
                "source_metadata": {"contactPerson": contact_person, "duty": duty, "phone": full_phone},
                "source_type": "staff_directory", "source_priority": 100, "extraction_status": "not_required",
            })
        self._progress("directory", len(results), len(results))
        return results

    def _crawl_events(self) -> list[dict]:
        cutoff = date.today() - relativedelta(months=settings.crawler_months)
        results = []
        detailed_count = 0
        page_limit = settings.crawler_incremental_pages if self.incremental else settings.crawler_max_pages
        for page in range(1, page_limit + 1):
            self._progress("events", page, page_limit)
            response = self._get(EVENT_LIST_URL, params={"paginationInfo.currentPageNo": page, "searchMenuSeq": 0})
            soup = BeautifulSoup(response.text, "html.parser")
            rows = soup.select(".tbody > ul")
            if not rows:
                break
            oldest = None
            for row in rows:
                link = row.select_one("a.detailLink[data-params]")
                date_node = row.select_one(".ulthu_date")
                if not link or not date_node:
                    continue
                match = __import__("re").search(r"(\d{2}\.\d{2}\.\d{2})", date_node.get_text(" ", strip=True))
                if not match:
                    continue
                published = datetime.strptime(match.group(1), "%y.%m.%d").date()
                oldest = min(oldest or published, published)
                if published < cutoff:
                    continue
                params = json.loads(link.get("data-params", "{}"))
                board_id = params.get("encMenuBoardSeq")
                menu_id = params.get("encMenuSeq")
                if not board_id:
                    continue
                title = normalize_text(link.get("title") or link.get_text(" ", strip=True))
                summary_node = row.select_one(".ulthu_txt")
                summary = normalize_text(summary_node.get_text(" ", strip=True) if summary_node else "")
                detail_url = urljoin(response.url, "/menu/board/info/e4058249224f49ab163131ce104214fb.do")
                detail_url = requests.Request("GET", detail_url, params={"encMenuSeq": menu_id, "encMenuBoardSeq": board_id}).prepare().url
                record = {
                    "source_id": f"event:{board_id}", "title": f"[행사안내] {title}", "content": summary,
                    "published_at": published.isoformat(), "source_url": detail_url, "department_name": None,
                    "attachment_names": [], "attachment_urls": [], "attachment_text": "", "content_links": [],
                    "attachment_manifest": [],
                    "source_type": "event", "source_priority": 35, "extraction_status": "not_required",
                }
                if (
                    detailed_count < settings.crawler_event_detail_limit
                    and any(term in f"{title} {summary}" for term in ACTIONABLE_EVENT_TERMS)
                ):
                    detail = self._get(detail_url)
                    detail_soup = BeautifulSoup(detail.text, "html.parser")
                    content_node = detail_soup.select_one(".tbl_view")
                    if content_node:
                        detailed_count += 1
                        record = self._page_record(
                            source_id=record["source_id"], title=record["title"], response_url=detail.url,
                            content_node=content_node, department=None, source_type="event", source_priority=40,
                        )
                        record["published_at"] = published.isoformat()
                results.append(record)
            if oldest and oldest < cutoff:
                break
        return results

    def _crawl_real(self) -> list[dict]:
        cutoff = date.today() - relativedelta(months=settings.crawler_months)
        candidates: dict[str, dict] = {}
        page_limit = settings.crawler_incremental_pages if self.incremental else settings.crawler_max_pages
        for page in range(1, page_limit + 1):
            self._progress("notice_listing", page, page_limit)
            response = self._get(settings.notice_list_url, params={"paginationInfo.currentPageNo": page, "searchMenuSeq": 0})
            soup = BeautifulSoup(response.text, "html.parser")
            oldest_normal = None
            rows = soup.select(".devTable .tbody > ul")
            if not rows:
                self.listing_complete = True
                break
            for row in rows:
                columns = row.find_all("li", recursive=False)
                link = row.select_one("a.detailLink[data-params]")
                if not link or len(columns) < 7:
                    continue
                params = json.loads(link.get("data-params", "{}"))
                published = datetime.strptime(columns[5].get_text(strip=True), "%y.%m.%d").date()
                if columns[0].get_text(strip=True).isdigit():
                    oldest_normal = min(oldest_normal or published, published)
                if published >= cutoff and params.get("encMenuBoardSeq"):
                    candidates[params["encMenuBoardSeq"]] = {
                        "source_id": params["encMenuBoardSeq"], "menu_id": params.get("encMenuSeq"),
                        "title": link.get("title", ""), "published_at": published.isoformat(),
                        "department_name": normalize_text(columns[4].get_text(" ", strip=True)),
                    }
            if oldest_normal and oldest_normal < cutoff:
                self.listing_complete = True
                break

        results = []
        worker_count = max(1, min(settings.crawler_detail_workers, 6))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="notice-detail") as executor:
            futures = [executor.submit(self._crawl_notice_detail, dict(item)) for item in candidates.values()]
            for detail_index, future in enumerate(as_completed(futures), start=1):
                results.append(future.result())
                self._progress("notice_details", detail_index, len(candidates))
        return results

    def _crawl_notice_detail(self, item: dict) -> dict:
        params = {"encMenuSeq": item.pop("menu_id"), "encMenuBoardSeq": item["source_id"]}
        response = self._get(settings.notice_detail_url, params=params)
        soup = BeautifulSoup(response.text, "html.parser")
        content_node = soup.select_one(".tbl_view")
        registrar_node = soup.select_one(".wri_area.colum80")
        registrar_contact = soup.select_one(".inputUserCtc")
        registrar_text = ""
        source_metadata = {}
        if registrar_node:
            # SNS 버튼의 숨김 텍스트는 제외하고 게시 부서와 등록자 연락처만 보존한다.
            registrar_copy = BeautifulSoup(str(registrar_node), "html.parser")
            for social in registrar_copy.select(".sns_area"):
                social.decompose()
            registrar_text = normalize_text(registrar_copy.get_text(" ", strip=True))
        if registrar_contact:
            contact_text = normalize_text(registrar_contact.get_text(" ", strip=True))
            contact_match = __import__("re").search(
                r"(?P<name>[가-힣]{2,4})\s*\(?(?P<phone>0\d{1,2}-\d{3,4}-\d{4})\)?",
                contact_text,
            )
            if contact_match:
                source_metadata = {
                    "registrarName": contact_match.group("name"),
                    "registrarPhone": contact_match.group("phone"),
                    "registrarDepartment": item.get("department_name"),
                }
        links = soup.select(".contents .sec_inner a[href*='download.do']")
        content_links = []
        if content_node:
            for link in content_node.select("a[href]"):
                href = link.get("href", "")
                if not href or "download.do" in href:
                    continue
                absolute = urljoin(response.url, href)
                if absolute.startswith("https://"):
                    content_links.append(absolute)
        names = [normalize_text(link.get_text(" ", strip=True)) for link in links]
        urls = [urljoin(settings.notice_base_url, link.get("href")) for link in links]
        if content_node:
            for image_index, image in enumerate(content_node.select("img[src]"), start=1):
                image_url = urljoin(response.url, image.get("src"))
                if image_url.startswith("https://") and image_url not in urls:
                    names.append(image.get("alt") or f"본문 이미지 {image_index}")
                    urls.append(image_url)
        # 각 작업이 독립 세션을 사용해 다운로드와 OCR을 겹쳐 수행한다.
        extractor = AttachmentExtractor(self._new_session())
        attachment_text, extraction_status = extractor.extract_many(names, urls)
        attachment_manifest = extractor.manifest(names, urls)
        return {
            **item,
            "content": normalize_text(
                f"{registrar_text}\n{content_node.get_text(' ', strip=True) if content_node else ''}"
            ),
            "source_url": response.url,
            "attachment_names": names,
            "attachment_urls": urls,
            "attachment_text": attachment_text,
            "attachment_manifest": attachment_manifest,
            "content_links": list(dict.fromkeys(content_links)),
            "source_snapshot": str(content_node) if content_node else "",
            "source_metadata": source_metadata,
            "source_type": "official_notice", "source_priority": 70,
            "extraction_status": extraction_status,
        }
