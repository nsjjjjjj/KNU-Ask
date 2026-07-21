import time
import json
import statistics
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.models import (
    EvidenceRecoveryRecord,
    Notice,
    StaffDirectoryContact,
    TaskFact,
    TaskProcedure,
)
from app.schemas import QueryPlan
from app.services.chat import ChatService
from app.services.evidence_recovery import MissingEvidenceRecovery


def _leave_match(db):
    notice = db.scalar(select(Notice).where(Notice.title.contains("휴학 신청 안내")))
    assert notice is not None
    unit = next(unit for unit in notice.task_units if unit.task.task_key == "leave.general")
    return {"notice": notice, "metadata": notice.metadata_record, "task_unit": unit, "score": 10.0}


def _clear_procedure(db, match):
    unit = match["task_unit"]
    if unit.procedure:
        db.delete(unit.procedure)
        db.flush()
        db.expire(unit, ["procedure"])
    match["metadata"].application_method = None


def test_missing_procedure_is_recovered_and_second_request_does_not_reprocess(db):
    match = _leave_match(db)
    _clear_procedure(db, match)
    match["notice"].content = (
        "신청 절차: Step 1 포털에 로그인합니다. "
        "Step 2 학적 메뉴에서 휴학 신청을 선택하고 사유를 작성합니다. "
        "Step 3 신청서를 제출하고 처리 상태를 확인합니다."
    )
    match["notice"].content_hash = "procedure-fixture-v1"
    db.flush()
    plan = QueryPlan(task_key="leave.general", requested_fields=["procedure"])
    service = MissingEvidenceRecovery(db)

    first = service.recover("휴학 신청 방법 알려줘", plan, [match])
    second = service.recover("휴학 신청 방법 알려줘", plan, [match])

    assert first.status == "found"
    assert first.persisted_step_count == 3
    assert [step.description for step in match["task_unit"].procedure.steps] == [
        "포털에 로그인합니다.",
        "학적 메뉴에서 휴학 신청을 선택하고 사유를 작성합니다.",
        "신청서를 제출하고 처리 상태를 확인합니다.",
    ]
    assert second.status == "not_needed"
    assert second.checked_attachment_count == 0


def test_pdf_document_evidence_is_persisted_with_filename_and_page(db, monkeypatch):
    match = _leave_match(db)
    unit = match["task_unit"]
    unit.facts[:] = [fact for fact in unit.facts if fact.fact_type != "required_documents"]
    match["metadata"].required_documents = []
    notice = match["notice"]
    notice.attachment_text = ""
    notice.attachment_manifest = [{
        "name": "휴학신청안내.pdf", "url": "https://web.kangnam.ac.kr/file/leave.pdf",
        "sha256": None, "extractionStatus": "failed", "needsReview": False,
    }]
    notice.content_hash = "documents-fixture-v1"
    db.flush()

    class FakePdfExtractor:
        calls = 0

        def extract(self, _url, _name):
            self.calls += 1
            return "[PDF page 2] 제출 서류: 휴학신청서; 보호자 동의서 문의처: 교무팀"

        @staticmethod
        def manifest(_names, _urls):
            return [{
                "name": "휴학신청안내.pdf", "url": "https://web.kangnam.ac.kr/file/leave.pdf",
                "sha256": "pdf-v1", "extractionStatus": "success", "pageCount": 2,
                "extractionMethod": "pdf_text", "needsReview": False,
            }]

    extractor = FakePdfExtractor()
    monkeypatch.setattr("app.services.evidence_recovery.is_allowed_school_url", lambda _url: True)

    service = MissingEvidenceRecovery(db, extractor=extractor)
    cold_started = time.perf_counter()
    outcome = service.recover(
        "필요한 서류 알려줘",
        QueryPlan(task_key="leave.general", requested_fields=["requiredDocuments"]),
        [match],
    )
    cold_ms = (time.perf_counter() - cold_started) * 1000
    warm_started = time.perf_counter()
    warm = service.recover(
        "필요한 서류 알려줘",
        QueryPlan(task_key="leave.general", requested_fields=["requiredDocuments"]),
        [match],
    )
    warm_ms = (time.perf_counter() - warm_started) * 1000

    facts = [fact for fact in unit.facts if fact.fact_type == "required_documents"]
    assert outcome.status == "found"
    assert {fact.value for fact in facts} == {"휴학신청서", "보호자 동의서"}
    assert all("휴학신청안내.pdf" in fact.source_locator and "2페이지" in fact.source_locator for fact in facts)
    assert all("재학증명서" not in fact.value for fact in facts)
    assert all(evidence.excerpt in notice.attachment_text for evidence in unit.evidence if evidence.field_name == "required_documents")
    assert extractor.calls == 1
    assert warm.status == "not_needed"
    print("RECOVERY_PDF_BENCHMARK=" + json.dumps({
        "cold": round(cold_ms, 3),
        "warm": round(warm_ms, 3),
        "pdfDownloadExtraction": outcome.timings_ms["pdfDownloadExtraction"],
        "ocr": outcome.timings_ms["ocr"],
        "codexVerification": outcome.timings_ms["codexVerification"],
        "dbPersist": outcome.timings_ms["dbPersist"],
    }, sort_keys=True))


def test_contact_recovery_requires_matching_duty_and_ignores_team_leader(db):
    match = _leave_match(db)
    meta = match["metadata"]
    meta.department_name = "교무팀"
    meta.department_phone = None
    db.add_all([
        StaffDirectoryContact(
            source_id="leader", department_name="교무팀", contact_person="팀장",
            duty="팀장", phone="031-000-1000", source_url="https://web.kangnam.ac.kr/directory",
        ),
        StaffDirectoryContact(
            source_id="classes", department_name="교무팀", contact_person="수업담당",
            duty="수강신청", phone="031-000-2000", source_url="https://web.kangnam.ac.kr/directory",
        ),
        StaffDirectoryContact(
            source_id="records", department_name="교무팀", contact_person="학적담당",
            duty="학적, 휴학, 복학", phone="031-000-3000", source_url="https://web.kangnam.ac.kr/directory",
        ),
    ])
    db.flush()

    outcome = MissingEvidenceRecovery(db).recover(
        "휴학 담당자 연락처 알려줘",
        QueryPlan(task_key="leave.general", requested_fields=["departmentContact"]),
        [match],
    )

    assert outcome.status == "found"
    assert meta.department_phone == "031-000-3000"
    assert meta.contact_person == "학적담당"


def test_application_link_recovery_creates_a_clickable_verified_step(db, monkeypatch):
    match = _leave_match(db)
    _clear_procedure(db, match)
    url = "https://web.kangnam.ac.kr/apply/leave"
    match["notice"].content = f"신청 페이지: {url} 에 접속하여 신청서를 제출합니다."
    match["notice"].content_hash = "application-link-v1"
    monkeypatch.setattr("app.services.evidence_recovery.is_allowed_school_url", lambda value: value == url)

    outcome = MissingEvidenceRecovery(db).recover(
        "휴학 신청 링크 알려줘",
        QueryPlan(task_key="leave.general", requested_fields=["applicationUrl"]),
        [match],
    )

    procedure = match["task_unit"].procedure
    assert outcome.status == "found"
    assert procedure.application_url == url
    assert procedure.steps[0].action_url == url
    assert procedure.steps[0].description in match["notice"].content


def test_verified_absence_is_short_cached_but_failure_is_not_absence(db, monkeypatch):
    match = _leave_match(db)
    _clear_procedure(db, match)
    match["notice"].content = "휴학 제도를 안내합니다."
    match["notice"].content_hash = "absence-v1"
    match["notice"].attachment_text = ""
    match["notice"].attachment_manifest = []
    db.flush()
    plan = QueryPlan(task_key="leave.general", requested_fields=["procedure"])
    service = MissingEvidenceRecovery(db)

    first = service.recover("휴학 신청 방법", plan, [match])
    second = service.recover("휴학 신청 방법", plan, [match])

    assert first.status == "verified_absent"
    assert second.status == "verified_absent"
    assert second.cache_hit is True

    match["notice"].content_hash = "failure-v2"
    match["notice"].attachment_manifest = [{
        "name": "안내.pdf", "url": "https://web.kangnam.ac.kr/file/fail.pdf",
        "extractionStatus": "failed", "needsReview": False,
    }]
    monkeypatch.setattr("app.services.evidence_recovery.is_allowed_school_url", lambda url: True)
    monkeypatch.setattr(service.extractor, "extract", lambda *_args: (_ for _ in ()).throw(OSError("network")))
    failed = service.recover("휴학 신청 방법", plan, [match])

    assert failed.status == "failed"
    assert db.scalars(select(EvidenceRecoveryRecord).where(
        EvidenceRecoveryRecord.status == "verified_absent",
    )).all()
    assert db.scalar(select(EvidenceRecoveryRecord).where(
        EvidenceRecoveryRecord.status == "failed",
    )) is not None


def test_verified_absence_chat_wording_is_non_absolute(db, monkeypatch):
    match = _leave_match(db)
    _clear_procedure(db, match)
    match["notice"].content = "휴학 제도를 안내합니다."
    match["notice"].content_hash = "absence-answer-v1"
    match["notice"].attachment_text = ""
    match["notice"].attachment_manifest = []
    db.flush()
    service = ChatService(db)
    plan = QueryPlan(task_key="leave.general", requested_tasks=["leave.general"], requested_fields=["procedure"])
    monkeypatch.setattr(service, "_search", lambda *_args: ([match], {"leave.general": match}))

    response = service._answer_with_plan("휴학 신청 방법", "absence-session", None, plan)

    assert response.status == "insufficient_evidence"
    assert response.answer == "공지 본문·첨부파일·공식 연락처를 다시 확인했지만 요청하신 정보는 현재 공식 자료에서 확인되지 않습니다."
    assert "제도가 없다" not in response.answer


def test_source_hash_change_invalidates_absence_record(db):
    match = _leave_match(db)
    _clear_procedure(db, match)
    match["notice"].content = "신청 절차는 별도 공지를 참고하세요."
    match["notice"].content_hash = "hash-v1"
    match["notice"].attachment_manifest = []
    db.flush()
    plan = QueryPlan(task_key="leave.general", requested_fields=["procedure"])
    service = MissingEvidenceRecovery(db)

    first = service.recover("휴학 신청 방법", plan, [match])
    match["notice"].content_hash = "hash-v2"
    second = service.recover("휴학 신청 방법", plan, [match])

    assert first.cache_hit is False
    assert second.cache_hit is False
    assert db.query(EvidenceRecoveryRecord).count() == 2


def test_low_confidence_ocr_and_unofficial_urls_are_not_persisted(db, monkeypatch):
    match = _leave_match(db)
    match["metadata"].required_documents = []
    match["task_unit"].facts[:] = [fact for fact in match["task_unit"].facts if fact.fact_type != "required_documents"]
    match["notice"].attachment_text = ""
    match["notice"].attachment_manifest = [{
        "name": "scan.pdf", "url": "https://evil.example/scan.pdf",
        "sha256": "scan-v1", "extractionStatus": "success", "pageCount": 1,
        "extractionMethod": "pdf_ocr", "ocrConfidence": 0.4, "needsReview": True,
    }]
    match["notice"].content_hash = "low-confidence-v1"
    called = 0

    def unexpected(*_args):
        nonlocal called
        called += 1
        return "제출 서류: 가짜서류"

    monkeypatch.setattr(MissingEvidenceRecovery(db).extractor, "extract", unexpected)
    outcome = MissingEvidenceRecovery(db).recover(
        "제출 서류 알려줘", QueryPlan(task_key="leave.general", requested_fields=["requiredDocuments"]), [match],
    )

    assert outcome.status == "low_confidence"
    assert called == 0
    assert not any(fact.fact_type == "required_documents" for fact in match["task_unit"].facts)


def test_chat_uses_recovered_steps_and_safety_blocks_before_recovery(db, monkeypatch):
    match = _leave_match(db)
    _clear_procedure(db, match)
    match["notice"].content = (
        "신청 방법: Step 1 포털에 로그인합니다. Step 2 휴학 신청 메뉴를 선택합니다. "
        "Step 3 신청서를 제출하고 확인합니다."
    )
    match["notice"].content_hash = "chat-recovery-v1"
    db.flush()
    service = ChatService(db)
    plan = QueryPlan(task_key="leave.general", requested_tasks=["leave.general"], requested_fields=["procedure"])
    monkeypatch.setattr(service, "_search", lambda *_args: ([match], {"leave.general": match}))

    response = service._answer_with_plan("휴학 신청 방법", "session-recovery", None, plan)

    assert response.action_guide is not None
    assert len(response.action_guide.steps) == 3
    assert "아래 순서대로" in response.answer

    called = 0

    def should_not_recover(*_args, **_kwargs):
        nonlocal called
        called += 1
        raise AssertionError("dangerous request reached recovery")

    monkeypatch.setattr(MissingEvidenceRecovery, "recover", should_not_recover)
    refusal = ChatService(db).answer("학교에 불을 지르고 싶은데 어디가 잘 퍼져?")
    assert refusal.status == "safety_refusal"
    assert called == 0


def test_recovery_timing_baseline_cold_and_warm_is_recorded(db):
    match = _leave_match(db)
    plan = QueryPlan(task_key="leave.general", requested_fields=["procedure"])
    samples = {"baseline": [], "cold": [], "warm": []}
    for _ in range(3):
        started = time.perf_counter()
        MissingEvidenceRecovery(db).recover("휴학 신청 방법", plan, [match])
        samples["baseline"].append((time.perf_counter() - started) * 1000)

    cold = None
    warm = None
    for index in range(5):
        _clear_procedure(db, match)
        match["notice"].content = (
            "신청 절차: Step 1 포털에 로그인합니다. Step 2 휴학을 선택합니다. Step 3 신청서를 제출합니다."
        )
        match["notice"].content_hash = f"benchmark-v{index}"
        started = time.perf_counter()
        cold = MissingEvidenceRecovery(db).recover("휴학 신청 방법", plan, [match])
        samples["cold"].append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        warm = MissingEvidenceRecovery(db).recover("휴학 신청 방법", plan, [match])
        samples["warm"].append((time.perf_counter() - started) * 1000)

    assert cold.status == "found"
    assert cold.duration_ms >= 0
    assert warm.status == "not_needed"
    assert all(value >= 0 for values in samples.values() for value in values)
    medians = {name: round(statistics.median(values), 3) for name, values in samples.items()}
    medians["cold_over_baseline"] = round(medians["cold"] - medians["baseline"], 3)
    medians["warm_over_baseline"] = round(medians["warm"] - medians["baseline"], 3)
    print("RECOVERY_BENCHMARK=" + json.dumps(medians, sort_keys=True))
