from datetime import datetime

from sqlalchemy import func, select

from app.models import ActionStep, ProcessingJob, TaskUnit
from app.schemas import (
    AdditionalFact, ImportantDate, StructuredActionGuide, StructuredNotice,
)
from app.services.ai import AIService
from app.services.processing import NoticeProcessor
from app.services.search import HybridSearch
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


def test_attachment_refresh_repairs_legacy_notice_without_full_crawl(db, monkeypatch):
    item = raw(source_id="legacy-attachment", content="첨부파일을 확인하세요.")
    item["attachment_names"] = ["안내.pdf"]
    item["attachment_urls"] = ["https://web.kangnam.ac.kr/files/guide.pdf"]
    notice, _ = NoticeProcessor(db).upsert(item)
    notice.attachment_text = ""
    notice.attachment_manifest = []
    notice.extraction_status = "not_required"
    monkeypatch.setattr(
        "app.services.processing.AttachmentExtractor.extract_many",
        lambda _self, _names, _urls: ("신청 기간과 절차", "success"),
    )
    monkeypatch.setattr(
        "app.services.processing.AttachmentExtractor.manifest",
        lambda _self, names, urls: [{"name": names[0], "url": urls[0], "extractionStatus": "success"}],
    )

    status = NoticeProcessor.refresh_attachments(notice)

    assert status == "success"
    assert notice.attachment_text == "신청 기간과 절차"
    assert notice.attachment_manifest[0]["extractionStatus"] == "success"
    assert notice.ai_processed is False


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


def test_academic_sections_are_persisted_as_separate_task_units(db):
    item = raw(source_id="academic-graduation", content="졸업 및 유예 안내")
    item.update({
        "title": "[상시 학사안내] 졸업",
        "source_type": "academic_guide",
        "source_priority": 110,
        "source_metadata": {"sections": [
            {"title": "졸업요건", "content": "2008학년도 이전 입학자도 8학기 등록 및 130학점 이상 취득", "sourceLocator": "HTML section:졸업요건"},
            {"title": "학사학위취득유예", "content": "졸업요건 충족 후 소정기한 내 신청", "sourceLocator": "HTML section:학사학위취득유예"},
        ]},
    })

    notice, _ = NoticeProcessor(db).upsert(item)
    db.commit()
    keys = {unit.task.task_key for unit in notice.task_units}

    assert keys == {"graduation.requirements", "graduation.defer"}
    assert db.scalar(select(func.count(TaskUnit.id)).where(TaskUnit.notice_id == notice.id)) == 2
    assert all(unit.academic_year is None and unit.semester is None for unit in notice.task_units)


def test_external_arbitrary_task_key_is_normalized_to_search_canonical_key(db):
    processor = NoticeProcessor(db)
    item = raw(source_id="tuition-image", content="등록금 납부 안내")
    notice, _ = processor.upsert(item)
    structured = StructuredNotice.model_validate({
        "category": "등록",
        "applicationPeriod": {},
        "eventPeriod": {},
        "target": {},
        "taskUnits": [{
            "taskKey": "regular_tuition_payment",
            "taskName": "정규등록금 납부",
            "parentTask": "tuition",
            "aliases": ["등록금 납부", "정규등록"],
            "target": {},
            "applicationPeriod": {},
            "eventPeriod": {},
            "documentSubmissionPeriod": {},
            "resultAnnouncementPeriod": {},
            "facts": [],
            "evidence": [],
            "confidence": 0.9,
        }],
    })

    processor.persist_structured(notice, structured)
    db.commit()

    saved_units = list(db.scalars(select(TaskUnit).where(TaskUnit.notice_id == notice.id)))
    assert [unit.task.task_key for unit in saved_units] == ["tuition.payment"]
    assert saved_units[0].task.name == "일반 등록금 납부"
    assert saved_units[0].title == "정규등록금 납부"


def test_external_structure_rejects_publication_date_as_application_period(db):
    processor = NoticeProcessor(db)
    item = raw(source_id="leave-publication-date", content="일반휴학은 휴학신청기간 또는 개강일로부터 4주 이내 신청")
    item["title"] = "[상시 학사안내] 휴학"
    notice, _ = processor.upsert(item)
    structured = StructuredNotice.model_validate({
        "category": "학사", "applicationPeriod": {}, "eventPeriod": {}, "target": {},
        "taskUnits": [{
            "taskKey": "leave.general", "taskName": "일반휴학 신청", "parentTask": "leave",
            "aliases": ["일반휴학"], "target": {},
            "applicationPeriod": {
                "start": "2026-07-20T00:00:00+09:00",
                "end": "2026-07-20T23:59:00+09:00",
            },
            "eventPeriod": {}, "documentSubmissionPeriod": {}, "resultAnnouncementPeriod": {},
            "facts": [], "evidence": [{
                "fieldName": "applicationPeriod",
                "excerpt": "휴학신청기간 또는 개강일로부터 4주 이내",
                "sourceType": "html", "sourceLocator": "HTML section:휴학",
                "confidence": 0.99,
            }],
            "confidence": 0.9,
        }],
    })

    grounded = NoticeProcessor.ground_external_structured(notice, structured)

    assert grounded.task_units[0].application_period.start is None
    assert grounded.task_units[0].application_period.end is None
    assert grounded.task_units[0].needs_review is True


def test_unclassified_academic_page_does_not_infer_task_from_exception_body(db):
    item = raw(source_id="academic-season", content="계절수업 유의사항")
    item.update({
        "title": "[상시 학사안내] 계절수업",
        "source_type": "academic_guide",
        "source_metadata": {"sections": [{
            "title": "유의사항", "content": "조기졸업대상자는 신청 학점을 확인해야 합니다.",
        }]},
    })

    notice, _ = NoticeProcessor(db).upsert(item)
    db.commit()

    assert notice.task_units == []


def test_step_labeled_academic_section_becomes_reusable_procedure():
    guide = NoticeProcessor._procedure_from_step_text(
        "leave.general", "휴학원 처리 절차",
        "1. 일반휴학 step 1 종합정보시스템 접속 step 2 일반휴학신청 클릭 "
        "step 3 휴학신청서 작성 step 4 학과장 승인 step 5 교학팀 결재 "
        "2. 입대휴학 step 1 입대휴학신청 클릭",
    )

    assert guide is not None
    assert [step.title for step in guide.steps] == [
        "종합정보시스템 접속", "2단계 진행", "휴학신청서 작성",
    ]
    assert all("학과장" not in step.description and "교학팀" not in step.description for step in guide.steps)
    assert all("입대휴학" not in step.description for step in guide.steps)


def test_pdf_arrow_application_procedure_is_structured_without_model_call():
    content = (
        "[PDF page 4] □지원 방법 경기대학교 Barun을 통해 접수 "
        "[PDF page 5] 참여링크: https://barun.kyonggi.ac.kr/링크 접속 → "
        "외부회원 가입 → 메뉴바에서 “비교과활동” → “웹 개발 집중캠프” 검색 → "
        "첨부파일의 지원서 작성 후 “신청하기”에 정보 입력 및 지원서 업로드 "
        "o지원서 접수 기간: ~7월 12일까지 o최종 선발: 7월 20일"
    )

    method = AIService._mock_application_method("크래프톤 지원", content, "신청")
    guide = AIService._mock_action_guide(
        "크래프톤 지원", content, "신청", method,
        ["https://barun.kyonggi.ac.kr/"],
    )

    assert method == (
        "링크 접속 → 외부회원 가입 → 메뉴바에서 “비교과활동” → "
        "“웹 개발 집중캠프” 검색 → 첨부파일의 지원서 작성 후 “신청하기”에 정보 입력 및 지원서 업로드"
    )
    assert guide is not None
    assert len(guide.steps) == 5
    assert all(step.action_url == "https://barun.kyonggi.ac.kr/" for step in guide.steps)
    assert all(step.link_label == "경기대 Barun 열기" for step in guide.steps)
    assert all(step.source_type == "pdf" for step in guide.steps)
    assert all(step.source_locator == "PDF page 5" for step in guide.steps)


def test_followup_steps_do_not_guess_a_link_when_multiple_sites_are_present():
    guide = AIService._mock_action_guide(
        "외부 프로그램 신청",
        "신청 방법: 링크 접속 → 회원가입 → 지원서 제출",
        "신청",
        "링크 접속 → 회원가입 → 지원서 제출",
        ["https://first.example/apply", "https://second.example/form"],
    )

    assert guide is not None
    assert all(step.action_url is None for step in guide.steps)


def test_program_summary_uses_definition_in_attachment_instead_of_registrar_prefix():
    summary = AIService._mock_notice_summary(
        "경기대학교-크래프톤 정글 웹개발 집중 캠프",
        "등록자 총무구매팀 박연희 (031-280-3169) "
        "[PDF page 7] 본 과정은 경기대학교와 크래프톤에서 함께 주최하는 단기 프로그래밍 캠프 "
        "“12일간의 몰입”으로서, 학생들이 단기간의 합숙과 몰입을 통해 "
        "AI·SW개발을 경험해보는 캠프입니다.",
    )

    assert summary.startswith("경기대학교와 크래프톤에서 함께 주최하는 단기 프로그래밍 캠프입니다.")
    assert "합숙과 몰입" in summary
    assert "등록자" not in summary


def test_application_period_prefers_document_submission_deadline_over_event_dates():
    start, end = extract_application_period(
        "코스 운영 일정: 7월 21일 ~ 8월 3일 "
        "지원서 접수 기간: ~7월 12일(일)까지 최종 선발: 7월 20일",
        datetime.fromisoformat("2026-06-11T09:00:00+09:00"),
    )

    assert start is None
    assert end == datetime.fromisoformat("2026-07-12T23:59:00+09:00")


def test_search_keeps_highest_scoring_section_from_same_notice(db):
    item = raw(source_id="academic-graduation-search", content="졸업 안내")
    item.update({
        "title": "[상시 학사안내] 졸업", "source_type": "academic_guide", "source_priority": 110,
        "source_metadata": {"sections": [
            {"title": "이수구분 표기", "content": "교양필수 교필 전공필수 전필"},
            {"title": "졸업 이수과목 및 학점", "content": "2021~2024학년도 입학자 최소졸업학점 130학점"},
        ]},
    })
    NoticeProcessor(db).upsert(item)
    db.commit()
    ai = AIService()
    question = "2024년도 입학생 졸업요건"

    results = HybridSearch(db, ai).search(question, ai.analyze_query(question), 5)

    assert results[0]["task_unit"].section_title == "졸업 이수과목 및 학점"


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


def test_external_structure_grounds_clear_category_and_explicit_no_documents(db):
    item = raw(source_id="leave-grounding", content="일반휴학 신청방법 및 구비서류없음")
    item["title"] = "[상시 학사안내] 일반휴학 신청 방법"
    notice, _ = NoticeProcessor(db).upsert(item)
    structured = StructuredNotice.model_validate({
        "category": "기타", "applicationPeriod": {}, "eventPeriod": {}, "target": {},
        "requiredDocuments": [],
    })

    grounded = NoticeProcessor.ground_external_structured(notice, structured)

    assert grounded.category == "학사"
    assert grounded.required_documents == ["별도 구비서류 없음"]


def test_external_structure_grounds_national_scholarship_category(db):
    item = raw(source_id="scholarship-grounding", content="국가장학금 1차 신청")
    item["title"] = "2026-2학기 국가장학금 1차 신청 안내"
    notice, _ = NoticeProcessor(db).upsert(item)

    grounded = NoticeProcessor.ground_external_structured(notice, StructuredNotice())

    assert grounded.category == "장학"


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
        "신규휴학신청 선택 → 휴학신청서 작성 및 제출"
    )
