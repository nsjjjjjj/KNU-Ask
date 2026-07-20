#!/usr/bin/env python3
"""강남대학교 학사안내와 최근 공지사항을 수집한다.

기본 동작:
- 지정된 학사안내 메뉴의 공개 본문을 수집한다.
- 공지사항은 실행일 기준 최근 2개월만 상세 페이지까지 수집한다.
- SQLite를 동기화하고 JSON 내보내기를 생성한다.
- 첨부파일은 기본적으로 메타데이터만 저장한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag
from dateutil.relativedelta import relativedelta


BASE_URL = "https://web.kangnam.ac.kr"
NOTICE_LIST_URL = BASE_URL + "/menu/f19069e6134f8f8aa7f689a4a675e66f.do"
NOTICE_DETAIL_URL = BASE_URL + "/menu/board/info/f19069e6134f8f8aa7f689a4a675e66f.do"

PHONE_PATTERN = re.compile(r"0\d{1,2}-\d{3,4}-\d{4}")
SPACE_PATTERN = re.compile(r"[ \t\r\f\v]+")
MULTI_NEWLINE_PATTERN = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class ReferenceTarget:
    key: str
    category: str
    label: str
    url: str


REFERENCE_TARGETS = [
    ReferenceTarget("academic_calendar", "학사일정", "학부 학사일정", f"{BASE_URL}/menu/02be162adc07170ec7ee034097d627e9.do"),
    ReferenceTarget("registration_guide", "등록", "등록안내", f"{BASE_URL}/menu/2af7d58d42bb566f3d177204bfacd937.do"),
    ReferenceTarget("tuition_refund", "등록", "등록금반환", f"{BASE_URL}/menu/b044668e4cdff06c9e979c1e6ebf535c.do"),
    ReferenceTarget("classes", "학사", "수업", f"{BASE_URL}/menu/fd8c126ac0e81458620beb18302bc271.do"),
    ReferenceTarget("credit_exchange", "학사", "학점교류", f"{BASE_URL}/menu/b6cdaa2d20ed253e51964ec6c6aeba1e.do"),
    ReferenceTarget("major", "학사", "전공", f"{BASE_URL}/menu/f18a64130de18975bfc2127ba53ee768.do"),
    ReferenceTarget("multiple_major", "학사", "다전공", f"{BASE_URL}/menu/b2d1211af4999ac7a3ae1e11ad581860.do"),
    ReferenceTarget("leave_return", "학사", "휴복학", f"{BASE_URL}/menu/12d2ee44cc4e95562f84a01bf953a054.do"),
    ReferenceTarget("graduation", "학사", "졸업", f"{BASE_URL}/menu/c5dc4b1d7b4dd402e5e6a7a8471eb55c.do"),
    ReferenceTarget("student_status", "학사", "학적", f"{BASE_URL}/menu/41c4ba211ab06cbc003455e07441b4f8.do"),
    ReferenceTarget("attendance", "학사", "전자출결시스템", f"{BASE_URL}/menu/86b86ff51a4c7d33a2cea85a3f4d8d40.do"),
    ReferenceTarget("certificates", "학사", "증명서 발급", f"{BASE_URL}/menu/b46b6e20bc53a0234ac9fc9a238b113a.do"),
    ReferenceTarget("teaching", "교직안내", "교직안내", "https://education.kangnam.ac.kr/"),
    ReferenceTarget("military_service", "병무", "학생병사", f"{BASE_URL}/menu/3b97657335d025de913a940dc19fa6b8.do"),
    ReferenceTarget("reserve_forces", "병무", "예비군", f"{BASE_URL}/menu/246d562e295a939edc605190e2b0221e.do"),
    ReferenceTarget("rotc", "병무", "ROTC", f"{BASE_URL}/menu/1d556fac41442ec4d365ad79cc53f2be.do"),
    ReferenceTarget("startup_credit", "창업교육안내", "창업대체학점인정제", f"{BASE_URL}/menu/a1ed5a7cd101b044997be8a2ff63b979.do"),
    ReferenceTarget("startup_leave", "창업교육안내", "창업휴학", f"{BASE_URL}/menu/07e3888786f407dd1940d83642a5788f.do"),
    ReferenceTarget("student_guides", "대학생활안내", "가이드 및 편람", f"{BASE_URL}/menu/3d1345da241450c621fe02936aeb96e7.do"),
    ReferenceTarget("recommended_books", "대학생활안내", "교양권장도서 100권 플러스", f"{BASE_URL}/menu/05e8ba854da6fe57e5092c60a1539e8c.do"),
]


def clean_text(value: str) -> str:
    lines = []
    for raw_line in value.splitlines():
        line = SPACE_PATTERN.sub(" ", raw_line).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return MULTI_NEWLINE_PATTERN.sub("\n\n", "\n".join(lines)).strip()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_notice_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%y.%m.%d").date()


def safe_filename(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" .")
    return value[:180] or "attachment"


class KnuCrawler:
    def __init__(self, delay: float, timeout: float) -> None:
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "KNU-Ask-Crawler/1.0 (educational demo; contact: project administrator)",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
        })
        self._last_request_at = 0.0

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        self._last_request_at = time.monotonic()
        response.raise_for_status()
        return response

    def soup(self, url: str, *, params: dict[str, Any] | None = None) -> BeautifulSoup:
        response = self.request("GET", url, params=params)
        return BeautifulSoup(response.text, "html.parser")

    @staticmethod
    def attachment_links(container: Tag, page_url: str) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for link in container.select("a[href*='download.do']"):
            url = urljoin(page_url, link.get("href", ""))
            if not url or url in seen:
                continue
            seen.add(url)
            items.append({
                "name": clean_text(link.get_text(" ", strip=True)) or "첨부파일",
                "url": url,
            })
        return items

    def crawl_reference(self, target: ReferenceTarget) -> dict[str, Any]:
        soup = self.soup(target.url)
        container = soup.select_one(".contents .sec_inner")
        if container is None:
            container = soup.select_one("main") or soup.select_one(".contents") or soup.body
        if container is None:
            raise ValueError(f"본문 영역을 찾지 못했습니다: {target.url}")

        clone = BeautifulSoup(str(container), "html.parser")
        for node in clone.select("script, style, noscript, .sns_area, .pagination"):
            node.decompose()
        content = clean_text(clone.get_text("\n", strip=True))
        attachments = self.attachment_links(container, target.url)
        return {
            **asdict(target),
            "content": content,
            "content_hash": sha256_text(content),
            "attachments": attachments,
            "crawled_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def crawl_notice_list_page(self, page: int) -> tuple[list[dict[str, Any]], int]:
        soup = self.soup(NOTICE_LIST_URL, params={
            "paginationInfo.currentPageNo": page,
            "searchMenuSeq": 0,
        })
        pagination = soup.select_one("ul.pagination[data-params]")
        max_page = page
        if pagination:
            try:
                max_page = int(json.loads(pagination.get("data-params", "{}"))["max"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass

        rows: list[dict[str, Any]] = []
        for row in soup.select(".devTable .tbody > ul"):
            columns = row.find_all("li", recursive=False)
            link = row.select_one("a.detailLink[data-params]")
            if not link or len(columns) < 7:
                continue
            try:
                detail_params = json.loads(link.get("data-params", "{}"))
            except json.JSONDecodeError:
                continue
            notice_id = detail_params.get("encMenuBoardSeq")
            menu_id = detail_params.get("encMenuSeq")
            if not notice_id or not menu_id:
                continue
            published = parse_notice_date(columns[5].get_text(" ", strip=True))
            first_column = columns[0].get_text(" ", strip=True)
            rows.append({
                "notice_id": notice_id,
                "menu_id": menu_id,
                "title": link.get("title", "").strip() or clean_text(link.get_text(" ", strip=True)),
                "category": clean_text(columns[2].get_text(" ", strip=True)),
                "department": clean_text(columns[4].get_text(" ", strip=True)),
                "published_at": published.isoformat(),
                "views": int(re.sub(r"\D", "", columns[6].get_text(" ", strip=True)) or 0),
                "is_pinned": not first_column.isdigit(),
            })
        return rows, max_page

    def crawl_notice_detail(self, item: dict[str, Any]) -> dict[str, Any]:
        params = {
            "encMenuSeq": item["menu_id"],
            "encMenuBoardSeq": item["notice_id"],
        }
        soup = self.soup(NOTICE_DETAIL_URL, params=params)
        title_node = soup.select_one(".tblw_subj")
        content_node = soup.select_one(".tbl_view")
        contact_node = soup.select_one(".inputUserCtc")
        content = clean_text(content_node.get_text("\n", strip=True)) if content_node else ""
        contact = clean_text(contact_node.get_text(" ", strip=True)) if contact_node else ""
        detail_url = requests.Request("GET", NOTICE_DETAIL_URL, params=params).prepare().url
        attachments = self.attachment_links(soup.select_one(".contents .sec_inner") or soup, detail_url)
        body_phones = sorted(set(PHONE_PATTERN.findall(content)))
        contact_phones = PHONE_PATTERN.findall(contact)
        return {
            **item,
            "title": clean_text(title_node.get_text(" ", strip=True)) if title_node else item["title"],
            "content": content,
            "content_hash": sha256_text(item["title"] + "\n" + content),
            "contact": contact,
            "contact_phone": contact_phones[0] if contact_phones else None,
            "body_phones": body_phones,
            "attachments": attachments,
            "source_url": detail_url,
            "crawled_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def crawl_recent_notices(self, cutoff: date, max_pages: int) -> tuple[list[dict[str, Any]], bool]:
        candidates: dict[str, dict[str, Any]] = {}
        completed_window = False
        page = 1
        site_max_page = max_pages

        while page <= min(max_pages, site_max_page):
            logging.info("공지 목록 %s페이지 수집", page)
            rows, site_max_page = self.crawl_notice_list_page(page)
            normal_dates = []
            for row in rows:
                published = date.fromisoformat(row["published_at"])
                if not row["is_pinned"]:
                    normal_dates.append(published)
                if published >= cutoff:
                    candidates[row["notice_id"]] = row

            if normal_dates and min(normal_dates) < cutoff:
                completed_window = True
                break
            if page >= site_max_page:
                completed_window = True
                break
            page += 1

        notices = []
        total = len(candidates)
        for index, item in enumerate(candidates.values(), start=1):
            logging.info("공지 상세 %s/%s: %s", index, total, item["title"])
            try:
                notices.append(self.crawl_notice_detail(item))
            except Exception:
                logging.exception("공지 상세 수집 실패: %s", item["notice_id"])
        return notices, completed_window and len(notices) == total

    def download_attachment(self, attachment: dict[str, str], destination: Path, max_bytes: int) -> dict[str, Any]:
        destination.mkdir(parents=True, exist_ok=True)
        response = self.request("GET", attachment["url"], stream=True)
        length = int(response.headers.get("content-length", "0") or 0)
        if length and length > max_bytes:
            raise ValueError(f"첨부파일 용량 제한 초과: {length} bytes")
        path = destination / safe_filename(attachment["name"])
        digest = hashlib.sha256()
        size = 0
        with path.open("wb") as file:
            for chunk in response.iter_content(1024 * 128):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    file.close()
                    path.unlink(missing_ok=True)
                    raise ValueError(f"첨부파일 용량 제한 초과: {size} bytes")
                digest.update(chunk)
                file.write(chunk)
        return {
            **attachment,
            "local_path": str(path.resolve()),
            "size": size,
            "sha256": digest.hexdigest(),
            "content_type": response.headers.get("content-type"),
        }


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS reference_pages (
            key TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            label TEXT NOT NULL,
            url TEXT NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            attachments_json TEXT NOT NULL,
            crawled_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notices (
            notice_id TEXT PRIMARY KEY,
            menu_id TEXT NOT NULL,
            title TEXT NOT NULL,
            category TEXT,
            department TEXT,
            published_at TEXT NOT NULL,
            views INTEGER NOT NULL DEFAULT 0,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            contact TEXT,
            contact_phone TEXT,
            body_phones_json TEXT NOT NULL,
            source_url TEXT NOT NULL,
            crawled_at TEXT NOT NULL,
            last_seen_run TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attachments (
            attachment_key TEXT PRIMARY KEY,
            notice_id TEXT NOT NULL REFERENCES notices(notice_id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            local_path TEXT,
            size INTEGER,
            sha256 TEXT,
            content_type TEXT
        );
    """)
    return connection


def save_reference(connection: sqlite3.Connection, page: dict[str, Any]) -> None:
    connection.execute("""
        INSERT INTO reference_pages
            (key, category, label, url, content, content_hash, attachments_json, crawled_at)
        VALUES (:key, :category, :label, :url, :content, :content_hash, :attachments_json, :crawled_at)
        ON CONFLICT(key) DO UPDATE SET
            category=excluded.category, label=excluded.label, url=excluded.url,
            content=excluded.content, content_hash=excluded.content_hash,
            attachments_json=excluded.attachments_json, crawled_at=excluded.crawled_at
    """, {**page, "attachments_json": json.dumps(page["attachments"], ensure_ascii=False)})


def save_notice(connection: sqlite3.Connection, notice: dict[str, Any], run_id: str) -> None:
    connection.execute("""
        INSERT INTO notices
            (notice_id, menu_id, title, category, department, published_at, views,
             content, content_hash, contact, contact_phone, body_phones_json,
             source_url, crawled_at, last_seen_run)
        VALUES
            (:notice_id, :menu_id, :title, :category, :department, :published_at, :views,
             :content, :content_hash, :contact, :contact_phone, :body_phones_json,
             :source_url, :crawled_at, :last_seen_run)
        ON CONFLICT(notice_id) DO UPDATE SET
            title=excluded.title, category=excluded.category, department=excluded.department,
            published_at=excluded.published_at, views=excluded.views, content=excluded.content,
            content_hash=excluded.content_hash, contact=excluded.contact,
            contact_phone=excluded.contact_phone, body_phones_json=excluded.body_phones_json,
            source_url=excluded.source_url, crawled_at=excluded.crawled_at,
            last_seen_run=excluded.last_seen_run
    """, {
        **notice,
        "body_phones_json": json.dumps(notice["body_phones"], ensure_ascii=False),
        "last_seen_run": run_id,
    })
    connection.execute("DELETE FROM attachments WHERE notice_id = ?", (notice["notice_id"],))
    for attachment in notice["attachments"]:
        attachment_key = hashlib.sha256(attachment["url"].encode("utf-8")).hexdigest()
        connection.execute("""
            INSERT INTO attachments
                (attachment_key, notice_id, name, url, local_path, size, sha256, content_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            attachment_key, notice["notice_id"], attachment["name"], attachment["url"],
            attachment.get("local_path"), attachment.get("size"), attachment.get("sha256"),
            attachment.get("content_type"),
        ))


def export_json(connection: sqlite3.Connection, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    references = []
    for row in connection.execute("SELECT * FROM reference_pages ORDER BY category, label"):
        item = dict(row)
        item["attachments"] = json.loads(item.pop("attachments_json"))
        references.append(item)

    notices = []
    for row in connection.execute("SELECT * FROM notices ORDER BY published_at DESC, notice_id"):
        item = dict(row)
        item["body_phones"] = json.loads(item.pop("body_phones_json"))
        item.pop("last_seen_run", None)
        item["attachments"] = [dict(file) for file in connection.execute(
            "SELECT name, url, local_path, size, sha256, content_type FROM attachments WHERE notice_id = ? ORDER BY name",
            (item["notice_id"],),
        )]
        notices.append(item)

    (output_dir / "reference_pages.json").write_text(
        json.dumps(references, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "notices.json").write_text(
        json.dumps(notices, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="강남대학교 학사안내 및 최근 공지 크롤러")
    parser.add_argument("--months", type=int, default=2, help="수집할 공지 기간(개월, 기본 2)")
    parser.add_argument("--max-pages", type=int, default=30, help="공지 목록 최대 탐색 페이지")
    parser.add_argument("--delay", type=float, default=0.6, help="HTTP 요청 간 최소 대기시간(초)")
    parser.add_argument("--timeout", type=float, default=20, help="HTTP 요청 제한시간(초)")
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    parser.add_argument("--db", type=Path, default=None, help="SQLite 경로(기본: output-dir/knu.db)")
    parser.add_argument("--skip-reference", action="store_true", help="학사안내 페이지 수집 생략")
    parser.add_argument("--skip-notices", action="store_true", help="공지사항 수집 생략")
    parser.add_argument("--download-attachments", action="store_true", help="공지 첨부파일 원본 다운로드")
    parser.add_argument("--max-attachment-mb", type=int, default=20, help="첨부파일 1개 최대 용량")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.months < 1:
        raise SystemExit("--months는 1 이상이어야 합니다.")

    output_dir = args.output_dir.resolve()
    db_path = (args.db or output_dir / "knu.db").resolve()
    cutoff = date.today() - relativedelta(months=args.months)
    run_id = datetime.now().astimezone().isoformat(timespec="seconds")
    crawler = KnuCrawler(args.delay, args.timeout)
    connection = connect_database(db_path)

    reference_success = 0
    if not args.skip_reference:
        for target in REFERENCE_TARGETS:
            logging.info("안내 페이지 수집: %s > %s", target.category, target.label)
            try:
                page = crawler.crawl_reference(target)
                save_reference(connection, page)
                reference_success += 1
                connection.commit()
            except Exception:
                logging.exception("안내 페이지 수집 실패: %s", target.url)

    notice_success = 0
    notice_complete = False
    if not args.skip_notices:
        notices, notice_complete = crawler.crawl_recent_notices(cutoff, args.max_pages)
        for notice in notices:
            if args.download_attachments:
                downloaded = []
                for attachment in notice["attachments"]:
                    try:
                        downloaded.append(crawler.download_attachment(
                            attachment,
                            output_dir / "attachments" / notice["notice_id"],
                            args.max_attachment_mb * 1024 * 1024,
                        ))
                    except Exception:
                        logging.exception("첨부파일 다운로드 실패: %s", attachment["url"])
                        downloaded.append(attachment)
                notice["attachments"] = downloaded
            save_notice(connection, notice, run_id)
            notice_success += 1

        # 기간 밖 데이터는 항상 제거한다. 원본에서 삭제된 공지는 수집 구간을 완주했을 때만 제거한다.
        connection.execute("DELETE FROM notices WHERE published_at < ?", (cutoff.isoformat(),))
        if notice_complete:
            connection.execute("DELETE FROM notices WHERE last_seen_run <> ?", (run_id,))
        else:
            logging.warning("공지 수집이 완전하지 않아 원본 삭제 공지 동기화는 생략합니다.")
        connection.commit()

    export_json(connection, output_dir)
    stored_notices = connection.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
    stored_references = connection.execute("SELECT COUNT(*) FROM reference_pages").fetchone()[0]
    connection.close()
    logging.info(
        "완료: 안내 %s개 수집(%s개 저장), 공지 %s개 수집(%s개 저장), 기준일 %s",
        reference_success, stored_references, notice_success, stored_notices, cutoff,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
