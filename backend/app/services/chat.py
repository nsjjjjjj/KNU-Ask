import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import FAQ, Notice
from app.schemas import (
    ActionGuideResponse,
    AnswerFact,
    ChatResponse,
    DepartmentInfo,
    NextAction,
    NoticeSummary,
    Period,
    SearchScope,
    SourceEvidence,
    StructuredActionStep,
)
from app.services.ai import AIService
from app.services.data_gaps import collect_answer_gaps
from app.services.notice_status import effective_status, effective_status_label
from app.services.search import HybridSearch
from app.services.staff_directory import resolve_staff_contact
from app.utils.text import normalize_text


KST = ZoneInfo("Asia/Seoul")


def _date_label(value: datetime | None) -> str:
    return value.astimezone(KST).strftime("%Y.%m.%d %H:%M") if value else "확인 필요"


def _plain_answer(value: str) -> str:
    """모델이 만든 Markdown 표식을 제거하고 일반 텍스트만 API로 내보낸다."""
    break_token = "KNUASKLINEBREAKTOKEN"
    text = normalize_text(value.replace("\n", f" {break_token} "))
    # 빈 줄이 연속되면 토큰이 맞닿아 하나가 남을 수 있으므로 먼저 하나로
    # 합친다. 내부 처리 토큰은 어떤 경우에도 사용자 답변에 노출하지 않는다.
    text = re.sub(rf"(?:\s*{break_token}\s*)+", f" {break_token} ", text)
    text = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", text)
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(rf"(?:^| {break_token} )#{{1,6}}\s*", " ", text)
    text = text.replace(f" {break_token} - ", "\n• ").replace(f" {break_token} * ", "\n• ")
    return text.replace(f" {break_token} ", "\n").strip()


def build_action_guide_response(notice: Notice) -> ActionGuideResponse | None:
    guide = notice.action_guide
    meta = notice.metadata_record
    if not guide or not guide.steps or not meta:
        return None
    targets = list(meta.target_student_types or [])
    targets.extend(f"{grade}학년" for grade in (meta.target_grades or []))
    targets.extend(meta.target_departments or [])
    return ActionGuideResponse(
        task_name=guide.task_name,
        summary=guide.summary,
        targets=list(dict.fromkeys(targets)),
        period=Period(start=meta.application_start, end=meta.application_end),
        prerequisites=guide.prerequisites or [],
        required_documents=meta.required_documents or [],
        eligibility_notes=meta.eligibility_notes or [],
        application_location=meta.application_location,
        fee_information=meta.fee_information,
        capacity=meta.capacity,
        selection_method=meta.selection_method,
        result_announcement=meta.result_announcement,
        cancellation_policy=meta.cancellation_policy,
        benefits=meta.benefits or [],
        credits_or_hours=meta.credits_or_hours,
        important_dates=meta.important_dates or [],
        steps=[StructuredActionStep(
            order=step.step_order,
            title=step.title,
            description=step.description,
            action_type=step.action_type,
            action_url=step.action_url,
            link_label=step.link_label,
            source_type=step.source_type,
            source_locator=step.source_locator,
            confidence=step.confidence,
        ) for step in guide.steps],
        warnings=guide.warnings or [],
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
    def _answer_presentation(message: str, meta, action_guide_visible: bool) -> tuple[list[AnswerFact], list[str]]:
        if action_guide_visible:
            return [], []

        facts: list[AnswerFact] = []
        notes: list[str] = []
        if ChatService._wants_date(message):
            if meta.application_start or meta.application_end:
                if meta.application_start and meta.application_end:
                    value = f"{_date_label(meta.application_start)} ~ {_date_label(meta.application_end)}"
                elif meta.application_end:
                    value = f"{_date_label(meta.application_end)}까지"
                else:
                    value = f"{_date_label(meta.application_start)}부터"
                facts.append(AnswerFact(label="본 신청 기간", value=value))

            wanted_labels = ("예비수강신청", "장애학생 선 수강신청", "수강신청 변경기간")
            for item in meta.important_dates or []:
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

        if meta.application_method:
            facts.append(AnswerFact(label="신청 방법", value=meta.application_method))
        if meta.application_location and meta.application_location not in (meta.application_method or ""):
            facts.append(AnswerFact(label="신청 장소", value=meta.application_location))

        if not ChatService._wants_date(message):
            notes.extend((meta.eligibility_notes or [])[:3])
        return facts[:6], notes[:4]

    @staticmethod
    def _deterministic_answer(message: str, match: dict, department: DepartmentInfo) -> str | None:
        notice = match["notice"]
        meta = match["metadata"]
        if any(word in message for word in ("전화", "연락처", "담당 부서", "어디에 문의")):
            if department.phone:
                hours = f" 운영시간은 {department.office_hours}입니다." if department.office_hours else ""
                source = " 학교 공식 연락처에서 업무 담당자를 확인했습니다." if department.contact_source else ""
                person = f" {department.contact_person} 담당자" if department.contact_person else ""
                duty = f"({department.contact_duty})" if department.contact_duty else ""
                return f"담당 부서는 {department.name or '원문에 표시된 부서'}이며{person}{duty} 전화번호는 {department.phone}입니다.{source}{hours}"
            return (
                f"담당 부서는 {meta.department_name or '원문 확인 필요'}로 확인되지만, "
                "현재 원문에는 담당 전화번호가 명시되어 있지 않습니다. 임의의 번호를 안내하지 않겠습니다."
            )
        if any(word in message for word in ("원문", "링크", "공지 보여", "공지 찾아")):
            return f"가장 관련 있는 공식 공지는 ‘{notice.title}’입니다. 아래 원문 보기에서 확인해 주세요."
        if ChatService._wants_date(message):
            if not meta.application_start and not meta.application_end:
                return (
                    f"‘{notice.title}’ 공식 공지는 찾았지만, 현재 저장된 원문 근거에서 "
                    "수강신청 날짜를 확인할 수 없습니다. 다른 학기의 날짜를 섞어 안내하지 않겠습니다. "
                    "아래 원문 보기에서 공지 이미지를 확인해 주세요."
                )
            if meta.application_start and meta.application_end:
                period = f"{_date_label(meta.application_start)}부터 {_date_label(meta.application_end)}까지"
            elif meta.application_end:
                period = f"{_date_label(meta.application_end)}까지"
            else:
                period = f"{_date_label(meta.application_start)}부터"
            return f"‘{notice.title}’ 기준 기간은 {period}입니다. 대상과 변경 여부는 아래 원문도 함께 확인해 주세요."
        return None

    def _department_info(self, message: str, notice: Notice, meta) -> DepartmentInfo:
        department = DepartmentInfo(
            name=meta.department_name,
            contact_person=meta.contact_person,
            contact_role=meta.contact_role,
            phone=meta.department_phone,
            email=meta.department_email,
            office_location=meta.department_office_location,
            office_hours=meta.department_office_hours,
        )
        if department.phone or not department.name:
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
    def _wants_action_guide(message: str) -> bool:
        return any(word in normalize_text(message) for word in (
            "어떻게", "방법", "절차", "순서", "단계", "어디서", "바로가기", "신청 링크", "하는 법", "해야 해", "해야 돼",
        ))

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
            "→", "접속", "로그인", "온라인", "방문", "제출", "업로드", "납부", "홈페이지", "시스템",
        ))

    def answer(self, message: str, session_id: str | None = None, selected_category: str | None = None) -> ChatResponse:
        session_id = session_id or str(uuid.uuid4())
        answer_id = str(uuid.uuid4())
        query = self.ai.analyze_query(message)
        if selected_category:
            query.category = selected_category

        faq = self._approved_faq(message)
        matches = HybridSearch(self.db, self.ai).search(message, query, 5)
        now = datetime.now(KST)
        notice_count = self.db.scalar(select(func.count(Notice.id)).where(Notice.is_archived.is_(False))) or 0
        scope = SearchScope(notice_count=notice_count)

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

        statuses = [effective_status(item["notice"], now) for item in matches]
        top_expired = statuses[0] == "expired"
        top = matches[0]
        top_meta = top["metadata"]
        department = self._department_info(message, top["notice"], top_meta)
        action_guide = build_action_guide_response(top["notice"])
        if action_guide:
            action_guide.department = department
        use_action_guide = bool(action_guide and self._wants_action_guide(message))
        wants_action_guide = self._wants_action_guide(message)
        usable_method = self._usable_application_method(top_meta.application_method)
        insufficient_procedure = wants_action_guide and not action_guide and not usable_method
        collect_answer_gaps(
            self.db, message, query, top["notice"],
            insufficient_procedure=insufficient_procedure,
        )
        deterministic = self._deterministic_answer(message, top, department)

        if action_guide and use_action_guide:
            if top_expired:
                deadline = _date_label(top_meta.application_end) if top_meta.application_end else "공지에 표시된 기한"
                answer = (
                    f"‘{action_guide.task_name}’ 신청은 {deadline}에 마감되어 현재 신청할 수 없습니다. "
                    "아래 절차는 참고용이며, 추가 모집 여부는 공식 원문에서 확인해 주세요."
                )
            else:
                answer = (
                    f"‘{action_guide.task_name}’의 신청 절차를 공식 공지에서 정리했습니다. "
                    "아래 단계대로 진행하고 제출 전 대상과 마감일을 원문에서 확인해 주세요."
                )
            if self._wants_contact(message):
                if department.phone:
                    source = " 학교 공식 연락처에서 보완한 번호입니다." if department.contact_source else ""
                    answer += f" 담당 부서는 {department.name or '원문 표시 부서'}, 전화번호는 {department.phone}입니다.{source}"
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
        elif insufficient_procedure:
            answer = (
                f"‘{top['notice'].title}’ 공지는 찾았지만, 현재 추출된 본문에는 신청 순서를 "
                "안전하게 안내할 충분한 근거가 없습니다. 절차를 추측하지 않으며 아래 공식 원문을 확인해 주세요."
            )
            answer_mode = "search_results_only"
        elif deterministic:
            answer = deterministic
            answer_mode = "deterministic"
        elif faq and faq.answer:
            answer = faq.answer
            answer_mode = "faq"
        else:
            answer = self.ai.generate_answer(message, matches)
            answer_mode = "generated"

        answer = _plain_answer(answer)
        answer_facts, answer_notes = self._answer_presentation(message, top_meta, use_action_guide)

        warnings = []
        status = "insufficient_evidence" if insufficient_procedure else "success"
        if insufficient_procedure:
            warnings.append("신청 절차가 이미지 또는 첨부파일에만 있을 수 있어 원문 확인이 필요합니다.")
        elif top_expired:
            status = "stale_only"
            warnings.append("가장 관련 높은 공지는 신청이 마감된 자료입니다.")

        summaries = []
        sources = []
        for item, item_status in zip(matches[:3], statuses[:3]):
            notice = item["notice"]
            metadata = item["metadata"]
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
            ))

        next_action = None
        if top_expired:
            next_action = NextAction(
                label="공식 공지에서 추가 모집 여부 확인",
                description="이 공지의 신청은 마감되었습니다.",
                url=top["notice"].source_url,
                deadline=top_meta.application_end,
            )
        elif action_guide and use_action_guide and action_guide.application_url:
            next_action = NextAction(
                label="신청 페이지로 이동",
                description="신청 전에 대상, 기간과 준비물을 확인해 주세요.",
                url=action_guide.application_url,
                deadline=top_meta.application_end,
            )
        elif top_meta.application_method or top["notice"].source_url:
            next_action = NextAction(
                label=top_meta.application_method or "공식 공지 원문 확인",
                description="신청 전에 대상과 최신 변경사항을 확인해 주세요.",
                url=top["notice"].source_url,
                deadline=top_meta.application_end,
            )

        return ChatResponse(
            answer_id=answer_id,
            answer=answer,
            status=status,
            answer_mode=answer_mode,
            answer_facts=answer_facts,
            answer_notes=answer_notes,
            matched_notices=summaries,
            sources=sources,
            department=department,
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
