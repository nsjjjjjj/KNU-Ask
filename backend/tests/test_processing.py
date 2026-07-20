from datetime import datetime

from sqlalchemy import func, select

from app.models import ActionStep, ProcessingJob
from app.schemas import AdditionalFact, ImportantDate, StructuredActionGuide, StructuredNotice
from app.services.ai import AIService
from app.services.processing import NoticeProcessor
from app.utils.text import extract_application_period, extract_notice_contact, extract_notice_email, rule_extract


def raw(source_id="new-1", content="새로운 공지 본문"):
    return {
        "source_id": source_id, "title": "새 공지", "content": content,
        "published_at": datetime.fromisoformat("2026-07-20T09:00:00+09:00"),
        "source_url": f"https://example.invalid/{source_id}", "department_name": "학사지원팀",
    }


def test_new_notice_is_saved_and_processed(db):
    notice, state = NoticeProcessor(db).upsert(raw())
    db.commit()
    assert state == "new"
    assert notice.ai_processed is True
    assert notice.metadata_record is not None
    assert notice.embedding_record is not None
    assert notice.embedding_record.embedding_model == "local-char-ngram-v2"
    assert notice.chunks


def test_same_hash_skips_reprocessing(db):
    processor = NoticeProcessor(db)
    notice, _ = processor.upsert(raw())
    db.commit()
    before = db.scalar(select(func.count(ProcessingJob.id)))
    same, state = processor.upsert(raw())
    db.commit()
    after = db.scalar(select(func.count(ProcessingJob.id)))
    assert state == "skipped"
    assert same.id == notice.id
    assert before == after


def test_changed_notice_reprocesses(db):
    processor = NoticeProcessor(db)
    notice, _ = processor.upsert(raw())
    old_hash = notice.content_hash
    changed, state = processor.upsert(raw(content="수정된 공지 본문"))
    db.commit()
    assert state == "updated"
    assert changed.content_hash != old_hash
    assert changed.ai_processed is True


def test_external_ai_provider_queues_without_persisting_rule_result(db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "notice_structuring_provider", "codex")
    notice, state = NoticeProcessor(db).upsert(raw(source_id="codex-only"))
    db.commit()

    assert state == "new"
    assert notice.ai_processed is False
    assert notice.metadata_record is None
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.notice_id == notice.id))
    assert job is not None
    assert job.status == "pending"


def test_long_notice_is_saved_as_multiple_search_chunks(db):
    item = raw(content=("휴학 신청 대상과 절차를 확인합니다. " * 180))
    notice, _ = NoticeProcessor(db).upsert(item)
    db.commit()

    assert len(notice.chunks) >= 2
    assert all(chunk.embedding_version == notice.embedding_version for chunk in notice.chunks)


def test_verified_content_links_survive_later_reprocessing(db):
    item = raw(content="학사시스템에서 신청하세요.")
    item["content_links"] = ["https://portal.kangnam.ac.kr/apply"]
    notice, _ = NoticeProcessor(db).upsert(item)
    db.commit()

    NoticeProcessor(db).process(notice)
    db.commit()

    assert notice.content_links == ["https://portal.kangnam.ac.kr/apply"]
    assert notice.action_guide.application_url == "https://portal.kangnam.ac.kr/apply"


def test_structured_json_validation_normalizes_allowed_values():
    result = StructuredNotice.model_validate({
        "category": "임의분류", "actionType": "임의행동", "noticeStatus": "invalid",
        "confidence": 0.7, "applicationPeriod": {}, "eventPeriod": {}, "target": {},
    })
    assert result.category == "기타"
    assert result.action_type == "기타"
    assert result.notice_status == "unknown"


def test_action_guide_is_stored_once_and_replaced_on_change(db):
    processor = NoticeProcessor(db)
    item = raw(content="학사시스템에서 휴학을 신청하고 제출 결과를 확인하세요.")
    item["content_links"] = ["https://portal.kangnam.ac.kr/apply"]
    notice, _ = processor.upsert(item)
    db.commit()

    assert notice.action_guide is not None
    assert [step.step_order for step in notice.action_guide.steps] == [1, 2, 3]
    assert notice.action_guide.application_url == "https://portal.kangnam.ac.kr/apply"

    changed = raw(content="학사시스템에서 휴학 신청서를 제출하세요.")
    changed["content_links"] = ["https://portal.kangnam.ac.kr/apply"]
    updated, state = processor.upsert(changed)
    db.commit()
    db.refresh(updated)

    assert state == "updated"
    assert [step.step_order for step in updated.action_guide.steps] == [1, 2, 3]
    assert db.scalar(select(func.count(ActionStep.id)).where(
        ActionStep.action_guide_id == updated.action_guide.id,
    )) == 3


def test_unverified_ai_link_is_not_saved(db):
    item = raw(content="학사시스템에서 신청하세요.")
    notice, _ = NoticeProcessor(db).upsert(item)
    db.commit()

    assert notice.action_guide is not None
    assert notice.action_guide.application_url is None
    assert all(step.action_url is None for step in notice.action_guide.steps)


def test_long_step_title_is_compacted_and_preserved_in_description():
    original = "신청 대상과 교육 내용 및 유의사항을 모두 확인한 다음 홈페이지에서 신청서를 제출하세요. " * 8
    guide = StructuredActionGuide.model_validate({
        "taskName": "교육 신청",
        "steps": [{
            "order": 1, "title": original, "description": "원문에서 추출한 단계입니다.",
            "actionType": "submit", "confidence": 0.9,
        }],
        "confidence": 0.9,
    })

    assert guide.steps[0].title == "신청 내용 제출"
    assert original.strip()[:200] in guide.steps[0].description
    assert len(guide.steps[0].description) <= 2000
    assert guide.steps[0].confidence == 0.5
    assert guide.needs_review is True


def test_invalid_action_guide_does_not_discard_notice_metadata():
    payload = {
        "category": "학사", "actionType": "신청", "noticeStatus": "active",
        "applicationPeriod": {}, "eventPeriod": {}, "target": {},
        "searchText": "휴학 신청 안내",
        "actionGuide": {
            "taskName": "휴학 신청",
            "steps": [{"order": index + 1, "title": "단계", "description": "설명"} for index in range(31)],
        },
    }

    result = AIService._validate_structured_notice(__import__("json").dumps(payload, ensure_ascii=False))

    assert result.category == "학사"
    assert result.search_text == "휴학 신청 안내"
    assert result.action_guide is None
    assert result.needs_review is True


def test_application_period_prefers_recruitment_dates_over_activity_dates():
    text = (
        "신청 기간: 2026. 7. 1. ~ 2026. 7. 12. "
        "교육 수행 기간: 2026. 8. 1. ~ 2026. 10. 31."
    )

    start, end = extract_application_period(text, datetime.fromisoformat("2026-06-20T09:00:00+09:00"))

    assert start.isoformat() == "2026-07-01T00:00:00+09:00"
    assert end.isoformat() == "2026-07-12T23:59:00+09:00"


def test_invalid_date_like_numbers_do_not_fail_notice_processing():
    start, end = extract_application_period(
        "신청 방법 2026. 14. 55. 확인 후 접수", datetime.fromisoformat("2026-06-20T09:00:00+09:00"),
    )

    assert start is None
    assert end is None


def test_application_period_uses_range_after_repeated_deadline_summary():
    text = "신청 기간 마감 5. 8. 2026. 4. 21. ~ 5. 8. 15:00 근무 기간 7월~8월"

    start, end = extract_application_period(text, datetime.fromisoformat("2026-04-01T09:00:00+09:00"))

    assert start.isoformat() == "2026-04-21T00:00:00+09:00"
    assert end.isoformat() == "2026-05-08T15:00:00+09:00"


def test_bare_application_word_does_not_use_later_activity_period():
    text = "개인 신청, 참가자는 팀으로 매칭합니다. 운영기간 2026. 7. 13. ~ 7. 25."

    start, end = extract_application_period(text, datetime.fromisoformat("2026-06-01T09:00:00+09:00"))

    assert start is None
    assert end is None


def test_application_period_treats_tilde_prefixed_single_date_as_deadline():
    text = (
        "학생 신청: ~ 2026. 7. 12.(일) 한국장학재단과 네이버폼 모두 신청 "
        "기관 요청: 2026. 7. 13. ~ 2026. 7. 16."
    )

    start, end = extract_application_period(text, datetime.fromisoformat("2026-07-02T09:00:00+09:00"))

    assert start is None
    assert end.isoformat() == "2026-07-12T23:59:00+09:00"


def test_mock_numbered_application_method_keeps_all_required_paths():
    content = (
        "신청방법(1,2번 모두 신청해야 접수 완료) "
        "1. 한국장학재단 장학금 신청: 7월 12일까지 "
        "2. 멘토링 희망 기관 네이버폼 신청: 7월 12일까지 ※ 대상 확인"
    )

    method = AIService._mock_application_method("멘토 모집", content, "신청")

    assert method == (
        "한국장학재단 장학금 신청 → 멘토링 희망 기관 네이버폼 신청 → "
        "모든 신청의 접수 완료 여부 확인"
    )


def test_mock_action_link_requires_matching_service_host():
    links = ["https://naver.me/example"]

    assert AIService._matching_action_link("한국장학재단 신청", links) is None
    assert AIService._matching_action_link("네이버폼 신청", links) == links[0]


def test_application_period_supports_same_month_shorthand_end_day():
    start, end = extract_application_period(
        "제출기한: 2026. 7. 7.(화) ~ 13.(월)",
        datetime.fromisoformat("2026-06-01T09:00:00+09:00"),
    )

    assert start.isoformat() == "2026-07-07T00:00:00+09:00"
    assert end.isoformat() == "2026-07-13T23:59:00+09:00"


def test_notice_title_category_wins_over_exclusion_word_in_body():
    structured = AIService()._mock_structure(
        "대학생 AI 교육지원장학금 멘토 모집",
        "휴학생은 신청 대상에서 제외합니다.",
        datetime.fromisoformat("2026-07-01T09:00:00+09:00"),
        None,
    )

    assert structured.category == "장학"
    assert structured.sub_category is None


def test_notice_contact_prefers_labeled_campus_phone_and_person():
    person, phone = extract_notice_contact(
        "운영기관 070-4222-1022 / 업무 담당: 문정선 주무관 / "
        "강남대 대학일자리플러스센터 문의 031-280-3881",
        "대학일자리플러스센터",
    )

    assert person == "문정선 주무관"
    assert phone == "031-280-3881"


def test_notice_contact_rejects_other_department_phone():
    person, phone = extract_notice_contact(
        "관리부서: 교무팀 장학금 취소 문의는 장학복지팀 031-280-3552로 연락하세요.",
        "교무팀",
    )

    assert person is None
    assert phone is None


def test_notice_email_prefers_labeled_contact_over_applicant_example():
    email = extract_notice_email(
        "신청자 이메일 example@student.test 입력. 교무팀 문의 이메일: academic@kangnam.ac.kr",
        "교무팀",
    )

    assert email == "academic@kangnam.ac.kr"


def test_notice_processing_stores_notice_specific_contact(db):
    notice, _ = NoticeProcessor(db).upsert(raw(
        content="업무 담당: 문정선 주무관 문의처: 02-2110-7786",
    ))
    db.commit()

    assert notice.metadata_record.contact_person == "문정선 주무관"
    assert notice.metadata_record.department_phone == "02-2110-7786"


def test_compact_academic_year_title_is_extracted():
    extracted = rule_extract("2026-2학기 수강신청 안내")

    assert extracted["academic_year"] == 2026
    assert extracted["semester"] == 2


def test_canonical_index_stores_frequently_asked_application_details(db):
    class DetailAI:
        embedding_model_name = "test-detail-embedding"

        @staticmethod
        def embedding(_text):
            return [0.0] * 1536

        @staticmethod
        def structure_notice(*_args, **_kwargs):
            return StructuredNotice(
                category="대학생활안내",
                action_type="신청",
                application_location="학생회관 2층 또는 온라인 신청 페이지",
                eligibility_notes=["재학생만 신청 가능", "휴학생 제외"],
                fee_information="참가비 10,000원, 선발 후 환불 불가",
                capacity="선착순 30명",
                selection_method="신청 순서대로 선발",
                result_announcement="7월 25일 개별 문자 안내",
                cancellation_policy="7월 23일까지 담당 부서에 취소 요청",
                benefits=["수료증 발급", "활동비 지급"],
                credits_or_hours="비교과 10시간 인정",
                important_dates=[ImportantDate(label="면접", description="7월 24일 온라인 면접", source_locator="본문 3번")],
                additional_facts=[AdditionalFact(
                    fact_type="이관 규칙", label="예비수강 이관 실패 처리",
                    value="정원 초과 과목은 본 수강신청 기간에 다시 신청해야 함",
                    student_actionable=True, source_type="body_image", source_locator="본문 이미지 2",
                    confidence=0.9,
                )],
                evidence_map={"capacity": "첨부파일 모집요강.pdf 2쪽"},
                department={
                    "name": "학생지원팀", "contactPerson": "김강남", "contactRole": "주무관",
                    "phone": "031-280-0000", "email": "help@kangnam.ac.kr",
                    "officeLocation": "학생회관 2층", "officeHours": "평일 09:00~17:00",
                },
                notice_status="active",
                confidence=0.9,
            )

    notice, _ = NoticeProcessor(db, ai=DetailAI()).upsert(raw(content="프로그램 신청 안내"))
    db.commit()

    meta = notice.metadata_record
    assert meta.application_location == "학생회관 2층 또는 온라인 신청 페이지"
    assert meta.eligibility_notes == ["재학생만 신청 가능", "휴학생 제외"]
    assert meta.fee_information == "참가비 10,000원, 선발 후 환불 불가"
    assert "선착순 30명" in meta.search_text
    assert "7월 25일 개별 문자 안내" in meta.search_text
    assert "7월 23일까지 담당 부서에 취소 요청" in meta.search_text
    assert meta.benefits == ["수료증 발급", "활동비 지급"]
    assert meta.credits_or_hours == "비교과 10시간 인정"
    assert meta.important_dates[0]["label"] == "면접"
    assert meta.additional_facts[0]["label"] == "예비수강 이관 실패 처리"
    assert "본 수강신청 기간에 다시 신청" in meta.search_text
    assert meta.evidence_map == {"capacity": "첨부파일 모집요강.pdf 2쪽"}
    assert meta.contact_role == "주무관"
    assert meta.department_email == "help@kangnam.ac.kr"
    assert meta.department_office_location == "학생회관 2층"


def test_mock_leave_guide_uses_official_general_leave_steps():
    method = AIService._mock_application_method(
        "[상시 학사안내] 일반휴학 신청 방법",
        "휴학원 처리 절차 학교 홈페이지 종합정보시스템 접속 "
        "학적변동관리>일반휴학신청>신규휴학신청 클릭 휴학신청서 작성",
        "신청",
    )

    assert method == (
        "종합정보시스템 접속 → 학적변동관리 → 일반휴학신청 → "
        "신규휴학신청 선택 → 휴학신청서 작성 및 제출 → 결재 상태 확인"
    )
