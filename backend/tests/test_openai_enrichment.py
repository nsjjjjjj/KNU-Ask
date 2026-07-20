import json

from app.services.ai.openai_enrichment import OpenAIEnrichmentService


class _Response:
    output_text = json.dumps({
        "category": "학사", "subCategory": "수강신청", "actionType": "신청",
        "applicationPeriod": {}, "eventPeriod": {}, "target": {},
        "department": {}, "keywords": ["수강신청"], "noticeStatus": "unknown",
        "confidence": 0.9,
    }, ensure_ascii=False)


class _Responses:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _Response()


class _Client:
    def __init__(self):
        self.responses = _Responses()


def test_openai_enrichment_sends_images_pdfs_and_strict_schema():
    client = _Client()
    service = OpenAIEnrichmentService(client=client)
    result = service.enrich({
        "instructions": "공지를 구조화하세요.",
        "notice": {
            "title": "2026-2학기 수강신청 안내", "content": "본문",
            "attachments": [
                {"url": "https://web.kangnam.ac.kr/comm/image.do?id=1", "contentType": "image/png"},
                {"url": "https://web.kangnam.ac.kr/comm/file.pdf", "contentType": "application/pdf"},
                {"url": "https://evil.example/image.png", "contentType": "image/png"},
            ],
        },
    })

    blocks = client.responses.kwargs["input"][0]["content"]
    assert result.sub_category == "수강신청"
    assert [block["type"] for block in blocks] == ["input_text", "input_image", "input_file"]
    output_format = client.responses.kwargs["text"]["format"]
    assert output_format["strict"] is True
    assert output_format["schema"]["additionalProperties"] is False
