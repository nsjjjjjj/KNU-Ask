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
