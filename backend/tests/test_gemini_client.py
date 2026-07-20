from app.services.ai import AIService


class _GeminiResponse:
    status_code = 200
    ok = True
    text = ""

    @staticmethod
    def json():
        return {
            "candidates": [{
                "content": {"parts": [{"text": "공식 공지 기준 답변입니다."}]},
            }],
        }


def test_gemini_provider_is_selected_when_configured(monkeypatch):
    from app.services.ai import client as client_module

    monkeypatch.setattr(client_module.settings, "mock_ai", False)
    monkeypatch.setattr(client_module.settings, "chat_provider", "gemini")
    monkeypatch.setattr(client_module.settings, "gemini_api_key", "test-key")

    ai = AIService()

    assert ai.chat_provider == "gemini"
    assert ai.chat_model_name.startswith("gemini/")


def test_gemini_request_keeps_key_in_header_and_reads_text(monkeypatch):
    from app.services.ai import client as client_module

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return _GeminiResponse()

    monkeypatch.setattr(client_module.settings, "gemini_api_key", "test-secret")
    monkeypatch.setattr(client_module.settings, "gemini_max_retries", 1)
    monkeypatch.setattr(client_module.requests, "post", fake_post)

    result = AIService.__new__(AIService)._gemini_generate("system", "question")

    assert result == "공식 공지 기준 답변입니다."
    assert captured["headers"]["x-goog-api-key"] == "test-secret"
    assert "test-secret" not in captured["url"]
    assert "test-secret" not in str(captured["json"])


def test_gemini_json_mode_is_used_for_structured_output(monkeypatch):
    from app.services.ai import client as client_module

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _GeminiResponse()

    monkeypatch.setattr(client_module.settings, "gemini_api_key", "test-secret")
    monkeypatch.setattr(client_module.settings, "gemini_max_retries", 1)
    monkeypatch.setattr(client_module.requests, "post", fake_post)

    AIService.__new__(AIService)._gemini_generate("system", "question", json_output=True)

    assert captured["json"]["generationConfig"]["responseMimeType"] == "application/json"
