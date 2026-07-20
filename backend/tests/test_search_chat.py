from app.schemas import QueryFilters
from app.services.ai import AIService
from app.services.chat import ChatService, _plain_answer
from app.services.notice_status import KST, _period_status, effective_status, effective_status_label
from datetime import datetime
from sqlalchemy import select
from app.models import DataGap, Notice, StaffDirectoryContact
from app.services.search import HybridSearch


def test_no_data_question_returns_required_sentence(db):
    response = ChatService(db).answer("교내 수영장 수온이 몇 도야?")
    assert response.has_data is False
    assert response.status == "no_result"
    assert "답할 근거를 찾지 못했습니다" in response.answer
    assert response.answer_mode == "department_handoff"
    gap = db.scalar(select(DataGap).where(DataGap.gap_type == "missing_search_evidence"))
    assert gap is not None
    assert "question" not in gap.context


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


def test_vector_search_returns_relevant_notice(db):
    filters = AIService().analyze_query("휴학 신청은 언제까지야?")
    results = HybridSearch(db, AIService()).search("휴학 신청은 언제까지야?", filters)
    assert results
    assert "휴학" in results[0]["notice"].title
    assert "창업휴학" not in results[0]["notice"].title


def test_real_semantic_embedding_can_rescue_a_paraphrase_without_keyword_overlap(db):
    class SemanticAI:
        embedding_provider = "ollama"

        @staticmethod
        def embedding(_message):
            return [1.0, 0.0]

    leave = db.scalar(select(Notice).where(Notice.title.contains("휴학 신청 안내")))
    for notice in db.scalars(select(Notice)):
        vector = [1.0, 0.0] if notice.id == leave.id else [0.0, 1.0]
        notice.embedding_record.embedding = vector
        for chunk in notice.chunks:
            chunk.embedding = vector
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
    assert "마감된 자료" in response.warnings[0]
    assert "현재 신청할 수 없습니다" in response.answer


def test_date_question_uses_deterministic_answer(db):
    response = ChatService(db).answer("등록금 납부 기간이 언제야?")
    assert response.answer_mode in {"faq", "deterministic"}
    assert response.sources
    assert response.next_action is not None


def test_date_word_does_not_fall_through_to_generated_answer(db):
    response = ChatService(db).answer("수강신청 일자를 알려줘")

    assert response.answer_mode == "deterministic"
    assert "다른 학기의 날짜를 섞어" not in response.answer or "원문" in response.answer


def test_schedule_question_returns_plain_text_and_structured_facts(db):
    response = ChatService(db).answer("수강신청 일정 알려줘")

    assert response.answer_mode == "deterministic"
    assert "**" not in response.answer
    assert any(fact.label == "본 신청 기간" for fact in response.answer_facts)


def test_markdown_is_removed_from_generated_answer():
    assert _plain_answer("기간은 **8월 5일**입니다.\n- 온라인 신청") == "기간은 8월 5일입니다.\n• 온라인 신청"


def test_internal_line_break_token_never_leaks_for_blank_lines():
    answer = _plain_answer("첫 문단입니다.\n\n두 번째 문단입니다.")

    assert answer == "첫 문단입니다.\n두 번째 문단입니다."
    assert "KNUASKLINEBREAKTOKEN" not in answer


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


def test_notice_status_uses_application_period_and_action_label(db):
    response = ChatService(db).answer("국가장학금 신청 방법 알려줘")
    notice = response.matched_notices[0]

    assert notice.notice_status == "expired"
    assert notice.status_label == "신청 마감"


def test_start_date_without_deadline_is_not_claimed_as_currently_open():
    start = datetime(2026, 7, 1, tzinfo=KST)
    now = datetime(2026, 7, 20, tzinfo=KST)

    assert _period_status(start, None, now) == "unknown"
