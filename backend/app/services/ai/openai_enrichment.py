from __future__ import annotations

import json
from urllib.parse import urlparse

from openai import OpenAI

from app.core.config import settings
from app.schemas import StructuredNotice


def strict_schema(value):
    """Pydantic 스키마를 OpenAI Structured Outputs의 strict 형태로 바꾼다."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "default":
                continue
            if key == "properties" and isinstance(item, dict):
                # 임의 키 map인 evidenceMap은 서버측 기본값으로 두고, 고정 필드는
                # Structured Outputs로 강제한다.
                result[key] = {
                    name: strict_schema(schema)
                    for name, schema in item.items()
                    if not (
                        isinstance(schema, dict)
                        and schema.get("type") == "object"
                        and not schema.get("properties")
                        and isinstance(schema.get("additionalProperties"), dict)
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


def _safe_school_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.hostname.endswith("kangnam.ac.kr")
    )


class OpenAIEnrichmentService:
    """학교 운영 환경에서 Codex 워커와 같은 스키마를 생성하는 Responses API 어댑터."""

    def __init__(self, client: OpenAI | None = None) -> None:
        if not settings.openai_api_key and client is None:
            raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")
        self.client = client or OpenAI(api_key=settings.openai_api_key)

    def enrich(self, job: dict) -> StructuredNotice:
        notice = job["notice"]
        content: list[dict] = [{
            "type": "input_text",
            "text": (
                f"{job.get('instructions') or ''}\n\n"
                "성공 기준:\n"
                "- 날짜·담당자·전화번호·이메일·대상·신청방법·링크·준비물·주의사항을 빠짐없이 구조화한다.\n"
                "- 전용 필드가 없는 새 정보는 additionalFacts에 근거 위치와 함께 보존한다.\n"
                "- 신청 가능한 기간과 행사 수행 기간을 분리한다.\n"
                "- 원문 또는 첨부에서 확인되지 않는 값은 null 또는 빈 배열로 둔다.\n"
                "- 이미지와 PDF를 본문 OCR보다 우선하는 원본 근거로 사용한다.\n\n"
                f"<notice_data>{json.dumps(notice, ensure_ascii=False, default=str)}</notice_data>"
            ),
        }]
        for attachment in (notice.get("attachments") or [])[:20]:
            url = str(attachment.get("url") or "")
            if not _safe_school_url(url):
                continue
            content_type = str(attachment.get("contentType") or "").lower()
            method = str(attachment.get("extractionMethod") or "").lower()
            if content_type.startswith("image/") or method == "image_ocr":
                content.append({
                    "type": "input_image", "image_url": url,
                    "detail": settings.openai_image_detail,
                })
            elif content_type == "application/pdf" or method in {"pdf_text", "pdf_ocr"}:
                content.append({"type": "input_file", "file_url": url, "detail": "high"})

        schema = strict_schema(StructuredNotice.model_json_schema(by_alias=True))
        response = self.client.responses.create(
            model=settings.openai_enrichment_model,
            reasoning={"effort": settings.openai_enrichment_reasoning_effort},
            input=[{"role": "user", "content": content}],
            text={
                "format": {
                    "type": "json_schema", "name": "knu_notice",
                    "strict": True, "schema": schema,
                },
            },
        )
        if not response.output_text:
            raise RuntimeError("OpenAI 구조화 응답이 비어 있습니다.")
        return StructuredNotice.model_validate_json(response.output_text)
