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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
    ("rotc", "ROTC", "1d556fac41442ec4d365ad79cc53f2be", "학군단"),
    ("startup-credit", "창업대체학점인정제", "a1ed5a7cd101b044997be8a2ff63b979", "창업교육센터"),
    ("startup-leave", "창업휴학", "07e3888786f407dd1940d83642a5788f", "창업교육센터"),
    ("manual", "대학생활 가이드 및 편람", "3d1345da241450c621fe02936aeb96e7", "교무팀"),
]

FAQ_ROOTS = [
    ("leave-return", "휴복학", "https://web.kangnam.ac.kr/menu/12d2ee44cc4e95562f84a01bf953a054.do", "교무팀"),
    ("graduation", "졸업", "https://web.kangnam.ac.kr/menu/c5dc4b1d7b4dd402e5e6a7a8471eb55c.do", "교무팀"),
    ("scholarship", "장학", "https://web.kangnam.ac.kr/menu/587b03863a80061da3ed29ec0329e06e.do", "장학복지팀"),
]
STATIC_GUIDE_ROOTS = [
    ("scholarship-campus", "교내장학금", "https://web.kangnam.ac.kr/menu/062e41fba927c0c76d1c0e929f931016.do", "장학복지팀", "scholarship_guide"),
    ("scholarship-external", "교외장학금", "https://web.kangnam.ac.kr/menu/d66bf6493c47349df62cfff448f4ec53.do", "장학복지팀", "scholarship_guide"),
    ("scholarship-national", "국가장학금", "https://web.kangnam.ac.kr/menu/59102156c31a166bc8bedb3986f5b71e.do", "장학복지팀", "scholarship_guide"),
    ("student-loan", "학자금대출", "https://web.kangnam.ac.kr/menu/f58cd181670c8adb4b4675fbba7131bf.do", "장학복지팀", "scholarship_guide"),
    ("counseling", "상담·심리검사", "https://web.kangnam.ac.kr/menu/328ef0e3952d295ae15b5a29d17967b9.do", "마음나눔센터", "student_service"),
    ("welfare", "후생기관", "https://web.kangnam.ac.kr/menu/b71566183e4ee1e2eedb079521ddb5b4.do", "장학복지팀", "student_service"),
    ("it-homepage", "홈페이지 이용안내", "https://web.kangnam.ac.kr/menu/160162b7704ce8d563632e815b99e766.do", "정보전산팀", "student_service"),
    ("shuttle", "무료셔틀 이용안내", "https://web.kangnam.ac.kr/menu/4990be9bdd4defbf92dde49a31ad1a3b.do", "총무구매팀", "student_service"),
    ("club-registration", "중앙동아리 등록 절차", "https://web.kangnam.ac.kr/menu/9cb42959565d685ac31af5efe5bdfd2a.do", "학생지원팀", "student_service"),
    ("it-wifi", "무선랜 이용안내", "https://cominfo.kangnam.ac.kr/menu/78d98f9c10771d94e1f4c20bdd82e9c4.do", "전산정보원", "student_service"),
    ("it-microsoft365", "Microsoft 365 이용안내", "https://cominfo.kangnam.ac.kr/menu/5e5ddcedbb3658f5c1c42bd11db19b6e.do", "전산정보원", "student_service"),
    ("teaching-application", "교직이수 예정자 선발", "https://education.kangnam.ac.kr/menu/e0f0cb6dc3528987a66abef063a150ae.do", "교직지원부", "student_service"),
    ("teaching-requirements", "교직 이수기준", "https://education.kangnam.ac.kr/menu/69ed8c0c8e29d26f2c44b8d0d35d12fc.do", "교직지원부", "student_service"),
    ("library-hours", "중앙도서관 이용시간", "https://lib.kangnam.ac.kr/Common?html=/Users/Knul/Docs/subguide02.cshtml", "중앙도서관", "library_guide"),
    ("library-loans", "중앙도서관 대출·반납·연장·예약", "https://lib.kangnam.ac.kr/Common?html=/Users/Knul/Docs/subguide05.cshtml", "중앙도서관", "library_guide"),
    ("library-services", "중앙도서관 타대학 이용·원문복사", "https://lib.kangnam.ac.kr/Common?html=/Users/Knul/Docs/VisitGuide.cshtml", "중앙도서관", "library_guide"),
]
EXTERNAL_STATIC_ROOTS = [
    ("dorm-admission", "심전생활관 입사안내", "https://shimjeon.kangnam.ac.kr/menu/028c1247e62f5397e3fb2da7d921aa4b.do", "심전생활관", "dormitory_guide"),
    ("exchange-outgoing", "교환학생 지원 및 파견절차", "https://oia.kangnam.ac.kr/menu/03610d2a6630fb893df53109ca6eb751.do", "대외교류센터", "international_guide"),
    ("volunteer", "사회봉사 안내", "https://jb.kangnam.ac.kr/menu/9e852d21bcbf50f152a170f9ba2968b9.do", "글로컬사회공헌센터", "student_service"),
    ("disability-support", "장애학생 지원 안내", "https://jcenter.kangnam.ac.kr/menu/ccbea1da2e35a6604fcc1f076d94449e.do", "장애학생지원센터", "student_service"),
]
DIRECTORY_URL = "https://web.kangnam.ac.kr/menu/d32b0ae4b98a62cad835c588275d3407.do"
EVENT_LIST_URL = "https://web.kangnam.ac.kr/menu/e4058249224f49ab163131ce104214fb.do"
CATALOG_URL = "https://web.kangnam.ac.kr/menu/f4505923435b612108ec36685e87cd72.do"
CAREER_URL = "https://career.kangnam.ac.kr/main.do"
REGULATION_URL = "https://web.kangnam.ac.kr/kyujeong/check.jsp"
REGULATION_APP_URL = "https://app.kangnam.ac.kr/knumis/mo_open"
REGULATION_TITLES = {
    "강남대학교학칙", "강남대학교학칙시행세칙", "교양및전공이수에관한규정",
    "조기졸업제운영규정", "졸업인증규정", "졸업인증시행지침",
    "장학금지급규정", "국가근로장학제도에 관한 규정",
}
SUBSITE_BOARD_SOURCES = [
    ("international", "대외교류센터", "https://oia.kangnam.ac.kr/menu/18bc4271daca8c13802edbbda0f72fd6.do", "대외교류센터", "international_notice", 24),
    ("dormitory", "심전생활관", "https://shimjeon.kangnam.ac.kr/menu/9541c1620860e0bc522b4eeb02be4524.do", "심전생활관", "dormitory_notice", 12),
    ("volunteer", "글로컬사회공헌센터", "https://jb.kangnam.ac.kr/menu/91ae7b34af5ae97734ddc069d715c123.do", "글로컬사회공헌센터", "student_service_notice", 12),
    ("disability", "장애학생지원센터", "https://jcenter.kangnam.ac.kr/menu/700498dc7bf59addccb889bfa0ed7475.do", "장애학생지원센터", "student_service_notice", 12),
]
ACTIONABLE_EVENT_TERMS = (
    "신청", "모집", "접수", "지원", "참여", "장학", "선발", "교육", "프로그램", "공모",
    "캠프", "특강", "설명회", "박람회", "공모전", "대회", "세미나", "채용", "봉사",
)
PILOT_ACADEMIC_SLUGS = {
    "calendar", "registration", "classes", "credit-exchange", "leave-return",
    "graduation", "academic-status", "certificate", "military", "reserve",
}
PILOT_STATIC_SLUGS = {
    "scholarship-campus", "scholarship-external", "scholarship-national", "student-loan",
    "counseling", "shuttle",
}


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
        profile: str | None = None,
    ) -> None:
        self.session = self._new_session()
        self.last_request = 0.0
        self.request_lock = threading.Lock()
        self.listing_complete = False
        self.progress_callback = progress_callback
        self.incremental = incremental
        self.profile = profile or ("incremental" if incremental else "full")
        self.attachments = AttachmentExtractor(self.session)
        self.failures: list[dict] = []

    @staticmethod
    def _new_session() -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": "KNU-Ask-Crawler/1.0 (internal MVP demo)"})
        retry = Retry(
            total=3, connect=3, read=3, backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
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
            # 응답 완료 뒤 추가 지연하지 않고 요청 시작 시점 사이의 최소
            # 간격을 보장한다. 세션 잠금은 응답까지 유지해 thread-safe하다.
            self.last_request = time.monotonic()
            response = self.session.get(url, timeout=20, **kwargs)
        response.raise_for_status()
        return response

    def _post(self, url: str, **kwargs) -> requests.Response:
        with self.request_lock:
            elapsed = time.monotonic() - self.last_request
            if elapsed < settings.crawler_delay_seconds:
                time.sleep(settings.crawler_delay_seconds - elapsed)
            self.last_request = time.monotonic()
            response = self.session.post(url, timeout=20, **kwargs)
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
        if self.profile == "incremental":
            notices = self._collect("official_notice", settings.notice_list_url, self._crawl_real)
            if settings.crawler_include_events:
                notices.extend(self._collect("event", EVENT_LIST_URL, self._crawl_events))
        elif self.profile == "daily":
            notices = self._collect(
                "academic_calendar", settings.notice_base_url,
                lambda: self._crawl_academic_guides({"calendar"}),
            )
        elif self.profile == "pilot":
            notices = self._collect("academic_guide", settings.notice_base_url, lambda: self._crawl_academic_guides(PILOT_ACADEMIC_SLUGS))
            notices.extend(self._collect("official_faq", FAQ_ROOTS[0][2], self._crawl_academic_faqs))
            notices.extend(self._collect(
                "static_guide", STATIC_GUIDE_ROOTS[0][2],
                lambda: self._crawl_static_guides(slugs=PILOT_STATIC_SLUGS),
            ))
            notices.extend(self._collect("university_catalog", CATALOG_URL, lambda: self._crawl_catalog(years={2024, 2025, 2026})))
            notices.extend(self._collect("career_program", CAREER_URL, lambda: self._crawl_career_programs(limit=5, current_only=True)))
            notices.extend(self._collect("external_guide", EXTERNAL_STATIC_ROOTS[0][2], lambda: self._crawl_external_static(limit=2)))
            notices.extend(self._collect("staff_directory", DIRECTORY_URL, self._crawl_directory))
            if settings.crawler_include_events:
                notices.extend(self._collect(
                    "event", EVENT_LIST_URL,
                    self._crawl_pilot_events,
                ))
        else:
            notices = self._collect("official_notice", settings.notice_list_url, self._crawl_real)
            notices.extend(self._collect("academic_guide", settings.notice_base_url, self._crawl_academic_guides))
            notices.extend(self._collect("official_faq", FAQ_ROOTS[0][2], self._crawl_academic_faqs))
            notices.extend(self._collect("static_guide", STATIC_GUIDE_ROOTS[0][2], self._crawl_static_guides))
            notices.extend(self._collect("university_catalog", CATALOG_URL, self._crawl_catalog))
            notices.extend(self._collect("career_program", CAREER_URL, self._crawl_career_programs))
            notices.extend(self._collect("external_guide", EXTERNAL_STATIC_ROOTS[0][2], self._crawl_external_static))
            notices.extend(self._collect("university_regulation", REGULATION_URL, self._crawl_regulations))
            notices.extend(self._collect("subsite_notice", SUBSITE_BOARD_SOURCES[0][2], self._crawl_subsite_boards))
            notices.extend(self._collect("staff_directory", DIRECTORY_URL, self._crawl_directory))
            if settings.crawler_include_events:
                notices.extend(self._collect("event", EVENT_LIST_URL, self._crawl_events))
        self._progress("processing", 0, len(notices))
        return notices

    def _collect(self, source_type: str, url: str, callback: Callable[[], list[dict]]) -> list[dict]:
        try:
            return callback()
        except Exception as exc:
            self.failures.append({
                "sourceType": source_type, "url": url,
                "reason": type(exc).__name__, "message": str(exc)[:500],
            })
            return []

    def _crawl_academic_guides(self, slugs: set[str] | None = None) -> list[dict]:
        """학사 메뉴의 루트와 탭 하위 문서를 모두 독립 근거로 수집한다."""
        results = []
        seen_content: set[str] = set()
        roots = [root for root in ACADEMIC_GUIDE_ROOTS if not slugs or root[0] in slugs]
        for index, (slug, root_title, menu_hash, department) in enumerate(roots, start=1):
            self._progress("academic_guides", index, len(roots))
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
                content_node = self._academic_content_node(soup)
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

    @staticmethod
    def _academic_content_node(soup: BeautifulSoup):
        """학교 CMS의 실제 편집 본문을 고른다.

        일부 학사안내(대표적으로 졸업)는 ``.contents``가 빈 껍데기이고
        실제 내용이 ``.cke_editable``에 있다. 첫 selector를 무조건 쓰지
        않고, CMS 편집 영역을 우선하되 내용이 있는 노드만 반환한다.
        """
        for selector in (
            ".cke_editable", ".sponge-layout-content-container-rightcontent",
            ".contents", ".cont", ".widget_content", "main",
        ):
            candidates = soup.select(selector)
            usable = [node for node in candidates if len(normalize_text(node.get_text(" ", strip=True))) >= 5]
            if usable:
                return max(usable, key=lambda node: len(normalize_text(node.get_text(" ", strip=True))))
        return None

    @staticmethod
    def _download_name(link, index: int) -> str:
        label = normalize_text(link.get_text(" ", strip=True))
        if label and label not in {"PDF보기", "다운로드", "첨부파일"}:
            return label
        cell = link.find_parent(["th", "td"])
        previous = cell.find_previous_sibling(["th", "td"]) if cell else None
        context = normalize_text(previous.get_text(" ", strip=True)) if previous else ""
        if context:
            extension = ".pdf" if "PDF" in label.upper() else ""
            return f"{context}{extension}"
        return f"첨부파일 {index}"

    @classmethod
    def _attachment_downloads(cls, root, response_url: str) -> tuple[list[str], list[str]]:
        """CMS 본문 영역 밖의 첨부 영역까지 전체 수집한다."""
        names: list[str] = []
        urls: list[str] = []
        for link in root.select("a[href]"):
            href = link.get("href", "")
            if "download.do" not in href:
                continue
            absolute = urljoin(response_url, href)
            parsed = urlparse(absolute)
            if (
                parsed.scheme != "https" or not parsed.hostname
                or not parsed.hostname.endswith("kangnam.ac.kr")
                or absolute in urls
            ):
                continue
            urls.append(absolute)
            names.append(cls._download_name(link, len(urls)))
        return names, urls

    @staticmethod
    def _attachment_audit(names: list[str], urls: list[str], manifest: list[dict]) -> dict:
        extracted = sum(item.get("extractionStatus") == "success" for item in manifest)
        collected = len(names) == len(urls) == len(manifest)
        return {
            "discoveredCount": len(urls),
            "storedCount": len(names),
            "manifestCount": len(manifest),
            "extractedCount": extracted,
            "collectionComplete": collected,
            "textExtractionComplete": collected and extracted == len(urls),
        }

    @staticmethod
    def _extracted_https_links(text: str) -> list[str]:
        links = []
        # PDF 문자 추출은 URL 뒤의 한글을 공백 없이 붙일 수 있다.
        # URL에 허용되는 ASCII 문자만 받아 뒤 절차 문장을 링크로 오인하지 않는다.
        for value in __import__("re").findall(
            r"https://[A-Za-z0-9.-]+(?::\d+)?(?:/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?",
            text or "",
        ):
            value = value.rstrip(".,;:ㆍ。，、〉》』）)")
            parsed = urlparse(value)
            if parsed.hostname and not parsed.username and not parsed.password:
                links.append(value)
        return list(dict.fromkeys(links))

    @classmethod
    def _academic_sections(cls, content_node, response_url: str) -> list[dict]:
        """고정 길이 청크가 아니라 화면의 학사 업무 제목 단위로 보존한다."""
        headings = content_node.select(".sec_tit")
        sections: list[dict] = []
        for index, heading in enumerate(headings, start=1):
            nodes = []
            cursor = heading.find_next_sibling()
            while cursor is not None:
                classes = cursor.get("class") or []
                if "sec_tit" in classes:
                    break
                nodes.append(cursor)
                cursor = cursor.find_next_sibling()
            text = normalize_text(" ".join(node.get_text(" ", strip=True) for node in nodes))
            links = []
            for node in nodes:
                for link_index, link in enumerate(node.select("a[href*='download.do']"), start=1):
                    links.append({
                        "name": cls._download_name(link, link_index),
                        "url": urljoin(response_url, link.get("href")),
                    })
            if text or links:
                sections.append({
                    "index": index,
                    "title": normalize_text(heading.get_text(" ", strip=True)),
                    "content": text,
                    "sourceLocator": f"HTML section:{normalize_text(heading.get_text(' ', strip=True))}",
                    "attachments": links,
                })
        if sections:
            return sections
        whole = normalize_text(content_node.get_text(" ", strip=True))
        return [{"index": 1, "title": None, "content": whole, "sourceLocator": "HTML body", "attachments": []}]

    def _page_record(
        self, *, source_id: str, title: str, response_url: str, content_node,
        department: str | None, source_type: str, source_priority: int,
        extract_attachments: bool = True, source_metadata: dict | None = None,
    ) -> dict:
        names, urls = self._attachment_downloads(content_node, response_url)
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
        if extract_attachments:
            attachment_text, extraction_status = self.attachments.extract_many(names, urls)
            attachment_manifest = self.attachments.manifest(names, urls)
        else:
            attachment_text, extraction_status, attachment_manifest = "", "deferred", []
        sections = self._academic_sections(content_node, response_url)
        content_links.extend(self._extracted_https_links(attachment_text))
        metadata = {"sections": sections, **(source_metadata or {})}
        metadata["attachmentAudit"] = self._attachment_audit(names, urls, attachment_manifest)
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
            "source_snapshot": str(content_node),
            "source_metadata": metadata,
            "source_type": source_type,
            "source_priority": source_priority,
            "extraction_status": extraction_status,
        }

    def _crawl_static_guides(
        self, scholarship_only: bool = False, slugs: set[str] | None = None,
    ) -> list[dict]:
        roots = [
            item for item in STATIC_GUIDE_ROOTS
            if (not scholarship_only or item[4] == "scholarship_guide")
            and (not slugs or item[0] in slugs)
        ]
        results = []
        for index, (slug, title, url, department, source_type) in enumerate(roots, start=1):
            self._progress("static_guides", index, len(roots))
            response = self._get(url)
            soup = BeautifulSoup(response.text, "html.parser")
            content_node = self._academic_content_node(soup)
            if not content_node:
                continue
            results.append(self._page_record(
                source_id=f"static-guide:{slug}", title=f"[상시 안내] {title}",
                response_url=response.url, content_node=content_node, department=department,
                source_type=source_type, source_priority=108,
            ))
        return results

    @staticmethod
    def _same_menu_tab_urls(root_url: str, soup: BeautifulSoup) -> list[str]:
        parsed_root = urlparse(root_url)
        urls = [root_url]
        for link in soup.select("a[href*='encMenuSeq=']"):
            absolute = urljoin(root_url, link.get("href"))
            parsed = urlparse(absolute)
            if parsed.netloc == parsed_root.netloc and parsed.path == parsed_root.path:
                urls.append(absolute)
        return list(dict.fromkeys(urls))

    def _crawl_academic_faqs(self) -> list[dict]:
        """FAQ 메뉴를 하드코딩된 한 탭이 아니라 업무별 모든 탭에서 찾는다."""
        results = []
        seen_questions: set[str] = set()
        for root_index, (slug, label, root_url, department) in enumerate(FAQ_ROOTS, start=1):
            self._progress("academic_faq", root_index, len(FAQ_ROOTS))
            root_response = self._get(root_url)
            root_soup = BeautifulSoup(root_response.text, "html.parser")
            for tab_url in self._same_menu_tab_urls(root_response.url, root_soup):
                for page in range(1, 30):
                    response = self._get(tab_url, params={"paginationInfo.currentPageNo": page})
                    soup = BeautifulSoup(response.text, "html.parser")
                    questions = soup.select(".togg_list.faq dt.togg_tit")
                    answers = soup.select(".togg_list.faq dd.togg_ol")
                    if not questions or len(questions) != len(answers):
                        break
                    for question_node, answer_node in zip(questions, answers):
                        question = normalize_text(
                            (question_node.select_one(".togg_txt") or question_node).get_text(" ", strip=True)
                        )
                        answer = normalize_text(answer_node.get_text(" ", strip=True))
                        digest = hashlib.sha1(f"{slug}|{question}".encode()).hexdigest()
                        if not question or not answer or digest in seen_questions:
                            continue
                        seen_questions.add(digest)
                        content_links = [
                            urljoin(response.url, item.get("href"))
                            for item in answer_node.select("a[href]")
                            if urljoin(response.url, item.get("href")).startswith("https://")
                        ]
                        results.append({
                            "source_id": f"official-faq:{slug}:{digest[:16]}",
                            "title": f"[공식 {label} FAQ] {question}", "content": answer,
                            "published_at": date.today().isoformat(), "source_url": response.url,
                            "department_name": department, "attachment_names": [], "attachment_urls": [],
                            "attachment_text": "", "attachment_manifest": [],
                            "content_links": list(dict.fromkeys(content_links)),
                            "source_snapshot": str(answer_node),
                            "source_metadata": {
                                "faqCategory": label,
                                "question": question,
                                "sourceLocator": f"FAQ:{question}",
                            },
                            "source_type": "official_faq", "source_priority": 112,
                            "extraction_status": "not_required",
                        })
                    pagination = soup.select_one(".pagination[data-params]")
                    try:
                        max_page = int(json.loads(pagination.get("data-params"))["max"]) if pagination else page
                    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                        max_page = page
                    if page >= max_page:
                        break
        return results

    def _crawl_catalog(self, years: set[int] | None = None) -> list[dict]:
        """대학요람의 규정·교육과정·전공과목 PDF를 연도별 독립 근거로 저장한다."""
        response = self._get(CATALOG_URL)
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        relevant = ("규정학칙", "교육과정", "전공과목")
        for heading in soup.select(".sec_tit"):
            match = __import__("re").search(r"(20\d{2})학년도", heading.get_text(" ", strip=True))
            if not match:
                continue
            academic_year = int(match.group(1))
            if years and academic_year not in years:
                continue
            wrapper = heading.find_next_sibling("div", class_="text_btn_w")
            if not wrapper:
                continue
            for row in wrapper.select("li"):
                label_node = row.select_one(".title")
                link = row.select_one("a[href*='download.do']")
                label = normalize_text(label_node.get_text(" ", strip=True) if label_node else "")
                if not link or not any(term in label for term in relevant):
                    continue
                file_url = urljoin(response.url, link.get("href"))
                file_name = f"{academic_year}학년도 대학요람 {label}.pdf"
                attachment_text, extraction_status = self.attachments.extract_many([file_name], [file_url])
                manifest = self.attachments.manifest([file_name], [file_url])
                source_key = hashlib.sha1(file_url.encode()).hexdigest()[:16]
                results.append({
                    "source_id": f"university-catalog:{academic_year}:{source_key}",
                    "title": f"[대학요람] {academic_year}학년도 {label}",
                    "content": f"{academic_year}학년도 대학요람 {label}",
                    "published_at": f"{academic_year}-01-01", "source_url": response.url,
                    "department_name": "교무팀", "attachment_names": [file_name],
                    "attachment_urls": [file_url], "attachment_text": attachment_text,
                    "attachment_manifest": manifest, "content_links": [],
                    "source_snapshot": str(row),
                    "source_metadata": {
                        "academicYear": academic_year, "catalogSection": label,
                        "sections": [{
                            "index": 1, "title": label, "content": f"{academic_year}학년도 {label}",
                            "sourceLocator": f"대학요람 {academic_year}학년도 {label}",
                            "attachments": [{"name": file_name, "url": file_url}],
                        }],
                    },
                    "source_type": "university_catalog", "source_priority": 118,
                    "extraction_status": extraction_status,
                })
        return results

    def _crawl_career_programs(self, limit: int | None = None, current_only: bool = False) -> list[dict]:
        response = self._get(CAREER_URL)
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for card in soup.select("li.program"):
            text = normalize_text(card.get_text(" ", strip=True))
            status = next((value for value in ("접수중", "접수준비", "진행중", "종료") if value in text), "unknown")
            if current_only and status not in {"접수중", "접수준비"}:
                continue
            title_node = card.select_one(".title")
            title = normalize_text(title_node.get_text(" ", strip=True) if title_node else "")
            onclick = card.get("onclick", "")
            path_match = __import__("re").search(r"'(/user/[^']+)'", onclick)
            detail_url = urljoin(response.url, path_match.group(1)) if path_match else response.url
            id_match = __import__("re").search(r"PRM_SEQ=(\d+)", detail_url)
            source_key = id_match.group(1) if id_match else hashlib.sha1(title.encode()).hexdigest()[:16]
            if not title:
                continue
            results.append({
                "source_id": f"career-program:{source_key}", "title": f"[취업 프로그램] {title}",
                "content": text, "published_at": date.today().isoformat(), "source_url": detail_url,
                "department_name": "대학일자리플러스센터", "attachment_names": [],
                "attachment_urls": [], "attachment_text": "", "attachment_manifest": [],
                "content_links": [detail_url], "source_snapshot": str(card),
                "source_metadata": {"programStatus": status},
                "source_type": "career_program", "source_priority": 68,
                "extraction_status": "not_required",
            })
            if limit and len(results) >= limit:
                break
        return results

    def _crawl_external_static(self, limit: int | None = None) -> list[dict]:
        results = []
        roots = EXTERNAL_STATIC_ROOTS[:limit] if limit else EXTERNAL_STATIC_ROOTS
        for index, (slug, title, url, department, source_type) in enumerate(roots, start=1):
            self._progress("external_guides", index, len(roots))
            response = self._get(url)
            soup = BeautifulSoup(response.text, "html.parser")
            content_node = self._academic_content_node(soup)
            if not content_node:
                continue
            results.append(self._page_record(
                source_id=f"external-guide:{slug}", title=f"[상시 안내] {title}",
                response_url=response.url, content_node=content_node, department=department,
                source_type=source_type, source_priority=106,
            ))
        return results

    @staticmethod
    def _decode_regulation(response: requests.Response) -> str:
        return response.content.decode("euc-kr", errors="replace")

    def _crawl_regulations(self) -> list[dict]:
        """프레임/자동 POST로 제공되는 공개 현행 규정 중 학생 업무 규정만 수집한다."""
        listing = self._post(
            f"{REGULATION_APP_URL}/rulesL1.jsp",
            data={"gubn": "N", "save_gubn": "", "rglt_cont2": "", "rglt_cont": ""},
        )
        soup = BeautifulSoup(self._decode_regulation(listing), "html.parser")
        candidates = []
        for row in soup.select("tr[onclick*='movepageNew']"):
            title = normalize_text(row.get_text(" ", strip=True))
            match = __import__("re").search(r"'([0-9]+),([0-9]+),([0-9]+)'", row.get("onclick", ""))
            if title in REGULATION_TITLES and match:
                candidates.append((title, *match.groups()))

        results = []
        for index, (title, pyon, jang, bnho) in enumerate(candidates, start=1):
            self._progress("regulations", index, len(candidates))
            payload = {
                "rglt_pyon": pyon, "rglt_jang": jang, "rglt_bnho": bnho,
                "rglt_cont": "", "rglt_alls": "", "save_gubn": "2", "gubn": "N",
            }
            detail = self._post(f"{REGULATION_APP_URL}/rulesL2.jsp", data=payload)
            detail_html = self._decode_regulation(detail)
            detail_soup = BeautifulSoup(detail_html, "html.parser")
            body = detail_soup.body or detail_soup
            content = normalize_text(body.get_text(" ", strip=True))
            if __import__("re").search(r"\[20\d{2}[.]\d{1,2}[.]\d{1,2}\s*폐지\]", content[:500]):
                continue
            amended = __import__("re").search(r"\[(20\d{2})[.]([01]?\d)[.]([0-3]?\d)\s*개정\]", content)
            if not amended:
                raise ValueError(f"current_regulation_version_missing:{title}")
            amended_date = f"{int(amended.group(1)):04d}-{int(amended.group(2)):02d}-{int(amended.group(3)):02d}"
            locator = f"현행 규정집 > {title} > {amended_date} 개정본"
            results.append({
                "source_id": f"regulation:{pyon}:{jang}:{bnho}:{amended_date}",
                "title": f"[현행 규정] {title}", "content": content,
                "published_at": amended_date, "source_url": REGULATION_URL,
                "department_name": "기획예산팀", "attachment_names": [],
                "attachment_urls": [], "attachment_text": "", "attachment_manifest": [],
                "content_links": [REGULATION_URL], "source_snapshot": detail_html,
                "source_metadata": {
                    "regulationName": title, "effectiveOrAmendedAt": amended_date,
                    "versionStatus": "current", "sourceLocator": locator,
                    "sections": [{
                        "index": 1, "title": title, "content": content,
                        "sourceLocator": locator, "attachments": [],
                    }],
                },
                "source_type": "university_regulation", "source_priority": 116,
                "extraction_status": "not_required",
            })
        return results

    def _crawl_subsite_boards(self) -> list[dict]:
        results = []
        for source_key, source_name, list_url, department, source_type, months in SUBSITE_BOARD_SOURCES:
            cutoff = date.today() - relativedelta(months=months)
            seen_page_signatures: set[tuple[str, ...]] = set()
            seen_board_ids: set[str] = set()
            for page in range(1, settings.crawler_max_pages + 1):
                self._progress(f"subsite_{source_key}", page, settings.crawler_max_pages)
                response = self._get(list_url, params={"paginationInfo.currentPageNo": page})
                soup = BeautifulSoup(response.text, "html.parser")
                rows = soup.select(".devTable .tbody > ul")
                if not rows:
                    break
                signature = tuple(
                    (row.select_one("a.detailLink[data-params]") or {}).get("data-params", "")
                    for row in rows
                )
                if signature in seen_page_signatures:
                    break
                seen_page_signatures.add(signature)
                oldest = None
                for row in rows:
                    columns = row.find_all("li", recursive=False)
                    link = row.select_one("a.detailLink[data-params]")
                    if not link or len(columns) < 5:
                        continue
                    date_match = __import__("re").search(r"(\d{2}[.]\d{2}[.]\d{2})", columns[-2].get_text(" ", strip=True))
                    if not date_match:
                        continue
                    published = datetime.strptime(date_match.group(1), "%y.%m.%d").date()
                    oldest = min(oldest or published, published)
                    if published < cutoff:
                        continue
                    params = json.loads(link.get("data-params", "{}"))
                    board_id = params.get("encMenuBoardSeq")
                    menu_id = params.get("encMenuSeq")
                    if not board_id or params.get("scrtWrtiYn") is True:
                        continue
                    board_id = str(board_id)
                    if board_id in seen_board_ids:
                        continue
                    seen_board_ids.add(board_id)
                    list_path = urlparse(list_url).path
                    detail_url = urljoin(list_url, f"/menu/board/info/{list_path.rsplit('/', 1)[-1]}")
                    detail_url = requests.Request(
                        "GET", detail_url,
                        params={"encMenuSeq": menu_id, "encMenuBoardSeq": board_id},
                    ).prepare().url
                    try:
                        detail = self._get(detail_url)
                        detail_soup = BeautifulSoup(detail.text, "html.parser")
                        content_node = detail_soup.select_one(".tbl_view")
                        if not content_node:
                            raise ValueError("detail_body_missing")
                        record = self._page_record(
                            source_id=f"{source_key}-notice:{board_id}",
                            title=f"[{source_name}] {normalize_text(link.get('title') or link.get_text(' ', strip=True))}",
                            response_url=detail.url, content_node=content_node,
                            department=department, source_type=source_type, source_priority=72,
                        )
                        record["published_at"] = published.isoformat()
                        record["source_metadata"] = {
                            **record.get("source_metadata", {}),
                            "author": normalize_text(columns[-3].get_text(" ", strip=True)),
                            "sourceOrganization": source_name,
                        }
                        results.append(record)
                    except Exception as exc:
                        self.failures.append({
                            "sourceType": source_type, "url": detail_url,
                            "reason": type(exc).__name__, "message": str(exc)[:300],
                        })
                if oldest and oldest < cutoff:
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

    def _crawl_events(
        self, months: int | None = None, page_limit: int | None = None,
        detail_limit: int | None = None, search_value: str | None = None,
        attachment_limit: int | None = None,
    ) -> list[dict]:
        cutoff = date.today() - relativedelta(months=months or settings.crawler_months)
        results = []
        detailed_count = 0
        full_detail_count = 0
        effective_page_limit = page_limit or (
            settings.crawler_incremental_pages if self.incremental else settings.crawler_max_pages
        )
        for page in range(1, effective_page_limit + 1):
            self._progress("events", page, effective_page_limit)
            params = {"paginationInfo.currentPageNo": page, "searchMenuSeq": 0}
            if search_value:
                params.update({"searchType": "ttl", "searchValue": search_value})
            response = self._get(EVENT_LIST_URL, params=params)
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
                author_text = normalize_text(date_node.select_one("span").get_text(" ", strip=True)) if date_node.select_one("span") else ""
                phone_match = __import__("re").search(r"0\d{1,2}-\d{3,4}-\d{4}", author_text)
                person_match = __import__("re").search(r"([가-힣]{2,4})\s*\(?(?:0\d{1,2}-\d{3,4}-\d{4})", author_text)
                detail_url = urljoin(response.url, "/menu/board/info/e4058249224f49ab163131ce104214fb.do")
                detail_url = requests.Request("GET", detail_url, params={"encMenuSeq": menu_id, "encMenuBoardSeq": board_id}).prepare().url
                record = {
                    "source_id": f"event:{board_id}", "title": f"[행사안내] {title}", "content": summary,
                    "published_at": published.isoformat(), "source_url": detail_url, "department_name": None,
                    "attachment_names": [], "attachment_urls": [], "attachment_text": "", "content_links": [],
                    "attachment_manifest": [],
                    "source_metadata": {
                        "authorText": author_text,
                        "registrarName": person_match.group(1) if person_match else None,
                        "registrarPhone": phone_match.group(0) if phone_match else None,
                        "eventScope": "external" if title.startswith("[") else "campus",
                    },
                    "source_type": "event", "source_priority": 35, "extraction_status": "not_required",
                }
                # 최근 범위의 모든 행사 본문은 저장한다. 첨부파일/OCR만
                # 신청형 행사에 제한해 검색 누락 없이 처리비용을 제어한다.
                should_fetch_detail = (
                    detail_limit is None or full_detail_count < detail_limit or "크래프톤" in title
                )
                if should_fetch_detail:
                    try:
                        detail = self._get(detail_url)
                        detail_soup = BeautifulSoup(detail.text, "html.parser")
                        content_node = detail_soup.select_one(".tbl_view")
                        if content_node:
                            full_detail_count += 1
                            actionable = any(term in f"{title} {summary}" for term in ACTIONABLE_EVENT_TERMS)
                            max_attachments = (
                                attachment_limit
                                if attachment_limit is not None
                                else settings.crawler_event_detail_limit
                            )
                            extract_attachments = actionable and detailed_count < max_attachments
                            if extract_attachments:
                                detailed_count += 1
                            record = self._page_record(
                                source_id=record["source_id"], title=record["title"], response_url=detail.url,
                                content_node=content_node, department=None, source_type="event", source_priority=40,
                                extract_attachments=extract_attachments, source_metadata=record["source_metadata"],
                            )
                            record["published_at"] = published.isoformat()
                    except Exception as exc:
                        record["extraction_status"] = "detail_failed"
                        record["source_metadata"]["detailFailure"] = {
                            "reason": type(exc).__name__, "message": str(exc)[:300],
                        }
                results.append(record)
            if oldest and oldest < cutoff:
                break
        return results

    def _crawl_pilot_events(self) -> list[dict]:
        """최근 한 달 표본과 필수 회귀검증용 정확한 행사명을 함께 수집한다."""
        recent = self._crawl_events(
            months=1, page_limit=50, detail_limit=None, attachment_limit=20,
        )
        # 현재 날짜 기준 한 달 밖으로 밀린 필수 검증 공지는 제목 검색으로만
        # 한 건 보강한다. 일반 시험 수집 범위를 임의로 두 달로 넓히지 않는다.
        regression = self._crawl_events(
            months=2, page_limit=1, detail_limit=None, search_value="크래프톤",
            attachment_limit=1,
        )
        return list({record["source_id"]: record for record in [*recent, *regression]}.values())

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
        names, urls = self._attachment_downloads(soup, response.url)
        content_links = []
        if content_node:
            for link in content_node.select("a[href]"):
                href = link.get("href", "")
                if not href or "download.do" in href:
                    continue
                absolute = urljoin(response.url, href)
                if absolute.startswith("https://"):
                    content_links.append(absolute)
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
        content_links.extend(self._extracted_https_links(attachment_text))
        source_metadata["attachmentAudit"] = self._attachment_audit(names, urls, attachment_manifest)
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
