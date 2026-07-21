from app.models import StaffDirectoryContact
from app.services.staff_directory import resolve_staff_contact, sync_staff_directory


def test_staff_directory_sync_and_duty_resolution(db):
    records = [
        {
            "source_id": "directory:1", "source_type": "staff_directory", "department_name": "교무팀",
            "content": "부서: 교무팀. 담당자 및 업무: 차우석 (교육과정,수강신청). 문의 전화번호: 031-280-3543.",
            "source_url": "https://web.kangnam.ac.kr/directory",
            "source_metadata": {"contactPerson": "차우석", "duty": "교육과정,수강신청", "phone": "031-280-3543"},
        },
        {
            "source_id": "directory:2", "source_type": "staff_directory", "department_name": "교무팀",
            "content": "부서: 교무팀. 담당자 및 업무: 조호성 (학적,성적). 문의 전화번호: 031-280-3542.",
            "source_url": "https://web.kangnam.ac.kr/directory",
            "source_metadata": {"contactPerson": "조호성", "duty": "학적,성적", "phone": "031-280-3542"},
        },
    ]

    assert sync_staff_directory(db, records) == 2
    result = resolve_staff_contact(db, "교무팀", "2026학년도 2학기 수강신청 일정")

    assert result is not None
    assert result.contact_person == "차우석"
    assert result.phone == "031-280-3543"
    assert db.query(StaffDirectoryContact).count() == 2

    leave = resolve_staff_contact(db, "교무팀", "휴학 신청 담당자 연락처")
    assert leave is not None
    assert leave.contact_person == "조호성"


def test_electronic_attendance_resolves_to_class_operations_contact(db):
    records = [
        {
            "source_id": "directory:leader", "source_type": "staff_directory", "department_name": "교무팀",
            "source_url": "https://web.kangnam.ac.kr/directory",
            "source_metadata": {"contactPerson": "팀장", "duty": "팀장", "phone": "031-280-3541"},
        },
        {
            "source_id": "directory:classes", "source_type": "staff_directory", "department_name": "교무팀",
            "source_url": "https://web.kangnam.ac.kr/directory",
            "source_metadata": {"contactPerson": "수업담당", "duty": "수업,강의평가", "phone": "031-280-3544"},
        },
    ]
    sync_staff_directory(db, records)

    result = resolve_staff_contact(db, "교무팀", "모바일 전자출결 사용 방법")

    assert result.contact_person == "수업담당"
    assert result.phone == "031-280-3544"
