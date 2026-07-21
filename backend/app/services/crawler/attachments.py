from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import pytesseract
import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from bs4 import BeautifulSoup
from pypdf import PdfReader

from app.core.config import settings
from app.utils.text import normalize_text


class AttachmentExtractionError(RuntimeError):
    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


class AttachmentExtractor:
    """첨부파일을 파일 해시 캐시와 함께 한 번만 추출한다."""

    EXTRACTOR_VERSION = 7

    def __init__(self, session: requests.Session) -> None:
        self.session = session
        self.request_timeout_seconds = 30.0
        self.max_pdf_pages = 50
        self.max_ocr_pages = 20
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
                "extractionStatus": cached.get("extraction_status") or ("success" if text else "failed" if cached else "not_extracted"),
                "failureReason": cached.get("failure_reason"),
                "ocrConfidence": cached.get("ocr_confidence"),
                "pageCount": cached.get("page_count"),
                "needsReview": bool(cached.get("needs_review")),
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

        # 강남대 파일 서버는 ETag/Last-Modified를 주지 않는 경우가 많다.
        # 성공한 최신 캐시는 짧은 TTL 동안 그대로 사용하고, TTL이 지난 뒤
        # 다시 받아 동일 해시인지 확인한다. OCR은 아래 해시 캐시로 별도 보호된다.
        cache_age_seconds = time.time() - cache_path.stat().st_mtime if cache_path.exists() else None
        cache_ttl_seconds = max(0, settings.crawler_attachment_cache_ttl_hours) * 3600
        if (
            cached.get("extractor_version") == self.EXTRACTOR_VERSION
            and cached.get("extraction_status") == "success"
            and cache_age_seconds is not None
            and cache_age_seconds < cache_ttl_seconds
        ):
            return cached.get("text", "")

        limit = settings.crawler_attachment_max_mb * 1024 * 1024
        conditional_headers = {}
        cache_version_matches = cached.get("extractor_version") == self.EXTRACTOR_VERSION
        if cache_version_matches and cached.get("etag"):
            conditional_headers["If-None-Match"] = cached["etag"]
        if cache_version_matches and cached.get("last_modified"):
            conditional_headers["If-Modified-Since"] = cached["last_modified"]
        try:
            response = self.session.get(
                url, timeout=self.request_timeout_seconds, stream=True, headers=conditional_headers,
            )
        except requests.Timeout as exc:
            self._write_cache(cache_path, {**cached, "url": url, "extraction_status": "failed", "failure_reason": "timeout"})
            raise AttachmentExtractionError("timeout") from exc
        except requests.RequestException as exc:
            self._write_cache(cache_path, {**cached, "url": url, "extraction_status": "failed", "failure_reason": "download_failed"})
            raise AttachmentExtractionError("download_failed") from exc
        if response.status_code == 304:
            return cached.get("text", "")
        response.raise_for_status()
        length = int(response.headers.get("content-length") or 0)
        if length and length > limit:
            self._write_cache(cache_path, {**cached, "url": url, "extraction_status": "failed", "failure_reason": "size_limit"})
            return ""
        body = bytearray()
        for block in response.iter_content(64 * 1024):
            body.extend(block)
            if len(body) > limit:
                self._write_cache(cache_path, {**cached, "url": url, "extraction_status": "failed", "failure_reason": "size_limit"})
                return ""

        body_digest = hashlib.sha256(body).hexdigest()
        if (
            cached.get("sha256") == body_digest
            and cached.get("extractor_version") == self.EXTRACTOR_VERSION
        ):
            return cached.get("text", "")
        hash_cache_path = self.cache_dir / f"sha256-{body_digest}.json"
        if hash_cache_path.exists():
            try:
                hash_cached = json.loads(hash_cache_path.read_text(encoding="utf-8"))
            except Exception:
                hash_cached = {}
            if hash_cached.get("extractor_version") == self.EXTRACTOR_VERSION:
                reused = {**hash_cached, "url": url, "etag": response.headers.get("etag"), "last_modified": response.headers.get("last-modified")}
                self._write_cache(cache_path, reused)
                return reused.get("text", "")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        try:
            text, extraction_method, ocr_confidence, page_count = self._extract_bytes_with_metadata(
                bytes(body), name, content_type,
            )
            failure_reason = None if text else ("unsupported" if extraction_method == "unsupported" else "ocr_failed")
        except subprocess.TimeoutExpired as exc:
            text, extraction_method, ocr_confidence, page_count = "", "pdf_ocr", None, None
            failure_reason = "timeout"
        except Exception as exc:
            text, extraction_method, ocr_confidence, page_count = "", "unknown", None, None
            failure_reason = "ocr_failed"
        needs_review = bool(
            extraction_method in {"image_ocr", "pdf_ocr"}
            and self._ocr_needs_review(text, ocr_confidence)
        )
        payload = {
            "url": url,
            "sha256": body_digest,
            "etag": response.headers.get("etag"),
            "last_modified": response.headers.get("last-modified"),
            "content_type": content_type,
            "extraction_method": extraction_method,
            "extraction_status": "success" if text else "failed",
            "failure_reason": failure_reason,
            "ocr_confidence": ocr_confidence,
            "page_count": page_count,
            "needs_review": needs_review,
            "extractor_version": self.EXTRACTOR_VERSION,
            "text": text,
        }
        self._write_cache(cache_path, payload)
        self._write_cache(hash_cache_path, payload)
        return text

    @staticmethod
    def _write_cache(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _extract_bytes(self, body: bytes, name: str, content_type: str) -> str:
        return self._extract_bytes_with_method(body, name, content_type)[0]

    def _extract_bytes_with_method(self, body: bytes, name: str, content_type: str) -> tuple[str, str]:
        text, method, _confidence, _pages = self._extract_bytes_with_metadata(body, name, content_type)
        return text, method

    def _extract_bytes_with_metadata(
        self, body: bytes, name: str, content_type: str,
    ) -> tuple[str, str, float | None, int | None]:
        suffix = Path(name.lower().split("?", 1)[0]).suffix
        if suffix == ".pdf" or content_type == "application/pdf" or body.startswith(b"%PDF-"):
            return self._extract_pdf_with_metadata(body)
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"} or content_type.startswith("image/"):
            text, confidence = self._ocr_image_with_confidence(Image.open(io.BytesIO(body)))
            return text, "image_ocr", confidence, 1
        # 강남대 image.do는 일부 본문 이미지에 확장자와 Content-Type을
        # 제공하지 않는다. Pillow가 실제 바이트를 이미지로 확인할 때만 OCR한다.
        try:
            image = Image.open(io.BytesIO(body))
            image.verify()
            image = Image.open(io.BytesIO(body))
            text, confidence = self._ocr_image_with_confidence(image)
            return text, "image_ocr", confidence, 1
        except Exception:
            pass
        if suffix in {".hwpx", ".docx", ".pptx", ".xlsx"}:
            return self._extract_xml_archive(body), "xml_text", None, None
        if suffix == ".hwp":
            return self._extract_legacy_hwp(body), "hwp_text", None, None
        if suffix in {".txt", ".csv"} or content_type.startswith("text/"):
            return self._decode_text(body), "plain_text", None, None
        return "", "unsupported", None, None

    @staticmethod
    def _extract_legacy_hwp(body: bytes) -> str:
        """구형 HWP 5.x를 로컬 pyhwp로 HTML 변환해 표 안의 글자까지 읽는다."""
        with tempfile.TemporaryDirectory(prefix="knuask-hwp-") as temp_dir:
            source = Path(temp_dir) / "source.hwp"
            output = Path(temp_dir) / "html"
            source.write_bytes(body)
            output.mkdir()
            subprocess.run(
                ["hwp5html", "--output", str(output), str(source)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
            )
            index = output / "index.xhtml"
            if not index.exists():
                return ""
            soup = BeautifulSoup(index.read_text(encoding="utf-8", errors="ignore"), "html.parser")
            return normalize_text(soup.get_text(" ", strip=True))

    def _extract_pdf(self, body: bytes) -> str:
        return self._extract_pdf_with_method(body)[0]

    def _extract_pdf_with_method(self, body: bytes) -> tuple[str, str]:
        text, method, _confidence, _pages = self._extract_pdf_with_metadata(body)
        return text, method

    def _extract_pdf_with_metadata(self, body: bytes) -> tuple[str, str, float | None, int | None]:
        try:
            reader = PdfReader(io.BytesIO(body))
            pages = [normalize_text(page.extract_text() or "") for page in reader.pages[:self.max_pdf_pages]]
            text = "\n\n".join(
                f"[PDF page {index}] {page}"
                for index, page in enumerate(pages, start=1) if page
            )
            page_count = min(len(reader.pages), self.max_pdf_pages)
        except Exception:
            text = ""
            page_count = None
        if len(text) >= 120:
            return text, "pdf_text", None, page_count

        # 스캔 PDF는 앞 20페이지만 이미지화하여 한국어+영어 OCR을 수행한다.
        with tempfile.TemporaryDirectory(prefix="knuask-pdf-") as temp_dir:
            pdf_path = Path(temp_dir) / "source.pdf"
            output_prefix = Path(temp_dir) / "page"
            pdf_path.write_bytes(body)
            subprocess.run(
                ["pdftoppm", "-f", "1", "-l", str(self.max_ocr_pages), "-jpeg", "-r", "180", str(pdf_path), str(output_prefix)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
            )
            ocr_results = [
                self._ocr_image_with_confidence(Image.open(path))
                for path in sorted(Path(temp_dir).glob("page-*.jpg"))
            ]
        ocr_text = normalize_text("\n\n".join(
            f"[PDF page {index}] {part}"
            for index, (part, _confidence) in enumerate(ocr_results, start=1) if part
        )) or text
        confidences = [confidence for _part, confidence in ocr_results if confidence is not None]
        confidence = sum(confidences) / len(confidences) if confidences else None
        return ocr_text, "pdf_ocr", confidence, len(ocr_results)

    @staticmethod
    def _ocr_image(image: Image.Image) -> str:
        return AttachmentExtractor._ocr_image_with_confidence(image)[0]

    @staticmethod
    def _ocr_needs_review(text: str, confidence: float | None) -> bool:
        """학생 업무의 핵심 패턴이 깨진 OCR을 자동 공개하지 않는다."""
        normalized = normalize_text(text)
        if len(normalized) < 120 or (confidence is not None and confidence < 0.65):
            return True
        date_context = any(term in normalized for term in ("기간", "일정", "마감", "까지"))
        has_date = bool(re.search(
            r"(?:20\d{2}[.년/-]\s*)?\d{1,2}[.월/-]\s*\d{1,2}(?:일)?|\d{1,2}:\d{2}",
            normalized,
        ))
        contact_context = any(term in normalized for term in ("문의", "전화", "연락처"))
        has_phone = bool(re.search(r"(?:0\d{1,2}[- )]?\d{3,4}[- ]?\d{4})", normalized))
        procedure_context = any(term in normalized for term in ("신청 방법", "신청 절차", "제출 방법"))
        has_action = any(term in normalized for term in (
            "로그인", "접속", "클릭", "선택", "작성", "제출", "방문", "납부", "업로드",
        ))
        return bool(
            (date_context and not has_date)
            or (contact_context and not has_phone)
            or (procedure_context and not has_action)
        )

    @staticmethod
    def _ocr_image_with_confidence(image: Image.Image) -> tuple[str, float | None]:
        image = ImageOps.exif_transpose(image).convert("RGB")
        if image.width * image.height > 20_000_000:
            image.thumbnail((4500, 4500))
        # 학사안내 표처럼 폭 500px 안팎의 작은 CMS 이미지는 원본 크기로
        # Tesseract에 넘기면 글자가 있어도 0자로 끝날 수 있다. 지나치게
        # 작은 이미지만 확대하고 대비·선명도를 보정해 표의 날짜와 금액을
        # 읽을 수 있게 한다.
        if image.width < 1400 or image.height < 300:
            scale = min(4, max(2, int(max(1400 / max(image.width, 1), 300 / max(image.height, 1)) + 0.999)))
            image = image.resize(
                (image.width * scale, image.height * scale),
                Image.Resampling.LANCZOS,
            )
        image = ImageOps.autocontrast(ImageOps.grayscale(image))
        image = ImageEnhance.Contrast(image).enhance(1.35)
        image = image.filter(ImageFilter.SHARPEN)
        data = pytesseract.image_to_data(
            image, lang="kor+eng", config="--psm 6", output_type=pytesseract.Output.DICT,
        )
        # 줄·칸이 뚜렷한 표는 단일 텍스트 블록(PSM 6)으로 분석할 때
        # 문자가 있어도 빈 결과가 나올 수 있다. 일반 공지의 기존 결과는
        # 유지하고, 실질적으로 읽지 못한 경우에만 표/열 인식 모드로
        # 재시도한다.
        first_words = [normalize_text(word) for word in data.get("text", []) if normalize_text(word)]
        if len(" ".join(first_words)) < 30:
            table_data = pytesseract.image_to_data(
                image, lang="kor+eng", config="--psm 4", output_type=pytesseract.Output.DICT,
            )
            table_words = [normalize_text(word) for word in table_data.get("text", []) if normalize_text(word)]
            if len(" ".join(table_words)) > len(" ".join(first_words)):
                data = table_data
        words = [normalize_text(word) for word in data.get("text", []) if normalize_text(word)]
        confidences = []
        for value in data.get("conf", []):
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if numeric >= 0:
                confidences.append(numeric / 100)
        return normalize_text(" ".join(words)), (sum(confidences) / len(confidences) if confidences else None)

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
