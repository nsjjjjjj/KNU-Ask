#!/usr/bin/env python3
"""새 공지에만 Codex 구조화/비전 보강을 적용하는 Mac 호스트 워커."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(os.environ.get("KNUASK_PROJECT_ROOT", Path(__file__).resolve().parents[1]))
DEFAULT_CODEX = Path("/Applications/Codex.app/Contents/Resources/codex")
API_BASE = os.environ.get("KNUASK_API_BASE", "http://localhost:8080/api").rstrip("/")


def _env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return None


ADMIN_TOKEN = _env_value("ADMIN_API_TOKEN") or "local-dev-admin"


def api_request(path: str, *, method: str = "GET", payload: dict | None = None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{API_BASE}{path}", data=data, method=method,
        headers={"X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            if response.status == 204:
                return None
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 204:
            return None
        detail = exc.read().decode(errors="replace")[:1000]
        raise RuntimeError(f"API {method} {path} failed: HTTP {exc.code} {detail}") from exc


def strict_schema(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "default":
                continue
            if key == "properties" and isinstance(item, dict):
                # OpenAI strict schema는 임의 키를 갖는 map을 허용하지 않는다.
                # evidenceMap 같은 보조 필드는 서버 기본값으로 두고 핵심 고정 필드만 받는다.
                result[key] = {
                    property_name: strict_schema(property_schema)
                    for property_name, property_schema in item.items()
                    if not (
                        isinstance(property_schema, dict)
                        and property_schema.get("type") == "object"
                        and not property_schema.get("properties")
                        and isinstance(property_schema.get("additionalProperties"), dict)
                    )
                }
            else:
                result[key] = strict_schema(item)
        if result.get("type") == "object" or "properties" in result:
            properties = result.get("properties", {})
            result["required"] = list(properties)
            result["additionalProperties"] = False
        return result
    if isinstance(value, list):
        return [strict_schema(item) for item in value]
    return value


def safe_attachment_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "https" and bool(parsed.hostname) and parsed.hostname.endswith("kangnam.ac.kr")


def download_visuals(notice: dict, temp_dir: Path) -> list[Path]:
    """이미지는 항상, OCR로 판정된 PDF는 첫 페이지까지 Codex에 전달한다."""
    images: list[Path] = []
    max_visuals = max(1, int(os.environ.get("KNUASK_MAX_VISUALS", "20")))
    for index, item in enumerate(notice.get("attachments") or []):
        if len(images) >= max_visuals:
            break
        url = str(item.get("url") or "")
        if not safe_attachment_url(url):
            continue
        content_type = str(item.get("contentType") or "").lower()
        method = str(item.get("extractionMethod") or "").lower()
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
        is_image = content_type.startswith("image/") or method == "image_ocr" or suffix in {
            ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff",
        }
        is_scanned_pdf = content_type == "application/pdf" and method == "pdf_ocr"
        if not is_image and not is_scanned_pdf:
            continue
        request = urllib.request.Request(url, headers={"User-Agent": "KNU-Ask-CodexWorker/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read(25 * 1024 * 1024 + 1)
        if len(body) > 25 * 1024 * 1024:
            continue
        extension = suffix if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".pdf"} else mimetypes.guess_extension(content_type) or ""
        if not extension and is_image:
            if body.startswith(b"\x89PNG\r\n\x1a\n"):
                extension = ".png"
            elif body.startswith(b"\xff\xd8\xff"):
                extension = ".jpg"
            elif body.startswith((b"GIF87a", b"GIF89a")):
                extension = ".gif"
            elif body.startswith(b"RIFF") and body[8:12] == b"WEBP":
                extension = ".webp"
        extension = extension or ".bin"
        source_path = temp_dir / f"attachment-{index + 1}{extension}"
        source_path.write_bytes(body)
        if is_image:
            images.append(source_path)
            continue
        preview_path = temp_dir / f"attachment-{index + 1}-page1.png"
        converted = subprocess.run(
            ["/usr/bin/sips", "-s", "format", "png", str(source_path), "--out", str(preview_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if converted.returncode == 0 and preview_path.exists():
            images.append(preview_path)
    return images


def build_prompt(job: dict) -> str:
    instructions = str(job.get("instructions") or "대학교 공지를 구조화된 JSON으로 변환하세요.")
    notice = job["notice"]
    return f"""{instructions}

추가 운영 규칙:
- 이 작업은 공개된 강남대학교 공지 한 건을 최초 1회 구조화하는 작업입니다.
- 첨부 이미지가 함께 제공되면 OCR 텍스트와 원본 이미지를 대조하세요.
- 이미지에 보이는 날짜, 표, 전화번호, 담당자, QR 주변 안내를 빠뜨리지 마세요.
- 전화번호·이메일·담당자·날짜·URL은 제공된 원문 또는 이미지에서 직접 확인되는 값만 반환하세요.
- 일반휴학처럼 여러 특수 유형을 함께 설명하는 상시 안내는 제목의 핵심 업무를 기준으로 대표 절차를 만드세요.
- 신청 가능한 기간(applicationPeriod)과 실제 활동 기간(eventPeriod)을 반드시 분리하세요.
- 전체 원문은 서버에 별도 보존되므로 searchText에는 검색에 필요한 핵심 용어를 중복 없이 담으세요.
- 명시적 순서가 있으면 학생이 바로 수행할 수 있도록 steps를 빠짐없이 순서대로 만드세요.
- 기존 전용 필드에 맞지 않는 새로운 정보는 버리지 말고 additionalFacts에 근거 위치와 함께 기록하세요.
- 반환값은 제공된 JSON 스키마를 정확히 따르는 JSON 하나뿐이어야 합니다.

<notice_data>
{json.dumps(notice, ensure_ascii=False, default=str)}
</notice_data>
"""


def run_codex(job: dict) -> dict:
    codex_bin = Path(os.environ.get("KNUASK_CODEX_BIN", str(DEFAULT_CODEX)))
    if not codex_bin.exists():
        raise RuntimeError(f"Codex CLI를 찾을 수 없습니다: {codex_bin}")
    schema = strict_schema(api_request("/enrichment/schema"))
    with tempfile.TemporaryDirectory(prefix="knuask-codex-") as temp_name:
        temp_dir = Path(temp_name)
        schema_path = temp_dir / "schema.json"
        output_path = temp_dir / "structured.json"
        schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        images = download_visuals(job["notice"], temp_dir)
        command = [
            str(codex_bin), "exec", "--ephemeral", "--sandbox", "read-only",
            "--skip-git-repo-check", "--ignore-rules", "--output-schema", str(schema_path),
            "--output-last-message", str(output_path), "--color", "never",
        ]
        if images:
            command.extend(["--image", *[str(path) for path in images]])
        command.append("-")
        completed = subprocess.run(
            command, cwd=temp_dir, input=build_prompt(job), text=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=900,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Codex 실행 실패({completed.returncode}): {completed.stderr[-1500:]}")
        if not output_path.exists():
            raise RuntimeError("Codex 구조화 결과 파일이 생성되지 않았습니다.")
        return json.loads(output_path.read_text(encoding="utf-8"))


def run_once() -> bool:
    job = api_request("/enrichment/jobs/next?worker=codex")
    if not job:
        return False
    job_id = int(job["jobId"])
    try:
        structured = run_codex(job)
        api_request(f"/enrichment/jobs/{job_id}/complete", method="POST", payload=structured)
        print(f"Codex enrichment completed: job={job_id} notice={job['notice']['id']}", flush=True)
    except Exception as exc:
        try:
            api_request(f"/enrichment/jobs/{job_id}/fail", method="POST", payload={"error": str(exc)[:2000]})
        except Exception:
            pass
        raise
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="작업 큐를 계속 감시합니다.")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    while True:
        try:
            worked = run_once()
        except Exception as exc:
            print(f"Codex enrichment failed: {exc}", file=sys.stderr, flush=True)
            if not args.watch:
                return 1
            worked = False
        if not args.watch:
            return 0
        time.sleep(1 if worked else max(args.interval, 5))


if __name__ == "__main__":
    raise SystemExit(main())
