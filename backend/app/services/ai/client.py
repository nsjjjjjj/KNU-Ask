from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from openai import OpenAI
from pydantic import ValidationError
import requests

from app.core.config import settings
from app.schemas import (
    CATEGORIES,
    DepartmentInfo,
    Period,
    QueryFilters,
    StructuredActionGuide,
    StructuredActionStep,
    StructuredNotice,
    Target,
)
from app.utils.text import extract_application_period, extract_notice_contact, extract_notice_email, normalize_text, rule_extract


PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"
logger = logging.getLogger(__name__)


def _prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


class AIService:
    @staticmethod
    def notice_structuring_prompt() -> str:
        return _prompt("notice_structuring.txt")

    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        requested_chat = settings.chat_provider.lower()
        if settings.mock_ai or requested_chat in {"rules", "mock", "none"}:
            self.chat_provider = "rules"
            self.chat_model_name = "rules-v2"
        elif requested_chat == "openai" and self.client:
            self.chat_provider = "openai"
            self.chat_model_name = settings.openai_chat_model
        elif requested_chat == "gemini" and settings.gemini_api_key:
            self.chat_provider = "gemini"
            self.chat_model_name = f"gemini/{settings.gemini_chat_model}"
        elif requested_chat in {"auto", "ollama", "local"} and self._ollama_has_model(settings.ollama_chat_model):
            self.chat_provider = "ollama"
            self.chat_model_name = f"ollama/{settings.ollama_chat_model}"
        elif requested_chat == "auto" and settings.gemini_api_key:
            self.chat_provider = "gemini"
            self.chat_model_name = f"gemini/{settings.gemini_chat_model}"
        elif requested_chat == "auto" and self.client:
            self.chat_provider = "openai"
            self.chat_model_name = settings.openai_chat_model
        else:
            # 로컬 모델이 잠시 내려가도 질문과 크롤링이 중단되지 않는다.
            self.chat_provider = "rules"
            self.chat_model_name = "rules-v2"
        # 이전 코드와 테스트에서 사용하는 이름을 호환용으로 유지한다.
        self.mock = self.chat_provider == "rules"

        requested = settings.embedding_provider.lower()
        if requested == "openai" or (requested == "auto" and settings.openai_api_key):
            self.embedding_provider = "openai"
            self.embedding_model_name = settings.openai_embedding_model
        elif requested in {"auto", "ollama", "local"} and self._ollama_has_model(settings.ollama_embedding_model):
            self.embedding_provider = "ollama"
            self.embedding_model_name = f"ollama/{settings.ollama_embedding_model}"
        else:
            # 오프라인에서도 검색이 멈추지 않게 하는 명시적인 어휘 폴백이다.
            # 의미 임베딩인 것처럼 OpenAI 모델명을 붙이지 않는다.
            self.embedding_provider = "lexical_fallback"
            self.embedding_model_name = "local-char-ngram-v2"

    @staticmethod
    def _ollama_has_model(model_name: str) -> bool:
        try:
            response = requests.get(
                f"{settings.ollama_base_url.rstrip('/')}/api/tags",
                timeout=min(settings.embedding_request_timeout, 0.8),
            )
            if not response.ok:
                return False
            models = response.json().get("models", [])
            wanted = model_name.split(":", 1)[0]
            return any(item.get("name", "").split(":", 1)[0] == wanted for item in models)
        except Exception:
            return False

    def _ollama_chat(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict | None = None,
        temperature: float = 0.0,
        num_ctx: int | None = None,
        num_predict: int | None = None,
    ) -> str:
        payload = {
            "model": settings.ollama_chat_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "think": False,
            "keep_alive": settings.ollama_chat_keep_alive,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx or settings.ollama_chat_num_ctx,
                "num_predict": num_predict or settings.ollama_chat_num_predict,
            },
        }
        if schema is not None:
            payload["format"] = schema
        response = requests.post(
            f"{settings.ollama_base_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=settings.ollama_chat_timeout,
        )
        if (
            not response.ok and schema is not None and response.status_code == 400
            and "failed to parse grammar" in response.text.lower()
        ):
            # 일부 로컬 모델은 큰 중첩 JSON Schema를 grammar로 변환하지 못한다.
            # 같은 스키마를 프롬프트에 유지한 채 JSON 모드로 재시도하고 Pydantic으로 검증한다.
            payload["format"] = "json"
            response = requests.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/chat",
                json=payload,
                timeout=settings.ollama_chat_timeout,
            )
        if not response.ok:
            raise RuntimeError(f"Ollama HTTP {response.status_code}: {response.text[:800]}")
        content = response.json().get("message", {}).get("content", "").strip()
        if not content:
            raise ValueError("Ollama가 빈 답변을 반환했습니다.")
        return content

    def _gemini_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        json_output: bool = False,
        max_output_tokens: int | None = None,
    ) -> str:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")
        generation_config = {
            "maxOutputTokens": max_output_tokens or settings.gemini_chat_max_output_tokens,
            "thinkingConfig": {"thinkingLevel": settings.gemini_thinking_level},
        }
        if json_output:
            generation_config["responseMimeType"] = "application/json"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": generation_config,
        }
        url = (
            f"{settings.gemini_api_base_url.rstrip('/')}/models/"
            f"{settings.gemini_chat_model}:generateContent"
        )
        retryable_statuses = {429, 500, 502, 503, 504}
        last_error: Exception | None = None
        for attempt in range(max(settings.gemini_max_retries, 1)):
            try:
                response = requests.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": settings.gemini_api_key,
                    },
                    json=payload,
                    timeout=settings.gemini_chat_timeout,
                )
                if response.status_code in retryable_statuses and attempt + 1 < settings.gemini_max_retries:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                if not response.ok:
                    raise RuntimeError(f"Gemini HTTP {response.status_code}: {response.text[:800]}")
                body = response.json()
                candidates = body.get("candidates") or []
                parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
                content = "".join(
                    part.get("text", "") for part in parts
                    if not part.get("thought") and part.get("text")
                ).strip()
                if not content:
                    block_reason = (body.get("promptFeedback") or {}).get("blockReason")
                    raise ValueError(f"Gemini가 빈 답변을 반환했습니다. blockReason={block_reason}")
                return content
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < settings.gemini_max_retries:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                break
        raise RuntimeError(f"Gemini 호출 실패: {last_error}") from last_error

    def _structured_chat(
        self, prompt_name: str, user_prompt: str, model_type,
        num_ctx: int | None = None, num_predict: int | None = None,
    ):
        schema = model_type.model_json_schema(by_alias=True)
        schema_hint = json.dumps(schema, ensure_ascii=False)
        system_prompt = f"{_prompt(prompt_name)}\n반환 JSON 스키마:\n{schema_hint}"
        if self.chat_provider == "ollama":
            raw = self._ollama_chat(
                system_prompt, user_prompt, schema=schema,
                num_ctx=num_ctx, num_predict=num_predict,
            )
        elif self.chat_provider == "gemini":
            raw = self._gemini_generate(
                system_prompt,
                user_prompt,
                json_output=True,
                max_output_tokens=num_predict,
            )
        elif self.chat_provider == "openai":
            response = self.client.chat.completions.create(
                model=settings.openai_chat_model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
            )
            raw = response.choices[0].message.content
        else:
            raise RuntimeError("구조화 AI 제공자를 사용할 수 없습니다.")
        return model_type.model_validate_json(raw)

    def embedding(self, text: str) -> list[float]:
        if self.embedding_provider == "openai":
            result = self.client.embeddings.create(
                model=settings.openai_embedding_model,
                input=text[:12000],
                dimensions=settings.embedding_dimensions,
            )
            return result.data[0].embedding
        if self.embedding_provider == "ollama":
            response = requests.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/embed",
                json={
                    "model": settings.ollama_embedding_model,
                    "input": text[:12000],
                    # 첫 질문마다 모델을 다시 읽는 지연을 피한다. Ollama가
                    # 메모리 압박을 받으면 이 시간 전에도 안전하게 내릴 수 있다.
                    "keep_alive": settings.ollama_embedding_keep_alive,
                },
                timeout=settings.embedding_request_timeout,
            )
            response.raise_for_status()
            payload = response.json()
            values = (payload.get("embeddings") or [payload.get("embedding") or []])[0]
            return self._fit_embedding(values)
        return self._lexical_embedding(text)

    @staticmethod
    def _fit_embedding(values: list[float]) -> list[float]:
        values = list(values[:settings.embedding_dimensions])
        if len(values) < settings.embedding_dimensions:
            values.extend([0.0] * (settings.embedding_dimensions - len(values)))
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]

    @staticmethod
    def _lexical_embedding(text: str) -> list[float]:
        vector = [0.0] * settings.embedding_dimensions
        normalized = normalize_text(text).lower()
        tokens = re.findall(r"[가-힣A-Za-z0-9]+", normalized)
        features = list(tokens)
        for token in tokens:
            padded = f"^{token}$"
            features.extend(padded[index:index + 3] for index in range(max(len(padded) - 2, 1)))
        for feature in features:
            digest = hashlib.sha256(feature.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % len(vector)
            vector[index] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def analyze_query(self, message: str) -> QueryFilters:
        rule_result = self._mock_query(message)
        if self.chat_provider == "rules":
            return rule_result
        if settings.local_ai_complex_queries_only and not self._needs_ai_query_analysis(message, rule_result):
            return rule_result
        try:
            ai_result = self._structured_chat(
                "query_analysis.txt",
                f"<user_question>{normalize_text(message)}</user_question>",
                QueryFilters,
            )
            return self._merge_query_filters(rule_result, ai_result)
        except Exception:
            return rule_result

    @staticmethod
    def _needs_ai_query_analysis(message: str, rule_result: QueryFilters) -> bool:
        text = normalize_text(message)
        complex_markers = (
            "그리고", "동시에", "함께", "먼저", "순서", "비교", "둘 다",
            "말고", "차이", "중 무엇", "어떤 방식", "어디로", "담당자",
        )
        requested_fields = sum(any(word in text for word in group) for group in (
            ("기간", "언제", "마감"),
            ("방법", "절차", "순서", "어떻게"),
            ("담당", "전화", "연락처", "이메일"),
            ("대상", "자격", "누가"),
            ("서류", "준비물"),
        ))
        known_subject_count = sum(subject in text for subject in (
            "휴학", "복학", "수강신청", "재수강", "등록금", "국가장학금",
            "학점교류", "증명서", "전자출결", "예비군", "자퇴", "졸업",
        ))
        if rule_result.sub_category and known_subject_count <= 1:
            # 업무가 명확하면 기간·절차·연락처를 여러 개 물어도 검색 조건은 이미 충분하다.
            return False
        return bool(
            len(text) >= 45
            or any(marker in text for marker in complex_markers)
            or requested_fields >= 2
            or known_subject_count >= 2
            or (not rule_result.category and len(text) >= 12)
        )

    @staticmethod
    def _merge_query_filters(rule_result: QueryFilters, ai_result: QueryFilters) -> QueryFilters:
        data = ai_result.model_dump()
        for field in (
            "category", "sub_category", "academic_year", "semester", "grade",
            "department", "student_status", "time_scope",
        ):
            rule_value = getattr(rule_result, field)
            if rule_value is not None:
                data[field] = rule_value
        if data.get("category") not in CATEGORIES:
            data["category"] = rule_result.category
        data["keywords"] = list(dict.fromkeys(rule_result.keywords + (ai_result.keywords or [])))[:15]
        data["intent"] = ai_result.intent or rule_result.intent
        return QueryFilters.model_validate(data)

    def structure_notice(
        self,
        title: str,
        content: str,
        published_at: datetime,
        department: dict | None = None,
        content_links: list[str] | None = None,
    ) -> StructuredNotice:
        extracted = rule_extract(f"{title}\n{content}")
        content_links = content_links or []
        if (
            settings.notice_structuring_provider.lower() in {"chat", "ollama", "openai", "local"}
            and self.chat_provider != "rules"
        ):
            user_payload = json.dumps({
                "title": normalize_text(title), "content": normalize_text(content)[:24000],
                "publishedAt": published_at.isoformat(), "ruleExtracted": extracted,
                "verifiedContentLinks": content_links,
            }, ensure_ascii=False)
            for _attempt in range(2):
                try:
                    structured = self._structured_chat(
                        "notice_structuring.txt",
                        f"<notice_data>{user_payload}</notice_data>",
                        StructuredNotice,
                        num_ctx=settings.ollama_structuring_num_ctx,
                        num_predict=settings.ollama_structuring_num_predict,
                    )
                    return self._validate_structured_notice(structured.model_dump_json(by_alias=True))
                except Exception as exc:
                    logger.warning(
                        "notice structuring failed provider=%s model=%s attempt=%s error=%s",
                        self.chat_provider, self.chat_model_name, _attempt + 1, exc,
                    )
                    continue
            result = self._mock_structure(title, content, published_at, department, content_links)
            result.needs_review = True
            result.confidence = 0.0
            return result
        return self._mock_structure(title, content, published_at, department, content_links)

    @staticmethod
    def _validate_structured_notice(raw_json: str) -> StructuredNotice:
        """신청 가이드만 잘못된 경우 공지 전체 구조화까지 버리지 않는다."""
        payload = json.loads(raw_json)
        try:
            return StructuredNotice.model_validate(payload)
        except ValidationError as exc:
            guide_only = all(
                error.get("loc") and error["loc"][0] in {"actionGuide", "action_guide"}
                for error in exc.errors()
            )
            if not guide_only:
                raise
            payload.pop("actionGuide", None)
            payload.pop("action_guide", None)
            payload["needsReview"] = True
            return StructuredNotice.model_validate(payload)

    def generate_answer(self, message: str, matches: list[dict]) -> str:
        if not matches:
            return "관련 데이터가 없습니다. 담당 부서로 직접 문의해주시길 바랍니다."
        if self.chat_provider != "rules":
            evidence_rows = []
            for item in matches[:5]:
                notice = item["notice"]
                metadata = item["metadata"]
                evidence_rows.append({
                    "title": notice.title,
                    "sourceUrl": notice.source_url,
                    "sourceType": notice.source_type,
                    "publishedAt": notice.published_at.isoformat(),
                    "evidence": normalize_text(item.get("chunk_text") or notice.content)[:2500],
                    "category": metadata.category,
                    "applicationStart": metadata.application_start.isoformat() if metadata.application_start else None,
                    "applicationEnd": metadata.application_end.isoformat() if metadata.application_end else None,
                    "applicationMethod": metadata.application_method,
                    "applicationLocation": metadata.application_location,
                    "eligibilityNotes": metadata.eligibility_notes,
                    "feeInformation": metadata.fee_information,
                    "capacity": metadata.capacity,
                    "selectionMethod": metadata.selection_method,
                    "resultAnnouncement": metadata.result_announcement,
                    "cancellationPolicy": metadata.cancellation_policy,
                    "benefits": metadata.benefits,
                    "creditsOrHours": metadata.credits_or_hours,
                    "importantDates": metadata.important_dates,
                    "department": metadata.department_name,
                    "contactPerson": metadata.contact_person,
                    "contactRole": metadata.contact_role,
                    "phone": metadata.department_phone,
                    "email": metadata.department_email,
                    "officeLocation": metadata.department_office_location,
                })
            evidence = json.dumps(evidence_rows, ensure_ascii=False)
            user_prompt = f"<user_question>{normalize_text(message)}</user_question>\n<notice_evidence>{evidence}</notice_evidence>"
            try:
                if self.chat_provider == "ollama":
                    return self._ollama_chat(_prompt("answer_generation.txt"), user_prompt, temperature=0.1)
                if self.chat_provider == "gemini":
                    return self._gemini_generate(_prompt("answer_generation.txt"), user_prompt)
                response = self.client.chat.completions.create(
                    model=settings.openai_chat_model,
                    messages=[
                        {"role": "system", "content": _prompt("answer_generation.txt")},
                        {"role": "user", "content": user_prompt},
                    ], temperature=0.1,
                )
                return response.choices[0].message.content.strip()
            except Exception as exc:
                logger.warning(
                    "answer generation failed provider=%s model=%s error=%s",
                    self.chat_provider,
                    self.chat_model_name,
                    exc,
                )
                # 외부 모델이 내려가도 아래의 검증 가능한 요약으로 즉시 폴백한다.
        top = matches[0]
        meta = top["metadata"]
        sentences = [f"‘{top['notice'].title}’ 공지를 찾았습니다."]
        if meta.application_start or meta.application_end:
            start = meta.application_start.strftime("%Y.%m.%d %H:%M") if meta.application_start else None
            end = meta.application_end.strftime("%Y.%m.%d %H:%M") if meta.application_end else None
            if start and end:
                sentences.append(f"신청 기간은 {start}부터 {end}까지입니다.")
            elif end:
                sentences.append(f"신청 마감은 {end}입니다.")
            else:
                sentences.append(f"신청 시작은 {start}입니다.")
        if meta.application_method:
            sentences.append(f"확인된 신청 방법은 {meta.application_method}입니다.")
        if meta.required_documents:
            sentences.append(f"준비 서류는 {', '.join(meta.required_documents)}입니다.")
        if meta.department_name:
            sentences.append(f"담당 부서는 {meta.department_name}입니다.")
        sentences.append("세부 조건과 변경 여부는 아래 공식 원문에서 확인해 주세요.")
        return " ".join(sentences)

    def _mock_query(self, message: str) -> QueryFilters:
        text = normalize_text(message)
        category = None
        sub = None
        mapping = [
            (("휴학", "복학", "수강", "재수강", "졸업", "학점", "자퇴", "제적", "학적", "증명서", "전자출결"), "학사"),
            (("등록금", "납부"), "등록"), (("장학",), "장학"),
            (("예비군", "병무", "군대", "입영"), "병무"),
            (("교직", "교육실습"), "교직안내"), (("창업",), "창업교육안내"),
            (("학사일정", "개강", "방학"), "학사일정"),
        ]
        for words, candidate in mapping:
            if any(word in text for word in words):
                category = candidate
                break
        if "등록금" in text and "납부" in text:
            sub = "등록금 납부"
        else:
            for token in [
                "국가장학금", "수강신청", "학점교류", "전자출결", "증명서", "재수강",
                "등록금", "예비군", "자퇴", "졸업", "휴학", "복학", "전화번호",
            ]:
                if token in text:
                    sub = token
                    break
        extracted = rule_extract(text)
        grade_match = re.search(r"([1-4])\s*학년", text)
        semester = extracted["semester"]
        if "이번 학기" in text and semester is None:
            semester = 1 if datetime.now().month <= 7 else 2
        stopwords = {"어떻게", "방법", "절차", "알려줘", "알려주세요", "언제", "언제까지", "어디서", "뭐야", "무엇"}
        suffixes = ("알려주세요", "알려줘", "인가요", "까지야", "에서는", "에서", "으로", "하는", "해줘", "해야", "은", "는", "이", "가", "을", "를", "에", "로", "와", "과", "도", "요", "해")
        keywords = []
        for token in re.findall(r"[가-힣A-Za-z0-9]+", text):
            normalized_token = token
            for suffix in suffixes:
                if normalized_token.endswith(suffix) and len(normalized_token) - len(suffix) >= 2:
                    normalized_token = normalized_token[:-len(suffix)]
                    break
            if len(normalized_token) >= 2 and normalized_token not in stopwords:
                keywords.append(normalized_token)
        if sub:
            keywords.insert(0, sub)
        keywords = list(dict.fromkeys(keywords))
        return QueryFilters(
            intent=f"{sub or category} 정보 확인" if sub or category else None,
            category=category, sub_category=sub,
            academic_year=extracted["academic_year"], semester=semester,
            grade=int(grade_match.group(1)) if grade_match else None,
            student_status="재학생" if "재학생" in text else None,
            time_scope="current" if any(x in text for x in [
                "이번", "현재", "언제", "지금", "일자", "날짜", "기간", "마감",
            ]) else None,
            keywords=keywords,
        )

    def _mock_structure(
        self,
        title: str,
        content: str,
        published_at: datetime,
        department: dict | None,
        content_links: list[str] | None = None,
    ) -> StructuredNotice:
        query = self._mock_query(title + " " + content)
        # 본문의 "휴학생 제외" 같은 대상 조건이 공지 분류를 덮어쓰지 않도록
        # 제목에서 확인되는 분류와 하위 주제를 우선한다.
        title_query = self._mock_query(title)
        if title_query.category:
            query.category = title_query.category
            query.sub_category = title_query.sub_category
        category = query.category or "기타"
        action = "기타"
        if any(word in title + content for word in ("접수", "모집", "지원")):
            action = "신청"
        for word in ["신청", "제출", "납부", "확인", "참석", "수강", "발급", "문의"]:
            if word in title + content:
                action = word
                break
        now = datetime.now(timezone.utc)
        status = "active"
        keywords = list(dict.fromkeys(query.keywords[:12]))
        phone = (department or {}).get("phone")
        office = (department or {}).get("office_hours")
        name = (department or {}).get("name")
        contact_person, notice_phone = extract_notice_contact(content, name)
        phone = notice_phone or phone
        contact_email = extract_notice_email(content, name)
        role_match = re.search(r"\b(주무관|교수|선생님|직원|팀장|센터장)\b", contact_person or "")
        contact_role = role_match.group(1) if role_match else None
        location_match = re.search(
            r"(?:사무실\s*위치|방문\s*장소|문의처\s*위치)\s*[:：]\s*(.{1,80}?)(?=\s+(?:전화|문의|담당|이메일|운영시간)\s*[:：]|$)",
            normalize_text(content),
        )
        application_method = self._mock_application_method(title, content, action)
        application_start, application_end = (
            extract_application_period(f"{title} {content}", published_at)
            if action != "기타" else (None, None)
        )
        action_guide = self._mock_action_guide(
            title, content, action, application_method, content_links or [],
        )
        search_text = (
            f"제목: {title}\n분류: {category} > {query.sub_category or ''}\n"
            f"학년도: {query.academic_year or ''}\n학기: {query.semester or ''}\n"
            f"행동: {action}\n담당부서: {name or ''}\n키워드: {', '.join(keywords)}\n본문: {normalize_text(content)[:2000]}"
        )
        if application_start and application_start > now:
            status = "upcoming"
        elif application_end and application_end < now:
            status = "expired"
        elif application_start or application_end:
            status = "active"
        elif action != "기타":
            status = "unknown"
        synonym_map = {
            "증명서": ["재학증명서", "졸업증명서", "성적증명서", "제증명", "발급"],
            "재수강": ["재이수", "성적 삭제", "재수강 기준"],
            "자퇴": ["학업 중단", "학적 상실", "자퇴원"],
            "전자출결": ["출석", "출결", "전자출석", "출결 오류"],
            "학점교류": ["타대학 수강", "교류대학"],
            "전화번호": ["연락처", "문의처", "내선번호", "담당자"],
        }
        return StructuredNotice(
            category=category, sub_category=query.sub_category,
            academic_year=query.academic_year, semester=query.semester,
            published_at=published_at,
            application_period=Period(start=application_start, end=application_end), event_period=Period(),
            target=Target(), action_type=action,
            application_method=application_method,
            department=DepartmentInfo(
                name=name, contact_person=contact_person, contact_role=contact_role,
                phone=phone, email=contact_email,
                office_location=normalize_text(location_match.group(1)) if location_match else None,
                office_hours=office,
            ),
            keywords=keywords, synonyms=synonym_map.get(query.sub_category or "", []), search_text=search_text,
            notice_status=status, confidence=0.8, needs_review=False,
            action_guide=action_guide,
        )

    @staticmethod
    def _mock_application_method(title: str, content: str, action: str) -> str | None:
        text = normalize_text(f"{title} {content}")
        if "휴학원 처리 절차" in text and "일반휴학신청" in text:
            return (
                "종합정보시스템 접속 → 학적변동관리 → 일반휴학신청 → "
                "신규휴학신청 선택 → 휴학신청서 작성 및 제출 → 결재 상태 확인"
            )
        if "복학이란" in text and "종합정보시스템" in text:
            return (
                "종합정보시스템 접속 → 학적변동관리 → 복학신청 → "
                "필요한 증빙서류 첨부 → 신청 내용 제출 → 처리 상태 확인"
            )
        numbered_method = re.search(
            r"(?:신청|접수|제출|납부)방법.{0,100}?\b1[.)]\s*(.+?)\s+\b2[.)]\s*(.+?)(?=\s+※|\s+\b3[.)]|$)",
            text,
        )
        if numbered_method:
            parts = []
            for raw_part in numbered_method.groups():
                # 날짜·부연 설명 앞의 콜론까지가 학생이 수행할 핵심 행동이다.
                compact = normalize_text(raw_part.split(":", 1)[0]).strip(" -")
                if "네이버폼" in raw_part and "네이버폼" not in compact:
                    compact = f"{compact} (네이버폼)"
                elif "구글폼" in raw_part and "구글폼" not in compact:
                    compact = f"{compact} (구글폼)"
                if compact:
                    parts.append(compact[:100])
            if len(parts) == 2:
                return " → ".join([*parts, "모든 신청의 접수 완료 여부 확인"])
        known = [
            ("학사시스템", "학사시스템 → 학적 메뉴 → 신청 내용 입력 및 제출"),
            ("수강신청시스템", "수강신청시스템 접속 → 과목 선택 → 수강신청 확인"),
            ("한국장학재단", "한국장학재단 홈페이지 접속 → 장학금 신청 → 제출 결과 확인"),
            ("가상계좌", "등록금 고지서 확인 → 지정 가상계좌로 납부 → 납부 결과 확인"),
            ("예비군대대", "신고서 준비 → 예비군대대에 제출 → 접수 여부 확인"),
        ]
        for keyword, method in known:
            if keyword in text:
                return method
        if action != "기타":
            for sentence in re.split(r"(?<=[.!?])\s+", content):
                if action in sentence:
                    return normalize_text(sentence)
        return None

    @staticmethod
    def _matching_action_link(step_text: str, content_links: list[str]) -> str | None:
        if not content_links:
            return None
        normalized_step = normalize_text(step_text).lower()
        host_tokens = []
        if "한국장학재단" in normalized_step:
            host_tokens = ["kosaf"]
        elif "네이버" in normalized_step or "naver" in normalized_step:
            host_tokens = ["naver"]
        elif any(token in normalized_step for token in ("학사시스템", "수강신청시스템", "강남대학교")):
            host_tokens = ["kangnam.ac.kr"]
        elif any(token in normalized_step for token in ("구글폼", "google form")):
            host_tokens = ["google.com", "forms.gle"]
        elif "홈페이지" in normalized_step and len(content_links) == 1:
            return content_links[0]
        for link in content_links:
            host = (urlparse(link).hostname or "").lower()
            if any(token in host for token in host_tokens):
                return link
        return None

    @staticmethod
    def _mock_action_guide(
        title: str,
        content: str,
        action: str,
        application_method: str | None,
        content_links: list[str],
    ) -> StructuredActionGuide | None:
        if not application_method or action == "기타":
            return None
        parts = [normalize_text(part) for part in re.split(r"\s*(?:→|>)\s*", application_method) if normalize_text(part)]
        explicit_method = bool(re.search(r"(?:신청|접수|제출|납부|지원)\s*방법", content))
        if len(parts) == 1 and not explicit_method:
            return None
        action_map = {"신청": "submit", "제출": "submit", "납부": "pay", "확인": "verify", "수강": "submit", "발급": "submit", "문의": "contact"}
        steps = []
        matched_links = []
        leave_step_descriptions = {
            "종합정보시스템 접속": "학교 홈페이지에서 종합정보시스템에 로그인합니다.",
            "학적변동관리": "학적변동관리 메뉴로 이동합니다.",
            "일반휴학신청": "일반휴학신청 메뉴를 선택합니다.",
            "신규휴학신청 선택": "신규휴학신청을 눌러 새 신청을 시작합니다.",
            "휴학신청서 작성 및 제출": "신청 내용과 휴학 기간을 확인한 뒤 휴학신청서를 작성해 제출합니다.",
            "결재 상태 확인": "소속 학부(과)장과 교학팀의 결재 상태를 확인합니다.",
        }
        for index, part in enumerate(parts, start=1):
            is_last = index == len(parts)
            matched_link = AIService._matching_action_link(part, content_links)
            if matched_link:
                matched_links.append(matched_link)
            step_type = action_map.get(action, "other") if is_last else ("open_url" if any(token in part for token in ("시스템", "홈페이지", "재단", "폼", "사이트")) else "navigate")
            steps.append(StructuredActionStep(
                order=index,
                title=part,
                description=leave_step_descriptions.get(part, f"{part} 단계를 진행합니다."),
                action_type=step_type,
                action_url=matched_link,
                link_label="신청 페이지" if matched_link else None,
                source_type="html",
                source_locator="공지 본문",
                confidence=0.75,
            ))
        requires_all_paths = bool(re.search(r"(?:모두|전부)\s*신청|[12]번\s*모두", content))
        unique_links = list(dict.fromkeys(matched_links))
        return StructuredActionGuide(
            task_name=title,
            summary=(
                "일반휴학은 종합정보시스템에서 신청하며, 소속 학부(과)장과 교학팀 결재 후 처리됩니다."
                if "[상시 학사안내] 일반휴학" in title else normalize_text(content)[:240] or None
            ),
            steps=steps,
            warnings=["안내된 신청을 모두 완료해야 접수가 완료됩니다."] if requires_all_paths else [],
            application_url=unique_links[0] if len(unique_links) == 1 and not requires_all_paths else None,
            confidence=0.75,
            needs_review=False,
        )
