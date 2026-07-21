from app.services.crawler import parse_sample_html
from app.services.crawler.attachments import AttachmentExtractor
import hashlib
import json
import requests
from PIL import Image
import io
from pathlib import Path
from bs4 import BeautifulSoup

from app.services.crawler.knu import KnuNoticeCrawler


def test_sample_html_parser_extracts_attachment():
    html = '<main data-source-id="x"><h1>공지</h1><time datetime="2026-07-20T09:00:00+09:00"></time><article>본문</article><a class="attachment" href="/a.pdf">안내.pdf</a></main>'
    result = parse_sample_html(html, "https://example.invalid/notice/x")
    assert result["source_id"] == "x"
    assert result["attachment_names"] == ["안내.pdf"]
    assert result["attachment_urls"] == ["https://example.invalid/a.pdf"]


def test_sample_html_parser_extracts_non_attachment_action_link():
    html = '<main data-source-id="x"><h1>공지</h1><time datetime="2026-07-20T09:00:00+09:00"></time><article>본문 <a href="/apply">신청하기</a></article></main>'
    result = parse_sample_html(html, "https://example.invalid/notice/x")

    assert result["content_links"] == ["https://example.invalid/apply"]


def test_attachment_extractor_decodes_korean_text_file():
    text = AttachmentExtractor(requests.Session())._extract_bytes(
        "휴학 신청 절차 안내".encode("utf-8"), "안내.txt", "text/plain",
    )

    assert text == "휴학 신청 절차 안내"


def test_legacy_hwp_uses_local_html_conversion_to_keep_table_text(monkeypatch):
    def fake_run(command, **_kwargs):
        output = Path(command[command.index("--output") + 1])
        (output / "index.xhtml").write_text(
            "<html><body><table><tr><td>지원동기</td><td>팀워크 경험</td></tr></table>"
            "<p>BARUN에 업로드</p></body></html>",
            encoding="utf-8",
        )

    monkeypatch.setattr("app.services.crawler.attachments.subprocess.run", fake_run)

    text, method = AttachmentExtractor(requests.Session())._extract_bytes_with_method(
        b"HWP fixture", "지원서.hwp", "application/octet-stream",
    )

    assert text == "지원동기 팀워크 경험 BARUN에 업로드"
    assert method == "hwp_text"


def test_attachment_manifest_records_hash_type_and_extraction_result(tmp_path):
    extractor = AttachmentExtractor(requests.Session())
    extractor.cache_dir = tmp_path
    url = "https://web.kangnam.ac.kr/files/guide.pdf"
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    (tmp_path / f"{key}.json").write_text(json.dumps({
        "sha256": "abc123", "content_type": "application/pdf", "etag": "v1",
        "last_modified": "Mon, 20 Jul 2026 00:00:00 GMT", "text": "추출된 본문",
        "extraction_method": "pdf_text",
    }, ensure_ascii=False), encoding="utf-8")

    manifest = extractor.manifest(["안내.pdf"], [url])

    assert manifest[0]["sha256"] == "abc123"
    assert manifest[0]["contentType"] == "application/pdf"
    assert manifest[0]["extractionMethod"] == "pdf_text"
    assert manifest[0]["extractionStatus"] == "success"
    assert manifest[0]["extractedCharacters"] == len("추출된 본문")


def test_extensionless_image_bytes_are_detected(monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
    monkeypatch.setattr(AttachmentExtractor, "_ocr_image_with_confidence", staticmethod(lambda _image: ("이미지 공지 내용", 0.95)))

    text, method = AttachmentExtractor(requests.Session())._extract_bytes_with_method(
        buffer.getvalue(), "본문 이미지 1", "",
    )

    assert text == "이미지 공지 내용"
    assert method == "image_ocr"


def test_academic_guide_uses_real_editor_body_when_contents_is_empty():
    soup = BeautifulSoup(
        '<div class="contents"></div><div class="cont"><div class="cke_editable">'
        '<h3 class="sec_tit">졸업요건</h3><div class="sec_inner">130학점 이상 취득</div>'
        '</div></div>',
        "html.parser",
    )

    node = KnuNoticeCrawler._academic_content_node(soup)
    sections = KnuNoticeCrawler._academic_sections(node, "https://web.kangnam.ac.kr/guide")

    assert "130학점" in node.get_text(" ", strip=True)
    assert sections[0]["title"] == "졸업요건"
    assert sections[0]["content"] == "130학점 이상 취득"


def test_library_guide_uses_scoped_right_content_instead_of_global_navigation():
    soup = BeautifulSoup(
        '<nav>도서관 메뉴 ' + ('메뉴 ' * 100) + '</nav>'
        '<div class="sponge-layout-content-container-rightcontent">'
        '<h4>대출기간 연장</h4><p>반납예정일 이전에 홈페이지에서 연장 신청합니다.</p></div>',
        "html.parser",
    )

    node = KnuNoticeCrawler._academic_content_node(soup)

    assert "반납예정일 이전" in node.get_text(" ", strip=True)
    assert node.name == "div"


def test_generic_pdf_link_keeps_admission_year_context():
    soup = BeautifulSoup(
        '<table><tr><td>2021~2024학년도</td><td><a href="/download.do?id=1">PDF보기</a></td></tr></table>',
        "html.parser",
    )

    assert KnuNoticeCrawler._download_name(soup.a, 1) == "2021~2024학년도.pdf"


def test_notice_attachment_discovery_includes_file_area_outside_body():
    soup = BeautifulSoup(
        '<div class="tbl_view"><div class="contents">공지 본문</div></div>'
        '<div class="wri_area file"><a class="link_file" '
        'href="/comm/cmnFile/download.do?encFileSeq=pdf">운영 안내.pdf</a></div>'
        '<div class="wri_area file"><a class="link_file" '
        'href="/comm/cmnFile/download.do?encFileSeq=form">지원서.hwp</a></div>',
        "html.parser",
    )

    names, urls = KnuNoticeCrawler._attachment_downloads(
        soup, "https://web.kangnam.ac.kr/menu/notice.do",
    )

    assert names == ["운영 안내.pdf", "지원서.hwp"]
    assert urls == [
        "https://web.kangnam.ac.kr/comm/cmnFile/download.do?encFileSeq=pdf",
        "https://web.kangnam.ac.kr/comm/cmnFile/download.do?encFileSeq=form",
    ]


def test_attachment_audit_exposes_collection_and_extraction_completeness():
    audit = KnuNoticeCrawler._attachment_audit(
        ["운영 안내.pdf", "지원서.hwp"],
        ["https://web.kangnam.ac.kr/a", "https://web.kangnam.ac.kr/b"],
        [
            {"extractionStatus": "success"},
            {"extractionStatus": "failed", "failureReason": "unsupported"},
        ],
    )

    assert audit == {
        "discoveredCount": 2,
        "storedCount": 2,
        "manifestCount": 2,
        "extractedCount": 1,
        "collectionComplete": True,
        "textExtractionComplete": False,
    }


def test_pdf_url_followed_by_korean_text_is_not_overcaptured():
    links = KnuNoticeCrawler._extracted_https_links(
        "https://barun.kyonggi.ac.kr/링크 접속 → 외부회원 가입",
    )

    assert links == ["https://barun.kyonggi.ac.kr/"]


def test_pdf_text_keeps_page_locators(monkeypatch):
    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    class Reader:
        def __init__(self, _stream):
            self.pages = [Page("첫 페이지 " * 30), Page("둘째 페이지 " * 30)]

    monkeypatch.setattr("app.services.crawler.attachments.PdfReader", Reader)
    text, method, _confidence, page_count = AttachmentExtractor(requests.Session())._extract_pdf_with_metadata(b"pdf")

    assert "[PDF page 1]" in text
    assert "[PDF page 2]" in text
    assert method == "pdf_text"
    assert page_count == 2


def test_ocr_business_fields_with_broken_patterns_require_review():
    broken = "휴학 신청 기간과 문의 전화 안내 " + ("상세 안내 문구 " * 20)
    complete = (
        "휴학 신청 기간은 2026.07.21부터 2026.07.31까지이며 "
        "문의 전화는 031-280-3000입니다. 신청 방법은 포털 로그인 후 신청서 작성 및 제출입니다. "
        + ("공식 안내 " * 20)
    )

    assert AttachmentExtractor._ocr_needs_review(broken, 0.92) is True
    assert AttachmentExtractor._ocr_needs_review(complete, 0.92) is False


def test_small_cms_image_is_upscaled_before_ocr(monkeypatch):
    captured = {}

    def fake_image_to_data(image, **_kwargs):
        captured["size"] = image.size
        return {"text": ["등록금", "반환"], "conf": ["95", "90"]}

    monkeypatch.setattr("app.services.crawler.attachments.pytesseract.image_to_data", fake_image_to_data)

    text, confidence = AttachmentExtractor._ocr_image_with_confidence(
        Image.new("RGB", (551, 122), "white"),
    )

    assert captured["size"][0] >= 1400
    assert captured["size"][1] >= 300
    assert text == "등록금 반환"
    assert confidence == 0.925


def test_table_ocr_retries_with_table_layout_when_block_mode_is_empty(monkeypatch):
    configs = []

    def fake_image_to_data(_image, **kwargs):
        configs.append(kwargs["config"])
        if kwargs["config"] == "--psm 6":
            return {"text": [], "conf": []}
        return {
            "text": ["학기", "개시일부터", "30일까지", "등록금의", "6분의", "5", "해당액"],
            "conf": ["90"] * 7,
        }

    monkeypatch.setattr("app.services.crawler.attachments.pytesseract.image_to_data", fake_image_to_data)

    text, confidence = AttachmentExtractor._ocr_image_with_confidence(
        Image.new("RGB", (551, 122), "white"),
    )

    assert configs == ["--psm 6", "--psm 4"]
    assert "등록금의 6분의 5 해당액" in text
    assert confidence == 0.9


def test_same_attachment_hash_reuses_extraction_across_urls(tmp_path, monkeypatch):
    class Response:
        status_code = 200
        headers = {"content-type": "text/plain"}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(_size):
            yield "동일한 공식 첨부 본문".encode("utf-8")

    class Session:
        @staticmethod
        def get(*_args, **_kwargs):
            return Response()

    extractor = AttachmentExtractor(Session())
    extractor.cache_dir = tmp_path
    calls = []
    original = extractor._extract_bytes_with_metadata

    def counted(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(extractor, "_extract_bytes_with_metadata", counted)
    first = extractor.extract("https://web.kangnam.ac.kr/file/a", "안내.txt")
    second = extractor.extract("https://web.kangnam.ac.kr/file/b", "복사본.txt")

    assert first == second == "동일한 공식 첨부 본문"
    assert len(calls) == 1


def test_recent_successful_attachment_cache_skips_redownload(tmp_path):
    url = "https://web.kangnam.ac.kr/file/cached.pdf"
    cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    (tmp_path / f"{cache_key}.json").write_text(json.dumps({
        "url": url,
        "extractor_version": AttachmentExtractor.EXTRACTOR_VERSION,
        "extraction_status": "success",
        "text": "이미 검증한 첨부 본문",
    }, ensure_ascii=False), encoding="utf-8")

    class Session:
        @staticmethod
        def get(*_args, **_kwargs):
            raise AssertionError("TTL 안의 캐시는 다시 내려받으면 안 됩니다.")

    extractor = AttachmentExtractor(Session())
    extractor.cache_dir = tmp_path

    assert extractor.extract(url, "안내.pdf") == "이미 검증한 첨부 본문"


def test_event_title_search_is_forwarded_to_school_board(monkeypatch):
    requested = []

    class Response:
        url = "https://web.kangnam.ac.kr/menu/events.do"
        text = '<div class="tbody"></div>'

        @staticmethod
        def raise_for_status():
            return None

    crawler = KnuNoticeCrawler(profile="pilot")

    def fake_get(url, **kwargs):
        requested.append((url, kwargs.get("params")))
        return Response()

    monkeypatch.setattr(crawler, "_get", fake_get)
    crawler._crawl_events(months=2, page_limit=1, search_value="크래프톤")

    assert requested[0][1]["searchType"] == "ttl"
    assert requested[0][1]["searchValue"] == "크래프톤"


def test_subsite_board_stops_when_server_repeats_last_page(monkeypatch):
    listing = '''<div class="devTable"><div class="tbody"><ul>
      <li>1</li><li><a class="detailLink" title="교환학생 모집" data-params='{"encMenuBoardSeq":"board-1","encMenuSeq":"menu-1"}'>교환학생 모집</a></li>
      <li>대외교류센터</li><li>26.07.20</li><li>10</li>
    </ul></div></div>'''

    class Response:
        def __init__(self, url, text):
            self.url = url
            self.text = text

    listing_calls = []
    detail_calls = []
    crawler = KnuNoticeCrawler(profile="full")
    monkeypatch.setattr(
        "app.services.crawler.knu.SUBSITE_BOARD_SOURCES",
        [("international", "대외교류센터", "https://oia.kangnam.ac.kr/list.do", "대외교류센터", "international_notice", 24)],
    )

    def fake_get(url, **kwargs):
        if "/menu/board/info/" in url:
            detail_calls.append(url)
            return Response(url, '<div class="tbl_view"><p>공식 모집 본문</p></div>')
        listing_calls.append(kwargs.get("params", {}).get("paginationInfo.currentPageNo"))
        return Response(url, listing)

    monkeypatch.setattr(crawler, "_get", fake_get)
    rows = crawler._crawl_subsite_boards()

    assert len(rows) == 1
    assert listing_calls == [1, 2]
    assert len(detail_calls) == 1


def test_public_regulation_frames_are_parsed_as_versioned_sources(monkeypatch):
    listing = """<table><tr onclick=\"movepageNew('true','8','2,1,1')\">
        <td>강남대학교학칙</td></tr></table>""".encode("euc-kr")
    detail = """<html><body><h1>강남대학교학칙</h1>
        <b>[2026.05.04 개정]</b><p>제1조 공식 규정 본문</p></body></html>""".encode("euc-kr")

    class Response:
        def __init__(self, content):
            self.content = content

    crawler = KnuNoticeCrawler(profile="full")
    responses = iter([Response(listing), Response(detail)])
    monkeypatch.setattr(crawler, "_post", lambda *_args, **_kwargs: next(responses))

    rows = crawler._crawl_regulations()

    assert len(rows) == 1
    assert rows[0]["source_id"] == "regulation:2:1:1:2026-05-04"
    assert rows[0]["source_metadata"]["versionStatus"] == "current"
    assert "제1조 공식 규정 본문" in rows[0]["content"]
