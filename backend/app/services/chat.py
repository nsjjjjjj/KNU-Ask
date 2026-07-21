import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import ChatSessionContext, FAQ, Notice, QueryMetric, StaffDirectoryContact
from app.schemas import (
    ActionGuideResponse,
    AnswerFact,
    AnswerMedia,
    ChatResponse,
    DepartmentInfo,
    NextAction,
    NoticeSummary,
    Period,
    QueryPlan,
    QuerySubQuery,
    SearchScope,
    SourceEvidence,
    StructuredActionStep,
    TaskAnswerResponse,
)
from app.services.ai import AIService
from app.services.data_gaps import collect_answer_gaps
from app.services.evidence_recovery import MissingEvidenceRecovery, RecoveryOutcome
from app.services.notice_status import effective_status, effective_status_label
from app.services.on_demand.cache import AnswerCacheStore
from app.services.on_demand.service import OnDemandEvidenceResolver, local_evidence_insufficient
from app.services.on_demand.sources import is_allowed_school_url
from app.services.search import HybridSearch
from app.services.search.task_rules import TASK_BY_KEY, guide_matches_query, visible_student_step
from app.services.staff_directory import resolve_staff_contact
from app.utils.text import is_dangerous_action_request, is_harm_reporting_request, normalize_text


KST = ZoneInfo("Asia/Seoul")

SHUTTLE_REQUEST_PATTERN = re.compile(r"(?:무료\s*)?(?:셔틀(?:버스)?|순환\s*버스|통학\s*버스|학교\s*버스)", re.I)
SHUTTLE_TIME_PATTERN = re.compile(r"(?:시간표|운행\s*시간|출발\s*시간|몇\s*시|언제|시간|시각)", re.I)
SCOPE_GENERIC_TERMS = {
    "강남대", "강남대학교", "학교", "교내", "학생", "관련", "질문", "분류",
    "신청", "방법", "절차", "기간", "일정", "시간", "언제", "어디", "알려줘", "알려주세요",
    "안내", "공식", "공지", "모집", "행사", "프로그램", "특강", "캠프", "부트캠프",
}


def _step_link_label(url: str | None, stored_label: str | None = None) -> str | None:
    """저장 스키마에 라벨이 없는 TaskProcedureStep도 확인된 URL로 안전한 버튼명을 만든다."""
    if stored_label:
        return stored_label
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    if host == "barun.kyonggi.ac.kr":
        return "경기대 Barun 열기"
    if host.endswith("kangnam.ac.kr"):
        return "강남대 사이트 열기"
    return "해당 사이트 열기"


def _match_task_key(match: dict) -> str | None:
    unit = match.get("task_unit")
    return unit.task.task_key if unit is not None and unit.task is not None else None


def _is_shuttle_request(message: str, query: QueryPlan) -> bool:
    return bool(
        SHUTTLE_REQUEST_PATTERN.search(normalize_text(message))
        or (query.context_applied and query.task_key == "shuttle.info")
    )


def _answer_media(message: str, query: QueryPlan, match: dict) -> list[AnswerMedia]:
    """시간표를 묻는 경우에만 공식 공지의 본문 이미지를 답변에 붙인다."""
    if not _is_shuttle_request(message, query) or not SHUTTLE_TIME_PATTERN.search(normalize_text(message)):
        return []
    if _match_task_key(match) != "shuttle.info" and query.task_key != "shuttle.info":
        return []

    notice = match["notice"]
    for index, item in enumerate(notice.attachment_manifest or []):
        name = normalize_text(str(item.get("name") or ""))
        url = str(item.get("url") or "")
        method = str(item.get("extractionMethod") or item.get("extraction_method") or "")
        if method != "image_ocr" and "본문 이미지" not in name and "/image.do?" not in url:
            continue
        return [AnswerMedia(
            url=f"/api/notices/{notice.id}/media/{index}",
            alt="강남대학교 무료 순환버스 운행 시간표",
            caption="공식 무료 순환버스 운행 시간표",
            source_url=notice.source_url,
            notice_id=notice.id,
        )]
    return []


def _search_first_match_supported(message: str, query: QueryPlan, match: dict) -> bool:
    """가능성 검색이 고유명사 없는 최근접 공지를 답으로 승격시키지 않게 한다."""
    if query.scope != "search_first" or query.context_applied:
        return True
    terms = []
    for raw in re.findall(r"[가-힣A-Za-z0-9]+", normalize_text(message).casefold()):
        term = raw
        for suffix in ("에서는", "에서", "에게", "으로", "라고", "이라고", "관련된", "관련", "은", "는", "이", "가", "을", "를", "에", "도", "요"):
            if term.endswith(suffix) and len(term) - len(suffix) >= 2:
                term = term[:-len(suffix)]
                break
        compact = re.sub(r"\s+", "", term)
        if len(compact) >= 2 and compact not in SCOPE_GENERIC_TERMS and compact not in terms:
            terms.append(compact)
    if not terms:
        return False

    notice = match["notice"]
    unit = match.get("task_unit")
    evidence = " ".join(
        [
            notice.title or "", notice.content or "", notice.attachment_text or "",
            getattr(unit, "title", "") or "", getattr(unit, "summary", "") or "",
        ]
        + [getattr(item, "excerpt", "") or "" for item in getattr(unit, "evidence", [])]
    )
    compact_evidence = re.sub(r"\s+", "", normalize_text(evidence).casefold())
    return any(term in compact_evidence for term in terms)


def _requested_graduation_cohort(message: str) -> tuple[int, int] | None:
    """졸업요건 질문의 단일 입학년도 또는 입학년도 구간을 반환한다."""
    normalized = normalize_text(message)
    range_match = re.search(
        r"(20\d{2})\s*[~∼-]\s*(20\d{2})\s*학년도(?:\s*(?:입학자|입학생))?",
        normalized,
    )
    if range_match:
        return int(range_match.group(1)), int(range_match.group(2))
    admission_match = re.search(r"(20\d{2})\s*(?:년도|학년도)?\s*(?:입학|입학생|학번)", normalized)
    if admission_match:
        year = int(admission_match.group(1))
        return year, year
    return None


def _matching_graduation_range(task_unit, cohort: tuple[int, int] | None) -> tuple[int, int] | None:
    if task_unit is None or cohort is None:
        return None
    requested_start, requested_end = cohort
    evidence_text = " ".join(
        [getattr(task_unit, "content", "") or "", getattr(task_unit, "search_text", "") or ""]
        + [getattr(fact, "value", "") or "" for fact in getattr(task_unit, "facts", [])]
        + [getattr(evidence, "excerpt", "") or "" for evidence in getattr(task_unit, "evidence", [])]
    )
    ranges = [
        (int(start_text), int(end_text))
        for start_text, end_text in re.findall(r"(20\d{2})\s*[~∼-]\s*(20\d{2})\s*학년도", evidence_text)
        if int(start_text) <= requested_start and requested_end <= int(end_text)
    ]
    for year_text, direction in re.findall(r"(20\d{2})\s*학년도\s*(이전|이후)", evidence_text):
        year = int(year_text)
        candidate = (0, year) if direction == "이전" else (year, 9999)
        if candidate[0] <= requested_start and requested_end <= candidate[1]:
            ranges.append(candidate)
    return min(ranges, key=lambda item: item[1] - item[0]) if ranges else None


def _graduation_range_label(year_range: tuple[int, int]) -> str:
    start_year, end_year = year_range
    if start_year == 0:
        return f"{end_year}학년도 이전"
    if end_year == 9999:
        return f"{start_year}학년도 이후"
    return f"{start_year}~{end_year}학년도"


def _compact_procedure_steps(steps: list) -> list:
    """긴 원문 절차를 근거 순서를 유지한 채 모바일용 최대 5단계로 묶는다."""
    visible = [step for step in steps if visible_student_step(step)]
    if len(visible) <= 5:
        return visible
    group_size = (len(visible) + 4) // 5
    compacted = []
    for start in range(0, len(visible), group_size):
        group = visible[start:start + group_size]
        first, last = group[0], group[-1]
        titles = list(dict.fromkeys(normalize_text(step.title) for step in group if normalize_text(step.title)))
        descriptions = list(dict.fromkeys(
            normalize_text(step.description) for step in group if normalize_text(step.description)
        ))
        compacted.append(SimpleNamespace(
            title=" · ".join(titles),
            description=" ".join(descriptions),
            action_type=last.action_type,
            action_url=next((step.action_url for step in reversed(group) if step.action_url), None),
            link_label=getattr(last, "link_label", None),
            source_type=first.source_type,
            source_locator=first.source_locator,
            confidence=min(step.confidence for step in group),
        ))
    return compacted


def _student_relevant_warning(value: str) -> bool:
    normalized = normalize_text(value)
    return not any(term in normalized for term in (
        "담당부서의 승인", "담당부서 승인", "지원센터의 승인", "지원센터 승인",
        "교학팀 결재", "교무팀 결재", "학과장 승인", "전공주임 승인", "내부 승인",
    ))


def _date_label(value: datetime | None) -> str:
    return value.astimezone(KST).strftime("%Y.%m.%d %H:%M") if value else "확인 필요"


def _parse_date(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
    else:
        return None
    return parsed.replace(tzinfo=KST) if parsed.tzinfo is None else parsed.astimezone(KST)


def _next_important_date(meta, now: datetime) -> tuple[dict, datetime, datetime | None] | None:
    """마감된 첫 단계 뒤에 남은 납부·제출 같은 후속 일정을 찾는다."""
    candidates = []
    for item in meta.important_dates or []:
        start = _parse_date(item.get("start"))
        end = _parse_date(item.get("end"))
        boundary = end or start
        if boundary and boundary >= now:
            candidates.append((item, start or boundary, end))
    return min(candidates, key=lambda candidate: candidate[1]) if candidates else None


def _match_status(item: dict, now: datetime) -> str:
    unit = item.get("task_unit")
    if unit is None or (not unit.application_start and not unit.application_end):
        return effective_status(item["notice"], now)
    start = _parse_date(unit.application_start)
    end = _parse_date(unit.application_end)
    if start and now < start:
        return "upcoming"
    if end and now > end:
        return "expired"
    return "active"


def _plain_answer(value: str) -> str:
    """모델이 만든 Markdown 표식을 제거하고 일반 텍스트만 API로 내보낸다."""
    break_token = "KNUASKLINEBREAKTOKEN"
    paragraph_token = "KNUASKPARAGRAPHTOKEN"
    with_paragraphs = re.sub(r"\n\s*\n+", f" {paragraph_token} ", value)
    text = normalize_text(with_paragraphs.replace("\n", f" {break_token} "))
    # 내부 처리 토큰은 어떤 경우에도 사용자 답변에 노출하지 않는다.
    text = re.sub(rf"(?:\s*{break_token}\s*)+", f" {break_token} ", text)
    text = re.sub(rf"(?:\s*{paragraph_token}\s*)+", f" {paragraph_token} ", text)
    text = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", text)
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(rf"(?:^| (?:{break_token}|{paragraph_token}) )#{{1,6}}\s*", " ", text)
    text = text.replace(f" {break_token} - ", "\n• ").replace(f" {break_token} * ", "\n• ")
    text = text.replace(f" {paragraph_token} - ", "\n\n• ").replace(f" {paragraph_token} * ", "\n\n• ")
    return text.replace(f" {paragraph_token} ", "\n\n").replace(f" {break_token} ", "\n").strip()


def _answer_sections(*values: str | None) -> str:
    """개요·상태·행동 안내를 의미 단위 문단으로 조립한다."""
    return "\n\n".join(normalize_text(value or "") for value in values if normalize_text(value or ""))


def _broad_leave_question(message: str) -> bool:
    compact = re.sub(r"\s+", "", normalize_text(message))
    return compact in {"휴학", "휴학안내", "휴학알려줘", "휴학정보"}


def _specific_relative_period(value: str | None) -> bool:
    """'학사일정에 정해진 기간' 같은 자리표시자를 실제 날짜 근거로 취급하지 않는다."""
    normalized = normalize_text(value or "")
    return bool(normalized) and not any(term in normalized for term in (
        "학사일정에 정해진", "학사일정에서 정한", "학사일정 참고", "별도 공지", "추후 공지",
    ))


def _event_camp_eligibility_facts(content: str | None) -> list[AnswerFact]:
    """구조화 facts가 비어도 수집된 공식 본문·PDF의 지원자격을 보존한다."""
    text = normalize_text(content or "")
    if not text or not any(term in text for term in ("지원자격", "참여 대상", "대 상")):
        return []

    facts: list[AnswerFact] = []
    if "강남대" in text and "인근 대학 재학생" in text:
        value = "강남대를 포함한 인근 대학 재학생"
        if re.search(r"학년\s*[,·]?\s*전공\s*무관|학년,\s*전공\s*무관", text):
            value += "(학년·전공 무관)"
        facts.append(AnswerFact(label="지원 대상", value=value))
    if "STEM" in text and re.search(r"2\s*학년\s*이상\s*권장", text):
        facts.append(AnswerFact(
            label="권장 대상",
            value="컴퓨터공학 전공 또는 STEM 관련 학습 경험이 있는 2학년 이상 학생",
        ))
    if "하나 이상의 프로그래밍 언어 사용 경험" in text:
        facts.append(AnswerFact(
            label="프로그래밍 경험",
            value="하나 이상의 프로그래밍 언어 사용 경험이 있는 사람",
        ))
    if "팀 프로젝트로 웹서비스 개발을 경험해보고 싶은 사람" in text:
        facts.append(AnswerFact(
            label="프로젝트 관심",
            value="팀 프로젝트로 웹서비스 개발을 경험해보고 싶은 사람",
        ))
    if "개발자로서의 커리어를 꿈꾸는" in text:
        facts.append(AnswerFact(
            label="진로 관심",
            value="개발자로서의 커리어를 희망하는 사람",
        ))
    return facts[:5]


def _relative_application_period(task_unit) -> tuple[str | None, str | None]:
    """날짜로 정규화할 수 없는 공식 신청 시기 규칙도 근거와 함께 보존한다."""
    if task_unit is None:
        return None, None
    for fact in task_unit.facts:
        if (
            getattr(fact, "fact_type", None) in {"application_window", "application_period", "application_timing"}
            and getattr(fact, "value", None)
        ):
            return normalize_text(fact.value), getattr(fact, "source_locator", None)
    for evidence in task_unit.evidence:
        field_name = (evidence.field_name or "").lower()
        excerpt = normalize_text(evidence.excerpt or "")
        if "applicationperiod" in field_name and excerpt:
            return excerpt, evidence.source_locator
    return None, None


def build_action_guide_response(notice: Notice, task_unit=None) -> ActionGuideResponse | None:
    task_procedure = task_unit.procedure if task_unit is not None else None
    # 업무 단위 검색이 성공했다면 문서 전체에서 만든 대표 절차를 대신
    # 붙이지 않는다. 해당 TaskUnit에 확정된 절차가 있을 때만 보여준다.
    guide = task_procedure if task_unit is not None else notice.action_guide
    meta = notice.metadata_record
    if not guide or not guide.steps or not meta:
        return None
    targets = list(task_unit.target_student_types or []) if task_unit is not None else list(meta.target_student_types or [])
    if task_unit is None:
        targets.extend(f"{grade}학년" for grade in (meta.target_grades or []))
        targets.extend(meta.target_departments or [])
    elif task_unit.target_departments:
        targets.extend(task_unit.target_departments)

    task_facts: dict[str, list[str]] = {}
    if task_unit is not None:
        for fact in task_unit.facts:
            if fact.confidence < 0.55:
                continue
            task_facts.setdefault((fact.fact_type or "other").lower(), []).append(fact.value)

    def fact_values(*types: str) -> list[str]:
        values: list[str] = []
        for fact_type in types:
            values.extend(task_facts.get(fact_type, []))
        return list(dict.fromkeys(value for value in values if value))

    def joined(values: list[str]) -> str | None:
        return "; ".join(values) or None

    # 여러 업무를 담은 문서는 공지 전체 메타데이터를 자식 업무에 섞지
    # 않는다. Codex가 업무 단위에 저장한 facts만 우선 사용한다.
    multi_task_notice = bool(
        task_unit is not None
        and len({unit.task.task_key for unit in notice.task_units}) > 1
    )
    task_documents = fact_values("required_document", "required_documents", "document", "documents")
    task_documents = [
        "별도 구비서류 없음" if value.strip() in {"없음", "해당 없음", "별도 없음"} else value
        for value in task_documents
    ]
    task_eligibility = fact_values("eligibility", "requirement", "exclusion")
    task_fees = fact_values("fee", "cost")
    task_capacity = fact_values("capacity")
    task_selection = fact_values("selection_method")
    task_results = fact_values("result_announcement")
    task_cancellation = fact_values("cancellation_policy")
    task_benefits = fact_values("benefit", "benefits")
    task_credits = fact_values("credits_or_hours", "credit", "hours")
    required_documents = task_documents if multi_task_notice else list(dict.fromkeys([
        *(meta.required_documents or []), *task_documents,
    ]))
    eligibility_notes = task_eligibility if multi_task_notice else list(dict.fromkeys([
        *(meta.eligibility_notes or []), *task_eligibility,
    ]))
    fee_information = (
        "; ".join(task_fees)
        if multi_task_notice and task_fees
        else (None if multi_task_notice else meta.fee_information)
    )
    task_methods = fact_values("application_method", "method")
    task_locations = fact_values("application_location", "location", "submission_location")
    task_dates = []
    if task_unit is not None:
        for fact in task_unit.facts:
            if fact.confidence < 0.55 or fact.fact_type not in {
                "date", "document_submission_period", "result_announcement_period",
            }:
                continue
            task_dates.append({
                "label": fact.label,
                "start": fact.valid_from.isoformat() if fact.valid_from else None,
                "end": fact.valid_to.isoformat() if fact.valid_to else None,
                "description": fact.value,
                "sourceLocator": fact.source_locator,
            })
        if not task_unit.application_start and not task_unit.application_end:
            relative_period, relative_locator = _relative_application_period(task_unit)
            if relative_period:
                task_dates.insert(0, {
                    "label": "신청 시기",
                    "start": None,
                    "end": None,
                    "description": relative_period,
                    "sourceLocator": relative_locator,
                })
    visible_steps = _compact_procedure_steps(list(guide.steps))
    if not visible_steps:
        return None
    response_steps = []
    seen_step_urls: set[str] = set()
    for order, step in enumerate(visible_steps, start=1):
        action_url = step.action_url
        normalized_url = action_url.rstrip("/") if action_url else None
        if normalized_url and normalized_url in seen_step_urls:
            action_url = None
        elif normalized_url:
            seen_step_urls.add(normalized_url)
        response_steps.append(StructuredActionStep(
            order=order,
            title=step.title,
            description=step.description,
            action_type=step.action_type,
            action_url=action_url,
            link_label=_step_link_label(action_url, getattr(step, "link_label", None)) if action_url else None,
            source_type=step.source_type,
            source_locator=step.source_locator,
            confidence=step.confidence,
        ))
    return ActionGuideResponse(
        task_name=(
            TASK_BY_KEY[task_unit.task.task_key].name
            if task_procedure is not None and task_unit.task.task_key in TASK_BY_KEY
            else (task_unit.title if task_procedure is not None else guide.task_name)
        ),
        summary=guide.summary,
        targets=list(dict.fromkeys(targets)),
        period=Period(
            start=task_unit.application_start if task_procedure is not None else meta.application_start,
            end=task_unit.application_end if task_procedure is not None else meta.application_end,
        ),
        prerequisites=guide.prerequisites or [],
        required_documents=required_documents,
        eligibility_notes=eligibility_notes,
        application_method=(task_methods[0] if task_methods else (None if multi_task_notice else meta.application_method)),
        application_location=(task_locations[0] if task_locations else (None if multi_task_notice else meta.application_location)),
        fee_information=fee_information,
        capacity=(joined(task_capacity) if task_unit is not None else meta.capacity),
        selection_method=(joined(task_selection) if task_unit is not None else meta.selection_method),
        result_announcement=(joined(task_results) if task_unit is not None else meta.result_announcement),
        cancellation_policy=(joined(task_cancellation) if task_unit is not None else meta.cancellation_policy),
        benefits=(task_benefits if task_unit is not None else (meta.benefits or [])),
        credits_or_hours=((task_credits[0] if task_credits else None) if task_unit is not None else meta.credits_or_hours),
        important_dates=task_dates if task_unit is not None else (meta.important_dates or []),
        additional_facts=[] if task_unit is not None else (meta.additional_facts or []),
        steps=response_steps,
        warnings=[warning for warning in (guide.warnings or []) if _student_relevant_warning(warning)],
        application_url=guide.application_url,
        source_url=notice.source_url,
        department=DepartmentInfo(
            name=meta.department_name,
            contact_person=meta.contact_person,
            contact_role=meta.contact_role,
            phone=meta.department_phone,
            email=meta.department_email,
            office_location=meta.department_office_location,
            office_hours=meta.department_office_hours,
        ),
        confidence=guide.confidence,
        needs_review=guide.needs_review,
    )


class ChatService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.ai = AIService()
        self.last_search_trace: list[dict] = []
        self._context_notice_ids: list[int] = []
        self.last_observability: dict = {}
        self._on_demand_resolver: OnDemandEvidenceResolver | None = None
        self._recovery_outcome = RecoveryOutcome()
        self._request_deadline: float | None = None

    def _record_metric(self, request_id: str, **values) -> None:
        metric = QueryMetric(request_id=request_id, **values)
        self.db.add(metric)
        self.last_observability = values
        self.db.commit()

    @staticmethod
    def _cacheable(response: ChatResponse) -> bool:
        if response.status == "clarification_required":
            return False
        if response.answer_mode == "generated":
            return False
        if (
            response.answer_mode == "search_results_only"
            and response.has_data
            and not response.answer_facts
            and response.action_guide is None
            and not response.task_results
        ):
            # 검색은 성공했지만 이미 추출된 구조화 근거를 답변에 쓰지 못한
            # 저품질 결과를 '검증 완료'로 재사용하지 않는다. 같은 질문을
            # 다시 받으면 최신 답변 조립 로직으로 재평가할 수 있어야 한다.
            return False
        if response.has_data:
            return bool(response.sources) and all(is_allowed_school_url(source.url) for source in response.sources)
        return response.status in {"no_result", "clarification_required", "insufficient_evidence"}

    def answer(self, message: str, session_id: str | None = None, selected_category: str | None = None) -> ChatResponse:
        """정확 캐시 → Gemini QueryPlan → canonical 캐시 → 근거 검색 순서."""
        started = time.perf_counter()
        self._request_deadline = started + min(settings.on_demand_timeout_seconds, 30.0)
        request_id = str(uuid.uuid4())
        session_id = session_id or str(uuid.uuid4())
        self.ai.call_stats = []
        self._on_demand_resolver = None
        self._recovery_outcome = RecoveryOutcome()
        if is_dangerous_action_request(message):
            response = ChatResponse(
                answer_id=str(uuid.uuid4()),
                answer=(
                    "사람이나 시설에 해를 끼치는 행동의 방법·장소·요령은 안내할 수 없습니다. "
                    "실제 위험이 있거나 누군가 실행할 가능성이 있다면 즉시 112 또는 119에 신고해 주세요."
                ),
                status="safety_refusal",
                answer_mode="deterministic",
                has_data=False,
                session_id=session_id,
                verified_at=datetime.now(KST),
                search_scope=SearchScope(
                    sources=[], notice_count=0,
                    description="안전 정책에 따라 외부 검색과 AI 호출을 수행하지 않음",
                ),
            )
            self._record_metric(
                request_id, exact_cache_hit=False, canonical_cache_hit=False,
                gemini_called=False, codex_called=False, local_search_used=False,
                live_search_used=False, search_attempts=0, checked_url_count=0,
                stage_timings_ms={"total": round((time.perf_counter() - started) * 1000, 1)},
                json_retried=False, fallback_reason="dangerous_action_blocked",
                final_source_urls=[], supported=False,
            )
            return response
        if is_harm_reporting_request(message):
            response = ChatResponse(
                answer_id=str(uuid.uuid4()),
                answer=(
                    "이 서비스는 강남대학교 공식 학사·행정·학생생활 안내만 제공하므로 "
                    "범죄 신고·피해 상담은 처리하지 않습니다. 관련 전문기관을 이용해 주세요."
                ),
                status="out_of_scope",
                answer_mode="deterministic",
                has_data=False,
                session_id=session_id,
                verified_at=datetime.now(KST),
                search_scope=SearchScope(
                    sources=[], notice_count=0,
                    description="서비스 범위 밖의 신고·피해 상담이므로 공식 자료 검색을 수행하지 않음",
                ),
            )
            self._record_metric(
                request_id, exact_cache_hit=False, canonical_cache_hit=False,
                gemini_called=False, codex_called=False, local_search_used=False,
                live_search_used=False, search_attempts=0, checked_url_count=0,
                stage_timings_ms={"total": round((time.perf_counter() - started) * 1000, 1)},
                json_retried=False, fallback_reason="harm_reporting_out_of_scope",
                final_source_urls=[], supported=False,
            )
            return response
        if _broad_leave_question(message):
            response = ChatResponse(
                answer_id=str(uuid.uuid4()),
                answer="휴학에 관해 어떤 내용을 찾으시나요?",
                status="clarification_required",
                answer_mode="deterministic",
                clarification_options=[
                    "휴학 신청 방법",
                    "휴학 신청 기간",
                    "휴학 종류와 조건",
                    "복학 방법",
                    "휴학 담당 부서",
                ],
                has_data=False,
                session_id=session_id,
                verified_at=datetime.now(KST),
                search_scope=SearchScope(sources=[], notice_count=0, description="질문 의도 확인"),
            )
            self._record_metric(
                request_id, exact_cache_hit=False, canonical_cache_hit=False,
                gemini_called=False, codex_called=False, local_search_used=False,
                live_search_used=False, search_attempts=0, checked_url_count=0,
                stage_timings_ms={"total": round((time.perf_counter() - started) * 1000, 1)},
                json_retried=False, fallback_reason="broad_leave_clarification",
                final_source_urls=[], supported=False,
            )
            return response
        cache = AnswerCacheStore(self.db)
        exact = cache.get_exact(message) if settings.on_demand_search_enabled else None
        if exact:
            response = cache.response(exact, session_id=session_id)
            self._record_metric(
                request_id, exact_cache_hit=True, canonical_cache_hit=False,
                gemini_called=False, codex_called=False, local_search_used=False,
                live_search_used=False, search_attempts=0, checked_url_count=0,
                stage_timings_ms={"total": round((time.perf_counter() - started) * 1000, 1)},
                json_retried=False, final_source_urls=[item.url for item in response.sources],
                supported=response.has_data,
            )
            return response

        query_started = time.perf_counter()
        query = self.ai.analyze_query(message)
        query_ms = round((time.perf_counter() - query_started) * 1000, 1)
        if query.scope == "out_of_scope":
            response = ChatResponse(
                answer_id=str(uuid.uuid4()),
                answer=(
                    "저는 강남대학교가 공식적으로 안내하는 학사·행정·학생생활·학생 참여 프로그램만 "
                    "도와드릴 수 있어요. 강남대 학생에게 안내된 내용과 관련해 질문해 주세요."
                ),
                status="out_of_scope",
                answer_mode="deterministic",
                has_data=False,
                session_id=session_id,
                query=query,
                verified_at=datetime.now(KST),
                search_scope=SearchScope(
                    sources=[], notice_count=0,
                    description="질문 범위 판정 결과 공식 자료 검색을 수행하지 않음",
                ),
            )
            self._record_metric(
                request_id, exact_cache_hit=False, canonical_cache_hit=False,
                gemini_called=any(item.get("provider") == "gemini" for item in self.ai.call_stats),
                codex_called=False, local_search_used=False, live_search_used=False,
                search_attempts=0, checked_url_count=0,
                stage_timings_ms={"queryAnalysis": query_ms, "total": round((time.perf_counter() - started) * 1000, 1)},
                json_retried=False, fallback_reason="out_of_scope",
                final_source_urls=[], supported=False,
            )
            return response
        if selected_category:
            query.category = selected_category
        elif query.task_key in TASK_BY_KEY:
            # canonical 업무키가 정해졌다면 모델이 잘못 고른 카테고리보다
            # 서버의 업무 분류를 우선해 검색·캐시 조건이 갈라지지 않게 한다.
            query.category = TASK_BY_KEY[query.task_key].category
        canonical = (
            cache.get_canonical(query, message)
            if settings.on_demand_search_enabled and not query.needs_clarification
            else None
        )
        if canonical:
            response = cache.response(canonical, session_id=session_id)
            self._record_metric(
                request_id, exact_cache_hit=False, canonical_cache_hit=True,
                gemini_called=any(item.get("provider") == "gemini" for item in self.ai.call_stats),
                codex_called=False, local_search_used=False, live_search_used=False,
                search_attempts=0, checked_url_count=0,
                stage_timings_ms={"queryAnalysis": query_ms, "total": round((time.perf_counter() - started) * 1000, 1)},
                json_retried=sum(item.get("operation") == "query_analysis" for item in self.ai.call_stats) > 1,
                final_source_urls=[item.url for item in response.sources], supported=response.has_data,
            )
            return response

        response = self._answer_with_plan(message, session_id, selected_category, query)
        resolver = self._on_demand_resolver
        if (
            settings.on_demand_search_enabled
            and self._cacheable(response)
            and self._recovery_outcome.status not in {"verified_absent", "failed", "low_confidence"}
        ):
            source_hashes = {}
            for source in response.sources:
                notice = self.db.get(Notice, source.notice_id)
                if notice:
                    source_hashes[source.url] = notice.content_hash
            cache.put(
                message=message, plan=query, response=response, supported=response.has_data,
                missing_facts=(
                    resolver.missing_facts if resolver and resolver.missing_facts
                    else (list(query.required_facts) if not response.has_data else [])
                ),
                source_hashes=source_hashes,
                gemini_model=self.ai.chat_model_name if any(
                    item.get("provider") == "gemini" for item in self.ai.call_stats
                ) else None,
                codex_model=settings.on_demand_codex_model if resolver and resolver.codex.called else None,
            )
            self.db.commit()
        search_attempts = resolver.gateway.search_attempts if resolver else 0
        checked_urls = resolver.gateway.checked_urls if resolver else 0
        fallback_reason = resolver.verification_failure if resolver else None
        recovery = self._recovery_outcome
        stage_timings = {"queryAnalysis": query_ms, **recovery.timings_ms}
        if recovery.triggered:
            stage_timings["evidenceRecovery"] = recovery.duration_ms
        stage_timings["total"] = round((time.perf_counter() - started) * 1000, 1)
        self._record_metric(
            request_id, exact_cache_hit=False, canonical_cache_hit=False,
            gemini_called=any(item.get("provider") == "gemini" for item in self.ai.call_stats),
            codex_called=bool(resolver and resolver.codex.called), local_search_used=True,
            live_search_used=bool(resolver), search_attempts=search_attempts,
            checked_url_count=checked_urls,
            gemini_input_tokens=getattr(self.ai, "last_gemini_input_tokens", None),
            gemini_output_tokens=getattr(self.ai, "last_gemini_output_tokens", None),
            codex_input_tokens=resolver.codex.input_tokens if resolver else None,
            codex_output_tokens=resolver.codex.output_tokens if resolver else None,
            stage_timings_ms=stage_timings,
            json_retried=sum(item.get("operation") == "query_analysis" for item in self.ai.call_stats) > 1,
            fallback_reason=fallback_reason,
            final_source_urls=[item.url for item in response.sources], supported=response.has_data,
            recovery_triggered=recovery.triggered,
            recovery_reason=recovery.reason,
            requested_missing_fields=recovery.requested_missing_fields,
            recovery_result=recovery.status if recovery.triggered else None,
            checked_attachment_count=recovery.checked_attachment_count,
            checked_page_count=recovery.checked_page_count,
            recovery_duration_ms=recovery.duration_ms if recovery.triggered else None,
            recovery_cache_hit=recovery.cache_hit,
            persisted_fact_count=recovery.persisted_fact_count,
            persisted_step_count=recovery.persisted_step_count,
        )
        return response

    @staticmethod
    def _is_follow_up(message: str) -> bool:
        text = normalize_text(message)
        return bool(
            text.startswith(("그럼", "그러면", "거기", "그건", "그거"))
            or (
                len(text) <= 28
                and any(token in text for token in (
                    "어디서 확인", "어디에서 확인", "링크", "원문", "전화번호도",
                    "담당자도", "연락처도", "어떻게 확인", "언제까지", "신청 기간",
                    "접수 기간", "최대 휴학", "가능 기간", "방법", "절차", "필요 서류",
                    "준비물", "대상", "자격", "납부", "반환", "교육훈련", "훈련 연기",
                ))
            )
        )

    def _apply_session_context(self, message: str, session_id: str, query: QueryPlan) -> QueryPlan:
        if query.requested_tasks or not self._is_follow_up(message):
            return query
        context = self.db.get(ChatSessionContext, session_id)
        now = datetime.now(timezone.utc)
        if not context or context.expires_at.replace(tzinfo=context.expires_at.tzinfo or timezone.utc) <= now:
            if context:
                self.db.delete(context)
                self.db.flush()
            query.follow_up = True
            return query

        task_keys = [key for key in context.task_keys or [] if key in TASK_BY_KEY]
        if not task_keys:
            query.follow_up = True
            return query
        query.requested_tasks = task_keys
        query.task_key = task_keys[0]
        primary_task = TASK_BY_KEY[task_keys[0]]
        query.category = primary_task.category
        query.sub_category = primary_task.name
        query.keywords = list(dict.fromkeys([
            primary_task.name, *primary_task.aliases, *query.keywords,
        ]))[:18]
        query.academic_year = query.academic_year or context.academic_year
        query.admission_year = query.admission_year or context.admission_year
        query.semester = query.semester or context.semester
        query.follow_up = True
        query.context_applied = True
        self._context_notice_ids = list(context.selected_notice_ids or [])
        query.sub_queries = [QuerySubQuery(
            task_key=key,
            task_name=TASK_BY_KEY[key].name,
            query_text=normalize_text(
                f"{context.academic_year or ''} {context.semester or ''}학기 "
                f"{context.admission_year or ''}년도 입학생 {TASK_BY_KEY[key].name} {message}"
            ),
        ) for key in task_keys]
        return query

    def _save_session_context(
        self, session_id: str, query: QueryPlan, matches: list[dict], department: DepartmentInfo | None,
    ) -> None:
        task_keys = query.requested_tasks or ([query.task_key] if query.task_key else [])
        if not task_keys:
            return
        context = self.db.get(ChatSessionContext, session_id) or ChatSessionContext(session_id=session_id)
        context.task_keys = task_keys
        context.academic_year = query.academic_year
        context.admission_year = query.admission_year
        context.semester = query.semester
        if matches:
            context.selected_notice_ids = list(dict.fromkeys(item["notice"].id for item in matches))[:10]
        context.department_name = department.name if department else None
        context.expires_at = datetime.now(timezone.utc) + timedelta(hours=2)
        self.db.add(context)
        self.db.flush()

    def end_session(self, session_id: str) -> None:
        context = self.db.get(ChatSessionContext, session_id)
        if context:
            self.db.delete(context)
            self.db.commit()

    def _search(self, message: str, query: QueryPlan) -> tuple[list[dict], dict[str, dict]]:
        """업무별로 검색한 뒤 TaskUnit 선택이 끝난 후에만 화면 후보를 합친다."""
        requested = query.requested_tasks or ([query.task_key] if query.task_key else [])
        task_matches: dict[str, dict] = {}
        merged: list[dict] = []
        self.last_search_trace = []
        if len(requested) <= 1:
            search_message = (
                query.sub_queries[0].query_text
                if query.context_applied and query.sub_queries
                else message
            )
            searcher = HybridSearch(self.db, self.ai)
            retrieved = searcher.search(search_message, query, 8)
            if query.time_scope == "current":
                active = [item for item in retrieved if _match_status(item, datetime.now(KST)) == "active"]
                if active:
                    retrieved = active
            matches = list(retrieved)
            self.ai.last_rerank_trace = []
            must_rerank = bool(
                query.task_key or query.academic_year or query.admission_year
                or query.semester or query.student_status
            )
            if len(matches) >= 2 and (
                must_rerank or matches[0]["score"] - matches[1]["score"] < 0.18
            ):
                matches = self.ai.rerank_candidates(search_message, query, matches)
            # 명시된 업무키는 문서 전체 후보보다 TaskUnit 경계를 우선한다.
            # 외부 재정렬기가 정확 업무 단위를 누락해도 규칙 검색 결과에서
            # 복구하며, 후속 질문은 직전 선택 공지를 먼저 유지한다.
            if requested:
                exact_units = [item for item in retrieved if (
                    item.get("task_unit") is not None
                    and item.get("canonical_task_key", item["task_unit"].task.task_key) == requested[0]
                )]
                if self._context_notice_ids:
                    contextual = [
                        item for item in exact_units
                        if item["notice"].id in self._context_notice_ids
                    ]
                    if contextual:
                        exact_units = contextual + [item for item in exact_units if item not in contextual]
                if exact_units:
                    chosen = exact_units[0]
                    matches = [chosen, *[
                        item for item in matches
                        if item["candidate_id"] != chosen["candidate_id"]
                    ]]
            self.last_search_trace.extend(searcher.last_trace)
            self.last_search_trace.extend(getattr(self.ai, "last_rerank_trace", []))
            if matches and requested:
                task_matches[requested[0]] = matches[0]
            return matches, task_matches

        sub_by_key = {item.task_key: item for item in query.sub_queries}
        for task_key in requested:
            task = TASK_BY_KEY.get(task_key)
            if not task:
                continue
            sub_query = sub_by_key.get(task_key)
            query_text = sub_query.query_text if sub_query else normalize_text(f"{task.name} {message}")
            task_filter = query.model_copy(deep=True)
            task_filter.task_key = task_key
            task_filter.category = task.category
            task_filter.sub_category = task.name
            task_filter.requested_tasks = [task_key]
            task_filter.sub_queries = []
            task_filter.keywords = list(dict.fromkeys([
                task.name, *task.aliases, *query.keywords,
            ]))[:18]
            searcher = HybridSearch(self.db, self.ai)
            retrieved = searcher.search(query_text, task_filter, 8)
            candidates = list(retrieved)
            self.ai.last_rerank_trace = []
            if len(candidates) >= 2:
                candidates = self.ai.rerank_candidates(query_text, task_filter, candidates)
            self.last_search_trace.extend({**entry, "requestedTask": task_key} for entry in searcher.last_trace)
            self.last_search_trace.extend(
                {**entry, "requestedTask": task_key}
                for entry in getattr(self.ai, "last_rerank_trace", [])
            )
            exact = next((item for item in retrieved if (
                item.get("task_unit") is not None
                and item.get("canonical_task_key", item["task_unit"].task.task_key) == task_key
            )), None)
            selected = exact or (candidates[0] if candidates else None)
            if selected:
                task_matches[task_key] = selected
                merged.append(selected)
        return merged, task_matches

    def _approved_faq(self, message: str) -> FAQ | None:
        normalized = normalize_text(message).replace(" ", "")
        rows = self.db.scalars(select(FAQ).where(FAQ.is_active.is_(True))).all()
        for faq in rows:
            question = normalize_text(faq.question).replace(" ", "")
            if normalized == question or (len(question) >= 5 and question in normalized):
                return faq
        return None

    @staticmethod
    def _wants_date(message: str) -> bool:
        return any(word in normalize_text(message) for word in (
            "언제", "기간", "일정", "마감", "일자", "날짜", "며칠", "몇 일",
        ))

    @staticmethod
    def _asks_application_period(message: str) -> bool:
        normalized = normalize_text(message)
        return bool(
            ChatService._wants_date(message)
            and any(term in normalized for term in (
                "신청", "접수", "지원 기간", "모집 기간", "납부 기간", "제출 기간",
            ))
        )

    @staticmethod
    def _asks_leave_duration(message: str) -> bool:
        normalized = normalize_text(message)
        return bool(
            "휴학" in normalized and "기간" in normalized
            and any(term in normalized for term in (
                "최대", "가능", "몇 학기", "몇학기", "연속", "통산", "몇 년", "몇년",
            ))
        )

    @staticmethod
    def _wants_fact_overview(message: str) -> bool:
        normalized = normalize_text(message)
        compact = re.sub(r"\s+", "", normalized)
        return (
            any(word in normalized for word in (
                "알려줘", "알려 주세요", "안내", "정보", "어떤 프로그램", "뭐야", "무엇",
            ))
            or any(word in compact for word in (
                "에대해", "에관해", "설명해", "소개해", "요약해", "무엇인지",
            ))
        )

    @staticmethod
    def _fact_focuses(message: str) -> set[str]:
        normalized = normalize_text(message)
        focus_terms = {
            "eligibility": ("대상", "자격", "누가", "누구", "지원 가능", "참여 가능"),
            "capacity": ("인원", "정원", "몇 명", "모집 규모"),
            "fee": ("참가비", "비용", "금액", "얼마", "수수료"),
            "venue": ("장소", "위치", "어디서", "어디에서"),
            "activity": ("주요 활동", "활동 내용", "프로그램 내용", "무엇을", "뭘 해"),
            "benefit": ("혜택", "지원 내용", "얻을 수"),
            "requirement": ("요건", "조건", "경험", "준비"),
            "documents": ("준비서류", "준비 서류", "구비서류", "필요 서류", "제출서류", "제출 서류"),
        }
        return {
            focus
            for focus, terms in focus_terms.items()
            if any(term in normalized for term in terms)
        }

    @staticmethod
    def _fact_focus(fact) -> str:
        fact_type = normalize_text(getattr(fact, "fact_type", "")).lower()
        label = normalize_text(getattr(fact, "label", ""))
        haystack = f"{fact_type} {label}".lower()
        if any(term in haystack for term in ("eligibility", "target", "참여 대상", "지원 대상")):
            return "eligibility"
        if any(term in haystack for term in ("capacity", "인원", "정원")):
            return "capacity"
        if any(term in haystack for term in ("fee", "cost", "참가비", "비용", "수수료")):
            return "fee"
        if any(term in haystack for term in ("venue", "location", "장소", "위치")):
            return "venue"
        if any(term in haystack for term in ("activity", "program", "주요 활동", "활동 내용")):
            return "activity"
        if any(term in haystack for term in ("benefit", "혜택", "지원 내용")):
            return "benefit"
        if any(term in haystack for term in (
            "recommendation", "requirement", "experience", "권장", "조건", "경험", "요건",
        )):
            return "requirement"
        if any(term in haystack for term in ("required_documents", "document", "제출 서류", "구비서류", "준비 서류")):
            return "documents"
        return "other"

    @staticmethod
    def _answer_presentation(message: str, meta, action_guide_visible: bool, task_unit=None) -> tuple[list[AnswerFact], list[str]]:
        if action_guide_visible:
            return [], []
        if ChatService._wants_contact(message):
            return [], []

        facts: list[AnswerFact] = []
        notes: list[str] = []
        normalized_message = normalize_text(message)
        task_key = task_unit.task.task_key if task_unit is not None else None
        fact_focuses = ChatService._fact_focuses(message)
        wants_overview = not fact_focuses and (
            ChatService._wants_fact_overview(message)
            or (
                task_key == "event.camp"
                and not ChatService._wants_date(message)
                and not any(term in normalized_message for term in (
                    "신청 방법", "신청 절차", "접수 방법", "지원 방법",
                    "전화", "연락처", "담당자", "문의처", "원문", "링크",
                ))
            )
        )
        asks_application_period = ChatService._asks_application_period(message)
        asks_leave_duration = ChatService._asks_leave_duration(message)
        application_start = task_unit.application_start if task_unit is not None else meta.application_start
        application_end = task_unit.application_end if task_unit is not None else meta.application_end
        event_start = getattr(task_unit, "event_start", None) if task_unit is not None else getattr(meta, "event_start", None)
        event_end = getattr(task_unit, "event_end", None) if task_unit is not None else getattr(meta, "event_end", None)

        def append_period(label: str, start, end) -> None:
            if start and end:
                value = f"{_date_label(start)} ~ {_date_label(end)}"
            elif end:
                value = f"{_date_label(end)}까지"
            else:
                value = f"{_date_label(start)}부터"
            facts.append(AnswerFact(label=label, value=value))

        if ChatService._wants_date(message) and not asks_leave_duration:
            if not asks_application_period and (event_start or event_end):
                append_period("행사 기간", event_start, event_end)
            elif application_start or application_end:
                if application_start and application_end:
                    value = f"{_date_label(application_start)} ~ {_date_label(application_end)}"
                    label = "본 신청 기간"
                elif application_end:
                    value = f"{_date_label(application_end)}까지"
                    label = f"{task_unit.title} 기한" if task_unit is not None else "신청 기한"
                else:
                    value = f"{_date_label(application_start)}부터"
                    label = f"{task_unit.title} 시작" if task_unit is not None else "신청 시작"
                facts.append(AnswerFact(label=label, value=value))
            elif task_unit is not None:
                relative_period, _ = _relative_application_period(task_unit)
                if _specific_relative_period(relative_period):
                    facts.append(AnswerFact(label="신청 시기", value=relative_period))

            wanted_labels = ("예비수강신청", "장애학생 선 수강신청", "수강신청 변경기간")
            for item in (meta.important_dates or []) if task_unit is None else []:
                label = str(item.get("label") or "")
                if not any(token in label for token in wanted_labels):
                    continue
                start_raw, end_raw = item.get("start"), item.get("end")
                start = datetime.fromisoformat(start_raw) if start_raw else None
                end = datetime.fromisoformat(end_raw) if end_raw else None
                if start and end:
                    value = f"{_date_label(start)} ~ {_date_label(end)}"
                elif start:
                    value = f"{_date_label(start)}부터"
                elif end:
                    value = f"{_date_label(end)}까지"
                else:
                    value = str(item.get("description") or "원문에서 확인")
                if not any(fact.label == label for fact in facts):
                    facts.append(AnswerFact(label=label, value=value))
                if len(facts) >= 4:
                    break

            if task_unit is not None:
                dated_facts = [fact for fact in task_unit.facts if fact.fact_type == "date"]
                focus_terms = [term for term in ("예비", "변경", "정정", "장애", "잔여") if term in message]
                if focus_terms:
                    dated_facts.sort(key=lambda fact: not any(term in fact.label for term in focus_terms))
                for fact in dated_facts:
                    if fact.label == "본 신청 기간" or any(existing.label == fact.label for existing in facts):
                        continue
                    facts.append(AnswerFact(label=fact.label, value=fact.value))
                    if len(facts) >= 6:
                        break
        elif wants_overview:
            if application_start or application_end:
                append_period("본 신청 기간", application_start, application_end)
            elif event_start or event_end:
                append_period("행사 기간", event_start, event_end)

        if task_unit is None and meta.application_method:
            facts.append(AnswerFact(label="신청 방법", value=meta.application_method))
        is_graduation_requirements = task_key == "graduation.requirements"
        if (
            not is_graduation_requirements
            and meta.application_location
            and meta.application_location not in (meta.application_method or "")
            and not ChatService._wants_date(message)
        ):
            facts.append(AnswerFact(label="신청 장소", value=meta.application_location))

        if task_unit is None and not ChatService._wants_date(message):
            notes.extend((meta.eligibility_notes or [])[:3])
        if (
            task_unit is not None and not action_guide_visible
            and task_unit.task.task_key == "graduation.requirements"
        ):
            cohort = _requested_graduation_cohort(message)
            matched_range = _matching_graduation_range(task_unit, cohort)
            if matched_range:
                range_label = _graduation_range_label(matched_range)
                facts.append(AnswerFact(
                    label=f"{range_label} 입학자 졸업이수 기준",
                    value="전용 졸업이수학점표(PDF)에서 학부(과)별 교양·전공·다전공 학점을 확인",
                ))
            for item in task_unit.facts:
                fact_text = normalize_text(f"{item.label} {item.value}")
                if cohort and "최소 졸업학점" in item.label:
                    credit_match = re.search(r"\d+\s*학점\s*이상", normalize_text(item.value))
                    if credit_match and not any(existing.label == item.label for existing in facts):
                        facts.append(AnswerFact(label=item.label, value=credit_match.group(0)))
                    continue
                if cohort and re.search(r"20\d{2}|학년도\s*(?:이전|이후)", fact_text):
                    # 요청 코호트 전용 표는 위에서 한 번만 표시하고, 다른
                    # 입학연도의 행이나 넓은 '이후' 행을 섞지 않는다.
                    continue
                if not any(term in fact_text for term in (
                    "최소 졸업학점", "최소 등록학기", "교육과정 이수", "졸업종합평가",
                    "채플 통과 횟수", "졸업이수학점", "졸업학점", "전공 이수", "제2전공",
                )):
                    continue
                if not any(existing.label == item.label for existing in facts):
                    facts.append(AnswerFact(label=item.label, value=item.value))
                if len(facts) >= 4:
                    break

        explicit_application_request = any(term in normalized_message for term in (
            "신청 방법", "신청 절차", "신청 기간", "접수 방법", "접수 기간", "지원 방법", "지원 기간",
        ))
        if task_unit is not None and asks_leave_duration:
            for item in task_unit.facts:
                fact_text = normalize_text(f"{item.fact_type} {item.label} {item.value}")
                if not any(term in fact_text for term in (
                    "maximum_leave_duration", "leave_duration_per_application", "leave_limit",
                    "최대 휴학", "신청 가능 기간", "1회 신청", "휴학 가능 기간",
                )):
                    continue
                if not any(existing.label == item.label for existing in facts):
                    facts.append(AnswerFact(label=item.label, value=item.value))
                if len(facts) >= 3:
                    break
        elif task_unit is not None and not is_graduation_requirements and not explicit_application_request:
            candidate_facts = [
                item for item in task_unit.facts
                if getattr(item, "student_actionable", False)
                and normalize_text(getattr(item, "label", ""))
                and normalize_text(getattr(item, "value", ""))
            ]
            if fact_focuses:
                candidate_facts = [
                    item for item in candidate_facts
                    if ChatService._fact_focus(item) in fact_focuses
                ]
            elif not wants_overview:
                candidate_facts = []

            priority = {
                "eligibility": 10, "capacity": 20, "fee": 30, "venue": 40,
                "activity": 50, "benefit": 60, "requirement": 70, "documents": 75, "other": 90,
            }
            candidate_facts.sort(key=lambda item: (
                priority[ChatService._fact_focus(item)],
                -float(getattr(item, "confidence", 0.0) or 0.0),
                normalize_text(item.label),
            ))
            for item in candidate_facts:
                if any(existing.label == item.label or existing.value == item.value for existing in facts):
                    continue
                facts.append(AnswerFact(label=item.label, value=item.value))
                if len(facts) >= 6:
                    break

        if task_key == "event.camp" and "eligibility" in fact_focuses and not facts:
            facts.extend(_event_camp_eligibility_facts(getattr(task_unit, "content", "")))

        if (
            task_key == "event.camp"
            and wants_overview
            and not (application_start or application_end)
            and not getattr(task_unit, "procedure", None)
        ):
            notes.append("신청 기간과 신청 절차는 공식 공지 본문에서 확인되지 않습니다.")
        return facts[:6], notes[:4]

    @staticmethod
    def _attach_fact_provenance(facts: list[AnswerFact], match: dict) -> None:
        notice = match["notice"]
        unit = match.get("task_unit")
        for fact in facts:
            fact.source_notice_id = notice.id
            fact.task_unit_id = unit.id if unit is not None else None
            if unit is None:
                fact.source_locator = "공지 본문"
                continue
            matching_fact = next((item for item in unit.facts if item.label == fact.label), None)
            if matching_fact and matching_fact.source_locator:
                fact.source_locator = matching_fact.source_locator
                continue
            evidence_names = (
                ("applicationPeriod", "date")
                if fact.label in {"본 신청 기간", "신청 시기"}
                else ("eventPeriod", "date")
                if fact.label == "행사 기간"
                else ("date", fact.label)
            )
            evidence = next((item for item in unit.evidence if any(
                name.lower() in (item.field_name or "").lower()
                for name in evidence_names
            )), None)
            fact.source_locator = (
                evidence.source_locator if evidence and evidence.source_locator
                else unit.section_title or "공식 원문 구간"
            )

    @staticmethod
    def _deterministic_answer(message: str, match: dict, department: DepartmentInfo) -> str | None:
        notice = match["notice"]
        meta = match["metadata"]
        task_unit = match.get("task_unit")
        application_start = task_unit.application_start if task_unit is not None else meta.application_start
        application_end = task_unit.application_end if task_unit is not None else meta.application_end
        relative_period, _ = _relative_application_period(task_unit)
        task_key = task_unit.task.task_key if task_unit is not None else None
        if task_key == "leave.general" and ChatService._asks_leave_duration(message):
            return "일반휴학의 1회 신청 단위와 재학 중 최대 가능 기간을 공식 안내 기준으로 정리했습니다."
        if (
            task_key == "event.camp"
            and ChatService._wants_fact_overview(message)
            and not ChatService._wants_date(message)
            and not ChatService._wants_action_guide(message)
        ):
            procedure = getattr(task_unit, "procedure", None) if task_unit is not None else None
            summary = normalize_text(
                getattr(procedure, "summary", None)
                or getattr(notice.action_guide, "summary", None)
                or getattr(task_unit, "summary", None)
                or ""
            )[:600]
            if summary:
                if summary[-1] not in ".!?":
                    summary += "."
                end = application_end
                if end:
                    comparable_end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
                    if comparable_end < datetime.now(timezone.utc):
                        return summary
                return summary
        if (
            task_key == "event.camp"
            and ChatService._wants_date(message)
            and not ChatService._asks_application_period(message)
        ):
            event_start = getattr(task_unit, "event_start", None) or meta.event_start
            event_end = getattr(task_unit, "event_end", None) or meta.event_end
            if event_start and event_end:
                period = f"{_date_label(event_start)}부터 {_date_label(event_end)}까지"
            elif event_end:
                period = f"{_date_label(event_end)}까지"
            elif event_start:
                period = f"{_date_label(event_start)}부터"
            else:
                period = None
            if period:
                return f"‘{task_unit.title}’ 행사 기간은 {period}입니다."
        if task_key == "event.camp" and "현재" in message and "신청" in message:
            if application_start and application_end:
                period = f"{_date_label(application_start)}부터 {_date_label(application_end)}까지"
            elif application_end:
                period = f"{_date_label(application_end)}까지"
            elif application_start:
                period = f"{_date_label(application_start)}부터"
            else:
                period = None
            title = re.sub(r"^\[행사안내]\s*", "", notice.title)
            answer = f"현재 신청받는 교외 캠프는 ‘{title}’입니다."
            if period:
                answer += f" 신청 기간은 {period}입니다."
            if task_unit.procedure and task_unit.procedure.steps:
                answer += " 아래 신청 가이드에서 접수 순서를 확인해 주세요."
            return answer
        if task_key == "scholarship.merit" and "신청" in message:
            selection_fact = next((
                fact for fact in task_unit.facts
                if "선발" in normalize_text(f"{fact.label} {fact.value}")
            ), None)
            if selection_fact:
                return (
                    "공식 안내에는 학생이 별도로 신청하는 절차가 아니라, 학교가 방학 중 적격자를 "
                    f"선발한다고 되어 있습니다. {normalize_text(selection_fact.value)}."
                )
        if task_key == "shuttle.info":
            source = re.sub(r"\s+", "", f"{notice.content} {notice.attachment_text}")
            stops = []
            if re.search(r"기[흥릉홍]역\(?4번출구", source):
                stops.append("기흥역 4번 출구")
            if "강남대역" in source:
                stops.append("강남대역")
            if "스타벅스" in source:
                stops.append("학교 앞 스타벅스")
            campus = [name for name in ("이공관", "본관", "인문사회관", "샬롬관") if name in source]
            if campus:
                stops.append("교내 " + "·".join(campus))
            if stops:
                answer = "무료 순환버스는 " + ", ".join(dict.fromkeys(stops)) + " 정차 지점을 운행합니다."
                if SHUTTLE_TIME_PATTERN.search(normalize_text(message)):
                    answer += " 아래 시간표 이미지에서 요일별 출발 시각을 확인할 수 있습니다."
                return answer
        if any(word in message for word in ("전화", "연락처", "담당 부서", "어디에 문의")):
            if department.phone:
                hours = f" 운영시간은 {department.office_hours}입니다." if department.office_hours else ""
                person = f" {department.contact_person} 담당자" if department.contact_person else ""
                duty = f"({department.contact_duty})" if department.contact_duty else ""
                return f"담당 부서는 {department.name or '원문에 표시된 부서'}이며{person}{duty} 전화번호는 {department.phone}입니다.{hours}"
            return (
                f"담당 부서는 {meta.department_name or '원문 확인 필요'}로 확인되지만, "
                "현재 원문에는 담당 전화번호가 명시되어 있지 않습니다. 임의의 번호를 안내하지 않겠습니다."
            )
        if any(word in message for word in ("원문", "링크", "공지 보여", "공지 찾아", "어디서 확인", "어디에서 확인")):
            return f"가장 관련 있는 공식 공지는 ‘{notice.title}’입니다. 아래 원문 보기에서 확인해 주세요."
        if task_unit is not None and task_unit.task.task_key == "graduation.requirements":
            cohort = _requested_graduation_cohort(message)
            matched_range = _matching_graduation_range(task_unit, cohort)
            if cohort and matched_range:
                requested_start, requested_end = cohort
                range_label = _graduation_range_label(matched_range)
                requested_label = (
                    f"{requested_start}년도 입학생" if requested_start == requested_end
                    else f"{requested_start}~{requested_end}학년도 입학생"
                )
                minimum_credit = next((
                    (re.search(r"\d+\s*학점\s*이상", normalize_text(fact.value)) or [normalize_text(fact.value)])[0]
                    for fact in task_unit.facts if "최소 졸업학점" in getattr(fact, "label", "")
                ), None)
                credit_sentence = f" 일반 기준상 최소 졸업학점은 {minimum_credit}입니다." if minimum_credit else ""
                return (
                    f"{requested_label}은 ‘{range_label} 졸업이수학점표’를 확인해야 합니다."
                    f"{credit_sentence} 학부(과)에 따라 교양·전공·다전공 학점이 달라지므로 아래 졸업 공식 원문에서 "
                    "본인 소속 기준표를 확인해 주세요."
                )
            graduation_facts = [
                fact for fact in task_unit.facts
                if any(term in normalize_text(f"{fact.label} {fact.value}") for term in (
                    "졸업이수학점", "졸업학점", "전공 이수", "제2전공",
                ))
                and not (cohort and re.search(
                    r"20\d{2}|학년도\s*(?:이전|이후)",
                    normalize_text(f"{fact.label} {fact.value}"),
                ))
            ]
            if graduation_facts:
                prefix = "요청한 입학년도에 공통 적용되는 공식 졸업요건 근거입니다. " if cohort else ""
                values = " ".join(
                    f"{fact.label}: {normalize_text(fact.value).rstrip(' .')}." for fact in graduation_facts[:3]
                )
                return prefix + values + " 학과·전공별 세부 기준은 아래 원문 표에서 확인해 주세요."
            if task_unit.summary:
                return normalize_text(task_unit.summary)[:500]
        if ChatService._wants_date(message):
            if not application_start and not application_end:
                if relative_period:
                    if _specific_relative_period(relative_period):
                        return f"신청 시기는 {relative_period}입니다."
                    return (
                        "현재 공식 안내에는 학기별 정확한 날짜가 명시되어 있지 않습니다. "
                        "아래 학사일정에서 최신 날짜를 확인해 주세요."
                    )
                return (
                    f"‘{notice.title}’ 공식 공지는 찾았지만, 현재 저장된 원문 근거에서 "
                    "질문하신 기간을 확인할 수 없습니다. 다른 학기의 날짜를 섞어 안내하지 않겠습니다. "
                    "아래 원문 보기에서 공지 이미지를 확인해 주세요."
                )
            if application_start and application_end:
                period = f"{_date_label(application_start)}부터 {_date_label(application_end)}까지"
            elif application_end:
                period = f"{_date_label(application_end)}까지"
            else:
                period = f"{_date_label(application_start)}부터"
            task_name = task_unit.title if task_unit is not None else (meta.sub_category or "신청")
            return f"{task_name} 기간은 {period}입니다."
        return None

    def _department_info(self, message: str, notice: Notice, meta) -> DepartmentInfo:
        department = DepartmentInfo(
            name=meta.department_name,
            contact_person=meta.contact_person,
            contact_role=meta.contact_role,
            contact_duty=meta.contact_role,
            phone=meta.department_phone,
            email=meta.department_email,
            office_location=meta.department_office_location,
            office_hours=meta.department_office_hours,
        )
        if department.phone:
            matching_staff = self.db.scalar(select(StaffDirectoryContact).where(
                StaffDirectoryContact.is_active.is_(True),
                StaffDirectoryContact.department_name == department.name,
                StaffDirectoryContact.phone == department.phone,
            ).limit(1))
            if matching_staff:
                department.contact_duty = department.contact_duty or matching_staff.duty
                department.contact_source = "강남대학교 공식 직원 연락처에서 보완"
                department.source_url = matching_staff.source_url
            return department
        if not department.name:
            return department
        source_contacts = self._notice_contact_options(notice)
        if source_contacts:
            if len(source_contacts) == 1:
                label, phone = source_contacts[0]
                department.name = label or department.name
                department.phone = phone
            else:
                department.name = "소속 대학별 문의처"
                department.phone = " / ".join(
                    f"{label} {phone}" if label else phone for label, phone in source_contacts
                )
            department.contact_source = "공지 원문의 문의처에서 확인"
            department.source_url = notice.source_url
            return department
        context = " ".join(filter(None, [
            message,
            notice.title,
            meta.sub_category,
            " ".join(meta.keywords or []),
        ]))
        resolved = resolve_staff_contact(
            self.db,
            department.name,
            context,
            preferred_person=department.contact_person,
        )
        if not resolved:
            return department
        department.phone = resolved.phone
        department.contact_person = department.contact_person or resolved.contact_person
        department.contact_duty = resolved.duty
        department.contact_source = "강남대학교 공식 직원 연락처에서 보완"
        department.source_url = resolved.source_url
        return department

    @staticmethod
    def _notice_contact_options(notice: Notice) -> list[tuple[str | None, str]]:
        """문의처 문맥의 학교 전화만 추출해 일반 직원명부 번호로 덮지 않게 한다."""
        source = normalize_text(f"{notice.content}\n{notice.attachment_text}")
        options: list[tuple[str | None, str]] = []
        for marker in re.finditer(r"문의처|문의전화|문의\s*[:：]", source):
            region = source[marker.start():marker.start() + 500]
            for phone_match in re.finditer(r"0(?:2|3\d|4\d|5\d|6\d)-\d{3,4}-\d{4}", region):
                prefix = region[max(0, phone_match.start() - 100):phone_match.start()]
                labels = re.findall(r"([가-힣A-Za-z0-9·]+(?:팀|센터|연대|처))", prefix)
                option = (labels[-1] if labels else None, phone_match.group(0))
                if option not in options:
                    options.append(option)
        return options[:8]

    @staticmethod
    def _wants_action_guide(message: str) -> bool:
        normalized = normalize_text(message)
        compact = re.sub(r"\s+", "", normalized)
        if "따로 신청" in normalized and not any(word in normalized for word in ("방법", "절차", "어떻게", "순서")):
            return False
        if any(phrase in normalized for phrase in ("어디서 확인", "어디에서 확인")) and not any(
            action in normalized for action in ("신청", "제출", "납부", "발급", "신고")
        ):
            return False
        return (
            any(word in normalized for word in (
                "어떻게", "방법", "절차", "순서", "단계", "어디서", "어디에", "제출", "신고",
                "바로가기", "신청 링크", "해야 해", "해야 돼",
                "준비서류", "준비 서류", "구비서류", "준비물", "필요 서류",
            ))
            or any(word in compact for word in ("하는법", "신청법", "접수법", "제출법"))
        )

    @staticmethod
    def _wants_documents(message: str) -> bool:
        return any(word in normalize_text(message) for word in (
            "준비서류", "준비 서류", "구비서류", "준비물", "필요 서류", "제출서류", "제출 서류",
        ))

    @staticmethod
    def _wants_eligibility(message: str) -> bool:
        return any(word in normalize_text(message) for word in ("자격", "조건", "대상", "누가"))

    @staticmethod
    def _wants_contact(message: str) -> bool:
        return any(word in normalize_text(message) for word in (
            "전화", "연락처", "담당자", "담당 부서", "문의처", "어디에 문의",
        ))

    @staticmethod
    def _usable_application_method(value: str | None) -> bool:
        if not value:
            return False
        return any(token in value for token in (
            "→", "접속", "로그인", "온라인", "방문", "직접", "신고", "제출", "업로드", "납부", "홈페이지", "시스템",
        ))

    @staticmethod
    def _safe_next_action(match: dict, guide: ActionGuideResponse | None, visible: bool) -> NextAction | None:
        """신청 페이지처럼 공식 원문과 다른 실제 행동 링크만 다음 행동으로 만든다."""
        notice = match["notice"]
        unit = match.get("task_unit")
        if visible and unit is not None and unit.procedure is not None and guide and guide.steps:
            deadline = _parse_date(unit.application_end)
            if deadline and deadline < datetime.now(KST):
                return None
            first = guide.steps[0]
            if len(normalize_text(first.title)) < 2 or first.confidence < 0.55:
                return None
            action_url = first.action_url or guide.application_url
            if not action_url or action_url.rstrip("/") == notice.source_url.rstrip("/"):
                return None
            return NextAction(
                label=first.title,
                description=first.description,
                url=action_url,
                deadline=unit.application_end,
            )
        return None

    def _multi_task_answer(
        self,
        message: str,
        session_id: str,
        answer_id: str,
        query: QueryPlan,
        task_matches: dict[str, dict],
        now: datetime,
        scope: SearchScope,
    ) -> ChatResponse:
        task_results: list[TaskAnswerResponse] = []
        sources: list[SourceEvidence] = []
        summaries_by_notice: dict[int, NoticeSummary] = {}
        selected_matches: list[dict] = []
        departments: list[DepartmentInfo] = []
        missing_tasks: list[str] = []
        missing_procedures: list[str] = []

        for task_key in query.requested_tasks:
            task = TASK_BY_KEY.get(task_key)
            match = task_matches.get(task_key)
            if not task or not match:
                missing_tasks.append(task.name if task else task_key)
                continue
            selected_matches.append(match)
            notice = match["notice"]
            meta = match["metadata"]
            unit = match.get("task_unit")
            department = self._department_info(message, notice, meta)
            departments.append(department)
            guide = build_action_guide_response(notice, unit)
            visible_guide = bool(guide and self._wants_action_guide(message))
            if "procedure" in query.requested_fields and not visible_guide:
                missing_procedures.append(task.name)
            facts, _notes = self._answer_presentation(message, meta, visible_guide, unit)
            self._attach_fact_provenance(facts, match)
            if facts:
                task_answer = f"{task.name}: " + ", ".join(f"{fact.label} {fact.value}" for fact in facts[:3])
            elif unit is not None and unit.summary:
                task_answer = f"{task.name}: {normalize_text(unit.summary)[:300]}"
            else:
                task_answer = f"{task.name}: ‘{notice.title}’ 공식 근거를 확인했습니다."
            next_action = self._safe_next_action(match, guide, visible_guide)
            task_results.append(TaskAnswerResponse(
                task_key=task_key,
                task_name=task.name,
                answer=task_answer,
                answer_facts=facts,
                action_guide=guide if visible_guide else None,
                next_action=next_action,
                department=department,
                source_notice_ids=[notice.id],
            ))

            current_status = _match_status(match, now)
            summaries_by_notice.setdefault(notice.id, NoticeSummary(
                id=notice.id, title=notice.title, category=meta.category,
                published_at=notice.published_at, notice_status=current_status,
                status_label=effective_status_label(notice, current_status),
                source_url=notice.source_url, score=match["score"],
            ))
            evidence = None
            if unit is not None and unit.evidence:
                evidence = max(unit.evidence, key=lambda item: item.confidence)
            excerpt = normalize_text(evidence.excerpt if evidence else (match.get("chunk_text") or notice.content))
            sources.append(SourceEvidence(
                notice_id=notice.id, title=notice.title, published_at=notice.published_at,
                effective_status=current_status,
                evidence_excerpt=excerpt[:320] + ("…" if len(excerpt) > 320 else ""),
                url=notice.source_url, task_key=task_key,
                task_unit_id=unit.id if unit is not None else None,
            ))

        if set(query.requested_tasks) == {"graduation.requirements", "graduation.early"}:
            answer = "일반 졸업요건과 조기졸업은 서로 다른 업무입니다. 아래에서 각각의 공식 근거를 나누어 비교해 주세요."
        else:
            answer = "요청한 업무를 서로 섞지 않고 각각의 공식 근거로 정리했습니다. 아래 업무별 안내를 순서대로 확인해 주세요."
        warnings = []
        status = "success"
        if missing_tasks:
            status = "insufficient_evidence"
            warnings.append(f"다음 업무는 충분한 공식 근거를 찾지 못했습니다: {', '.join(missing_tasks)}")
        if missing_procedures:
            status = "insufficient_evidence"
            warnings.append(
                "검증된 단계형 절차가 아직 없는 업무: " + ", ".join(missing_procedures)
            )
        primary_department = departments[0] if departments else DepartmentInfo()
        self._save_session_context(session_id, query, selected_matches, primary_department)
        self.db.commit()
        return ChatResponse(
            answer_id=answer_id,
            answer=answer,
            status=status,
            answer_mode="deterministic",
            matched_notices=list(summaries_by_notice.values()),
            sources=sources,
            task_results=task_results,
            department=DepartmentInfo(),
            warnings=warnings,
            original_url=sources[0].url if sources else None,
            has_data=bool(task_results),
            session_id=session_id,
            query=query,
            verified_at=now,
            search_scope=scope,
        )

    def _answer_with_plan(
        self,
        message: str,
        session_id: str,
        selected_category: str | None,
        query: QueryPlan,
    ) -> ChatResponse:
        self._context_notice_ids = []
        answer_id = str(uuid.uuid4())
        query = self._apply_session_context(message, session_id, query)
        if selected_category:
            query.category = selected_category

        if query.needs_clarification and query.clarification_question:
            self._save_session_context(session_id, query, [], None)
            self.db.commit()
            notice_count = self.db.scalar(select(func.count(Notice.id)).where(
                Notice.is_archived.is_(False), Notice.ai_processed.is_(True),
            )) or 0
            return ChatResponse(
                answer_id=answer_id,
                answer=query.clarification_question,
                status="clarification_required",
                answer_mode="deterministic",
                clarification_options=query.clarification_options,
                has_data=False,
                session_id=session_id,
                query=query,
                verified_at=datetime.now(KST),
                search_scope=SearchScope(
                    notice_count=notice_count,
                    description="질문 의도 확인 후 공식 자료 검색",
                ),
            )

        if query.follow_up and not query.context_applied and not query.requested_tasks:
            return ChatResponse(
                answer_id=answer_id,
                answer="어떤 업무를 이어서 묻는 것인지 확인이 필요합니다. 예: ‘졸업요건은 어디서 확인해?’처럼 업무명을 함께 적어 주세요.",
                status="clarification_required",
                answer_mode="department_handoff",
                has_data=False,
                session_id=session_id,
                query=query,
                verified_at=datetime.now(KST),
                search_scope=SearchScope(
                    notice_count=self.db.scalar(select(func.count(Notice.id)).where(
                        Notice.is_archived.is_(False), Notice.ai_processed.is_(True),
                    )) or 0,
                ),
            )

        faq = self._approved_faq(message)
        matches, task_matches = self._search(message, query)
        if query.scope == "search_first" and not query.context_applied:
            matches = [item for item in matches if _search_first_match_supported(message, query, item)]
            task_matches = {
                key: item for key, item in task_matches.items()
                if _search_first_match_supported(message, query, item)
            }
        if not _is_shuttle_request(message, query):
            # 벡터 유사도만으로 셔틀 공지가 선택되면 전혀 무관한 질문에도
            # 정류장 안내가 나올 수 있으므로 현재 질문의 명시적 의도를 확인한다.
            matches = [item for item in matches if _match_task_key(item) != "shuttle.info"]
            task_matches.pop("shuttle.info", None)
        now = datetime.now(KST)
        notice_count = self.db.scalar(select(func.count(Notice.id)).where(
            Notice.is_archived.is_(False), Notice.ai_processed.is_(True),
        )) or 0
        scope = SearchScope(notice_count=notice_count)

        if settings.missing_evidence_recovery_enabled and matches:
            recovery_deadline = min(
                self._request_deadline or (time.perf_counter() + settings.missing_evidence_recovery_timeout_seconds),
                time.perf_counter() + settings.missing_evidence_recovery_timeout_seconds,
            )
            recovery_started = time.perf_counter()
            try:
                with self.db.begin_nested():
                    self._recovery_outcome = MissingEvidenceRecovery(self.db).recover(
                        message, query, matches, deadline=recovery_deadline,
                    )
            except Exception:
                self._recovery_outcome = RecoveryOutcome(
                    triggered=True,
                    status="failed",
                    reason="recovery_internal_error",
                    duration_ms=round((time.perf_counter() - recovery_started) * 1000, 1),
                )

        if (
            settings.on_demand_search_enabled
            and settings.on_demand_live_search_enabled
            and settings.on_demand_codex_enabled
            and not settings.mock_ai
            and local_evidence_insufficient(query, matches)
        ):
            self._on_demand_resolver = OnDemandEvidenceResolver(self.db)
            live_response = self._on_demand_resolver.resolve(
                query, session_id=session_id, answer_id=answer_id,
                timeout_seconds=max(
                    0.5,
                    (self._request_deadline or time.perf_counter()) - time.perf_counter(),
                ),
            )
            if live_response is not None:
                self.db.commit()
                return live_response

        if not matches:
            collect_answer_gaps(self.db, message, query, None, no_result=True)
            return ChatResponse(
                answer_id=answer_id,
                answer=(
                    "현재 수집된 공식 공지와 검수 FAQ에서 이 질문에 답할 근거를 찾지 못했습니다. "
                    "내용을 추측하지 않겠습니다. 질문에 학년도·학기·대상을 추가하거나 담당 부서에 확인해 주세요."
                ),
                status="no_result",
                answer_mode="department_handoff",
                has_data=False,
                session_id=session_id,
                query=query,
                verified_at=now,
                search_scope=scope,
            )

        if len(query.requested_tasks) > 1:
            return self._multi_task_answer(
                message, session_id, answer_id, query, task_matches, now, scope,
            )

        statuses = [_match_status(item, now) for item in matches]
        top_expired = statuses[0] == "expired"
        top = matches[0]
        top_meta = top["metadata"]
        next_important = _next_important_date(top_meta, now) if top_expired else None
        department = self._department_info(message, top["notice"], top_meta)
        action_guide = build_action_guide_response(top["notice"], top.get("task_unit"))
        if (
            action_guide
            and top.get("task_unit") is None
            and not guide_matches_query(message, action_guide.task_name)
        ):
            action_guide = None
        if action_guide:
            action_guide.department = department
        use_action_guide = bool(action_guide and self._wants_action_guide(message))
        wants_action_guide = self._wants_action_guide(message) and query.task_key != "shuttle.info"
        usable_method = bool(
            top.get("task_unit") is None
            and self._usable_application_method(top_meta.application_method)
        )
        insufficient_procedure = wants_action_guide and not action_guide and not usable_method
        collect_answer_gaps(
            self.db, message, query, top["notice"],
            insufficient_procedure=insufficient_procedure,
            resolved_fields={
                field for field, value in (
                    ("department_phone", department.phone),
                    ("contact_person", department.contact_person),
                    ("department_email", department.email),
                    ("department_office_location", department.office_location),
                ) if value
            },
        )
        deterministic = self._deterministic_answer(message, top, department)
        relative_period, _ = _relative_application_period(top.get("task_unit"))
        answer_facts, answer_notes = self._answer_presentation(
            message, top_meta, use_action_guide, top.get("task_unit"),
        )

        if action_guide and use_action_guide:
            overview = normalize_text(action_guide.summary or "")[:500].rstrip()
            if overview and overview[-1] not in ".!?":
                overview += "."
            if top_expired and next_important:
                item, next_start, next_end = next_important
                next_period = (
                    f"{_date_label(next_start)}부터 {_date_label(next_end)}까지"
                    if next_end else f"{_date_label(next_start)}부터"
                )
                answer = (
                    _answer_sections(
                        overview,
                        f"‘{action_guide.task_name}’의 첫 신청 단계는 마감됐지만, "
                        f"후속 일정 ‘{item.get('label') or '다음 단계'}’: {next_period}. "
                        "아래에서 전체 순서와 단계별 일정을 확인해 주세요.",
                    )
                )
            elif top_expired:
                answer = _answer_sections(overview, "아래 절차는 지난 모집 기준 참고용입니다.")
            elif self._wants_documents(message) and action_guide.required_documents:
                period_prefix = (
                    f"신청 시기: {relative_period}. "
                    if self._wants_date(message) and relative_period else ""
                )
                answer = f"{period_prefix}준비 서류: {', '.join(action_guide.required_documents)}."
            elif self._wants_eligibility(message) and action_guide.eligibility_notes:
                answer = f"‘{action_guide.task_name}’ 신청 조건을 아래에서 확인해 주세요."
            else:
                procedure_label = (
                    "납부 절차" if "납부" in action_guide.task_name and "신청" not in action_guide.task_name
                    else "신청 절차"
                )
                if top_meta.application_start or top_meta.application_end:
                    answer = f"‘{action_guide.task_name}’ {procedure_label}입니다. 아래 순서대로 진행해 주세요."
                else:
                    answer = f"‘{action_guide.task_name}’ 진행 방법입니다. 아래 순서대로 진행해 주세요."
            if self._wants_contact(message):
                if department.phone:
                    answer += f" 담당 부서는 {department.name or '원문 표시 부서'}, 전화번호는 {department.phone}입니다."
                else:
                    answer += (
                        f" 담당 부서는 ‘{top_meta.department_name or '원문 확인 필요'}’으로 확인되지만, "
                        "이 원문에는 전화번호가 명시되어 있지 않습니다."
                    )
            answer_mode = "action_guide"
        elif wants_action_guide and usable_method:
            answer = (
                f"‘{top['notice'].title}’ 공지에서 확인되는 방법은 "
                f"“{top_meta.application_method}”입니다. 세부 메뉴와 제출 조건은 원문에서 확인해 주세요."
            )
            answer_mode = "deterministic"
        elif (
            self._recovery_outcome.status == "verified_absent"
            and self._recovery_outcome.remaining_missing_fields
        ):
            answer = (
                "공지 본문·첨부파일·공식 연락처를 다시 확인했지만 요청하신 정보는 "
                "현재 공식 자료에서 확인되지 않습니다."
            )
            answer_mode = "search_results_only"
        elif insufficient_procedure:
            answer = (
                f"‘{top['notice'].title}’ 공지는 찾았지만, 현재 추출된 본문에는 신청 순서를 "
                "안전하게 안내할 충분한 근거가 없습니다. 절차를 추측하지 않으며 아래 공식 원문을 확인해 주세요."
            )
            answer_mode = "search_results_only"
        elif deterministic is not None:
            answer = deterministic
            answer_mode = "deterministic"
        elif faq and faq.answer:
            answer = faq.answer
            answer_mode = "faq"
        elif answer_facts:
            subject = (
                top["task_unit"].title
                if top.get("task_unit") is not None
                else top["notice"].title
            )
            answer = f"‘{subject}’에 대해 공식 원문에서 확인된 핵심 정보입니다."
            answer_mode = "deterministic"
        elif not settings.on_demand_search_enabled:
            answer = self.ai.generate_answer(message, matches)
            answer_mode = "generated"
        else:
            answer = (
                f"‘{top['notice'].title}’ 공식 자료를 찾았습니다. "
                "현재 검증 가능한 근거만 아래에 표시하며, 근거에 없는 내용은 추측하지 않습니다."
            )
            answer_mode = "search_results_only"

        answer = _plain_answer(answer)
        self._attach_fact_provenance(answer_facts, top)

        warnings = []
        status = (
            "insufficient_evidence"
            if insufficient_procedure or (
                self._recovery_outcome.status == "verified_absent"
                and self._recovery_outcome.remaining_missing_fields
            )
            else "success"
        )
        if insufficient_procedure:
            warnings.append("신청 절차가 이미지 또는 첨부파일에만 있을 수 있어 원문 확인이 필요합니다.")
        elif top_expired and not next_important:
            status = "stale_only"
        elif top_expired and next_important:
            warnings.append("첫 신청 단계는 마감됐지만 이 공지에는 아직 남은 후속 일정이 있습니다.")

        requested_period_missing = bool(
            "application_period" in query.requested_fields
            and (query.task_key != "event.camp" or self._asks_application_period(message))
            and not _specific_relative_period(relative_period)
            and not (
                (top.get("task_unit") and (
                    top["task_unit"].application_start or top["task_unit"].application_end
                ))
                or (top_meta.application_start or top_meta.application_end)
            )
        )
        if requested_period_missing:
            status = "insufficient_evidence"
            if not any(phrase in answer for phrase in (
                "기간을 확인할 수 없습니다", "정확한 날짜가 명시되어 있지 않습니다", "구체적인 기간은 현재 원문에 없어",
            )):
                answer = _plain_answer(
                    f"{answer} 구체적인 기간은 현재 원문에 없어 학사일정 또는 새 공지를 확인해야 합니다."
                )

        summaries = []
        sources = []
        # 단일 업무 답변은 최종 선택 후보 하나만 화면에 노출한다. 후보
        # 2·3위는 진단 로그에는 남지만 학생에게 관련 공지처럼 보이지 않는다.
        for item, item_status in zip(matches[:1], statuses[:1]):
            notice = item["notice"]
            metadata = item["metadata"]
            unit = item.get("task_unit")
            summaries.append(NoticeSummary(
                id=notice.id,
                title=notice.title,
                category=metadata.category,
                published_at=notice.published_at,
                notice_status=item_status,
                status_label=effective_status_label(notice, item_status),
                source_url=notice.source_url,
                score=item["score"],
            ))
            excerpt = normalize_text(item.get("chunk_text") or notice.content)
            sources.append(SourceEvidence(
                notice_id=notice.id,
                title=notice.title,
                published_at=notice.published_at,
                effective_status=item_status,
                evidence_excerpt=excerpt[:240] + ("…" if len(excerpt) > 240 else ""),
                url=notice.source_url,
                task_key=unit.task.task_key if unit is not None else query.task_key,
                task_unit_id=unit.id if unit is not None else None,
            ))

        next_action = self._safe_next_action(top, action_guide, use_action_guide)
        if requested_period_missing:
            calendar_notice = self.db.scalar(select(Notice).where(
                Notice.is_archived.is_(False),
                Notice.title.contains("학사일정"),
            ).order_by(Notice.published_at.desc()).limit(1))
            if calendar_notice:
                next_action = NextAction(
                    label="학사일정에서 날짜 확인",
                    description="학기별 정확한 날짜는 최신 학사일정 원문에서 확인해 주세요.",
                    url=calendar_notice.source_url,
                )
        answer_media = _answer_media(message, query, top)
        response_department = DepartmentInfo() if answer_media else department

        self._save_session_context(session_id, query, [top], department)
        self.db.commit()

        return ChatResponse(
            answer_id=answer_id,
            answer=answer,
            status=status,
            answer_mode=answer_mode,
            answer_facts=answer_facts,
            answer_notes=answer_notes,
            matched_notices=summaries,
            sources=sources,
            media=answer_media,
            department=response_department,
            next_action=next_action,
            action_guide=action_guide if use_action_guide else None,
            warnings=warnings,
            original_url=top["notice"].source_url,
            has_data=True,
            session_id=session_id,
            query=query,
            verified_at=now,
            search_scope=scope,
        )
