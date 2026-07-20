from app.services.crawler import parse_sample_html
from app.services.crawler.attachments import AttachmentExtractor
import hashlib
import json
import requests
from PIL import Image
import io


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
    monkeypatch.setattr(AttachmentExtractor, "_ocr_image", staticmethod(lambda _image: "이미지 공지 내용"))

    text, method = AttachmentExtractor(requests.Session())._extract_bytes_with_method(
        buffer.getvalue(), "본문 이미지 1", "",
    )

    assert text == "이미지 공지 내용"
    assert method == "image_ocr"
