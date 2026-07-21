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
    CandidateRerankResult,
    DepartmentInfo,
    Period,
    QueryFilters,
    QueryPlan,
    QuerySubQuery,
    StructuredActionGuide,
    StructuredActionStep,
    StructuredNotice,
    Target,
)
from app.services.search.task_rules import TASK_BY_KEY, detect_task, detect_tasks
from app.utils.text import extract_application_period, extract_notice_contact, extract_notice_email, normalize_text, rule_extract


PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"
logger = logging.getLogger(__name__)

ALLOWED_REQUESTED_FIELDS = {
    "application_period", "event_period", "procedure", "source_location",
    "required_documents", "eligibility", "department_contact", "application_method",
    "application_location", "fee_information", "capacity", "selection_method",
    "result_announcement", "cancellation_policy", "benefits", "credits_or_hours",
    "leave_duration", "date", "schedule", "documents",
}
ALLOWED_REQUIRED_FACTS = {
    "totalCredits", "majorCredits", "generalEducationCredits", "applicationPeriod",
    "eventPeriod", "procedure", "requiredDocuments", "departmentContact", "eligibility",
    "credits", "leaveDuration", "applicationMethod", "applicationLocation", "feeInformation",
    "capacity", "selectionMethod", "resultAnnouncement", "cancellationPolicy", "benefits",
    "creditsOrHours",
}

EXTERNAL_OPPORTUNITY_PATTERN = re.compile(
    r"(?:부트\s*캠프|캠프|공모전|대외\s*활동|인턴|채용|취업|장학|특강|설명회|"
    r"프로그램|교육|연수|세미나|워크숍|해커톤|경진대회|서포터즈|봉사|모집|행사)",
    re.I,
)
KNU_CONTEXT_PATTERN = re.compile(
    r"(?:강남대(?:학교)?|교내|캠퍼스|학교|학생지원|학사|수강|휴학|복학|졸업|"
    r"등록금|장학금|예비군|전자출결|증명서|셔틀|순환\s*버스|도서관|기숙사|학생식당)",
    re.I,
)
OBVIOUS_GENERAL_PATTERN = re.compile(
    r"(?:파이썬|자바스크립트|코드\s*(?:짜|작성)|알고리즘|퀵\s*소트|날씨|일기예보|"
    r"번역해|레시피|주식\s*추천|코인\s*추천|비트코인|연예인|게임\s*공략)",
    re.I,
)
FOREIGN_UNIVERSITY_PATTERN = re.compile(
    r"([가-힣A-Za-z]{2,20}(?:대학교|대학|대))(?=\s|의|에서|은|는|이|가|$)",
    re.I,
)
FOREIGN_ADMIN_TERMS = {
    "학사", "학사일정", "수강", "수강신청", "휴학", "복학", "졸업", "입학",
    "등록금", "장학금", "기숙사", "도서관", "셔틀", "학과", "전공", "교수",
    "연락처", "전화번호", "정보", "일정", "기간", "방법", "알려줘", "알려주세요",
}


def _rule_scope(text: str, task, category: str | None) -> tuple[str, float, str]:
    """명백한 범위 밖 질문이 검색기의 최근접 공지로 흘러가지 않게 한다."""
    opportunity = bool(EXTERNAL_OPPORTUNITY_PATTERN.search(text))
    local_college_names = {
        "경영대", "경영대학", "인문사회융합대", "인문사회융합대학",
        "공과대", "공과대학", "복지융합대", "복지융합대학", "사범대", "사범대학",
    }
    foreign_matches = [
        match for match in FOREIGN_UNIVERSITY_PATTERN.finditer(text)
        if match.group(1) not in local_college_names
        and not match.group(1).startswith(("강남대", "강남대학교"))
    ]
    foreign_university = bool(foreign_matches)
    foreign_remainder = text
    for match in foreign_matches:
        foreign_remainder = foreign_remainder.replace(match.group(1), " ")
    has_distinctive_external_subject = any(
        token not in FOREIGN_ADMIN_TERMS
        for token in re.findall(r"[가-힣A-Za-z0-9]+", normalize_text(foreign_remainder))
        if len(token) >= 2
    )
    explicit_knu = bool(re.search(r"강남대(?:학교)?", text))
    follow_up = bool(re.match(r"\s*(?:그럼|그러면|거기|그건|그거)", text))
    if foreign_university:
        if opportunity or has_distinctive_external_subject:
            return "search_first", 0.95, "외부 대학과 고유 대상의 조합은 강남대 공식 게시 여부를 먼저 확인"
        return "out_of_scope", 0.95, "다른 대학 자체의 학사·행정 또는 일반 질문"
    if opportunity:
        return "search_first", 0.9, "외부 학생 프로그램의 강남대 공식 게시 가능성 확인"
    if OBVIOUS_GENERAL_PATTERN.search(text):
        return "out_of_scope", 0.98, "학교 표현이 섞여 있어도 요청 자체는 명백한 일반 질문"
    if task is not None or category is not None:
        return "in_scope", 0.95, "강남대 학생 업무로 식별됨"
    if follow_up:
        return "search_first", 0.8, "이전 학교 안내를 잇는 짧은 후속 질문 가능성"
    if explicit_knu or KNU_CONTEXT_PATTERN.search(text):
        return "search_first", 0.75, "강남대 관련 가능성이 있어 공식 자료 검색 필요"
    return "out_of_scope", 0.95, "강남대 공식 안내와 관련된 단서가 없음"


class GeminiTruncatedError(ValueError):
    """Gemini가 JSON을 끝까지 만들지 못했을 때만 짧게 재시도한다."""


def _prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


class AIService:
    @staticmethod
    def notice_structuring_prompt() -> str:
        return _prompt("notice_structuring.txt")

    @staticmethod
    def _relevant_excerpt(text: str, question: str, limit: int = 3000) -> str:
        """긴 첨부에서 질문 연도·업무 주변 근거를 모델에 전달한다."""
        source = normalize_text(text)
        if len(source) <= limit:
            return source
        years = re.findall(r"20\d{2}", question)
        for year in years:
            attachment = re.search(rf"첨부파일[^:]{{0,100}}{year}[^:]*:", source)
            if attachment:
                start = max(attachment.start() - 220, 0)
                return source[start:start + limit]
        tokens = [
            token for token in re.findall(r"[가-힣A-Za-z0-9]+", normalize_text(question))
            if len(token) >= 2 and token not in {"알려줘", "알려주세요", "방법", "기간", "기준", "어떻게"}
        ]
        positions = [(source.find(token), len(token)) for token in tokens if source.find(token) >= 0]
        if not positions:
            return source[:limit]
        position, _ = max(positions, key=lambda item: item[1])
        start = max(position - min(400, limit // 5), 0)
        return source[start:start + limit]

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
        self.last_rerank_trace: list[dict] = []
        # 운영 검증용 호출 메타데이터만 보존한다. 프롬프트·응답·키·토큰값은
        # 기록하지 않아 질문 원문이나 비밀값이 진단 로그에 남지 않는다.
        self.call_stats: list[dict] = []
        self.last_gemini_input_tokens: int | None = None
        self.last_gemini_output_tokens: int | None = None

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
        response_json_schema: dict | None = None,
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
        if response_json_schema is not None:
            generation_config["responseJsonSchema"] = response_json_schema
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
                usage = body.get("usageMetadata") or {}
                self.last_gemini_input_tokens = usage.get("promptTokenCount")
                self.last_gemini_output_tokens = usage.get("candidatesTokenCount")
                candidates = body.get("candidates") or []
                finish_reason = candidates[0].get("finishReason") if candidates else None
                if finish_reason == "MAX_TOKENS":
                    raise GeminiTruncatedError("Gemini JSON output was truncated")
                parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
                content = "".join(
                    part.get("text", "") for part in parts
                    if not part.get("thought") and part.get("text")
                ).strip()
                if not content:
                    block_reason = (body.get("promptFeedback") or {}).get("blockReason")
                    raise ValueError(f"Gemini가 빈 답변을 반환했습니다. blockReason={block_reason}")
                return content
            except GeminiTruncatedError:
                raise
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
        gemini_schema = self._gemini_compatible_schema(schema)
        schema_hint = json.dumps(
            gemini_schema if self.chat_provider == "gemini" else schema,
            ensure_ascii=False,
        )
        system_prompt = f"{_prompt(prompt_name)}\n반환 JSON 스키마:\n{schema_hint}"
        started = time.perf_counter()
        succeeded = False
        try:
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
                    response_json_schema=gemini_schema,
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
            result = model_type.model_validate_json(raw)
            succeeded = True
            return result
        finally:
            self.call_stats.append({
                "operation": prompt_name.removesuffix(".txt"),
                "provider": self.chat_provider,
                "model": self.chat_model_name,
                "succeeded": succeeded,
                "elapsedMs": round((time.perf_counter() - started) * 1000, 1),
            })

    @staticmethod
    def _gemini_compatible_schema(schema: dict) -> dict:
        """Gemini structured output이 지원하는 JSON Schema 부분집합만 남긴다."""
        supported = {
            "$id", "$defs", "$ref", "$anchor", "type", "format", "title", "description",
            "enum", "items", "prefixItems", "minItems", "maxItems", "minimum", "maximum",
            "anyOf", "oneOf", "properties", "additionalProperties", "required", "propertyOrdering",
        }

        def clean(value):
            if isinstance(value, list):
                return [clean(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {}
            for key, item in value.items():
                if key not in supported:
                    continue
                if key in {"properties", "$defs"}:
                    result[key] = {name: clean(child) for name, child in item.items()}
                else:
                    result[key] = clean(item)
            return result

        result = clean(schema)
        if result.get("title") == "QueryPlan":
            # QueryPlan의 subQueries는 동일한 대형 task enum을 중첩 객체에서
            # 한 번 더 참조한다. Gemini REST structured output은 이 조합을
            # INVALID_ARGUMENT로 거부하므로, 최상위 requestedTasks를 AI가
            # 판별하고 서버가 검증된 subQuery를 만드는 구조로 제한한다.
            result.get("properties", {}).pop("subQueries", None)
            result.pop("$defs", None)
        return result

    def embedding(self, text: str) -> list[float]:
        if self.embedding_provider == "openai":
            started = time.perf_counter()
            succeeded = False
            try:
                result = self.client.embeddings.create(
                    model=settings.openai_embedding_model,
                    input=text[:12000],
                    dimensions=settings.embedding_dimensions,
                )
                succeeded = True
                return result.data[0].embedding
            finally:
                self.call_stats.append({
                    "operation": "embedding", "provider": "openai",
                    "model": self.embedding_model_name, "succeeded": succeeded,
                    "elapsedMs": round((time.perf_counter() - started) * 1000, 1),
                })
        if self.embedding_provider == "ollama":
            started = time.perf_counter()
            succeeded = False
            try:
                response = requests.post(
                    f"{settings.ollama_base_url.rstrip('/')}/api/embed",
                    json={
                        "model": settings.ollama_embedding_model,
                        # 현재 Ollama의 BGE-M3 컨텍스트(4096)를 넘는 긴 한국어
                        # 문서는 청크 단위로 처리한다.
                        "input": text[:3200],
                        "truncate": True,
                        # 첫 질문마다 모델을 다시 읽는 지연을 피한다. Ollama가
                        # 메모리 압박을 받으면 이 시간 전에도 안전하게 내릴 수 있다.
                        "keep_alive": settings.ollama_embedding_keep_alive,
                    },
                    timeout=settings.embedding_request_timeout,
                )
                response.raise_for_status()
                payload = response.json()
                values = (payload.get("embeddings") or [payload.get("embedding") or []])[0]
                succeeded = True
                return self._fit_embedding(values)
            finally:
                self.call_stats.append({
                    "operation": "embedding", "provider": "ollama",
                    "model": self.embedding_model_name, "succeeded": succeeded,
                    "elapsedMs": round((time.perf_counter() - started) * 1000, 1),
                })
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

    def analyze_query(self, message: str) -> QueryPlan:
        rule_result = self._mock_query(message)
        if rule_result.scope == "out_of_scope":
            return rule_result
        if self.chat_provider == "rules":
            return rule_result
        if (
            not settings.on_demand_search_enabled
            and settings.local_ai_complex_queries_only
            and not self._needs_ai_query_analysis(message, rule_result)
        ):
            return rule_result
        # 정상 경로는 정확히 한 번 호출한다. JSON 절단/스키마 실패일 때만
        # 출력 예산을 줄여 한 번 더 시도하고, 이후에는 규칙 분석으로 끝낸다.
        for output_budget in (512, 256):
            try:
                ai_result = self._structured_chat(
                    "query_analysis.txt",
                    f"<user_question>{normalize_text(message)}</user_question>",
                    QueryPlan,
                    num_predict=output_budget,
                )
                return self._merge_query_filters(rule_result, ai_result)
            except (ValidationError, GeminiTruncatedError, json.JSONDecodeError) as exc:
                logger.warning("query plan JSON retryable failure budget=%s error=%s", output_budget, exc)
                continue
            except Exception as exc:
                logger.warning("query analysis fallback provider=%s error=%s", self.chat_provider, exc)
                break
        return rule_result

    def rerank_candidates(self, message: str, query: QueryFilters, matches: list[dict]) -> list[dict]:
        """Gemini는 검색을 대신하지 않고 이미 찾은 소수 후보의 업무 일치만 판정한다."""
        if not settings.llm_candidate_reranking_enabled or self.chat_provider == "rules" or len(matches) < 2:
            return matches
        # canonical 업무키가 이미 분석된 질문은 학년도·학기·입학년도·대상
        # 제약을 적용한 결정론적 순위를 그대로 사용한다. 외부 모델은 알 수
        # 없는/복합 표현의 보조 판정에만 써서 지연·토큰·JSON 절단 실패가
        # 정상 검색을 흔들지 않게 한다.
        if query.task_key in TASK_BY_KEY:
            return matches
        self.last_rerank_trace = []
        candidates = []
        for item in matches[:8]:
            notice = item["notice"]
            unit = item.get("task_unit")
            candidates.append({
                "candidateId": item["candidate_id"],
                "noticeId": notice.id,
                "taskUnitId": unit.id if unit is not None else None,
                "title": notice.title,
                "taskKey": unit.task.task_key if unit is not None else None,
                "taskName": unit.task.name if unit is not None else None,
                "sectionTitle": unit.section_title if unit is not None else None,
                "evidence": self._relevant_excerpt(item.get("chunk_text") or notice.content, message, 1800),
                "retrievalScore": float(item["score"]),
            })
        payload = json.dumps({
            "question": normalize_text(message),
            "queryPlan": query.model_dump(mode="json", by_alias=True),
            "candidates": candidates,
        }, ensure_ascii=False)
        try:
            result = self._structured_chat(
                "candidate_reranking.txt", f"<rerank_data>{payload}</rerank_data>",
                CandidateRerankResult, num_predict=1200,
            )
        except Exception as exc:
            logger.warning("candidate reranking failed provider=%s error=%s", self.chat_provider, exc)
            return matches
        judgements = {item.candidate_id: item for item in result.candidates}
        reranked = []
        for item in matches:
            judgement = judgements.get(item["candidate_id"])
            trace = {
                "candidateId": item["candidate_id"],
                "noticeId": item["notice"].id,
                "decision": "excluded" if judgement and (judgement.rejection_reason or judgement.relevance < 0.3) else "kept",
                "relevance": judgement.relevance if judgement else None,
                "matchedFields": judgement.matched_fields if judgement else [],
                "reason": judgement.rejection_reason if judgement else "AI 판정 없음",
            }
            self.last_rerank_trace.append(trace)
            logger.info("rerank_candidate %s", trace)
            if judgement and (judgement.rejection_reason or judgement.relevance < 0.3):
                continue
            copy = dict(item)
            if judgement:
                copy["score"] = round(float(copy["score"]) * 0.7 + judgement.relevance * 0.3, 5)
                copy["candidate_judgement"] = judgement
            reranked.append(copy)
        # 외부 재정렬기가 모든 공식 후보를 제거한 경우 검색 자체를 실패로
        # 바꾸지 않는다. 강한 규칙 조건을 통과한 원래 후보를 사용하고 AI
        # 배제 사유는 진단 로그에만 남긴다.
        if not reranked:
            logger.warning("candidate reranking removed all matches; using constrained retrieval fallback")
            return matches
        return sorted(reranked, key=lambda item: item["score"], reverse=True)

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
    def _merge_query_filters(rule_result: QueryPlan, ai_result: QueryPlan) -> QueryPlan:
        rule_scope = getattr(rule_result, "scope", "search_first")
        if rule_scope == "out_of_scope":
            return rule_result
        data = ai_result.model_dump()
        # 규칙 판정은 명백한 범위 밖 질문의 안전 경계이고, search_first는
        # 외부 프로그램을 모델이 성급히 제외하지 못하게 하는 완충 구간이다.
        ai_scope = getattr(ai_result, "scope", "search_first")
        rule_reason = getattr(rule_result, "scope_reason", None) or ""
        protected_search = rule_reason.startswith("외부") or "후속 질문" in rule_reason
        resolved_scope = rule_scope
        if (
            rule_scope == "search_first"
            and ai_scope == "out_of_scope"
            and float(getattr(ai_result, "scope_confidence", 0.5)) >= 0.75
            and not protected_search
        ):
            resolved_scope = "out_of_scope"
        if resolved_scope == "out_of_scope":
            return QueryPlan(
                scope="out_of_scope",
                scope_confidence=max(
                    float(getattr(rule_result, "scope_confidence", 0.5)),
                    float(getattr(ai_result, "scope_confidence", 0.5)),
                ),
                scope_reason=getattr(ai_result, "scope_reason", None) or rule_reason or None,
                intent_confidence=0.0,
            )
        data["scope"] = resolved_scope
        data["scope_confidence"] = max(
            float(getattr(rule_result, "scope_confidence", 0.5)),
            float(getattr(ai_result, "scope_confidence", 0.5)),
        )
        data["scope_reason"] = (
            rule_reason
            or getattr(ai_result, "scope_reason", None)
        )
        for field in (
            "task_key", "category", "sub_category", "academic_year", "admission_year", "semester", "grade",
            "department", "student_status", "time_scope", "college",
        ):
            rule_value = getattr(rule_result, field, None)
            if rule_value is not None:
                data[field] = rule_value
        if data.get("category") not in CATEGORIES:
            data["category"] = rule_result.category
        data["keywords"] = list(dict.fromkeys(rule_result.keywords + (ai_result.keywords or [])))[:15]
        data["intent"] = ai_result.intent or rule_result.intent
        data["intent_confidence"] = min(
            float(getattr(rule_result, "intent_confidence", 1.0)),
            float(getattr(ai_result, "intent_confidence", 1.0)),
        )
        if getattr(rule_result, "requested_tasks", None):
            data["requested_tasks"] = rule_result.requested_tasks
            data["sub_queries"] = [item.model_dump() for item in rule_result.sub_queries]
        ai_requested_fields = [
            value for value in (getattr(ai_result, "requested_fields", []) or [])
            if value in ALLOWED_REQUESTED_FIELDS
        ]
        data["requested_fields"] = list(dict.fromkeys(
            getattr(rule_result, "requested_fields", []) + ai_requested_fields
        ))[:20]
        ai_required_facts = [
            value for value in (getattr(ai_result, "required_facts", []) or [])
            if value in ALLOWED_REQUIRED_FACTS
        ]
        data["required_facts"] = list(dict.fromkeys(
            getattr(rule_result, "required_facts", []) + ai_required_facts
        ))[:12]
        data["search_terms"] = list(dict.fromkeys(
            (getattr(ai_result, "search_terms", []) or []) + getattr(rule_result, "search_terms", [])
        ))[:4]
        if getattr(rule_result, "needs_clarification", False):
            data["needs_clarification"] = True
            data["clarification_question"] = rule_result.clarification_question
            data["clarification_options"] = list(rule_result.clarification_options)
        elif getattr(ai_result, "needs_clarification", False) and rule_result.task_key is None:
            data["needs_clarification"] = True
            data["clarification_question"] = ai_result.clarification_question
            data["clarification_options"] = list(ai_result.clarification_options)[:3]
        else:
            data["needs_clarification"] = False
            data["clarification_question"] = None
            data["clarification_options"] = []
        return QueryPlan.model_validate(data)

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
                    "evidence": self._relevant_excerpt(item.get("chunk_text") or notice.content, message, 3200),
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
            started = time.perf_counter()
            succeeded = False
            try:
                if self.chat_provider == "ollama":
                    answer = self._ollama_chat(_prompt("answer_generation.txt"), user_prompt, temperature=0.1)
                    succeeded = True
                    return answer
                if self.chat_provider == "gemini":
                    answer = self._gemini_generate(_prompt("answer_generation.txt"), user_prompt)
                    succeeded = True
                    return answer
                response = self.client.chat.completions.create(
                    model=settings.openai_chat_model,
                    messages=[
                        {"role": "system", "content": _prompt("answer_generation.txt")},
                        {"role": "user", "content": user_prompt},
                    ], temperature=0.1,
                )
                succeeded = True
                return response.choices[0].message.content.strip()
            except Exception as exc:
                logger.warning(
                    "answer generation failed provider=%s model=%s error=%s",
                    self.chat_provider,
                    self.chat_model_name,
                    exc,
                )
                # 외부 모델이 내려가도 아래의 검증 가능한 요약으로 즉시 폴백한다.
            finally:
                self.call_stats.append({
                    "operation": "answer_generation", "provider": self.chat_provider,
                    "model": self.chat_model_name, "succeeded": succeeded,
                    "elapsedMs": round((time.perf_counter() - started) * 1000, 1),
                })
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

    def _mock_query(self, message: str) -> QueryPlan:
        text = normalize_text(message)
        compact_text = re.sub(r"\s+", "", text)
        task = detect_task(text)
        tasks = detect_tasks(text)
        # 공식 공지의 고유한 외부 프로그램명을 '캠프'를 생략해
        # 물어도 모델 판단에 의존하지 않고 행사 업무로 검색한다.
        if not task and "경기대" in compact_text and "크래프톤" in compact_text:
            task = TASK_BY_KEY["event.camp"]
            tasks = [task]
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
        if task:
            category = task.category
            sub = task.name
        if not task and "등록금" in text and "납부" in text:
            sub = "등록금 납부"
        elif not task:
            for token in [
                "국가장학금", "수강신청", "학점교류", "전자출결", "증명서", "재수강",
                "등록금", "예비군", "자퇴", "졸업", "휴학", "복학", "전화번호",
            ]:
                if token in text:
                    sub = token
                    break
        scope, scope_confidence, scope_reason = _rule_scope(text, task, category)
        if scope == "out_of_scope":
            return QueryPlan(
                scope=scope,
                scope_confidence=scope_confidence,
                scope_reason=scope_reason,
                intent_confidence=0.0,
            )

        extracted = rule_extract(text)
        # 졸업요건의 연도는 제도 시행연도가 아니라 입학 코호트를 뜻한다.
        # 특히 "2021~2024학년도 졸업요건"처럼 '입학자'를 생략한 표현도
        # 범위의 끝 연도를 검색 필터로 사용해 해당 코호트 표를 찾는다.
        graduation_cohort = re.search(
            r"(20\d{2})\s*[~∼-]\s*(20\d{2})\s*학년도(?:\s*(?:입학자|입학생))?",
            text,
        ) if task and task.key == "graduation.requirements" else None
        if task and task.key == "graduation.requirements":
            if graduation_cohort:
                extracted["admission_year"] = int(graduation_cohort.group(2))
                extracted["academic_year"] = None
            elif extracted.get("admission_year") is None and extracted.get("academic_year"):
                extracted["admission_year"] = extracted["academic_year"]
                extracted["academic_year"] = None
        grade_match = re.search(r"(?<!\d)([1-4])\s*학년(?!도)", text)
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
        if sub and sub.replace(" ", "") in text.replace(" ", ""):
            keywords.insert(0, sub)
        keywords = list(dict.fromkeys(keywords))[:15]
        requested_fields = []
        for field, words in (
            ("application_period", ("기간", "언제", "일정", "마감", "일자", "날짜")),
            ("procedure", ("방법", "절차", "순서", "어떻게", "제출")),
            ("source_location", ("어디서 확인", "어디에서 확인", "원문", "링크")),
            ("required_documents", ("서류", "준비물")),
            ("eligibility", ("대상", "자격", "조건", "누가")),
            ("department_contact", ("담당", "전화", "연락처", "문의처")),
        ):
            if any(word in text for word in words):
                requested_fields.append(field)
        asks_leave_duration = bool(
            task and task.key == "leave.general" and "기간" in text
            and any(word in text for word in ("최대", "가능", "몇 학기", "몇학기", "연속", "통산", "몇 년", "몇년"))
        )
        if asks_leave_duration:
            requested_fields = [field for field in requested_fields if field != "application_period"]
            requested_fields.append("leave_duration")
        if any(word in compact_text for word in ("하는법", "신청법", "접수법", "제출법")):
            requested_fields.append("procedure")
        requested_fields = list(dict.fromkeys(requested_fields))
        required_facts = []
        if task and task.key == "graduation.requirements":
            required_facts = ["totalCredits", "majorCredits", "generalEducationCredits"]
        else:
            if "학점" in text:
                required_facts.append("credits")
            if "기간" in text or "일정" in text or "언제" in text:
                required_facts.append("applicationPeriod")
            if asks_leave_duration:
                required_facts = [fact for fact in required_facts if fact != "applicationPeriod"]
                required_facts.append("leaveDuration")
            if any(word in text for word in ("방법", "절차", "어떻게")):
                required_facts.append("procedure")
            elif any(word in compact_text for word in ("하는법", "신청법", "접수법", "제출법")):
                required_facts.append("procedure")
            if any(word in text for word in ("서류", "준비물")):
                required_facts.append("requiredDocuments")
            if any(word in text for word in ("담당", "전화", "연락처", "문의처")):
                required_facts.append("departmentContact")
            if any(word in text for word in ("대상", "자격", "조건", "누가")):
                required_facts.append("eligibility")

        college = next((name for name in (
            "경영대", "경영대학", "인문사회융합대", "공과대", "복지융합대", "사범대",
        ) if name in text), None)
        department_match = re.search(r"([가-힣A-Za-z0-9·]{2,30}(?:학과|전공))", text)
        department = department_match.group(1) if department_match else None
        needs_clarification = bool(
            task and task.key == "graduation.requirements" and college and not department
        )
        clarification_question = (
            f"{college} 내 어느 학과 또는 전공인가요?"
            if needs_clarification else None
        )
        clarification_options: list[str] = []

        # 같은 짧은 표현이 서로 다른 공식 기준을 가리키는 경우에는 검색
        # 결과가 우연히 먼저 나온 뜻으로 확정되지 않도록 한 번 더 묻는다.
        if compact_text in {"휴학", "휴학안내", "휴학알려줘", "휴학정보"}:
            needs_clarification = True
            clarification_question = "휴학에 관해 어떤 내용을 찾으시나요?"
            clarification_options = ["휴학 신청 방법", "휴학 신청 기간", "휴학 종류와 조건"]
        elif (
            task and task.key == "leave.general" and "기간" in text
            and not any(word in text for word in ("신청", "접수", "언제부터", "언제까지", "마감", "최대", "가능", "몇 학기", "몇학기"))
        ):
            needs_clarification = True
            clarification_question = "휴학 신청 기간과 최대 휴학 가능 기간 중 어떤 것을 찾으시나요?"
            clarification_options = ["휴학 신청 기간", "최대 휴학 가능 기간"]
        elif task is None and "장학" in text:
            needs_clarification = True
            clarification_question = "어떤 장학금 정보를 찾으시나요?"
            clarification_options = ["국가장학금", "성적우수장학금", "교내·교외 장학금"]
        elif task is None and "등록금" in text:
            needs_clarification = True
            clarification_question = "등록금 납부와 반환 중 어떤 내용을 찾으시나요?"
            clarification_options = ["등록금 납부", "등록금 반환"]
        elif task is None and "예비군" in text:
            needs_clarification = True
            clarification_question = "예비군의 어떤 업무를 찾으시나요?"
            clarification_options = ["학생예비군 편성·전입", "예비군 교육훈련", "훈련 연기·신고"]
        elif task is None and "성적" in text:
            needs_clarification = True
            clarification_question = "성적 확인과 이의신청 중 어떤 내용을 찾으시나요?"
            clarification_options = ["성적 확인", "성적 이의신청"]
        elif task is None and "졸업" in text:
            needs_clarification = True
            clarification_question = "일반 졸업요건과 다른 졸업 업무 중 어떤 내용을 찾으시나요?"
            clarification_options = ["일반 졸업요건", "졸업인증제", "조기졸업"]

        intent_confidence = (
            0.45 if needs_clarification
            else 0.95 if task is not None
            else 0.7 if category is not None
            else 0.3
        )
        scope_parts = [
            str(extracted.get("academic_year") or ""),
            f"{semester}학기" if semester else "",
            f"{extracted.get('admission_year')}년도 입학생" if extracted.get("admission_year") else "",
        ]
        field_text = " ".join(requested_fields)
        sub_queries = [QuerySubQuery(
            task_key=item.key,
            task_name=item.name,
            query_text=normalize_text(f"{' '.join(scope_parts)} {item.name} {field_text}"),
        ) for item in tasks]
        return QueryPlan(
            scope=scope,
            scope_confidence=scope_confidence,
            scope_reason=scope_reason,
            intent=f"{sub or category} 정보 확인" if sub or category else None,
            task_key=task.key if task else None,
            category=category, sub_category=sub,
            academic_year=extracted["academic_year"], semester=semester,
            admission_year=extracted.get("admission_year"),
            grade=int(grade_match.group(1)) if grade_match else None,
            college=college,
            department=department,
            student_status="재학생" if "재학생" in text else None,
            time_scope="current" if any(x in text for x in [
                "이번", "현재", "언제", "지금", "일자", "날짜", "기간", "마감",
            ]) else None,
            keywords=keywords,
            requested_tasks=[item.key for item in tasks],
            requested_fields=requested_fields,
            required_facts=required_facts,
            search_terms=list(dict.fromkeys(term for term in [
                normalize_text(f"{extracted.get('admission_year') or extracted.get('academic_year') or ''} {college or department or ''} {sub or category or ''}"),
                normalize_text(f"{sub or category or ''} 대학요람 학사안내"),
            ] if term))[:2],
            intent_confidence=intent_confidence,
            needs_clarification=needs_clarification,
            clarification_question=clarification_question,
            clarification_options=clarification_options,
            sub_queries=sub_queries,
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
    def _attachment_arrow_method(content: str) -> tuple[str, str] | None:
        """PDF에서 추출된 화살표 지원 순서를 모델 호출 없이 복원한다."""
        for match in re.finditer(
            r"\[PDF page\s+(\d+)\]\s*(.*?)(?=\[PDF page\s+\d+\]|\Z)",
            content,
            re.DOTALL | re.IGNORECASE,
        ):
            page_number, page_text = match.group(1), normalize_text(match.group(2))
            if "→" not in page_text or not any(
                token in page_text for token in ("신청하기", "지원서", "신청서", "접수")
            ):
                continue
            start_tokens = ("링크 접속", "홈페이지 접속", "사이트 접속", "로그인")
            starts = [page_text.find(token) for token in start_tokens if token in page_text]
            if not starts:
                continue
            start = min(value for value in starts if value >= 0)
            tail = page_text[start:]
            stop = re.search(
                r"(?:\s|^)(?:o|□|■|※)\s*(?:지원서\s*접수\s*기간|신청\s*기간|접수\s*기간|최종\s*선발|문의)",
                tail,
            )
            if stop:
                tail = tail[:stop.start()]
            parts = [
                normalize_text(part).strip(" -:：")
                for part in tail.split("→")
                if normalize_text(part).strip(" -:：")
            ]
            if len(parts) < 2:
                continue
            return " → ".join(parts[:12]), f"PDF page {page_number}"
        return None

    @staticmethod
    def _mock_application_method(title: str, content: str, action: str) -> str | None:
        text = normalize_text(f"{title} {content}")
        if "휴학원 처리 절차" in text and "일반휴학신청" in text:
            return (
                "종합정보시스템 접속 → 학적변동관리 → 일반휴학신청 → "
                "신규휴학신청 선택 → 휴학신청서 작성 및 제출"
            )
        if "복학이란" in text and "종합정보시스템" in text:
            return (
                "종합정보시스템 접속 → 학적변동관리 → 복학신청 → "
                "필요한 증빙서류 첨부 → 신청 내용 제출"
            )
        attachment_method = AIService._attachment_arrow_method(content)
        if attachment_method:
            return attachment_method[0]
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
        elif "링크" in normalized_step and len(content_links) == 1:
            return content_links[0]
        elif "홈페이지" in normalized_step and len(content_links) == 1:
            return content_links[0]
        for link in content_links:
            host = (urlparse(link).hostname or "").lower()
            if any(token in host for token in host_tokens):
                return link
        return None

    @staticmethod
    def _action_link_label(url: str | None) -> str | None:
        if not url:
            return None
        host = (urlparse(url).hostname or "").lower()
        if host == "barun.kyonggi.ac.kr":
            return "경기대 Barun 열기"
        if host.endswith("kangnam.ac.kr"):
            return "강남대 사이트 열기"
        return "해당 사이트 열기"

    @staticmethod
    def _mock_notice_summary(title: str, content: str) -> str:
        """등록자·파일 표시가 아닌 행사의 '무엇인지'를 로컬 규칙으로 요약한다."""
        text = normalize_text(content)
        joint_program = re.search(
            r"본\s*(?:과정|프로그램)은\s*(.{2,100}?)에서\s*함께\s*주최하는\s*"
            r"(.{2,240}?)으로서[,]?\s*(.{10,360}?(?:입니다|합니다))",
            text,
        )
        if joint_program:
            organizers = normalize_text(joint_program.group(1)).strip(" -")
            program = normalize_text(joint_program.group(2)).strip(" -")
            purpose = normalize_text(joint_program.group(3)).strip(" -.") + "."
            # 긴 부제가 따옴표 안에 들어 있으면 유형까지만 남겨
            # 카드 첫 문장이 프로그램 설명으로 바로 읽히게 한다.
            program = re.split(r"[“\"]", program, maxsplit=1)[0].strip(" -") or program
            return normalize_text(
                f"{organizers}에서 함께 주최하는 {program}입니다. {purpose}"
            )[:600]

        # 공지 앞의 '등록자/부서'를 피하고 프로그램 정의가 든 첫 문장을 사용한다.
        for sentence in re.split(r"(?<=[.!?])\s+|(?=\[PDF page\s+\d+\])", text):
            sentence = normalize_text(re.sub(r"\[PDF page\s+\d+\]", "", sentence))
            if not 30 <= len(sentence) <= 600:
                continue
            if sentence.startswith(("등록자", "[첨부파일:")):
                continue
            if any(token in sentence for token in ("프로그램", "캠프", "행사", "교육")) and any(
                token in sentence for token in ("입니다", "합니다", "제공", "주최")
            ):
                return sentence[:600]
        return f"‘{normalize_text(title)}’에 대한 공식 안내입니다."

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
        attachment_method = AIService._attachment_arrow_method(content)
        source_type = "pdf" if attachment_method else "html"
        source_locator = attachment_method[1] if attachment_method else "공지 본문"
        action_map = {"신청": "submit", "제출": "submit", "납부": "pay", "확인": "verify", "수강": "submit", "발급": "submit", "문의": "contact"}
        steps = []
        matched_links = []
        directly_matched_links = list(dict.fromkeys(filter(None, (
            AIService._matching_action_link(part, content_links) for part in parts[:30]
        ))))
        flow_url = directly_matched_links[0] if len(directly_matched_links) == 1 else None
        linked_flow_terms = (
            "가입", "로그인", "메뉴", "검색", "신청하기", "업로드",
            "정보 입력", "작성", "선택", "제출", "납부", "확인",
        )
        leave_step_descriptions = {
            "종합정보시스템 접속": "학교 홈페이지에서 종합정보시스템에 로그인합니다.",
            "학적변동관리": "학적변동관리 메뉴로 이동합니다.",
            "일반휴학신청": "일반휴학신청 메뉴를 선택합니다.",
            "신규휴학신청 선택": "신규휴학신청을 눌러 새 신청을 시작합니다.",
            "휴학신청서 작성 및 제출": "신청 내용과 휴학 기간을 확인한 뒤 휴학신청서를 작성해 제출합니다.",
        }
        for index, part in enumerate(parts[:30], start=1):
            is_last = index == len(parts)
            matched_link = AIService._matching_action_link(part, content_links)
            # 공식 근거에서 단 하나의 신청 사이트가 확인되고,
            # 후속 단계가 그 사이트 안의 회원가입·메뉴·업로드 작업이면
            # 같은 검증 링크를 단계 버튼에도 연결한다.
            if not matched_link and flow_url and any(term in part for term in linked_flow_terms):
                matched_link = flow_url
            if matched_link:
                matched_links.append(matched_link)
            step_type = action_map.get(action, "other") if is_last else ("open_url" if any(token in part for token in ("시스템", "홈페이지", "재단", "폼", "사이트")) else "navigate")
            steps.append(StructuredActionStep(
                order=index,
                title=part,
                description=leave_step_descriptions.get(part, f"{part} 단계를 진행합니다."),
                action_type=step_type,
                action_url=matched_link,
                link_label=AIService._action_link_label(matched_link),
                source_type=source_type,
                source_locator=source_locator,
                confidence=0.75,
            ))
        requires_all_paths = bool(re.search(r"(?:모두|전부)\s*신청|[12]번\s*모두", content))
        unique_links = list(dict.fromkeys(matched_links))
        return StructuredActionGuide(
            task_name=title,
            summary=(
                "일반휴학은 종합정보시스템에서 신청하며, 소속 학부(과)장과 교학팀 결재 후 처리됩니다."
                if "[상시 학사안내] 일반휴학" in title
                else AIService._mock_notice_summary(title, content)
            ),
            steps=steps,
            warnings=["안내된 신청을 모두 완료해야 접수가 완료됩니다."] if requires_all_paths else [],
            application_url=unique_links[0] if len(unique_links) == 1 and not requires_all_paths else None,
            confidence=0.75,
            needs_review=False,
        )
