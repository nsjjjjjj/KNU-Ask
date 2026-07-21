from app.schemas import DepartmentInfo, QueryFilters
from app.services.ai import AIService
from app.services.chat import (
    ChatService, _compact_procedure_steps, _plain_answer,
    _event_camp_eligibility_facts, _relative_application_period, _requested_graduation_cohort,
    _step_link_label, _student_relevant_warning,
)
from app.services.notice_status import KST, _period_status, effective_status, effective_status_label
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from sqlalchemy import select
from app.models import ChatSessionContext, DataGap, Notice, StaffDirectoryContact
from app.services.processing import NoticeProcessor
from app.services.search import HybridSearch
from app.services.search.hybrid import (
    _graduation_source_role, _identity_terms, _implausible_period_rejection, _is_procedure_query, _period_scope_score,
    _specific_task_scope_rejection, _tuition_source_role,
)
from app.services.search.task_rules import candidate_task_score
from app.services.search.task_rules import detect_task, detect_tasks
from app.services.search.task_rules import visible_student_step


def test_no_data_question_returns_required_sentence(db):
    response = ChatService(db).answer("교내 수영장 수온이 몇 도야?")
    assert response.has_data is False
    assert response.status == "no_result"
    assert "답할 근거를 찾지 못했습니다" in response.answer
    assert response.answer_mode == "department_handoff"
    gap = db.scalar(select(DataGap).where(DataGap.gap_type == "missing_search_evidence"))
    assert gap is not None
    assert "question" not in gap.context


def test_where_to_submit_is_treated_as_a_procedure_question():
    assert ChatService._wants_action_guide("예비군 신고는 어디에 제출해?") is True
    assert ChatService._usable_application_method("근거서류를 첨부해 본인이 직접 신고한다") is True


def test_verified_barun_url_has_a_clear_step_button_label():
    assert _step_link_label("https://barun.kyonggi.ac.kr/") == "경기대 Barun 열기"


def test_bare_leave_question_requests_a_specific_intent_without_search(db):
    response = ChatService(db).answer("휴학")

    assert response.status == "clarification_required"
    assert response.has_data is False
    assert response.matched_notices == []
    assert response.clarification_options == [
        "휴학 신청 방법",
        "휴학 신청 기간",
        "휴학 종류와 조건",
        "복학 방법",
        "휴학 담당 부서",
    ]


def test_eligibility_question_does_not_expand_the_full_application_guide():
    assert ChatService._wants_action_guide("경기대 크래프톤 지원자격 알려줘") is False


def test_event_camp_eligibility_falls_back_to_collected_pdf_text():
    facts = _event_camp_eligibility_facts(
        "□(지원자격) 참여 대상: 강남대 등 인근 대학 재학생(학년, 전공 무관) "
        "컴퓨터공학 전공 또는 STEM 관련 학습 경험이 있는 2학년 이상 권장. "
        "하나 이상의 프로그래밍 언어 사용 경험이 있는 사람. "
        "팀 프로젝트로 웹서비스 개발을 경험해보고 싶은 사람. "
        "개발자로서의 커리어를 꿈꾸는 사람."
    )

    assert [fact.label for fact in facts] == [
        "지원 대상", "권장 대상", "프로그래밍 경험", "프로젝트 관심", "진로 관심",
    ]
    assert "강남대" in facts[0].value


def test_compact_doing_how_expression_is_treated_as_procedure_request():
    plan = AIService().analyze_query("휴학신청하는법")

    assert ChatService._wants_action_guide("휴학신청하는법") is True
    assert _is_procedure_query("휴학신청하는법") is True
    assert "procedure" in plan.requested_fields
    assert "procedure" in plan.required_facts


def test_compact_leave_procedure_returns_student_steps_not_catalog(db):
    response = ChatService(db).answer("휴학신청하는법")

    assert response.answer_mode == "action_guide"
    assert response.action_guide is not None
    assert len(response.action_guide.steps) >= 3
    assert "시스템" in " ".join(
        f"{step.title} {step.description}" for step in response.action_guide.steps
    )
    assert all("대학요람" not in notice.title and "규정학칙" not in notice.title for notice in response.matched_notices)
    assert "공식 자료를 찾았습니다" not in response.answer
    assert response.next_action is None


def test_long_application_flow_is_compacted_and_internal_follow_up_is_hidden():
    steps = [SimpleNamespace(
        title=f"{index}단계", description=f"{index}번 행동", action_type="navigate",
        action_url=None, source_type="html", source_locator=f"step:{index}", confidence=0.9,
    ) for index in range(1, 10)]
    steps.append(SimpleNamespace(
        title="합격 통보 확인", description="기업 심사 결과를 확인한다.", action_type="verify",
        action_url=None, source_type="html", source_locator="step:10", confidence=0.9,
    ))

    compacted = _compact_procedure_steps(steps)

    assert len(compacted) == 5
    assert all("합격 통보" not in step.title for step in compacted)
    assert _student_relevant_warning("학생 제출 후 지원센터의 승인 처리가 필요하다.") is False


def test_yes_no_application_question_does_not_require_invented_steps():
    assert ChatService._wants_action_guide("성적우수장학금은 따로 신청해야 해?") is False


def test_dormitory_heading_maps_to_canonical_task():
    assert detect_task("[상시 안내] 심전생활관 입사안내").key == "dormitory.apply"


def test_static_guide_stays_always_even_when_it_contains_semester_event_dates():
    guide = SimpleNamespace(
        metadata_record=SimpleNamespace(
            application_start=None, application_end=None,
            event_start=datetime.fromisoformat("2026-03-01T00:00:00+09:00"),
            event_end=datetime.fromisoformat("2026-06-30T23:59:00+09:00"),
            action_type="안내",
        ),
        notice_status="always",
        action_guide=None,
    )
    assert effective_status(guide, datetime.fromisoformat("2026-07-21T12:00:00+09:00")) == "always"


def test_repeated_missing_field_questions_are_aggregated_without_raw_chat(db):
    service = ChatService(db)
    service.answer("휴학 담당자 이메일 알려줘")
    service.answer("휴학 담당자 이메일 알려줘")

    gaps = db.scalars(select(DataGap).where(DataGap.field_name.in_({"contact_person", "department_email"}))).all()

    assert gaps
    assert all(gap.occurrence_count == 2 for gap in gaps)
    assert all("휴학 담당자 이메일 알려줘" not in str(gap.context) for gap in gaps)


def test_metadata_category_filter(db):
    filters = QueryFilters(category="등록", sub_category="등록금", keywords=["등록금", "납부"])
    results = HybridSearch(db, AIService()).search("등록금 납부 기간", filters)
    assert results
    assert all(item["metadata"].category == "등록" for item in results)


def test_date_query_prefers_current_notice_with_period_over_older_notice_without_period(db):
    NoticeProcessor(db).upsert({
        "source_id": "tuition-2025-no-period",
        "source_type": "official_notice",
        "source_url": "https://web.kangnam.ac.kr/notice/tuition-2025-no-period",
        "title": "2025학년도 2학기 등록금 납부 안내",
        "content": "등록금 고지서를 확인한 뒤 가상계좌로 납부합니다.",
        "published_at": datetime.fromisoformat("2026-07-21T09:00:00+09:00"),
        "department_name": "재무팀",
        "attachment_names": [], "attachment_urls": [], "content_links": [],
    })
    db.commit()

    response = ChatService(db).answer("등록금 납부 기간 알려줘")

    assert response.matched_notices[0].title == "2026학년도 2학기 등록금 납부 안내"
    assert response.status != "insufficient_evidence"


def test_vector_search_returns_relevant_notice(db):
    filters = AIService().analyze_query("휴학 신청은 언제까지야?")
    results = HybridSearch(db, AIService()).search("휴학 신청은 언제까지야?", filters)
    assert results
    assert "휴학" in results[0]["notice"].title
    assert "창업휴학" not in results[0]["notice"].title


def test_general_internship_excludes_startup_credit_notice_even_when_body_mentions_internship(db):
    processor = NoticeProcessor(db)
    common = {
        "source_type": "official_notice",
        "published_at": datetime.fromisoformat("2026-07-21T09:00:00+09:00"),
        "department_name": "대학일자리플러스센터",
        "attachment_names": [], "attachment_urls": [], "content_links": [],
    }
    processor.upsert({
        **common,
        "source_id": "startup-credit-regression",
        "source_url": "https://web.kangnam.ac.kr/notice/startup-credit",
        "title": "[창업] 창업 대체학점 제도 및 신청 안내",
        "content": "현장실습 창업형 단기 과정은 창업 대체학점으로 인정됩니다.",
    })
    processor.upsert({
        **common,
        "source_id": "internship-regression",
        "source_url": "https://web.kangnam.ac.kr/notice/internship",
        "title": "[현장실습] 현장실습학기제 참가 학생 모집",
        "content": "현장실습 온라인 시스템에서 참가 신청서를 제출합니다.",
    })
    db.commit()

    results = HybridSearch(db, AIService()).search(
        "현장실습은 어떻게 신청해?",
        QueryFilters(task_key="career.internship", keywords=["현장실습", "신청"]),
    )

    assert results
    assert "[현장실습]" in results[0]["notice"].title
    assert all("창업 대체학점" not in item["notice"].title for item in results)


def test_real_semantic_embedding_can_rescue_a_paraphrase_without_keyword_overlap(db):
    class SemanticAI:
        embedding_provider = "ollama"
        embedding_model_name = "test-semantic"

        @staticmethod
        def embedding(_message):
            return [1.0, 0.0]

    leave = db.scalar(select(Notice).where(Notice.title.contains("휴학 신청 안내")))
    for notice in db.scalars(select(Notice)):
        vector = [1.0, 0.0] if notice.id == leave.id else [0.0, 1.0]
        notice.embedding_record.embedding = vector
        notice.embedding_record.embedding_model = "test-semantic"
        for chunk in notice.chunks:
            chunk.embedding = vector
            chunk.embedding_model = "test-semantic"
    db.commit()

    results = HybridSearch(db, SemanticAI()).search(
        "학교를 잠시 쉬고 싶어요",
        QueryFilters(keywords=["잠시", "쉬고"]),
    )

    assert results
    assert results[0]["notice"].id == leave.id


def test_mock_query_removes_particles_and_question_words():
    filters = AIService().analyze_query("휴학은 어떻게 신청해?")

    assert filters.keywords[:2] == ["휴학", "신청"]
    assert "어떻게" not in filters.keywords


def test_query_separates_admission_year_from_academic_year():
    filters = AIService().analyze_query("2024년도 입학생 졸업요건 알려줘")

    assert filters.task_key == "graduation.requirements"
    assert filters.admission_year == 2024
    assert filters.academic_year is None
    assert filters.grade is None


def test_general_graduation_rejects_early_graduation_candidate():
    eligible, _, reason = candidate_task_score(
        "2024년도 입학생 졸업요건",
        title="조기졸업 신청 안내",
        content="조기졸업 대상자는 6학기 이상 등록해야 합니다.",
        task_key="graduation.early",
    )

    assert eligible is False
    assert reason


def test_general_leave_rejects_medical_leave_candidate():
    eligible, _, reason = candidate_task_score(
        "휴학 신청 기간과 준비서류 알려줘",
        title="질병휴학 신청",
        content="질병휴학은 상시 신청하며 진단서가 필요합니다.",
        task_key="leave.general",
    )

    assert eligible is False
    assert reason == "질문과 다른 세부 업무 제목"


def test_relative_application_window_is_kept_as_period_evidence():
    unit = SimpleNamespace(
        facts=[SimpleNamespace(
            fact_type="application_window", value="휴학신청기간 또는 개강일로부터 4주 이내",
            source_locator="HTML section:일반휴학",
        )],
        evidence=[],
    )

    value, locator = _relative_application_period(unit)

    assert value == "휴학신청기간 또는 개강일로부터 4주 이내"
    assert locator == "HTML section:일반휴학"


def test_internal_approval_status_is_not_a_numbered_student_step():
    step = SimpleNamespace(
        actor="student", student_action_required=True,
        title="처리 상태 확인", description="학과장 승인 후 최종 결재완료 여부를 확인합니다.",
    )

    assert visible_student_step(step) is False


def test_leave_search_rejects_notice_that_only_mentions_leave_as_student_status(db):
    NoticeProcessor(db).upsert({
        "source_id": "job-club-for-students-on-leave",
        "source_type": "official_notice",
        "source_url": "https://web.kangnam.ac.kr/notice/job-club",
        "title": "공기업 취업동아리 모집",
        "content": "4학년 재학생과 휴학생을 모집합니다. 네이버폼으로 신청하세요.",
        "published_at": datetime.fromisoformat("2026-07-21T09:00:00+09:00"),
        "department_name": "대학일자리플러스센터",
        "attachment_names": [], "attachment_urls": [], "content_links": [],
    })
    db.commit()

    results = HybridSearch(db, AIService()).search(
        "휴학 신청 기간 알려줘", AIService().analyze_query("휴학 신청 기간 알려줘"), 8,
    )

    assert results
    assert all(item["notice"].title != "공기업 취업동아리 모집" for item in results)


def test_general_course_registration_rejects_seasonal_course_candidate():
    eligible, _, reason = candidate_task_score(
        "수강신청 방법 알려줘",
        title="2026학년도 여름 계절수업 수강신청 안내",
        content="계절수업 수강신청은 종합정보시스템에서 진행합니다.",
        task_key="course.registration",
    )

    assert eligible is False
    assert reason


def test_general_tuition_rejects_seasonal_course_fee_candidate():
    eligible, _, reason = candidate_task_score(
        "등록금 납부 기간 알려줘",
        title="여름 계절수업 수강료 납부 안내",
        content="계절수업 수강료를 납부합니다.",
        task_key="tuition.payment",
    )

    assert eligible is False
    assert reason


def test_grade_check_and_appeal_are_canonical_tasks_not_credit_abandonment():
    tasks = detect_tasks("이번 학기 성적 확인과 이의신청 기간 알려줘")

    assert {task.key for task in tasks} == {"grade.check", "grade.appeal"}
    assert detect_task("성적 확인 기간 알려줘").key == "grade.check"


def test_general_tuition_prefers_registration_guide_over_leave_faq():
    assert _tuition_source_role(
        "등록금 고지서 출력과 납부 방법 알려줘", "tuition.payment",
        "[공식 휴복학 FAQ] 휴학하려 하는데 등록금을 납부해야 하나요?", "official_faq",
    ) == "reject_special_case"
    assert _tuition_source_role(
        "등록금 고지서 출력과 납부 방법 알려줘", "tuition.payment",
        "[상시 학사안내] 등록안내", "academic_guide",
    ) == "canonical_guide"


def test_graduation_admission_range_can_be_grounded_in_fact_evidence():
    unit = SimpleNamespace(
        application_start=None,
        application_end=None,
        content="입학년도별 졸업요건표를 확인하세요.",
        search_text="",
        summary="졸업요건 안내",
        task=SimpleNamespace(task_key="graduation.requirements"),
        facts=[SimpleNamespace(value="2021~2024학년도 졸업이수학점표")],
        evidence=[],
    )
    answer = ChatService._deterministic_answer(
        "2024년도 입학생 졸업요건 알려줘",
        {
            "notice": SimpleNamespace(title="졸업요건 안내"),
            "metadata": SimpleNamespace(application_start=None, application_end=None),
            "task_unit": unit,
        },
        DepartmentInfo(),
    )

    assert "2021~2024학년도 졸업이수학점표" in answer


def test_graduation_year_range_is_treated_as_admission_cohort():
    filters = AIService().analyze_query("2021~2024학년도의 졸업요건 알려줘")

    assert filters.task_key == "graduation.requirements"
    assert filters.admission_year == 2024
    assert filters.academic_year is None
    assert _requested_graduation_cohort("2021~2024학년도의 졸업요건") == (2021, 2024)


def test_general_graduation_rejects_reorganization_source_but_explicit_query_keeps_it():
    assert _graduation_source_role(
        "2021~2024학년도 졸업요건 알려줘", "graduation.requirements",
        "[상시 학사안내] 학사구조개편 관련 학사제도 안내", "편제변경 후 졸업요건 확인",
        "academic_guide",
    ) == "reject_reorganization"
    assert _graduation_source_role(
        "학사구조개편 후 졸업요건 알려줘", "graduation.requirements",
        "[상시 학사안내] 학사구조개편 관련 학사제도 안내", "편제변경 후 졸업요건 확인",
        "academic_guide",
    ) == "neutral"
    assert _graduation_source_role(
        "2021~2024학년도 졸업요건 알려줘", "graduation.requirements",
        "[상시 학사안내] 졸업", "졸업요건 확인 졸업요건 및 졸업 이수과목·학점",
        "academic_guide",
    ) == "canonical_guide"


def test_graduation_facts_show_requested_cohort_and_hide_other_year_rows():
    unit = SimpleNamespace(
        application_start=None,
        application_end=None,
        content="입학년도별 졸업요건표를 확인하세요.",
        search_text="2021~2024학년도 기준이 별도 PDF로 제공됨",
        task=SimpleNamespace(task_key="graduation.requirements"),
        evidence=[],
        facts=[
            SimpleNamespace(label="2012학년도 이전 입학자 총 졸업학점", value="총 140학점"),
            SimpleNamespace(label="최소 졸업학점", value="130학점 이상, 2012학년도 이전은 140학점"),
            SimpleNamespace(label="최소 등록학기", value="8학기 이상 등록"),
        ],
    )

    facts, _ = ChatService._answer_presentation(
        "2021~2024학년도의 졸업요건 알려줘", SimpleNamespace(
            application_start=None, application_end=None, application_location=None,
        ), False, unit,
    )

    rendered = " ".join(f"{fact.label} {fact.value}" for fact in facts)
    assert "2021~2024학년도 입학자 졸업이수 기준" in rendered
    assert "2012학년도 이전 입학자 총 졸업학점" not in rendered
    assert "신청 장소" not in rendered
    assert "최소 등록학기" in rendered


def test_graduation_open_ended_cohort_uses_its_specific_table():
    unit = SimpleNamespace(
        application_start=None, application_end=None,
        content="입학년도별 졸업요건표", search_text="2025학년도 이후 기준이 별도 PDF로 제공됨",
        summary="졸업요건 안내", task=SimpleNamespace(task_key="graduation.requirements"),
        facts=[SimpleNamespace(label="최소 졸업학점", value="130학점 이상")], evidence=[],
    )

    answer = ChatService._deterministic_answer(
        "2026학년도 입학생 졸업요건 알려줘",
        {
            "notice": SimpleNamespace(title="졸업 안내"),
            "metadata": SimpleNamespace(application_start=None, application_end=None),
            "task_unit": unit,
        },
        DepartmentInfo(),
    )

    assert "2025학년도 이후 졸업이수학점표" in answer


def test_more_specific_task_name_wins_when_title_contains_two_tasks():
    task = detect_task("학점등록대상자 2026-2학기 수강신청 및 등록금 납부 안내")

    assert task.key == "tuition.credit"


def test_credit_registration_payment_is_separate_from_course_registration():
    task = detect_task("학점등록대상자 등록금 납부 방법")

    assert task.key == "tuition.credit.payment"


def test_credit_registration_combined_question_uses_parent_workflow():
    task = detect_task("학점등록대상자 수강신청과 등록금 납부 절차 알려줘")

    assert task.key == "tuition.credit"


def test_generic_reserve_report_question_uses_defer_workflow():
    task = detect_task("예비군 신고는 어디에 제출해?")

    assert task.key == "reserve.defer"


def test_reserve_transfer_question_stays_in_transfer_workflow():
    task = detect_task("학생예비군 전입신고 방법")

    assert task.key == "reserve.transfer"


def test_graduate_career_program_is_not_general_graduation_requirements():
    assert detect_task("졸업생특화프로그램 참여자 모집") is None


def test_long_attachment_excerpt_centers_requested_admission_year():
    text = (
        "이전 입학년도 기준 " + ("과거 기준 " * 600)
        + "첨부파일 2021~2024학년도.pdf: 2024학년도 입학자는 최소졸업학점 130학점"
        + (" 후속 내용" * 600)
    )

    excerpt = AIService._relevant_excerpt(text, "2024년도 입학생 졸업요건", 600)

    assert "2024학년도 입학자" in excerpt
    assert "최소졸업학점 130학점" in excerpt


def test_multi_field_question_with_clear_subject_skips_local_query_analysis():
    rules = AIService._mock_query(AIService.__new__(AIService), "휴학 신청 순서와 담당자 연락처를 함께 알려줘")

    assert AIService._needs_ai_query_analysis("휴학 신청 순서와 담당자 연락처를 함께 알려줘", rules) is False


def test_multi_subject_question_uses_local_query_analysis():
    rules = AIService._mock_query(AIService.__new__(AIService), "복학과 수강신청 중 무엇을 먼저 해야 해?")

    assert AIService._needs_ai_query_analysis("복학과 수강신청 중 무엇을 먼저 해야 해?", rules) is True


def test_rule_filters_win_over_invalid_ai_category():
    rules = QueryFilters(category="학사", sub_category="휴학", keywords=["휴학", "신청"])
    ai = QueryFilters(category="존재하지 않는 분류", keywords=["담당자", "연락처"])

    merged = AIService._merge_query_filters(rules, ai)

    assert merged.category == "학사"
    assert merged.sub_category == "휴학"
    assert merged.keywords == ["휴학", "신청", "담당자", "연락처"]


def test_search_first_requires_program_identity_in_selected_notice():
    from types import SimpleNamespace
    from app.services.chat import _search_first_match_supported
    from app.schemas import QueryPlan

    query = QueryPlan(scope="search_first", taskKey="event.camp")
    unrelated = {"notice": SimpleNamespace(
        title="더 레인보우 특강 신청 안내", content="마음건강 특강입니다.", attachment_text="",
    ), "task_unit": None}
    krafton = {"notice": SimpleNamespace(
        title="경기대학교-크래프톤 정글 웹개발 집중 캠프", content="캠프 참가자를 모집합니다.", attachment_text="",
    ), "task_unit": None}

    assert _search_first_match_supported("강남대 파이썬 특강 신청 방법", query, unrelated) is False
    assert _search_first_match_supported("경기대 크래프톤 부트캠프 신청 방법", query, krafton) is True


def test_procedure_question_does_not_turn_short_label_into_fake_step(db):
    response = ChatService(db).answer("복학은 어떻게 신청해?")

    assert response.answer_mode in {"search_results_only", "department_handoff"}
    if response.has_data:
        assert response.action_guide is None
        assert response.status == "insufficient_evidence"


def test_expired_notice_warning(db):
    response = ChatService(db).answer("국가장학금 신청 방법 알려줘")
    assert response.has_data is True
    assert response.status == "stale_only"
    assert response.answer_mode == "action_guide"
    assert response.warnings == []
    assert "현재 신청할 수 없습니다" not in response.answer
    assert "마감" not in response.answer
    assert response.action_guide.summary
    assert response.action_guide.summary[:100] in response.answer


def test_date_question_uses_deterministic_answer(db):
    response = ChatService(db).answer("등록금 납부 기간이 언제야?")
    assert response.answer_mode in {"faq", "deterministic"}
    assert response.sources
    assert response.next_action is None


def test_date_word_does_not_fall_through_to_generated_answer(db):
    response = ChatService(db).answer("수강신청 일자를 알려줘")

    assert response.answer_mode == "deterministic"
    assert "다른 학기의 날짜를 섞어" not in response.answer or "원문" in response.answer


def test_expired_first_phase_does_not_hide_upcoming_followup_step(db):
    notice = db.scalar(select(Notice).where(Notice.title.contains("국가장학금")))
    notice.metadata_record.important_dates = [{
        "label": "서류 제출 기간",
        "start": (datetime.now(KST) + timedelta(days=5)).isoformat(),
        "end": (datetime.now(KST) + timedelta(days=7)).isoformat(),
        "description": "선발자 서류 제출",
    }]
    db.commit()

    response = ChatService(db).answer("국가장학금 신청 방법 알려줘")

    assert response.status == "success"
    assert "첫 신청 단계는 마감" in response.answer
    assert "서류 제출 기간" in response.answer
    assert response.next_action is None


def test_multi_task_query_plan_keeps_course_and_tuition_separate():
    plan = AIService().analyze_query("수강신청과 등록금 납부를 모두 알려줘")

    assert plan.requested_tasks == ["course.registration", "tuition.payment"]
    assert {item.task_key for item in plan.sub_queries} == {"course.registration", "tuition.payment"}


def test_known_canonical_task_does_not_spend_external_rerank_call():
    service = AIService()
    service.chat_provider = "gemini"
    matches = [{"candidate_id": "task:1"}, {"candidate_id": "task:2"}]

    result = service.rerank_candidates(
        "수강신청 일정 알려줘", QueryFilters(task_key="course.registration"), matches,
    )

    assert result is matches
    assert service.call_stats == []


def test_multi_task_answer_uses_independent_sources(db):
    response = ChatService(db).answer("수강신청과 등록금 납부를 순서대로 알려줘")

    assert {item.task_key for item in response.task_results} == {"course.registration", "tuition.payment"}
    assert len(response.matched_notices) == 2
    assert all(item.source_notice_ids for item in response.task_results)
    assert all(source.task_key for source in response.sources)


def test_graduation_comparison_detects_two_distinct_tasks():
    tasks = detect_tasks("졸업요건과 조기졸업 요건 차이 알려줘")

    assert {task.key for task in tasks} == {"graduation.requirements", "graduation.early"}


def test_follow_up_uses_minimal_session_context_and_delete_clears_it(db):
    service = ChatService(db)
    first = service.answer("2026학년도 2학기 수강신청 일정 알려줘")
    follow_up = service.answer("그럼 어디서 확인해?", first.session_id)

    assert follow_up.query.context_applied is True
    assert follow_up.query.task_key == "course.registration"
    assert follow_up.matched_notices[0].id == first.matched_notices[0].id
    context = db.get(ChatSessionContext, first.session_id)
    assert context is not None
    assert not hasattr(context, "question")

    service.end_session(first.session_id)
    missing = service.answer("그럼 어디서 확인해?", first.session_id)
    assert missing.status == "clarification_required"


def test_where_to_check_follow_up_is_not_treated_as_application_procedure():
    assert ChatService._wants_action_guide("그럼 어디서 확인해?") is False
    assert AIService().analyze_query("그럼 어디서 확인해?").requested_fields == ["source_location"]


def test_ambiguous_leave_period_asks_once_before_search(db):
    response = ChatService(db).answer("휴학 기간 알려줘")

    assert response.status == "clarification_required"
    assert response.has_data is False
    assert response.clarification_options == ["휴학 신청 기간", "최대 휴학 가능 기간"]
    assert response.matched_notices == []


def test_clear_leave_period_meanings_do_not_ask_again(db):
    application = ChatService(db).answer("휴학 신청 기간 알려줘")
    duration = ChatService(db).answer("최대 휴학 가능 기간 알려줘")

    assert application.status != "clarification_required"
    assert duration.status != "clarification_required"
    assert duration.query.requested_fields == ["leave_duration"]


def test_broad_scholarship_question_requests_type(db):
    response = ChatService(db).answer("장학금 알려줘")

    assert response.status == "clarification_required"
    assert response.clarification_options == ["국가장학금", "성적우수장학금", "교내·교외 장학금"]


def test_graduation_requirement_answer_never_uses_defer_application_fragment(db):
    notice, _ = NoticeProcessor(db).upsert({
        "source_id": "academic-graduation-test", "source_type": "academic_guide",
        "source_url": "https://web.kangnam.ac.kr/guide/graduation",
        "title": "[상시 학사안내] 졸업요건",
        "content": "졸업요건은 8학기 이상 등록하고 130학점 이상 취득해야 한다.",
        "published_at": datetime.fromisoformat("2026-07-20T09:00:00+09:00"),
        "department_name": "교무팀", "attachment_names": [], "attachment_urls": [],
        "content_links": [],
    })
    # 공통 메타데이터에 다른 신청 문장이 있어도 TaskUnit 업무가 선택되면
    # 그 문장을 일반 졸업요건 답변에 사용하지 않아야 한다.
    notice.metadata_record.application_method = "학사학위취득유예 신청 가."
    db.commit()
    response = ChatService(db).answer("졸업요건 알려줘")

    assert response.has_data is True
    assert "학사학위취득유예 신청 가." not in response.answer


def test_single_task_response_hides_lower_ranked_notice_cards(db):
    response = ChatService(db).answer("2026학년도 2학기 수강신청 일정 알려줘")

    assert len(response.matched_notices) == 1
    assert "수강신청" in response.matched_notices[0].title


def test_general_course_question_excludes_new_and_transfer_student_notice(db):
    NoticeProcessor(db).upsert({
        "source_id": "special-course-2026-2",
        "source_type": "official_notice",
        "source_url": "https://web.kangnam.ac.kr/notice/special-course",
        "title": "2026학년도 2학기 신·편입생 수강신청 수강신청 일정",
        "content": "신입생과 편입생 수강신청 일정 안내입니다.",
        "published_at": datetime.fromisoformat("2026-07-20T09:00:00+09:00"),
        "department_name": "교무팀",
        "attachment_names": [], "attachment_urls": [], "content_links": [],
    })
    db.commit()

    service = ChatService(db)
    response = service.answer("2026학년도 2학기 수강신청 일정 알려줘")

    assert response.matched_notices[0].title == "2026학년도 2학기 수강신청 일정"
    assert any(
        item["decision"] == "excluded" and "특정 학생 대상" in " ".join(item.get("reasons", []))
        for item in service.last_search_trace
        if "신·편입생" in item.get("title", "")
    )


def test_task_unit_candidates_keep_distinct_candidate_ids(db):
    notice = db.scalar(select(Notice).where(Notice.title.contains("수강신청 일정")))
    assert notice.task_units
    unit = notice.task_units[0]

    results = HybridSearch(db, AIService()).search(
        "2026학년도 2학기 수강신청 일정",
        AIService().analyze_query("2026학년도 2학기 수강신청 일정"),
        8,
    )

    task_candidates = [item for item in results if item.get("task_unit") is not None]
    assert task_candidates
    assert task_candidates[0]["candidate_id"] == f"task:{unit.id}"
    assert task_candidates[0]["candidate_id"] != f"notice:{notice.id}"


def test_application_method_fragment_is_not_used_as_next_action(db):
    notice = db.scalar(select(Notice).where(Notice.title.contains("등록금 납부")))
    notice.metadata_record.application_method = "학사학위취득유예 신청 가."
    db.commit()

    response = ChatService(db).answer("등록금 납부 기간 알려줘")

    assert response.next_action is None
    assert all("신청 가." not in (result.next_action.label if result.next_action else "") for result in response.task_results)


def test_schedule_question_returns_plain_text_and_structured_facts(db):
    response = ChatService(db).answer("수강신청 일정 알려줘")

    assert response.answer_mode == "deterministic"
    assert response.answer.startswith("수강신청 기간은 ")
    assert "**" not in response.answer
    assert any(fact.label == "본 신청 기간" for fact in response.answer_facts)
    assert all("문서종류:" not in source.evidence_excerpt for source in response.sources)


def test_general_course_schedule_rejects_unrequested_prerequisite_deadline():
    assert _specific_task_scope_rejection(
        "2학기 수강신청 언제야?", "course.registration", "수강신청 전 제1전공 신청",
    ) == "질문에 없는 수강신청 세부 일정: 제1전공 신청"
    assert _specific_task_scope_rejection(
        "수강신청 전 제1전공 신청 기한은?", "course.registration", "수강신청 전 제1전공 신청",
    ) is None


def test_complete_application_period_outranks_one_sided_date():
    point = datetime.fromisoformat("2026-08-05T10:00:00+09:00")
    end = datetime.fromisoformat("2026-08-06T23:59:00+09:00")

    assert _period_scope_score(point, end, "수강신청 언제야?") > _period_scope_score(
        None, point, "수강신청 언제야?",
    )


def test_combined_multi_phase_course_period_is_rejected_for_schedule_answer():
    start = datetime.fromisoformat("2026-01-13T00:00:00+09:00")
    end = datetime.fromisoformat("2026-08-06T23:59:00+09:00")

    assert _implausible_period_rejection(
        "course.registration", start, end, date_query=True,
    ) == "여러 수강신청 세부 일정을 합친 과도하게 긴 기간"
    assert _implausible_period_rejection(
        "course.registration", datetime.fromisoformat("2026-08-05T10:00:00+09:00"),
        datetime.fromisoformat("2026-08-06T23:59:00+09:00"), date_query=True,
    ) is None


def test_one_sided_task_deadline_is_not_labeled_as_main_application_period():
    deadline = datetime.fromisoformat("2026-08-05T10:00:00+09:00")
    unit = SimpleNamespace(
        title="수강신청 전 제1전공 신청", application_start=None,
        application_end=deadline, facts=[], task=SimpleNamespace(task_key="course.registration"),
    )
    meta = SimpleNamespace(
        application_start=None, application_end=None, important_dates=[],
        application_method=None, application_location=None,
    )

    facts, _ = ChatService._answer_presentation("기한이 언제야?", meta, False, unit)

    assert facts[0].label == "수강신청 전 제1전공 신청 기한"
    assert facts[0].value == "2026.08.05 10:00까지"


def _camp_overview_unit():
    def fact(fact_type, label, value):
        return SimpleNamespace(
            fact_type=fact_type, label=label, value=value,
            student_actionable=True, confidence=1.0, source_locator="HTML body",
        )

    return SimpleNamespace(
        title="크래프톤 정글 웹개발 집중 캠프 참여",
        application_start=None, application_end=None,
        event_start=datetime.fromisoformat("2026-08-03T00:00:00+09:00"),
        event_end=datetime.fromisoformat("2026-08-14T23:59:00+09:00"),
        task=SimpleNamespace(task_key="event.camp"), procedure=None, evidence=[],
        facts=[
            fact("eligibility", "참여 대상", "강남대를 포함한 인근 대학 재학생"),
            fact("recommendation", "권장 대상", "융합교육 경험이 있는 3~4학년 학생"),
            fact("experience_requirement", "프로그래밍 경험", "하나 이상의 프로그래밍 언어 사용 경험"),
            fact("capacity", "모집 인원", "각 대학별 5명 이내"),
            fact("fee", "참가비", "35만원/인"),
            fact("venue", "행사 장소", "크래프톤 정글 캠퍼스"),
        ],
    )


def _camp_overview_meta():
    return SimpleNamespace(
        application_start=None, application_end=None,
        event_start=None, event_end=None,
        application_method=None, application_location=None,
        eligibility_notes=[], important_dates=[],
    )


def test_general_event_question_returns_grounded_overview_cards():
    facts, notes = ChatService._answer_presentation(
        "경기대 크래프톤 부트캠프에 대해 알려줘",
        _camp_overview_meta(), False, _camp_overview_unit(),
    )

    rendered = {fact.label: fact.value for fact in facts}
    assert list(rendered) == ["행사 기간", "참여 대상", "모집 인원", "참가비", "행사 장소", "권장 대상"]
    assert rendered["행사 기간"] == "2026.08.03 00:00 ~ 2026.08.14 23:59"
    assert rendered["참가비"] == "35만원/인"
    assert notes == ["신청 기간과 신청 절차는 공식 공지 본문에서 확인되지 않습니다."]


def test_expired_event_overview_leaves_deadline_to_the_status_presentation():
    end = datetime.fromisoformat("2026-07-12T23:59:00+09:00")
    unit = _camp_overview_unit()
    unit.application_end = end
    notice = SimpleNamespace(
        title="경기대학교-크래프톤 정글 웹개발 집중 캠프",
        action_guide=SimpleNamespace(
            summary=(
                "경기대학교와 크래프톤에서 함께 주최하는 단기 프로그래밍 캠프입니다. "
                "합숙과 몰입을 통해 AI·SW 개발을 경험합니다."
            ),
        ),
    )
    match = {"notice": notice, "metadata": _camp_overview_meta(), "task_unit": unit}

    answer = ChatService._deterministic_answer(
        "경기대 크래프톤 캠프가 뭐야?", match, DepartmentInfo(),
    )

    assert answer.startswith("경기대학교와 크래프톤에서 함께 주최하는")
    assert "AI·SW 개발" in answer
    assert "마감" not in answer


def test_contact_question_does_not_include_unrelated_fact_cards():
    meta = _camp_overview_meta()
    facts, notes = ChatService._answer_presentation(
        "휴학 담당자 연락처 알려줘", meta, False, _camp_overview_unit(),
    )

    assert facts == []
    assert notes == []


def test_compact_korean_overview_expression_returns_same_grounded_cards():
    facts, notes = ChatService._answer_presentation(
        "경기대 크래프톤 부트캠프에대해",
        _camp_overview_meta(), False, _camp_overview_unit(),
    )

    assert [fact.label for fact in facts] == [
        "행사 기간", "참여 대상", "모집 인원", "참가비", "행사 장소", "권장 대상",
    ]
    assert notes == ["신청 기간과 신청 절차는 공식 공지 본문에서 확인되지 않습니다."]


def test_bare_event_name_defaults_to_grounded_overview_cards():
    facts, _ = ChatService._answer_presentation(
        "강냉이 부스트캠프",
        _camp_overview_meta(), False, _camp_overview_unit(),
    )

    assert facts[0].label == "행사 기간"
    assert "참여 대상" in [fact.label for fact in facts]


def test_overview_intent_accepts_spacing_and_common_paraphrases():
    assert ChatService._wants_fact_overview("크래프톤 부트캠프에대해") is True
    assert ChatService._wants_fact_overview("크래프톤 부트캠프에 관해 설명해줘") is True
    assert ChatService._wants_fact_overview("크래프톤 부트캠프를 소개해 주세요") is True


def test_event_application_period_question_never_relabels_event_dates():
    facts, _ = ChatService._answer_presentation(
        "크래프톤 캠프 신청 기간 언제야?",
        _camp_overview_meta(), False, _camp_overview_unit(),
    )

    assert all(fact.label != "행사 기간" for fact in facts)
    assert all("2026.08.03" not in fact.value for fact in facts)


def test_generic_event_period_question_uses_event_dates_not_application_dates():
    unit = _camp_overview_unit()
    meta = _camp_overview_meta()
    answer = ChatService._deterministic_answer(
        "강냉이 부스트캠프 언제야?",
        {
            "notice": SimpleNamespace(title="2026 AI 강냉 부스트캠프"),
            "metadata": meta,
            "task_unit": unit,
        },
        DepartmentInfo(),
    )
    facts, _ = ChatService._answer_presentation(
        "강냉이 부스트캠프 언제야?", meta, False, unit,
    )

    assert answer == "‘크래프톤 정글 웹개발 집중 캠프 참여’ 행사 기간은 2026.08.03 00:00부터 2026.08.14 23:59까지입니다."
    assert facts[0].label == "행사 기간"
    assert ChatService._asks_application_period("강냉이 부스트캠프 언제야?") is False
    assert ChatService._asks_application_period("강냉이 부스트캠프 신청 기간 언제야?") is True


def test_specific_event_fact_question_only_prioritizes_requested_card():
    facts, _ = ChatService._answer_presentation(
        "크래프톤 캠프 참가비 얼마야?",
        _camp_overview_meta(), False, _camp_overview_unit(),
    )

    assert [(fact.label, fact.value) for fact in facts] == [("참가비", "35만원/인")]


def test_event_identity_terms_keep_brand_and_drop_generic_question_words():
    filters = QueryFilters(
        task_key="event.camp",
        keywords=["크래프톤", "캠프", "신청", "기간", "언제야"],
    )

    assert _identity_terms(filters) == ["크래프톤"]


def test_named_event_question_excludes_other_event_notice_candidates(db):
    processor = NoticeProcessor(db)
    common = {
        "source_type": "event",
        "published_at": datetime.fromisoformat("2026-07-21T09:00:00+09:00"),
        "department_name": "SW사업단",
        "attachment_names": [], "attachment_urls": [], "content_links": [],
    }
    processor.upsert({
        **common,
        "source_id": "krafton-event-identity",
        "source_url": "https://web.kangnam.ac.kr/event/krafton",
        "title": "경기대학교-크래프톤 정글 웹개발 집중 캠프",
        "content": "크래프톤 정글 캠프는 8월 3일부터 14일까지 진행됩니다.",
    })
    processor.upsert({
        **common,
        "source_id": "abc-event-identity",
        "source_url": "https://web.kangnam.ac.kr/event/abc",
        "title": "한신대학교 ABC캠프 해커톤대회",
        "content": "ABC캠프 참가 신청 기간은 7월 30일까지입니다.",
    })
    db.commit()

    results = HybridSearch(db, AIService()).search(
        "크래프톤 캠프 신청 기간 언제야?",
        QueryFilters(
            task_key="event.camp", category="기타",
            keywords=["크래프톤", "캠프", "신청", "기간", "언제야"],
        ),
        8,
    )

    assert results
    assert all("ABC캠프" not in item["notice"].title for item in results)
    assert "크래프톤" in results[0]["notice"].title


def test_markdown_is_removed_from_generated_answer():
    assert _plain_answer("기간은 **8월 5일**입니다.\n- 온라인 신청") == "기간은 8월 5일입니다.\n• 온라인 신청"


def test_internal_line_break_token_never_leaks_for_blank_lines():
    answer = _plain_answer("첫 문단입니다.\n\n두 번째 문단입니다.")

    assert answer == "첫 문단입니다.\n\n두 번째 문단입니다."
    assert "KNUASKLINEBREAKTOKEN" not in answer
    assert "KNUASKPARAGRAPHTOKEN" not in answer


def test_mismatched_embedding_models_do_not_create_semantic_matches(db):
    class BgeAI:
        embedding_provider = "ollama"
        embedding_model_name = "ollama/bge-m3"

        @staticmethod
        def embedding(_message):
            return [1.0, 0.0]

    unrelated = db.scalar(select(Notice).where(Notice.title.contains("국가장학금")))
    unrelated.embedding_record.embedding = [1.0, 0.0]
    unrelated.embedding_record.embedding_model = "text-embedding-3-small"
    db.commit()

    results = HybridSearch(db, BgeAI()).search(
        "수영장 온도",
        QueryFilters(keywords=["수영장", "온도"]),
    )

    assert all(item["notice"].id != unrelated.id for item in results)


def test_application_question_returns_reusable_step_guide(db):
    response = ChatService(db).answer("휴학은 어떻게 신청해?")

    assert response.answer_mode == "action_guide"
    assert response.action_guide is not None
    assert [step.order for step in response.action_guide.steps] == [1, 2, 3]
    assert response.action_guide.source_url


def test_procedure_and_contact_question_returns_steps_and_explicit_missing_contact(db):
    response = ChatService(db).answer("휴학 신청 순서와 담당자 연락처를 함께 알려줘")

    assert response.answer_mode == "action_guide"
    assert response.action_guide is not None
    assert "전화번호" in response.answer


def test_missing_notice_phone_is_supplemented_from_official_staff_directory(db):
    notice = db.scalar(select(Notice).where(Notice.title.contains("수강신청")))
    notice.metadata_record.department_name = "교무팀"
    notice.metadata_record.department_phone = None
    notice.metadata_record.contact_person = None
    db.add_all([
        StaffDirectoryContact(
            source_id="directory:leader", department_name="교무팀", contact_person="이용신",
            duty="팀장", phone="031-280-3541", source_url="https://web.kangnam.ac.kr/directory",
        ),
        StaffDirectoryContact(
            source_id="directory:registration", department_name="교무팀", contact_person="차우석",
            duty="교육과정,수강신청", phone="031-280-3543", source_url="https://web.kangnam.ac.kr/directory",
        ),
    ])
    db.commit()

    response = ChatService(db).answer("수강신청 담당자 연락처 알려줘")

    assert response.department.phone == "031-280-3543"
    assert response.department.contact_person == "차우석"
    assert response.department.contact_duty == "교육과정,수강신청"
    assert response.department.contact_source == "강남대학교 공식 직원 연락처에서 보완"
    assert "031-280-3543" in response.answer
    assert "공식 직원 연락처에서 보완" not in response.answer
    assert "공식 연락처에서 업무 담당자를 확인" not in response.answer
    assert db.scalar(select(DataGap).where(
        DataGap.notice_id == notice.id,
        DataGap.field_name == "department_phone",
    )) is None


def test_gap_terms_do_not_treat_subject_words_as_requested_output_fields():
    from app.services.data_gaps import requested_fields

    assert "credits_or_hours" not in requested_fields("학점등록대상자 수강신청 방법")
    assert "benefits" not in requested_fields("국가장학금 신청 방법")


def test_multiple_notice_contacts_are_not_replaced_with_unrelated_directory_phone(db):
    notice = db.scalar(select(Notice).where(Notice.title.contains("수강신청")))
    notice.metadata_record.department_name = "교무팀"
    notice.metadata_record.department_phone = None
    notice.attachment_text = (
        "문의처 - 교학1팀(복지융합대학): 031-280-3872 "
        "- 교학2팀(공과대학): 031-280-3463"
    )
    db.commit()

    department = ChatService(db)._department_info("신청 방법", notice, notice.metadata_record)

    assert department.name == "소속 대학별 문의처"
    assert department.phone == "교학1팀 031-280-3872 / 교학2팀 031-280-3463"
    assert department.contact_source == "공지 원문의 문의처에서 확인"


def test_notice_status_uses_application_period_and_action_label(db):
    response = ChatService(db).answer("국가장학금 신청 방법 알려줘")
    notice = response.matched_notices[0]

    assert notice.notice_status == "expired"
    assert notice.status_label == "신청 마감"


def test_start_date_without_deadline_is_not_claimed_as_currently_open():
    start = datetime(2026, 7, 1, tzinfo=KST)
    now = datetime(2026, 7, 20, tzinfo=KST)

    assert _period_status(start, None, now) == "unknown"
