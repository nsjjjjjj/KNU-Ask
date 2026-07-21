from __future__ import annotations

import math
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    KnowledgeTask, Notice, NoticeChunk, NoticeEmbedding, NoticeMetadata,
    TaskUnit, TaskUnitEmbedding,
)
from app.schemas import QueryFilters
from app.services.ai import AIService
from app.services.notice_status import effective_status
from app.services.search.task_rules import TASK_BY_KEY, candidate_task_score, detect_task


logger = logging.getLogger(__name__)
SPECIAL_STUDENT_TERMS = {
    "신입생": ("신입생", "신·편입생", "신편입생"),
    "편입생": ("편입생", "신·편입생", "신편입생"),
    "장애학생": ("장애학생", "장애 학생"),
    "재입학생": ("재입학생", "재입학"),
}
IDENTITY_STOPWORDS = {
    "신청", "방법", "알려줘", "안내", "현재", "가능", "있는", "2024", "2025", "2026",
    "캠프", "프로그램", "학년도", "년도", "학생", "입학생", "요건", "기간", "일정",
    "언제", "언제야", "얼마", "얼마야", "참가비", "비용", "금액", "대상", "자격",
    "장소", "위치", "부트캠프", "해커톤", "웹개발", "교육", "행사", "정보", "대해",
    "ai", "sw", "참여", "지원", "접수", "모집",
}
COURSE_REGISTRATION_DETAIL_SCOPES = (
    ("예비수강신청", ("예비수강신청",)),
    ("장애학생 선수강신청", ("장애학생", "선수강신청")),
    ("제1전공 신청", ("제1전공", "전공신청")),
    ("잔여인원 조회", ("잔여인원",)),
)


def _graduation_source_role(
    message: str, task_key: str | None, notice_title: str, unit_heading: str,
    source_type: str | None = None,
) -> str:
    """일반 졸업요건과 학사구조개편 경과조치의 역할을 분리한다."""
    if task_key != "graduation.requirements":
        return "neutral"
    query = re.sub(r"\s+", "", message)
    title = re.sub(r"\s+", "", notice_title)
    heading = re.sub(r"\s+", "", unit_heading)
    asks_reorganization = any(term in query for term in (
        "학사구조개편", "구조개편", "편제변경", "소속변경", "복학후", "재입학후",
    ))
    reorganization_source = "학사구조개편" in title or "편제변경" in heading
    if reorganization_source and not asks_reorganization:
        return "reject_reorganization"
    if source_type == "academic_guide" and not reorganization_source and "졸업" in title and any(
        term in heading for term in ("졸업요건", "졸업이수과목", "졸업이수학점")
    ):
        return "canonical_guide"
    return "neutral"


def _tuition_source_role(message: str, task_key: str | None, notice_title: str, source_type: str) -> str:
    """일반 등록금 질문에 휴학 FAQ나 계절수업 납부를 대표 근거로 쓰지 않는다."""
    if task_key != "tuition.payment":
        return "neutral"
    query = re.sub(r"\s+", "", message)
    title = re.sub(r"\s+", "", notice_title)
    if "휴학" in title and "휴학" not in query:
        return "reject_special_case"
    if any(term in title for term in ("계절수업", "계절학기")) and not any(
        term in query for term in ("계절수업", "계절학기")
    ):
        return "reject_special_case"
    if source_type == "academic_guide" and "등록안내" in title:
        return "canonical_guide"
    return "neutral"


def _identity_text(value: str) -> str:
    """기관 약칭과 구두점 차이를 없애 정확한 고유명사 제목을 비교한다."""
    compact = re.sub(r"[^0-9a-z가-힣]+", "", (value or "").lower())
    return compact.replace("대학교", "대")


def _identity_terms(filters: QueryFilters) -> list[str]:
    # 고유 행사명을 묻는 경우에만 이름 일치를 강한 조건으로 쓴다.
    # 학사업무의 '신청 자격·절차' 같은 일반 필드를 고유명사로 취급하면
    # 오히려 더 완전한 전용 안내보다 짧은 페이지가 앞설 수 있다.
    if filters.task_key != "event.camp":
        return []
    terms = []
    for value in filters.keywords or []:
        normalized = _identity_text(value)
        if len(normalized) < 2 or normalized in IDENTITY_STOPWORDS or re.fullmatch(r"20\d{2}", normalized):
            continue
        terms.append(normalized)
    return list(dict.fromkeys(terms))


def _is_procedure_query(message: str) -> bool:
    normalized = (message or "").lower()
    compact = re.sub(r"\s+", "", normalized)
    return (
        any(term in normalized for term in (
            "방법", "절차", "순서", "어떻게", "어디서", "제출", "하는 법",
        ))
        or any(term in compact for term in ("하는법", "신청법", "접수법", "제출법"))
    )


def _specific_task_scope_rejection(message: str, task_key: str | None, unit_heading: str) -> str | None:
    """일반 수강신청 질문에 같은 업무키의 별도 선행·특례 일정을 섞지 않는다."""
    if task_key != "course.registration":
        return None
    query = re.sub(r"\s+", "", message)
    heading = re.sub(r"\s+", "", unit_heading)
    for label, terms in COURSE_REGISTRATION_DETAIL_SCOPES:
        if any(term in heading for term in terms) and not any(term in query for term in terms):
            return f"질문에 없는 수강신청 세부 일정: {label}"
    return None


def _period_scope_score(start, end, message: str) -> float:
    """기간 질문에서는 시작·종료가 모두 검증된 업무를 한쪽 날짜보다 우선한다."""
    if start and end:
        return 0.55
    if start or end:
        return 0.35 if any(term in message for term in ("마감", "기한", "까지", "시작", "부터")) else 0.05
    return -0.35


def _implausible_period_rejection(task_key: str | None, start, end, *, date_query: bool) -> str | None:
    """여러 학기·대상 일정을 하나의 시작/끝으로 합친 수강신청 구간을 배제한다."""
    if task_key != "course.registration" or not date_query or not start or not end:
        return None
    if end - start > timedelta(days=45):
        return "여러 수강신청 세부 일정을 합친 과도하게 긴 기간"
    return None


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


class HybridSearch:
    def __init__(self, db: Session, ai: AIService | None = None) -> None:
        self.db = db
        self.ai = ai or AIService()
        self.last_trace: list[dict] = []

    def _trace(self, candidate_id: str, notice: Notice, decision: str, reasons: list[str], score: float | None = None) -> None:
        entry = {
            "candidateId": candidate_id,
            "noticeId": notice.id,
            "title": notice.title,
            "decision": decision,
            "reasons": reasons,
            "score": score,
        }
        self.last_trace.append(entry)
        logger.info("search_candidate %s", entry)

    @staticmethod
    def _student_scope_rejection(message: str, filters: QueryFilters, title: str, targets: list[str] | None = None) -> str | None:
        normalized = re.sub(r"\s+", "", message)
        candidate = re.sub(r"\s+", "", f"{title} {' '.join(targets or [])}")
        requested_special = {
            label for label, terms in SPECIAL_STUDENT_TERMS.items()
            if any(re.sub(r"\s+", "", term) in normalized for term in terms)
        }
        target_values = [re.sub(r"\s+", "", value) for value in (targets or [])]
        has_general_target = any(
            marker in value
            for value in target_values
            for marker in ("재학생", "전체학생", "학부생", "대학생", "전체")
        )
        for label, terms in SPECIAL_STUDENT_TERMS.items():
            candidate_has = any(re.sub(r"\s+", "", term) in candidate for term in terms)
            title_has = any(re.sub(r"\s+", "", term) in re.sub(r"\s+", "", title) for term in terms)
            if candidate_has and label not in requested_special and (title_has or not has_general_target):
                return f"질문에 없는 특정 학생 대상: {label}"
        if filters.student_status and filters.student_status not in {"재학생", "일반"}:
            if filters.student_status not in candidate:
                return f"학생 구분 불일치: {filters.student_status}"
        return None

    def search(self, message: str, filters: QueryFilters, limit: int = 5) -> list[dict]:
        self.last_trace = []
        vector = self.ai.embedding(message)
        task_results = self._task_unit_search(message, filters, vector)
        structured_period_available = any(
            item["task_unit"].application_start or item["task_unit"].application_end
            for item in task_results
        )
        base = (
            select(Notice, NoticeMetadata, NoticeEmbedding, NoticeChunk)
            .join(NoticeMetadata, NoticeMetadata.notice_id == Notice.id)
            .join(NoticeEmbedding, NoticeEmbedding.notice_id == Notice.id)
            .outerjoin(NoticeChunk, NoticeChunk.notice_id == Notice.id)
            .where(Notice.is_archived.is_(False), Notice.ai_processed.is_(True))
        )
        stmt = base
        # 카테고리는 1차 후보군에만 적용하고 0건이면 완화 검색으로 다시 찾는다.
        if filters.category:
            stmt = stmt.where(NoticeMetadata.category == filters.category)
        rows = self.db.execute(stmt).all()
        if not rows and filters.category:
            rows = self.db.execute(base).all()

        results_by_candidate: dict[str, dict] = {}
        now = datetime.now(timezone.utc)
        normalized_message = message.lower()
        identity_terms = _identity_terms(filters)
        contact_query = any(term in normalized_message for term in ("전화", "연락처", "담당자", "담당 부서", "문의처", "어디에 문의"))
        procedure_query = _is_procedure_query(message)
        specialized_terms = ("창업", "입대", "군입대", "질병", "육아", "임신", "출산")
        semantic_embeddings = self.ai.embedding_provider in {"openai", "ollama"}
        vector_weight = 0.18 if semantic_embeddings else 0.05
        for notice, metadata, embedding, chunk in rows:
            candidate_id = f"notice:{notice.id}"
            if notice.source_type == "staff_directory" and not contact_query:
                self._trace(candidate_id, notice, "excluded", ["연락처 질문이 아닌 직원명부 후보"])
                continue
            if procedure_query and notice.source_type == "university_catalog":
                self._trace(candidate_id, notice, "excluded", [
                    "실행 절차 질문에는 대학요람·학칙보다 학생용 업무 안내를 우선함",
                ])
                continue
            title_years = {int(value) for value in re.findall(r"20\d{2}", notice.title)}
            if filters.academic_year and title_years and filters.academic_year not in title_years:
                self._trace(candidate_id, notice, "excluded", [f"학년도 불일치: 요청 {filters.academic_year}, 제목 {sorted(title_years)}"])
                continue
            if filters.academic_year and metadata.academic_year and metadata.academic_year != filters.academic_year:
                self._trace(candidate_id, notice, "excluded", [f"학년도 불일치: 요청 {filters.academic_year}, 구조화 {metadata.academic_year}"])
                continue
            title_semesters = {int(value) for value in re.findall(r"([12])\s*학기", notice.title)}
            if filters.semester and title_semesters and filters.semester not in title_semesters:
                self._trace(candidate_id, notice, "excluded", [f"학기 불일치: 요청 {filters.semester}, 제목 {sorted(title_semesters)}"])
                continue
            if filters.semester and metadata.semester and metadata.semester != filters.semester:
                self._trace(candidate_id, notice, "excluded", [f"학기 불일치: 요청 {filters.semester}, 구조화 {metadata.semester}"])
                continue
            student_rejection = self._student_scope_rejection(
                message, filters, notice.title, metadata.target_student_types,
            )
            if student_rejection:
                self._trace(candidate_id, notice, "excluded", [student_rejection])
                continue
            if "현장실습" in normalized_message and "현장실습" not in re.sub(r"\s+", "", notice.title):
                self._trace(candidate_id, notice, "excluded", ["현장실습 질문에 일반 인턴·채용 공고 제외"])
                continue
            if filters.task_key == "dormitory.apply" and not any(
                term in re.sub(r"\s+", "", notice.title) for term in ("기숙사", "생활관", "입사")
            ):
                self._trace(candidate_id, notice, "excluded", ["기숙사 질문에 다른 생활 안내 제외"])
                continue
            candidate_vector = chunk.embedding if chunk is not None else embedding.embedding
            candidate_model = chunk.embedding_model if chunk is not None else embedding.embedding_model
            query_model = getattr(self.ai, "embedding_model_name", None)
            # 서로 다른 임베딩 모델의 벡터는 좌표 공간 자체가 다르므로 코사인
            # 유사도를 계산하면 의미 없는 점수가 된다. 부분 재인덱싱 중에는
            # 현재 모델로 만든 문서만 의미 검색에 사용하고 나머지는 키워드,
            # 메타데이터, 최신성 점수로 안전하게 검색한다.
            compatible_embedding = bool(query_model and candidate_model == query_model)
            vector_score = cosine_similarity(vector, list(candidate_vector)) if compatible_embedding else 0.0
            title_text = notice.title.lower()
            identity_title = _identity_text(notice.title)
            identity_hits = sum(1 for term in identity_terms if term in identity_title)
            identity_coverage = identity_hits / max(len(identity_terms), 1)
            if identity_terms and identity_hits == 0:
                self._trace(candidate_id, notice, "excluded", [
                    "질문에 포함된 행사 고유명사가 공지 제목과 일치하지 않음",
                ])
                continue
            chunk_text = chunk.search_text if chunk is not None else ""
            text = (
                f"{notice.title} {' '.join(metadata.keywords or [])} "
                f"{' '.join(metadata.synonyms or [])} {metadata.search_text or ''} "
                f"{notice.content} {notice.attachment_text} {chunk_text}"
            ).lower()
            hits = sum(1 for keyword in filters.keywords if keyword.lower() in text)
            keyword_score = min(hits / max(len(filters.keywords), 1), 1.0)
            title_hits = sum(1 for keyword in filters.keywords if keyword.lower() in title_text)
            title_score = min(title_hits / max(len(filters.keywords), 1), 1.0)
            metadata_score = 0.0
            if filters.category and metadata.category == filters.category:
                metadata_score += 0.25
            if filters.sub_category and metadata.sub_category and filters.sub_category in metadata.sub_category:
                metadata_score += 0.25
            if filters.academic_year and metadata.academic_year == filters.academic_year:
                metadata_score += 0.1
            if filters.semester and metadata.semester == filters.semester:
                metadata_score += 0.1
            current_status = effective_status(notice)
            status_score = {"active": 0.15, "upcoming": 0.1, "always": 0.12, "unknown": 0.03, "expired": 0.0}.get(current_status, 0.0)
            if filters.time_scope == "current" and current_status == "expired":
                status_score -= 0.3
            freshness_score = 0.0
            if filters.time_scope == "current" and notice.source_type in {"official_notice", "event"}:
                published_at = notice.published_at
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
                age_days = max((now - published_at).days, 0)
                freshness_score = max(0.0, 1.0 - age_days / 365) * 0.12
            source_score = min(max(notice.source_priority, 0), 120) / 120 * 0.2
            if procedure_query and notice.source_type in {
                "academic_guide", "official_faq", "scholarship_guide", "student_service",
                "dormitory_guide", "international_guide",
            }:
                source_score += 0.2
            specialization_penalty = sum(
                0.18 for term in specialized_terms if term in title_text and term not in normalized_message
            )
            if filters.task_key == "leave.general" and "휴학" not in re.sub(r"\s+", "", notice.title):
                self._trace(candidate_id, notice, "excluded", ["공지 제목에 휴학 업무가 없음"])
                continue
            eligible_task, task_bonus, _ = candidate_task_score(
                message,
                title=notice.title,
                content=chunk.text if chunk is not None else f"{notice.content} {notice.attachment_text}",
            )
            if not eligible_task:
                self._trace(candidate_id, notice, "excluded", [_ or "세부 업무 불일치"])
                continue
            subcategory_match = False
            if filters.sub_category and not filters.task_key:
                subject = filters.sub_category.lower()
                authoritative_body_match = (
                    notice.source_type == "staff_directory"
                    and subject in text
                )
                if subject not in title_text and not authoritative_body_match:
                    subcategory_match = False
                else:
                    subcategory_match = True
            # 실임베딩을 쓰는 경우에는 표현이 전혀 겹치지 않는 자연어 질문도
            # 높은 의미 유사도가 확인되면 후보로 남긴다. 어휘 폴백에는 적용하지
            # 않아 해시 충돌이 근거 없는 답변으로 이어지는 것을 막는다.
            semantic_match = semantic_embeddings and compatible_embedding and vector_score >= 0.49
            if filters.keywords and hits == 0 and not subcategory_match and not semantic_match:
                self._trace(candidate_id, notice, "excluded", ["핵심어·세부업무·의미 유사도 근거 없음"])
                continue
            score = (
                vector_score * vector_weight + keyword_score * 0.32 + title_score * 0.30
                + metadata_score + status_score + freshness_score + source_score
                + task_bonus - specialization_penalty
            )
            if identity_terms:
                score += identity_coverage * 0.9
            if (
                filters.admission_year and notice.source_type == "university_catalog"
                and title_years and filters.admission_year not in title_years
            ):
                self._trace(candidate_id, notice, "excluded", [
                    f"입학년도 요람 불일치: 요청 {filters.admission_year}, 요람 {sorted(title_years)}",
                ])
                continue
            if (
                filters.time_scope == "current"
                and structured_period_available
                and not metadata.application_start
                and not metadata.application_end
            ):
                score -= 0.35
            item = {
                "notice": notice, "metadata": metadata, "score": round(score, 5),
                "chunk_text": chunk.text if chunk is not None else notice.content,
                "candidate_id": candidate_id,
                "hard_match_priority": 3 if identity_terms and identity_coverage >= 0.5 else (
                    2 if filters.admission_year and notice.source_type == "university_catalog"
                    and filters.admission_year in title_years else 0
                ),
                "selection_reasons": [reason for reason in (
                    "정확한 학년도" if filters.academic_year and metadata.academic_year == filters.academic_year else None,
                    "정확한 학기" if filters.semester and metadata.semester == filters.semester else None,
                    "업무 표현 일치" if task_bonus > 0 else None,
                    "제목 핵심어 일치" if title_score > 0 else None,
                ) if reason],
            }
            previous = results_by_candidate.get(candidate_id)
            if previous is None or item["score"] > previous["score"]:
                results_by_candidate[candidate_id] = item
        # 근거가 약한 결과를 억지로 답변에 사용하지 않는다.
        # 같은 공지에서 업무 단위 근거가 있으면 문서 전체 청크보다 우선한다.
        for item in task_results:
            results_by_candidate[item["candidate_id"]] = item
        results = sorted(
            results_by_candidate.values(),
            key=lambda item: (
                item.get("hard_match_priority", 0), item["score"],
                item["notice"].source_priority, item["notice"].published_at,
            ),
            reverse=True,
        )
        filtered_results = [item for item in results if item["score"] >= 0.34][:limit]
        for item in filtered_results:
            self._trace(
                item["candidate_id"], item["notice"], "selected",
                item.get("selection_reasons") or ["복합 검색 점수 통과"], item["score"],
            )
        if not filtered_results and filters.category:
            return self.search(message, filters.model_copy(update={"category": None}), limit)
        return filtered_results

    def _task_unit_search(self, message: str, filters: QueryFilters, vector: list[float]) -> list[dict]:
        stmt = (
            select(TaskUnit, KnowledgeTask, TaskUnitEmbedding, Notice, NoticeMetadata)
            .join(KnowledgeTask, TaskUnit.task_id == KnowledgeTask.id)
            .join(TaskUnitEmbedding, TaskUnitEmbedding.task_unit_id == TaskUnit.id)
            .join(Notice, TaskUnit.notice_id == Notice.id)
            .join(NoticeMetadata, NoticeMetadata.notice_id == Notice.id)
            .where(Notice.is_archived.is_(False), Notice.ai_processed.is_(True))
        )
        if filters.category:
            stmt = stmt.where(KnowledgeTask.category == filters.category)
        rows = self.db.execute(stmt).all()
        semantic_embeddings = self.ai.embedding_provider in {"openai", "ollama"}
        query_model = getattr(self.ai, "embedding_model_name", None)
        normalized_message = message.lower()
        identity_terms = _identity_terms(filters)
        procedure_query = _is_procedure_query(message)
        requested_fields = set(getattr(filters, "requested_fields", []) or [])
        date_query = bool(
            requested_fields.intersection({"application_period", "date", "schedule"})
            or any(term in normalized_message for term in ("기간", "일정", "일자", "언제", "날짜", "몇 일", "며칠"))
        )
        current_year = datetime.now(timezone.utc).year
        results: list[dict] = []
        for unit, task, embedding, notice, metadata in rows:
            candidate_id = f"task:{unit.id}"
            if procedure_query and notice.source_type == "university_catalog":
                self._trace(candidate_id, notice, "excluded", [
                    "실행 절차 질문에는 대학요람·학칙보다 학생용 업무 안내를 우선함",
                ])
                continue
            title_years = {int(value) for value in re.findall(r"20\d{2}", notice.title)}
            if filters.academic_year and title_years and filters.academic_year not in title_years:
                self._trace(candidate_id, notice, "excluded", [f"학년도 불일치: 요청 {filters.academic_year}, 제목 {sorted(title_years)}"])
                continue
            if filters.academic_year and unit.academic_year and unit.academic_year != filters.academic_year:
                self._trace(candidate_id, notice, "excluded", [f"학년도 불일치: 요청 {filters.academic_year}, 업무 {unit.academic_year}"])
                continue
            title_semesters = {int(value) for value in re.findall(r"([12])\s*학기", notice.title)}
            if filters.semester and title_semesters and filters.semester not in title_semesters:
                self._trace(candidate_id, notice, "excluded", [f"학기 불일치: 요청 {filters.semester}, 제목 {sorted(title_semesters)}"])
                continue
            if filters.semester and unit.semester and unit.semester != filters.semester:
                self._trace(candidate_id, notice, "excluded", [f"학기 불일치: 요청 {filters.semester}, 업무 {unit.semester}"])
                continue
            student_rejection = self._student_scope_rejection(
                message, filters, notice.title, unit.target_student_types,
            )
            if student_rejection:
                self._trace(candidate_id, notice, "excluded", [student_rejection])
                continue
            requested_documents = bool(requested_fields.intersection({"required_documents", "documents"}))
            if "학사일정" in notice.title and (
                requested_documents or procedure_query
                or (task.task_key.startswith("graduation.") and not date_query)
            ):
                self._trace(candidate_id, notice, "excluded", ["달력 근거는 절차·서류·요건의 주 근거로 사용하지 않음"])
                continue
            actual_heading = re.sub(r"\s+", "", f"{notice.title} {unit.section_title or ''}")
            canonical_from_heading = detect_task(actual_heading)
            candidate_task_key = (
                task.task_key if task.task_key in TASK_BY_KEY
                else (canonical_from_heading.key if canonical_from_heading else task.task_key)
            )
            graduation_source_role = _graduation_source_role(
                message, candidate_task_key, notice.title,
                f"{unit.title} {unit.section_title or ''}", notice.source_type,
            )
            if graduation_source_role == "reject_reorganization":
                self._trace(candidate_id, notice, "excluded", [
                    "일반 졸업요건 질문에는 학사구조개편 경과조치를 주 근거로 사용하지 않음",
                ])
                continue
            tuition_source_role = _tuition_source_role(
                message, candidate_task_key, notice.title, notice.source_type,
            )
            if tuition_source_role == "reject_special_case":
                self._trace(candidate_id, notice, "excluded", [
                    "일반 등록금 질문에는 휴학 FAQ·계절수업 납부를 주 근거로 사용하지 않음",
                ])
                continue
            detail_rejection = _specific_task_scope_rejection(
                message, candidate_task_key, f"{unit.title} {unit.section_title or ''}",
            )
            if detail_rejection:
                self._trace(candidate_id, notice, "excluded", [detail_rejection])
                continue
            period_rejection = _implausible_period_rejection(
                candidate_task_key, unit.application_start, unit.application_end,
                date_query=date_query,
            )
            if period_rejection:
                self._trace(candidate_id, notice, "excluded", [period_rejection])
                continue
            if "교환학생" in normalized_message and "교환학생" not in actual_heading:
                self._trace(candidate_id, notice, "excluded", ["교환학생 질문에 복수학위 등 다른 해외파견 업무 제외"])
                continue
            if "현장실습" in normalized_message:
                compact_title = re.sub(r"\s+", "", notice.title)
                # 구조화/OCR 구간에 '현장실습 창업형'이라는 설명이 있어도
                # 공지의 실제 업무가 창업 대체학점이면 일반 현장실습 신청의
                # 주 근거가 될 수 없다. 기간성 공지는 제목에 업무가 명시된
                # 경우만 허용하고, 정적 안내는 하위 섹션 제목을 허용한다.
                if (
                    notice.source_type in {"official_notice", "event"}
                    and "현장실습" not in compact_title
                ) or "창업대체학점" in compact_title:
                    self._trace(candidate_id, notice, "excluded", [
                        "일반 현장실습 질문에 창업 대체학점·다른 실습 업무 제외",
                    ])
                    continue
            eligible, task_bonus, _ = candidate_task_score(
                message, title=f"{unit.title} {unit.section_title or ''} {notice.title}",
                content=unit.content, task_key=candidate_task_key, aliases=task.aliases,
            )
            if not eligible:
                self._trace(candidate_id, notice, "excluded", [_ or "세부 업무 불일치"])
                continue
            # 구조화 모델이 대상 설명의 '휴학생'을 실제 휴학 업무로 잘못
            # 분류한 과거 데이터는 생성된 unit.title만으로 통과시키지 않는다.
            # 실제 공지/구간 제목에 휴학 업무가 명시돼야 일반휴학 후보가 된다.
            if candidate_task_key == "leave.general" and "휴학" not in actual_heading:
                self._trace(candidate_id, notice, "excluded", ["실제 공지·구간 제목에 휴학 업무가 없음"])
                continue
            if filters.admission_year:
                if notice.source_type == "university_catalog" and title_years and filters.admission_year not in title_years:
                    self._trace(candidate_id, notice, "excluded", [
                        f"입학년도 요람 불일치: 요청 {filters.admission_year}, 요람 {sorted(title_years)}",
                    ])
                    continue
                if unit.admission_year_start and filters.admission_year < unit.admission_year_start:
                    self._trace(candidate_id, notice, "excluded", [f"입학년도 범위 이전: {filters.admission_year}"])
                    continue
                if unit.admission_year_end and filters.admission_year > unit.admission_year_end:
                    self._trace(candidate_id, notice, "excluded", [f"입학년도 범위 이후: {filters.admission_year}"])
                    continue
            compatible = bool(query_model and embedding.embedding_model == query_model)
            vector_score = cosine_similarity(vector, list(embedding.embedding)) if compatible else 0.0
            searchable = unit.search_text.lower()
            hits = sum(1 for keyword in filters.keywords if keyword.lower() in searchable)
            keyword_score = min(hits / max(len(filters.keywords), 1), 1.0)
            title_text = f"{unit.title} {unit.section_title or ''} {notice.title}".lower()
            identity_title = _identity_text(f"{notice.title} {unit.section_title or ''}")
            identity_hits = sum(1 for term in identity_terms if term in identity_title)
            identity_coverage = identity_hits / max(len(identity_terms), 1)
            if identity_terms and identity_hits == 0:
                self._trace(candidate_id, notice, "excluded", [
                    "질문에 포함된 행사 고유명사가 공지 제목과 일치하지 않음",
                ])
                continue
            title_hits = sum(1 for keyword in filters.keywords if keyword.lower() in title_text)
            title_score = min(title_hits / max(len(filters.keywords), 1), 1.0)
            semantic_match = semantic_embeddings and compatible and vector_score >= 0.47
            exact_task = bool(filters.task_key and filters.task_key == candidate_task_key)
            if filters.keywords and not hits and not semantic_match and not exact_task:
                self._trace(candidate_id, notice, "excluded", ["핵심어·정확 업무키·의미 유사도 근거 없음"])
                continue
            scope_score = 0.0
            if filters.academic_year and unit.academic_year == filters.academic_year:
                scope_score += 0.1
            if filters.semester and unit.semester == filters.semester:
                scope_score += 0.08
            has_admission_range = bool(unit.admission_year_start or unit.admission_year_end)
            if filters.admission_year and has_admission_range:
                # 범위 밖 후보는 위에서 이미 제거했다. 남은 명시 범위는
                # 입학년도 없는 일반 설명보다 강하게 우선한다.
                scope_score += 0.55
            elif filters.admission_year and str(filters.admission_year) in searchable:
                scope_score += 0.2
            if (
                filters.admission_year and notice.source_type == "university_catalog"
                and filters.admission_year in title_years
            ):
                scope_score += 0.9
            # 학년도를 생략한 일정 질문은 날짜가 실제로 구조화된 최신
            # 공지를 우선한다. 과거 공지의 제목 유사도만 높아 기간 없는
            # 후보가 먼저 선택되는 것을 막는다.
            if date_query:
                scope_score += _period_scope_score(unit.application_start, unit.application_end, normalized_message)
            if not filters.academic_year and unit.academic_year:
                if unit.academic_year == current_year:
                    scope_score += 0.18
                elif unit.academic_year == current_year - 1:
                    scope_score += 0.08
            current_status = effective_status(notice)
            if unit.application_start or unit.application_end:
                start = unit.application_start
                end = unit.application_end
                if start and start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                if end and end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                current = datetime.now(timezone.utc)
                if start and current < start:
                    current_status = "upcoming"
                elif end and current > end:
                    current_status = "expired"
                else:
                    current_status = "active"
            status_score = {"active": 0.12, "upcoming": 0.08, "always": 0.14, "unknown": 0.02}.get(current_status, 0.0)
            if filters.time_scope == "current" and current_status == "expired":
                status_score -= 0.25
            if filters.time_scope == "current":
                status_score += {
                    "active": 0.8, "upcoming": -0.35, "always": -0.2,
                    "unknown": -0.55, "expired": -0.8,
                }.get(current_status, -0.55)
            source_score = min(max(notice.source_priority, 0), 120) / 120 * 0.18
            has_document_fact = any(
                fact.fact_type in {"required_documents", "documents"}
                or any(term in normalize for term in ("구비서류", "준비서류", "제출서류"))
                for fact in unit.facts
                for normalize in [re.sub(r"\s+", "", f"{fact.label} {fact.value}")]
            )
            if requested_documents:
                source_score += 0.6 if has_document_fact else -0.4
            if procedure_query:
                source_score += 0.25 if unit.procedure is not None else -0.2
                # 방법·절차를 묻는 질문에서는 상시 제도 설명보다 실제 신청
                # 기간과 절차가 함께 구조화된 최신 공지를 우선한다. 마감된
                # 공지라면 ChatService가 마감 상태를 명확히 표시한다.
                if (
                    unit.procedure is not None
                    and notice.source_type in {"official_notice", "event"}
                    and (unit.application_start or unit.application_end)
                ):
                    source_score += 0.2
            if (procedure_query or requested_documents) and notice.source_type == "university_catalog":
                source_score -= 0.65
            if procedure_query and current_status == "expired":
                source_score -= 0.45
            canonical_leave_procedure = bool(
                procedure_query
                and candidate_task_key == "leave.general"
                and notice.source_type == "academic_guide"
                and unit.procedure is not None
                and "휴학" in actual_heading
                and any(term in actual_heading for term in ("일반휴학", "휴학신청", "상시학사안내휴학"))
            )
            if canonical_leave_procedure:
                source_score += 1.2
            score = (
                vector_score * (0.22 if semantic_embeddings else 0.05)
                + keyword_score * 0.25 + title_score * 0.22 + task_bonus
                + scope_score + status_score + source_score
            )
            if graduation_source_role == "canonical_guide":
                score += 0.85
            if tuition_source_role == "canonical_guide":
                score += 0.85
            if len(identity_terms) >= 2:
                score += identity_coverage * 0.9
            exact_identity = bool(identity_terms and identity_coverage >= 0.5)
            catalog_cohort = bool(
                filters.admission_year and notice.source_type == "university_catalog"
                and filters.admission_year in title_years
            )
            exact_academic_notice = bool(
                exact_task and notice.source_type == "official_notice"
                and filters.academic_year and unit.academic_year == filters.academic_year
                and filters.semester and unit.semester == filters.semester
            )
            results.append({
                "notice": notice,
                "metadata": metadata,
                "task_unit": unit,
                "canonical_task_key": candidate_task_key,
                "score": round(score, 5),
                "chunk_text": unit.content,
                "candidate_id": candidate_id,
                "hard_match_priority": 4 if (
                    graduation_source_role == "canonical_guide" or tuition_source_role == "canonical_guide"
                    or canonical_leave_procedure
                ) else (
                    3 if (exact_identity or exact_academic_notice) else (
                    2 if catalog_cohort else (1 if exact_task else 0)
                    )
                ),
                "selection_reasons": [reason for reason in (
                    "정확한 업무키" if exact_task else None,
                    "정확한 학년도" if filters.academic_year and unit.academic_year == filters.academic_year else None,
                    "정확한 학기" if filters.semester and unit.semester == filters.semester else None,
                    "입학년도 범위 일치" if filters.admission_year and has_admission_range else None,
                    "졸업 전용 학사안내" if graduation_source_role == "canonical_guide" else None,
                    "등록금 전용 학사안내" if tuition_source_role == "canonical_guide" else None,
                    "질문한 기간 근거 보유" if date_query and (unit.application_start or unit.application_end) else None,
                    "현재 학년도" if not filters.academic_year and unit.academic_year == current_year else None,
                    "검증된 절차 보유" if procedure_query and unit.procedure is not None else None,
                    "학생용 일반휴학 절차 안내" if canonical_leave_procedure else None,
                ) if reason],
            })
        return sorted(
            results,
            key=lambda item: (
                item.get("hard_match_priority", 0), item["score"],
                item["notice"].source_priority, item["notice"].published_at,
            ),
            reverse=True,
        )
