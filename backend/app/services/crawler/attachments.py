from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import pytesseract
import requests
from PIL import Image
from pypdf import PdfReader

from app.core.config import settings
from app.utils.text import normalize_text


class AttachmentExtractor:
    """첨부파일을 파일 해시 캐시와 함께 한 번만 추출한다."""

    EXTRACTOR_VERSION = 2

    def __init__(self, session: requests.Session) -> None:
        self.session = session
        self.cache_dir = Path(settings.crawler_attachment_cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def extract_many(self, names: list[str], urls: list[str]) -> tuple[str, str]:
        if not settings.crawler_extract_attachments or not urls:
            return "", "not_required" if not urls else "disabled"
        texts: list[str] = []
        failures = 0
        for index, url in enumerate(urls):
            name = names[index] if index < len(names) else url.rsplit("/", 1)[-1]
            try:
                text = self.extract(url, name)
                if text:
                    texts.append(f"[첨부파일: {name}]\n{text}")
                else:
                    failures += 1
            except Exception:
                failures += 1
        status = "success" if texts and not failures else "partial" if texts else "failed"
        return normalize_text("\n\n".join(texts)), status

    def manifest(self, names: list[str], urls: list[str]) -> list[dict]:
        """본문을 중복 저장하지 않고 첨부의 출처·무결성·추출 결과를 기록한다."""
        result = []
        for index, url in enumerate(urls):
            name = names[index] if index < len(names) else url.rsplit("/", 1)[-1]
            cache_path = self.cache_dir / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"
            cached = {}
            if cache_path.exists():
                try:
                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                except Exception:
                    cached = {}
            text = cached.get("text", "")
            result.append({
                "name": name,
                "url": url,
                "sha256": cached.get("sha256"),
                "contentType": cached.get("content_type"),
                "etag": cached.get("etag"),
                "lastModified": cached.get("last_modified"),
                "extractionMethod": cached.get("extraction_method"),
                "extractionStatus": "success" if text else "failed" if cached else "not_extracted",
                "extractedCharacters": len(text),
            })
        return result

    def extract(self, url: str, name: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith("kangnam.ac.kr"):
            return ""
        cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        cache_path = self.cache_dir / f"{cache_key}.json"
        cached: dict = {}
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))

        limit = settings.crawler_attachment_max_mb * 1024 * 1024
        conditional_headers = {}
        if cached.get("etag"):
            conditional_headers["If-None-Match"] = cached["etag"]
        if cached.get("last_modified"):
            conditional_headers["If-Modified-Since"] = cached["last_modified"]
        response = self.session.get(url, timeout=30, stream=True, headers=conditional_headers)
        if response.status_code == 304:
            return cached.get("text", "")
        response.raise_for_status()
        length = int(response.headers.get("content-length") or 0)
        if length and length > limit:
            return ""
        body = bytearray()
        for block in response.iter_content(64 * 1024):
            body.extend(block)
            if len(body) > limit:
                return ""

        body_digest = hashlib.sha256(body).hexdigest()
        if (
            cached.get("sha256") == body_digest
            and cached.get("extractor_version") == self.EXTRACTOR_VERSION
        ):
            return cached.get("text", "")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        text, extraction_method = self._extract_bytes_with_method(bytes(body), name, content_type)
        cache_path.write_text(json.dumps({
            "url": url,
            "sha256": body_digest,
            "etag": response.headers.get("etag"),
            "last_modified": response.headers.get("last-modified"),
            "content_type": content_type,
            "extraction_method": extraction_method,
            "extractor_version": self.EXTRACTOR_VERSION,
            "text": text,
        }, ensure_ascii=False), encoding="utf-8")
        return text

    def _extract_bytes(self, body: bytes, name: str, content_type: str) -> str:
        return self._extract_bytes_with_method(body, name, content_type)[0]

    def _extract_bytes_with_method(self, body: bytes, name: str, content_type: str) -> tuple[str, str]:
        suffix = Path(name.lower().split("?", 1)[0]).suffix
        if suffix == ".pdf" or content_type == "application/pdf" or body.startswith(b"%PDF-"):
            return self._extract_pdf_with_method(body)
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"} or content_type.startswith("image/"):
            return self._ocr_image(Image.open(io.BytesIO(body))), "image_ocr"
        # 강남대 image.do는 일부 본문 이미지에 확장자와 Content-Type을
        # 제공하지 않는다. Pillow가 실제 바이트를 이미지로 확인할 때만 OCR한다.
        try:
            image = Image.open(io.BytesIO(body))
            image.verify()
            image = Image.open(io.BytesIO(body))
            return self._ocr_image(image), "image_ocr"
        except Exception:
            pass
        if suffix in {".hwpx", ".docx", ".pptx", ".xlsx"}:
            return self._extract_xml_archive(body), "xml_text"
        if suffix in {".txt", ".csv"} or content_type.startswith("text/"):
            return self._decode_text(body), "plain_text"
        return "", "unsupported"

    def _extract_pdf(self, body: bytes) -> str:
        return self._extract_pdf_with_method(body)[0]

    def _extract_pdf_with_method(self, body: bytes) -> tuple[str, str]:
        try:
            reader = PdfReader(io.BytesIO(body))
            pages = [normalize_text(page.extract_text() or "") for page in reader.pages[:50]]
            text = "\n".join(page for page in pages if page)
        except Exception:
            text = ""
        if len(text) >= 120:
            return text, "pdf_text"

        # 스캔 PDF는 앞 20페이지만 이미지화하여 한국어+영어 OCR을 수행한다.
        with tempfile.TemporaryDirectory(prefix="knuask-pdf-") as temp_dir:
            pdf_path = Path(temp_dir) / "source.pdf"
            output_prefix = Path(temp_dir) / "page"
            pdf_path.write_bytes(body)
            subprocess.run(
                ["pdftoppm", "-f", "1", "-l", "20", "-jpeg", "-r", "180", str(pdf_path), str(output_prefix)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
            )
            ocr = [self._ocr_image(Image.open(path)) for path in sorted(Path(temp_dir).glob("page-*.jpg"))]
        return normalize_text("\n".join(part for part in ocr if part)) or text, "pdf_ocr"

    @staticmethod
    def _ocr_image(image: Image.Image) -> str:
        if image.width * image.height > 20_000_000:
            image.thumbnail((4500, 4500))
        return normalize_text(pytesseract.image_to_string(image.convert("RGB"), lang="kor+eng", config="--psm 6"))

    @staticmethod
    def _extract_xml_archive(body: bytes) -> str:
        parts: list[str] = []
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            for member in archive.namelist():
                lowered = member.lower()
                if not lowered.endswith(".xml") or not any(token in lowered for token in (
                    "contents/section", "word/document", "ppt/slides/slide", "sharedstrings",
                )):
                    continue
                xml = AttachmentExtractor._decode_text(archive.read(member))
                parts.extend(re.findall(r">([^<>]+)<", xml))
        return normalize_text(" ".join(parts))

    @staticmethod
    def _decode_text(body: bytes) -> str:
        for encoding in ("utf-8", "cp949", "euc-kr", "utf-16"):
            try:
                return body.decode(encoding)
            except UnicodeDecodeError:
                continue
        return body.decode("utf-8", errors="ignore")
