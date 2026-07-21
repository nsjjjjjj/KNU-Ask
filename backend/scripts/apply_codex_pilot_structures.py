"""Codex가 원문·이미지를 검토한 대표 공지의 구조화 결과를 적용한다."""

from __future__ import annotations

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.session import SessionLocal
from app.models import Notice
from app.schemas import StructuredNotice
from app.services.processing import NoticeProcessor


KST = ZoneInfo("Asia/Seoul")


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=KST)


def course_registration() -> StructuredNotice:
    main = {"start": dt("2026-08-05T10:00"), "end": dt("2026-08-06T23:59")}
    procedure = {
        "taskName": "2026-2학기 수강신청",
        "summary": "수강신청시스템에서 강좌와 시간표를 확인한 뒤 신청 결과를 확인합니다.",
        "steps": [
            {"order": 1, "title": "수강신청시스템 로그인", "description": "수강신청시스템에 로그인합니다.", "actionType": "navigate", "sourceType": "body_image", "sourceLocator": "본문 이미지 1", "confidence": 0.95},
            {"order": 2, "title": "강좌·시간표 및 잔여인원 확인", "description": "강의시간표와 강좌별 신청 가능 잔여인원을 확인합니다.", "actionType": "verify", "sourceType": "body_image", "sourceLocator": "본문 이미지 1", "confidence": 0.95},
            {"order": 3, "title": "과목 수강신청", "description": "신청할 과목을 선택해 수강신청을 완료합니다.", "actionType": "submit", "sourceType": "body_image", "sourceLocator": "본문 이미지 1", "confidence": 0.9},
            {"order": 4, "title": "신청 결과 확인", "description": "신청 과목과 수강대기 상태를 최종 확인합니다.", "actionType": "verify", "sourceType": "body_image", "sourceLocator": "본문 이미지 1", "confidence": 0.9},
        ],
        "confidence": 0.93,
    }
    important = [
        {"label": "시스템 테스트 기간", "start": dt("2026-07-08T10:00"), "end": dt("2026-07-09T12:00"), "description": "전체 재학생 대상", "sourceLocator": "본문 이미지 1"},
        {"label": "예비수강신청", "start": dt("2026-07-14T10:00"), "end": dt("2026-07-15T15:00"), "description": "전체 재학생 대상", "sourceLocator": "본문 이미지 1"},
        {"label": "잔여인원 조회", "start": dt("2026-08-03T14:00"), "description": "강좌별 신청 가능 잔여인원 조회", "sourceLocator": "본문 이미지 1"},
        {"label": "장애학생 선 수강신청", "start": dt("2026-08-04T10:00"), "end": dt("2026-08-04T15:00"), "sourceLocator": "본문 이미지 1"},
        {"label": "수강신청 변경기간", "start": dt("2026-09-01T10:00"), "end": dt("2026-09-07T23:59"), "sourceLocator": "본문 이미지 1"},
    ]
    return StructuredNotice.model_validate({
        "category": "학사", "subCategory": "수강신청", "academicYear": 2026, "semester": 2,
        "applicationPeriod": main, "eventPeriod": {}, "target": {"studentTypes": ["전체 재학생"]},
        "actionType": "신청", "applicationMethod": "수강신청시스템 로그인 → 강좌·시간표 확인 → 과목 신청 → 신청 결과 확인",
        "importantDates": important,
        "additionalFacts": [
            {"factType": "eligibility", "label": "재입학자 신청 가능 시점", "value": "본 수강신청부터 가능", "sourceType": "body_image", "sourceLocator": "본문 이미지 1", "confidence": 0.95},
            {"factType": "credit_carryover", "label": "잔여학점 이월", "value": "미신청 잔여학점은 최대 3학점까지 다음 학기로 이월 가능", "sourceType": "body_image", "sourceLocator": "본문 이미지 4", "confidence": 0.9},
        ],
        "evidenceMap": {"applicationPeriod": "본문 이미지 1의 수강신청 기간 안내 표", "department": "공지 본문의 등록자 정보"},
        "department": {"name": "교무팀", "contactPerson": "차우석", "phone": "031-280-3543"},
        "keywords": ["수강신청", "2026-2학기", "강의시간표"], "synonyms": ["본 수강신청", "과목 신청"],
        "noticeStatus": "upcoming", "confidence": 0.96, "actionGuide": procedure,
        "taskUnits": [{
            "taskKey": "course.registration", "taskName": "2026-2학기 수강신청", "parentTask": "course",
            "sectionTitle": "수강신청 기간 안내", "summary": "2026-2학기 본 수강신청과 변경기간 안내",
            "aliases": ["수강신청", "본 수강신청", "과목 신청"], "excludedIntents": ["수강신청 변경만", "계절수업"],
            "academicYear": 2026, "semester": 2, "target": {"studentTypes": ["전체 재학생"]},
            "applicationPeriod": main, "eventPeriod": {},
            "facts": [
                {"factType": "date", "label": "시스템 테스트 기간", "value": "2026.07.08 10:00 ~ 2026.07.09 12:00", "sourceLocator": "본문 이미지 1", "confidence": 0.95},
                {"factType": "date", "label": "예비수강신청", "value": "2026.07.14 10:00 ~ 2026.07.15 15:00", "sourceLocator": "본문 이미지 1", "confidence": 0.95},
                {"factType": "date", "label": "잔여인원 조회", "value": "2026.08.03 14:00 이후", "sourceLocator": "본문 이미지 1", "confidence": 0.95},
                {"factType": "date", "label": "장애학생 선 수강신청", "value": "2026.08.04 10:00 ~ 15:00", "sourceLocator": "본문 이미지 1", "confidence": 0.95},
                {"factType": "date", "label": "수강신청 변경기간", "value": "2026.09.01 10:00 ~ 2026.09.07 23:59", "sourceLocator": "본문 이미지 1", "confidence": 0.95},
            ],
            "evidence": [{"fieldName": "applicationPeriod", "excerpt": "수강신청 2026.08.05.(수) 10:00 ~ 08.06.(목) 23:59", "sourceType": "body_image", "sourceLocator": "본문 이미지 1", "confidence": 1.0}],
            "procedure": procedure, "confidence": 0.96,
        }, {
            "taskKey": "course.change", "taskName": "2026-2학기 수강신청 변경", "parentTask": "course",
            "sectionTitle": "수강신청 변경기간", "summary": "신청 과목을 추가·취소하는 2026-2학기 변경기간",
            "aliases": ["수강신청 변경", "수강변경", "정정기간"], "excludedIntents": ["본 수강신청만"],
            "academicYear": 2026, "semester": 2, "target": {"studentTypes": ["수강신청 변경 희망자"]},
            "applicationPeriod": {"start": dt("2026-09-01T10:00"), "end": dt("2026-09-07T23:59")}, "eventPeriod": {},
            "facts": [{"factType": "date", "label": "수강신청 변경기간", "value": "2026.09.01 10:00 ~ 2026.09.07 23:59", "sourceLocator": "본문 이미지 1", "confidence": 1.0}],
            "evidence": [{"fieldName": "applicationPeriod", "excerpt": "수강신청 변경기간 2026.09.01.(화) 10:00 ~ 09.07.(월) 23:59", "sourceType": "body_image", "sourceLocator": "본문 이미지 1", "confidence": 1.0}],
            "procedure": {
                "taskName": "2026-2학기 수강신청 변경",
                "summary": "수강신청시스템에서 기존 신청 내역을 확인하고 과목을 추가·취소합니다.",
                "steps": [
                    {"order": 1, "title": "수강신청시스템 로그인", "description": "변경기간에 수강신청시스템에 로그인합니다.", "actionType": "navigate", "sourceType": "body_image", "sourceLocator": "본문 이미지 1", "confidence": 0.9},
                    {"order": 2, "title": "기존 신청 내역 확인", "description": "현재 신청된 과목과 잔여 신청 가능 학점을 확인합니다.", "actionType": "verify", "sourceType": "body_image", "sourceLocator": "본문 이미지 1", "confidence": 0.9},
                    {"order": 3, "title": "과목 추가·취소", "description": "변경할 과목을 추가하거나 취소합니다.", "actionType": "submit", "sourceType": "body_image", "sourceLocator": "본문 이미지 1", "confidence": 0.9},
                    {"order": 4, "title": "변경 결과 확인", "description": "최종 수강신청 내역을 다시 확인합니다.", "actionType": "verify", "sourceType": "body_image", "sourceLocator": "본문 이미지 1", "confidence": 0.9},
                ],
                "confidence": 0.9,
            },
            "confidence": 0.96,
        }],
    })


def course_registration_guide() -> StructuredNotice:
    step_rows = [
        ("공지·강의시간표 확인", "수강신청 안내와 강의시간표 공지에서 개설 강좌와 안내사항을 확인합니다.", "verify"),
        ("수강 계획 수립", "졸업요건을 고려해 필요한 강좌와 시간표를 정리합니다.", "other"),
        ("수강신청시스템 접속", "학교 자주찾는서비스의 수강신청시스템 배너를 선택해 시스템에 접속합니다.", "navigate"),
        ("강좌 수강신청", "수강신청 기간 안에 계획한 강좌를 신청하고 결과를 확인합니다.", "submit"),
        ("수강신청 확인서 확인", "수강신청 확인서를 출력해 최종 신청 내역을 확인합니다.", "verify"),
    ]
    steps = [{
        "order": order, "title": title, "description": description, "actionType": action_type,
        "actor": "student", "studentActionRequired": True, "sourceType": "html",
        "sourceLocator": "HTML section:수강신청방법", "confidence": 1.0,
    } for order, (title, description, action_type) in enumerate(step_rows, start=1)]
    procedure = {
        "taskName": "수강신청", "summary": "강좌를 확인하고 계획을 세운 뒤 수강신청 결과를 확인합니다.",
        "steps": steps, "confidence": 1.0,
    }
    return StructuredNotice.model_validate({
        "category": "학사", "subCategory": "수강신청", "applicationPeriod": {}, "eventPeriod": {},
        "target": {"studentTypes": ["수강신청 대상 학생"]}, "actionType": "신청",
        "applicationMethod": "강의시간표 확인 → 수강 계획 수립 → 수강신청시스템 접속 → 강좌 신청 → 신청 결과 확인",
        "department": {"name": "교무팀"},
        "keywords": ["수강신청", "강의시간표", "수강신청시스템", "수강신청 확인서"],
        "evidenceMap": {
            "applicationMethod": "step 1 수강신청 안내 및 강의시간표 공지사항을 확인하여 개설된 강좌 확인 및 안내사항 숙지",
        },
        "noticeStatus": "always", "confidence": 1.0, "actionGuide": procedure,
        "taskUnits": [{
            "taskKey": "course.registration", "taskName": "수강신청", "parentTask": "course",
            "sectionTitle": "수강신청방법", "summary": "강좌 확인부터 수강신청 확인서 검토까지의 학생 절차",
            "aliases": ["수강신청", "과목 신청"], "excludedIntents": ["수강포기", "수강신청 변경"],
            "target": {"studentTypes": ["수강신청 대상 학생"]},
            "applicationPeriod": {}, "eventPeriod": {},
            "facts": [
                {
                    "factType": "credit_limit", "label": "일반학기 신청 가능 학점",
                    "value": "최저 12학점, 최대 19학점", "sourceType": "html",
                    "sourceLocator": "HTML section:수강신청 학점", "confidence": 1.0,
                },
                {
                    "factType": "credit_limit", "label": "졸업 최종학기 최저 학점",
                    "value": "9학점", "sourceType": "html",
                    "sourceLocator": "HTML section:수강신청 학점", "confidence": 1.0,
                },
            ],
            "evidence": [{
                "fieldName": "applicationMethod",
                "excerpt": "step 1 수강신청 안내 및 강의시간표 공지사항을 확인하여 개설된 강좌 확인 및 안내사항 숙지",
                "sourceType": "html", "sourceLocator": "HTML section:수강신청방법", "confidence": 1.0,
            }],
            "procedure": procedure, "confidence": 1.0,
        }],
    })


def credit_registration() -> StructuredNotice:
    payment_period = {"start": dt("2026-09-09T00:00"), "end": dt("2026-09-11T17:00")}
    course_period = {"start": dt("2026-07-14T10:00"), "end": dt("2026-07-15T15:00")}
    payment_procedure = {
        "taskName": "학점등록대상자 등록금 납부",
        "summary": "고지서를 출력하고 은행 창구 또는 고지서의 가상계좌로 납부합니다.",
        "warnings": ["수강학점 변경 후 최종 등록금액을 확인하고 납부하세요.", "납부기간에는 매일 17시에 마감됩니다."],
        "steps": [
            {"order": 1, "title": "종합정보시스템 로그인", "description": "종합정보시스템에 로그인합니다.", "actionType": "navigate", "sourceType": "attachment_image", "sourceLocator": "스크린샷 2026-07-15 130354.png", "confidence": 1.0},
            {"order": 2, "title": "등록관리 메뉴 이동", "description": "등록관리 메뉴로 이동합니다.", "actionType": "navigate", "sourceType": "attachment_image", "sourceLocator": "스크린샷 2026-07-15 130354.png", "confidence": 1.0},
            {"order": 3, "title": "등록금 고지서 출력", "description": "본인의 최종 수강학점과 등록금액을 확인한 뒤 고지서를 출력합니다.", "actionType": "verify", "sourceType": "attachment_image", "sourceLocator": "스크린샷 2026-07-15 130354.png", "confidence": 1.0},
            {"order": 4, "title": "등록금 납부", "description": "은행 창구에서 직접 납부하거나 고지서상의 가상계좌로 송금합니다.", "actionType": "pay", "sourceType": "attachment_image", "sourceLocator": "스크린샷 2026-07-15 130354.png", "confidence": 1.0},
        ],
        "confidence": 1.0,
    }
    combined_procedure = {
        "taskName": "학점등록대상자 수강신청 및 등록금 납부",
        "summary": "수강신청을 먼저 완료한 뒤 최종 학점과 고지 금액을 확인하고 등록금을 납부합니다.",
        "warnings": ["수강학점 변경 후 최종 등록금액을 확인하고 납부하세요.", "납부기간에는 매일 17시에 마감됩니다."],
        "steps": [
            {"order": 1, "title": "학점등록대상자 수강신청", "description": "2026.07.14 10:00부터 07.15 15:00까지 수강할 과목을 신청합니다.", "actionType": "submit", "sourceType": "attachment_image", "sourceLocator": "스크린샷 2026-07-15 130342.png", "confidence": 1.0},
            {"order": 2, "title": "최종 수강학점 확인", "description": "수강신청 변경 사항을 반영한 최종 수강학점을 확인합니다.", "actionType": "verify", "sourceType": "attachment_image", "sourceLocator": "스크린샷 2026-07-15 130354.png", "confidence": 1.0},
            {"order": 3, "title": "등록금 고지서 출력", "description": "종합정보시스템의 등록관리에서 최종 등록금액을 확인하고 고지서를 출력합니다.", "actionType": "verify", "sourceType": "attachment_image", "sourceLocator": "스크린샷 2026-07-15 130354.png", "confidence": 1.0},
            {"order": 4, "title": "등록금 납부", "description": "2026.09.09부터 09.11 17:00까지 은행 창구 또는 고지서상의 가상계좌로 납부합니다.", "actionType": "pay", "sourceType": "attachment_image", "sourceLocator": "스크린샷 2026-07-15 130354.png", "confidence": 1.0},
        ],
        "confidence": 1.0,
    }
    tiers = [
        ("1학점 미만 또는 학적만 유지", "해당 학기 등록금의 10분의 1"),
        ("1~3학점", "해당 학기 등록금의 6분의 1"),
        ("4~6학점", "해당 학기 등록금의 3분의 1"),
        ("7~9학점", "해당 학기 등록금의 2분의 1"),
        ("10학점 이상", "해당 학기 등록금 전액"),
    ]
    facts = [
        {"factType": "fee", "label": label, "value": value, "sourceLocator": "스크린샷 2026-07-15 130354.png", "confidence": 1.0}
        for label, value in tiers
    ]
    return StructuredNotice.model_validate({
        "category": "등록", "subCategory": "학점등록대상자", "academicYear": 2026, "semester": 2,
        "applicationPeriod": payment_period, "eventPeriod": {}, "target": {"studentTypes": ["정규학기 초과 재학자", "학사학위취득유예자", "졸업미비자"]},
        "actionType": "납부", "applicationMethod": "종합정보시스템 로그인 → 등록관리 → 등록금 고지서 출력 → 은행 창구 직접 납부 또는 고지서상 가상계좌 송금",
        "feeInformation": "; ".join(f"{label}: {value}" for label, value in tiers),
        "importantDates": [
            {"label": "학점등록대상자 수강신청", **course_period, "sourceLocator": "스크린샷 2026-07-15 130342.png"},
            {"label": "등록금 납부 기간", **payment_period, "description": "납부기간 중 매일 17시 마감", "sourceLocator": "스크린샷 2026-07-15 130354.png"},
            {"label": "등록금 고지서 조회", "start": dt("2026-09-09T00:00"), "description": "변경 시 SMS 발송", "sourceLocator": "스크린샷 2026-07-15 130342.png"},
        ],
        "department": {"name": "회계경리팀", "phone": "031-280-3563"},
        "keywords": ["학점등록대상자", "등록금 납부", "수강신청"], "synonyms": ["학점등록", "정규학기 초과자"],
        "evidenceMap": {"applicationPeriod": "스크린샷 2026-07-15 130342.png", "applicationMethod": "스크린샷 2026-07-15 130354.png", "feeInformation": "스크린샷 2026-07-15 130354.png"},
        "noticeStatus": "upcoming", "confidence": 1.0, "actionGuide": combined_procedure,
        "taskUnits": [
            {
                "taskKey": "tuition.credit", "taskName": "학점등록대상자 수강신청 및 등록금 납부", "parentTask": "tuition",
                "sectionTitle": "수강신청 및 등록금 납부", "summary": "수강신청 후 최종 학점에 따른 등록금을 납부하는 전체 절차",
                "aliases": ["학점등록대상자", "학점등록 수강신청과 납부", "학점등록 전체 절차"],
                "academicYear": 2026, "semester": 2,
                "target": {"studentTypes": ["정규학기 초과 재학자", "학사학위취득유예자", "졸업미비자"]},
                "applicationPeriod": course_period, "eventPeriod": {},
                "facts": [
                    {"factType": "date", "label": "수강신청 기간", "value": "2026.07.14 10:00 ~ 2026.07.15 15:00", "sourceLocator": "스크린샷 2026-07-15 130342.png", "confidence": 1.0},
                    {"factType": "date", "label": "등록금 납부 기간", "value": "2026.09.09 ~ 2026.09.11 17:00", "sourceLocator": "스크린샷 2026-07-15 130354.png", "confidence": 1.0},
                    *facts,
                ],
                "evidence": [
                    {"fieldName": "applicationPeriod", "excerpt": "수강신청 2026.07.14.(화) 10:00 ~ 07.15.(수) 15:00", "sourceType": "attachment_image", "sourceLocator": "스크린샷 2026-07-15 130342.png", "confidence": 1.0},
                    {"fieldName": "applicationMethod", "excerpt": "등록금 고지서 출력 후 은행 창구 또는 고지서상의 가상계좌로 납부", "sourceType": "attachment_image", "sourceLocator": "스크린샷 2026-07-15 130354.png", "confidence": 1.0},
                ],
                "procedure": combined_procedure, "confidence": 1.0,
            },
            {
                "taskKey": "tuition.credit.course", "taskName": "학점등록대상자 수강신청", "parentTask": "tuition.credit",
                "sectionTitle": "수강신청 및 등록금 납부 기간", "summary": "학점등록대상자의 2026-2학기 수강신청 기간",
                "aliases": ["학점등록대상자 수강신청", "학점등록 수강신청"], "academicYear": 2026, "semester": 2,
                "target": {"studentTypes": ["정규학기 초과 재학자"]}, "applicationPeriod": course_period, "eventPeriod": {},
                "facts": [{"factType": "date", "label": "수강신청 기간", "value": "2026.07.14 10:00 ~ 2026.07.15 15:00", "sourceLocator": "스크린샷 2026-07-15 130342.png", "confidence": 1.0}],
                "evidence": [{"fieldName": "applicationPeriod", "excerpt": "2026.07.14.(화) 10:00 ~ 07.15.(수) 15:00", "sourceType": "attachment_image", "sourceLocator": "스크린샷 2026-07-15 130342.png", "confidence": 1.0}],
                "confidence": 1.0,
            },
            {
                "taskKey": "tuition.credit.payment", "taskName": "학점등록대상자 등록금 납부", "parentTask": "tuition.credit",
                "sectionTitle": "등록금 납부 방법", "summary": "학점등록대상자의 등록금 고지서 출력 및 납부 방법",
                "aliases": ["학점등록대상자 등록금 납부", "학점등록금 납부"], "academicYear": 2026, "semester": 2,
                "target": {"studentTypes": ["정규학기 초과 재학자", "학사학위취득유예자", "졸업미비자"]},
                "applicationPeriod": payment_period, "eventPeriod": {}, "facts": facts,
                "evidence": [{"fieldName": "applicationMethod", "excerpt": "종합정보시스템 로그인 → 등록관리 → 등록금 고지서 출력 → 은행 창구 직접 납부 또는 고지서상의 가상계좌로 송금", "sourceType": "attachment_image", "sourceLocator": "스크린샷 2026-07-15 130354.png", "confidence": 1.0}],
                "procedure": payment_procedure, "confidence": 1.0,
            },
        ],
    })


def general_leave() -> StructuredNotice:
    procedure = {
        "taskName": "일반휴학",
        "summary": "웹 종합정보시스템에서 일반휴학을 신청하고 학과와 교학팀의 결재 상태를 확인합니다.",
        "warnings": ["모바일에서는 신청할 수 없습니다.", "신청 후 소속 학과(부)장 승인을 거쳐 교학팀이 최종 처리합니다."],
        "steps": [
            {"order": 1, "title": "종합정보시스템 접속", "description": "학교 홈페이지에서 웹 종합정보시스템에 로그인합니다.", "actionType": "navigate", "sourceType": "html", "sourceLocator": "HTML section:휴학원 처리 절차", "confidence": 1.0},
            {"order": 2, "title": "일반휴학 신규 신청 선택", "description": "학적변동관리 → 일반휴학신청 → 신규휴학신청을 선택합니다.", "actionType": "navigate", "sourceType": "html", "sourceLocator": "HTML section:휴학원 처리 절차", "confidence": 1.0},
            {"order": 3, "title": "휴학신청서 작성", "description": "일반휴학 기간과 신청 내용을 입력해 휴학신청서를 제출합니다. 일반휴학은 별도 구비서류가 없습니다.", "actionType": "submit", "sourceType": "html", "sourceLocator": "HTML section:휴학 종류 및 신청방법", "confidence": 1.0},
            {"order": 4, "title": "학과장·전공주임 승인", "description": "소속 학과장 또는 전공주임교수의 승인을 기다립니다.", "actionType": "verify", "sourceType": "html", "sourceLocator": "HTML section:휴학원 처리 절차", "confidence": 1.0},
            {"order": 5, "title": "교학팀 결재 확인", "description": "종합정보시스템에서 교학팀의 최종 결재 완료 상태를 확인합니다.", "actionType": "verify", "sourceType": "html", "sourceLocator": "HTML section:기타", "confidence": 1.0},
        ],
        "confidence": 1.0,
    }
    eligibility = [
        "입학 첫 학기 신입생·편입생·재입학생은 원칙적으로 일반휴학을 신청할 수 없음",
        "9학기 이상 등록자는 원칙적으로 가정 사정에 따른 일반휴학을 신청할 수 없음",
        "재학 중 일반휴학은 총 3회까지 가능하며 1회에 1~2학기를 선택",
    ]
    return StructuredNotice.model_validate({
        "category": "학사", "subCategory": "일반휴학", "applicationPeriod": {}, "eventPeriod": {},
        "target": {"studentTypes": ["일반휴학을 신청하려는 재학생"]}, "actionType": "신청",
        "applicationMethod": "웹 종합정보시스템 접속 → 학적변동관리 → 일반휴학신청 → 신규휴학신청 → 휴학신청서 제출 → 결재 상태 확인",
        "applicationLocation": "웹 종합정보시스템(모바일 신청 불가)",
        "requiredDocuments": ["별도 구비서류 없음"], "eligibilityNotes": eligibility,
        "department": {"name": "교무팀"},
        "keywords": ["일반휴학", "휴학신청", "휴학신청서", "학적변동관리"],
        "synonyms": ["학교를 잠시 쉬기", "일반 휴학", "휴학 연장"],
        "evidenceMap": {
            "requiredDocuments": "HTML section:휴학 종류 및 신청방법의 일반휴학 행",
            "applicationMethod": "HTML section:휴학원 처리 절차의 일반휴학 1~5단계",
            "eligibilityNotes": "HTML section:휴학 신청",
        },
        "noticeStatus": "always", "confidence": 1.0, "actionGuide": procedure,
        "taskUnits": [{
            "taskKey": "leave.general", "taskName": "일반휴학", "parentTask": "leave",
            "sectionTitle": "일반휴학 신청 및 처리 절차", "summary": "일반휴학의 대상, 서류, 신청 경로와 결재 순서",
            "aliases": ["일반휴학", "휴학", "휴학 연장"],
            "excludedIntents": ["입대휴학", "질병휴학", "임신·출산휴학", "육아휴학", "창업휴학"],
            "target": {"studentTypes": ["일반휴학을 신청하려는 재학생"]},
            "applicationPeriod": {}, "eventPeriod": {},
            "facts": [
                {"factType": "required_document", "label": "구비서류", "value": "별도 구비서류 없음", "sourceLocator": "HTML section:휴학 종류 및 신청방법", "confidence": 1.0},
                *[{"factType": "eligibility", "label": "신청 조건", "value": value, "sourceLocator": "HTML section:휴학 신청", "confidence": 1.0} for value in eligibility],
            ],
            "evidence": [{"fieldName": "applicationMethod", "excerpt": "종합정보시스템 접속 → 일반휴학신청 → 신청서 작성 → 학과장 승인 → 교학팀 결재", "sourceType": "html", "sourceLocator": "HTML section:휴학원 처리 절차", "confidence": 1.0}],
            "procedure": procedure, "confidence": 1.0,
        }],
    })


def return_to_school() -> StructuredNotice:
    procedure = {
        "taskName": "복학",
        "summary": "웹 종합정보시스템에서 복학원서를 작성해 제출합니다.",
        "steps": [
            {
                "order": 1, "title": "종합정보시스템 접속",
                "description": "학교 홈페이지에서 웹 종합정보시스템에 로그인합니다.",
                "actionType": "navigate", "actor": "student", "studentActionRequired": True,
                "sourceType": "html", "sourceLocator": "HTML section:복학원 처리절차", "confidence": 1.0,
            },
            {
                "order": 2, "title": "신규복학신청 선택",
                "description": "학적변동관리 → 복학신청 → 신규복학신청을 선택합니다.",
                "actionType": "navigate", "actor": "student", "studentActionRequired": True,
                "sourceType": "html", "sourceLocator": "HTML section:복학원 처리절차", "confidence": 1.0,
            },
            {
                "order": 3, "title": "복학원서 작성 및 제출",
                "description": "복학원서를 작성하고 필요한 경우 군복무 증빙서류를 첨부해 제출합니다.",
                "actionType": "submit", "actor": "student", "studentActionRequired": True,
                "sourceType": "html", "sourceLocator": "HTML section:복학원 처리절차", "confidence": 1.0,
            },
        ],
        "warnings": [
            "모바일 신청과 방문 접수는 할 수 없습니다.",
            "복학 승인 후 학사일정에 맞춰 수강신청과 등록금 납부를 별도로 완료해야 합니다.",
        ],
        "confidence": 1.0,
    }
    return StructuredNotice.model_validate({
        "category": "학사", "subCategory": "복학", "applicationPeriod": {}, "eventPeriod": {},
        "target": {"studentTypes": ["휴학기간 만료 후 학업을 계속하려는 학생"]},
        "actionType": "신청",
        "applicationMethod": "학교 홈페이지 → 종합정보시스템 → 학적변동관리 → 복학신청 → 신규복학신청 → 복학원서 작성",
        "applicationLocation": "웹 종합정보시스템(모바일 신청·방문 접수 불가)",
        "department": {"name": "교무팀"},
        "keywords": ["복학", "복학신청", "신규복학신청", "복학원서"],
        "synonyms": ["학교로 돌아오기", "휴학 종료"],
        "additionalFacts": [
            {
                "factType": "military_return_document", "label": "전역자 증빙서류",
                "value": "전역증 앞뒤면, 병적증명서, 병역사항 포함 주민등록초본 중 1부",
                "appliesTo": ["전역자"], "sourceType": "html",
                "sourceLocator": "HTML section:입대휴학자 복학안내", "confidence": 1.0,
            },
            {
                "factType": "post_approval_task", "label": "승인 후 학생 업무",
                "value": "학사일정에 맞춰 수강신청과 등록금 납부 완료",
                "appliesTo": ["복학 승인 학생"], "sourceType": "html",
                "sourceLocator": "HTML section:유의사항", "confidence": 1.0,
            },
        ],
        "evidenceMap": {
            "applicationMethod": "홈페이지 종합정보시스템에서 신청 (모바일 신청 불가, 방문 접수 불가)",
        },
        "noticeStatus": "always", "confidence": 1.0, "actionGuide": procedure,
        "taskUnits": [{
            "taskKey": "return.general", "taskName": "복학", "parentTask": "return",
            "sectionTitle": "복학 신청 방법", "summary": "웹 종합정보시스템에서 복학원서를 작성해 제출하는 절차",
            "aliases": ["복학", "복학신청", "신규복학신청"],
            "target": {"studentTypes": ["휴학기간 만료 후 학업을 계속하려는 학생"]},
            "applicationPeriod": {}, "eventPeriod": {},
            "facts": [
                {
                    "factType": "application_method", "label": "신청 경로",
                    "value": "종합정보시스템 → 학적변동관리 → 복학신청 → 신규복학신청",
                    "sourceType": "html", "studentActionable": True,
                    "sourceLocator": "HTML section:복학원 처리절차", "confidence": 1.0,
                },
                {
                    "factType": "post_approval_task", "label": "승인 후 학생 업무",
                    "value": "수강신청 및 등록금 납부",
                    "sourceType": "html", "studentActionable": True,
                    "sourceLocator": "HTML section:유의사항", "confidence": 1.0,
                },
            ],
            "evidence": [{
                "fieldName": "applicationMethod",
                "excerpt": "홈페이지 종합정보시스템에서 신청 (모바일 신청 불가, 방문 접수 불가)",
                "sourceType": "html", "sourceLocator": "HTML section:신청시기 및 방법", "confidence": 1.0,
            }],
            "procedure": procedure, "confidence": 1.0,
        }],
    })


def early_graduation_notice() -> StructuredNotice:
    period = {"start": dt("2026-05-18T10:00"), "end": dt("2026-05-29T23:59")}
    eligibility = [
        "2026-1학기 현재 4학기 재학 중인 학생",
        "2026-1학기 수강신청 학점을 포함해 68학점 이상 이수 예정인 학생",
        "선발 시 68학점 이상 취득하고 재학 4개 학기 전체 평균평점이 4.00 이상이어야 함",
        "편입학자·재입학자·학칙 제56조에 따라 징계받은 학생은 선발 제외",
    ]
    procedure = {
        "taskName": "조기졸업 신청",
        "summary": "종합정보시스템의 학적관리에서 조기졸업 신청서를 입력해 제출합니다.",
        "warnings": ["신청만으로 선발되는 것이 아니며 2026-1학기 성적 처리 후 기준을 충족해야 합니다."],
        "steps": [
            {"order": 1, "title": "종합정보시스템 로그인", "description": "종합정보시스템에 로그인합니다.", "actionType": "navigate", "sourceType": "attachment_image", "sourceLocator": "[홈페이지공지] 2026-1학기 조기졸업 신청 안내.jpg", "confidence": 0.98},
            {"order": 2, "title": "학적관리 메뉴 이동", "description": "학적관리 메뉴에서 조기졸업신청을 선택합니다.", "actionType": "navigate", "sourceType": "attachment_image", "sourceLocator": "[홈페이지공지] 2026-1학기 조기졸업 신청 안내.jpg", "confidence": 0.98},
            {"order": 3, "title": "조기졸업 신청서 입력", "description": "신청 내용을 입력하고 자격조건을 다시 확인합니다.", "actionType": "submit", "sourceType": "attachment_image", "sourceLocator": "[홈페이지공지] 2026-1학기 조기졸업 신청 안내.jpg", "confidence": 0.95},
            {"order": 4, "title": "조기졸업 신청 완료", "description": "조기졸업신청 버튼을 눌러 신청을 완료합니다.", "actionType": "submit", "sourceType": "attachment_image", "sourceLocator": "[홈페이지공지] 2026-1학기 조기졸업 신청 안내.jpg", "confidence": 0.98},
            {"order": 5, "title": "선발 결과 확인", "description": "2026.07.13 예정인 선발 결과를 종합정보시스템에서 확인합니다.", "actionType": "verify", "sourceType": "attachment_image", "sourceLocator": "[홈페이지공지] 2026-1학기 조기졸업 신청 안내.jpg", "confidence": 0.95},
        ],
        "confidence": 0.97,
    }
    return StructuredNotice.model_validate({
        "category": "학사", "subCategory": "조기졸업", "academicYear": 2026, "semester": 1,
        "applicationPeriod": period, "eventPeriod": {},
        "target": {"studentTypes": ["4학기 재학생"]}, "actionType": "신청",
        "applicationMethod": "종합정보시스템 로그인 → 학적관리 → 조기졸업신청 → 신청서 입력 → 조기졸업신청 버튼 클릭",
        "eligibilityNotes": eligibility,
        "benefits": ["선발되면 다음 학기 수강신청부터 최대 수강신청 학점에 3학점을 추가 신청 가능"],
        "resultAnnouncement": "2026.07.13 예정, 종합정보시스템에서 확인",
        "importantDates": [{"label": "선발 확정 및 발표", "start": dt("2026-07-13T00:00"), "description": "종합정보시스템에서 확인", "sourceLocator": "첨부 이미지 6번 항목"}],
        "department": {"name": "소속 교학팀"},
        "keywords": ["조기졸업", "조기졸업 신청", "선발기준"], "synonyms": ["수업연한 단축", "조기 졸업"],
        "evidenceMap": {"applicationPeriod": "첨부 이미지 1번 항목", "eligibilityNotes": "첨부 이미지 2·4·7번 항목", "applicationMethod": "첨부 이미지 3번 항목"},
        "noticeStatus": "expired", "confidence": 0.97, "actionGuide": procedure,
        "taskUnits": [{
            "taskKey": "graduation.early", "taskName": "조기졸업 신청", "parentTask": "graduation",
            "sectionTitle": "2026-1학기 조기졸업 신청 안내", "summary": "2026-1학기 조기졸업 신청 기간, 선발기준과 신청 절차",
            "aliases": ["조기졸업", "조기졸업 신청", "수업연한 단축"], "excludedIntents": ["일반 졸업요건", "졸업인증제"],
            "academicYear": 2026, "semester": 1, "target": {"studentTypes": ["4학기 재학생"]},
            "applicationPeriod": period, "eventPeriod": {},
            "facts": [
                *[{"factType": "eligibility", "label": "선발 조건", "value": value, "sourceLocator": "첨부 이미지 2·4·7번 항목", "confidence": 0.97} for value in eligibility],
                {"factType": "benefit", "label": "선발 혜택", "value": "다음 학기부터 최대 수강신청 학점에 3학점 추가 신청 가능", "sourceLocator": "첨부 이미지 5번 항목", "confidence": 0.97},
            ],
            "evidence": [{"fieldName": "applicationMethod", "excerpt": "종합정보시스템 로그인 > 학적관리 > 조기졸업신청", "sourceType": "attachment_image", "sourceLocator": "[홈페이지공지] 2026-1학기 조기졸업 신청 안내.jpg", "confidence": 0.98}],
            "procedure": procedure, "confidence": 0.97,
        }],
    })


def early_graduation_guide() -> StructuredNotice:
    eligibility = [
        "4학기차까지 68학점 이상 이수해야 함(2012년 이전 기준은 72학점)",
        "4학기차까지 취득한 전체 평점평균이 4.00 이상이어야 함",
        "편입학자·재입학자·학칙 제56조에 따라 징계받은 학생은 선발 제외",
    ]
    procedure = {
        "taskName": "조기졸업",
        "summary": "종합정보시스템에서 조기졸업 신청서를 작성해 제출합니다.",
        "steps": [
            {"order": 1, "title": "종합정보시스템 접속", "description": "학교 홈페이지에서 종합정보시스템에 접속합니다.", "actionType": "navigate", "sourceType": "html", "sourceLocator": "HTML section:신청절차", "confidence": 1.0},
            {"order": 2, "title": "학적관리 메뉴 이동", "description": "학적관리를 클릭합니다.", "actionType": "navigate", "sourceType": "html", "sourceLocator": "HTML section:신청절차", "confidence": 1.0},
            {"order": 3, "title": "조기졸업신청 선택", "description": "조기졸업신청 메뉴를 클릭합니다.", "actionType": "navigate", "sourceType": "html", "sourceLocator": "HTML section:신청절차", "confidence": 1.0},
            {"order": 4, "title": "신청서 입력 및 제출", "description": "신청서를 입력한 후 조기졸업신청 버튼을 눌러 제출합니다.", "actionType": "submit", "sourceType": "html", "sourceLocator": "HTML section:신청절차", "confidence": 1.0},
        ],
        "confidence": 1.0,
    }
    return StructuredNotice.model_validate({
        "category": "학사", "subCategory": "조기졸업", "applicationPeriod": {}, "eventPeriod": {},
        "target": {"studentTypes": ["조기졸업을 희망하는 재학생"]}, "actionType": "신청",
        "applicationMethod": "학교 홈페이지 → 종합정보시스템 → 학적관리 → 조기졸업신청 → 신청서 입력 및 제출",
        "eligibilityNotes": eligibility, "department": {"name": "교무팀"},
        "keywords": ["조기졸업", "대상자선발", "신청절차"], "synonyms": ["조기 졸업", "수업연한 단축"],
        "evidenceMap": {"eligibilityNotes": "HTML section:대상자선발·제한", "applicationMethod": "HTML section:신청절차"},
        "noticeStatus": "always", "confidence": 1.0, "actionGuide": procedure,
        "taskUnits": [{
            "taskKey": "graduation.early", "taskName": "조기졸업", "parentTask": "graduation",
            "sectionTitle": "조기졸업 요건 및 신청절차", "summary": "조기졸업 선발요건, 제외대상과 신청 경로",
            "aliases": ["조기졸업", "조기 졸업"], "excludedIntents": ["일반 졸업요건", "졸업인증제"],
            "target": {"studentTypes": ["조기졸업을 희망하는 재학생"]}, "applicationPeriod": {}, "eventPeriod": {},
            "facts": [*[{"factType": "eligibility", "label": "선발 조건", "value": value, "sourceLocator": "HTML section:대상자선발·제한", "confidence": 1.0} for value in eligibility]],
            "evidence": [{"fieldName": "applicationMethod", "excerpt": "종합정보시스템 접속 → 학적관리 → 조기졸업신청 → 신청서 입력", "sourceType": "html", "sourceLocator": "HTML section:신청절차", "confidence": 1.0}],
            "procedure": procedure, "confidence": 1.0,
        }],
    })


def reserve_guide() -> StructuredNotice:
    defer_procedure = {
        "taskName": "예비군 훈련 연기 신고",
        "summary": "연기 사유를 입증하는 근거서류를 첨부해 본인이 직접 신고합니다.",
        "warnings": ["원문에는 구체적인 제출 창구가 명시되지 않아 대학직장예비군연대에 확인해야 합니다.", "천재지변 등 긴급상황에서만 먼저 유선 신고 후 조치할 수 있습니다."],
        "steps": [
            {"order": 1, "title": "연기 사유 근거서류 준비", "description": "질병, 직계 존비속의 관혼상제, 구속, 국가기관 주관 시험 등 연기 사유를 입증할 서류를 준비합니다.", "actionType": "other", "sourceType": "html", "sourceLocator": "HTML section:대학직장예비군 교육훈련", "confidence": 1.0},
            {"order": 2, "title": "근거서류 첨부 후 직접 신고", "description": "준비한 근거서류를 첨부해 본인이 직접 연기 신고를 합니다.", "actionType": "submit", "sourceType": "html", "sourceLocator": "HTML section:대학직장예비군 교육훈련", "confidence": 1.0},
            {"order": 3, "title": "제출 장소 확인", "description": "구체 제출 장소는 대학직장예비군연대(승리관 102호, 031-280-3595~6)에 확인합니다.", "actionType": "contact", "sourceType": "html", "sourceLocator": "HTML section:행정안내", "confidence": 1.0},
        ],
        "confidence": 1.0,
    }
    return StructuredNotice.model_validate({
        "category": "병무", "subCategory": "예비군", "applicationPeriod": {}, "eventPeriod": {},
        "target": {"studentTypes": ["본교 재학생·교직원 중 예비군 대상자"]}, "actionType": "신고",
        "applicationMethod": "훈련 연기 사유 발생 시 근거서류를 첨부해 본인이 직접 신고; 천재지변 등 긴급상황은 유선 신고 후 조치 가능",
        "applicationLocation": "구체 제출 장소는 대학직장예비군연대에 확인",
        "requiredDocuments": ["훈련 연기 사유를 입증하는 근거서류"],
        "department": {"name": "대학직장예비군연대", "phone": "031-280-3595~6", "officeLocation": "승리관 102호"},
        "keywords": ["예비군", "예비군 편성", "교육훈련", "훈련 연기", "예비군 신고"],
        "synonyms": ["학생예비군", "직장예비군", "훈련연기 신고"],
        "evidenceMap": {"applicationMethod": "HTML section:대학직장예비군 교육훈련 4번", "department": "HTML section:행정안내"},
        "noticeStatus": "always", "confidence": 1.0, "actionGuide": defer_procedure,
        "taskUnits": [
            {
                "taskKey": "reserve.transfer", "taskName": "학생예비군 편성·전입", "parentTask": "reserve",
                "sectionTitle": "학생예비군 편성", "summary": "본교 소속 예비군 대상자의 대학직장예비군 편성 의무와 대상",
                "aliases": ["학생예비군 편성", "예비군 전입", "전입신고"], "applicationPeriod": {}, "eventPeriod": {},
                "target": {"studentTypes": ["본교 재학생·교직원 중 예비군 대상자"]},
                "facts": [{"factType": "eligibility", "label": "편성 대상", "value": "본교 재학생·교직원 중 예비군 대상자는 대학직장예비군에 편성", "sourceLocator": "HTML section:의무·편성대상", "confidence": 1.0}],
                "evidence": [{"fieldName": "eligibility", "excerpt": "예비군대원은 전원 대학직장예비군에 편성되어야 함", "sourceType": "html", "sourceLocator": "HTML section:의무", "confidence": 1.0}],
                "confidence": 1.0,
            },
            {
                "taskKey": "reserve.training", "taskName": "예비군 교육훈련", "parentTask": "reserve",
                "sectionTitle": "대학직장예비군 교육훈련", "summary": "훈련시간, 복장, 준비물, 입소시간과 훈련장 위치",
                "aliases": ["예비군훈련", "교육훈련"], "applicationPeriod": {}, "eventPeriod": {},
                "target": {"studentTypes": ["학생·교원·직원 예비군"]},
                "facts": [
                    {"factType": "required_document", "label": "훈련 준비물", "value": "신분증", "sourceLocator": "HTML section:대학직장예비군 교육훈련", "confidence": 1.0},
                    {"factType": "location", "label": "훈련장", "value": "용인시 운학동 예비군훈련장", "sourceLocator": "HTML section:대학직장예비군 교육훈련", "confidence": 1.0},
                ],
                "evidence": [{"fieldName": "training", "excerpt": "훈련 입소시간은 해당일 09:00까지이며 신분증을 지참", "sourceType": "html", "sourceLocator": "HTML section:대학직장예비군 교육훈련", "confidence": 1.0}],
                "confidence": 1.0,
            },
            {
                "taskKey": "reserve.defer", "taskName": "예비군 훈련 연기 신고", "parentTask": "reserve",
                "sectionTitle": "교육훈련 연기", "summary": "훈련 연기 사유, 근거서류, 직접 신고와 긴급 유선 예외",
                "aliases": ["예비군 신고", "훈련 연기", "교육훈련 연기"], "applicationPeriod": {}, "eventPeriod": {},
                "target": {"studentTypes": ["훈련 연기 사유가 발생한 예비군 대상자"]},
                "facts": [
                    {"factType": "required_document", "label": "연기 신고 서류", "value": "훈련 연기 사유를 입증하는 근거서류", "sourceLocator": "HTML section:대학직장예비군 교육훈련", "confidence": 1.0},
                    {"factType": "method", "label": "신고 방법", "value": "근거서류를 첨부하여 본인이 직접 신고", "sourceLocator": "HTML section:대학직장예비군 교육훈련", "confidence": 1.0},
                    {"factType": "exception", "label": "긴급상황 예외", "value": "천재지변 등 긴급상황은 유선 신고 후 조치 가능", "sourceLocator": "HTML section:대학직장예비군 교육훈련", "confidence": 1.0},
                ],
                "evidence": [{"fieldName": "applicationMethod", "excerpt": "근거서류를 첨부하여 본인이 직접 신고한다. 천재지변 등 긴급상황은 유선 신고 후 조치 가능", "sourceType": "html", "sourceLocator": "HTML section:대학직장예비군 교육훈련", "confidence": 1.0}],
                "procedure": defer_procedure, "confidence": 1.0,
            },
        ],
    })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", type=int, action="append", default=[])
    requested = set(parser.parse_args().only)
    db = SessionLocal()
    try:
        processor = NoticeProcessor(db)
        for notice_id, structured in (
            (24, course_registration()),
            (761, course_registration_guide()),
            (13, credit_registration()),
            (773, general_leave()),
            (774, return_to_school()),
            (126, early_graduation_notice()),
            (777, early_graduation_guide()),
            (758, reserve_guide()),
        ):
            if requested and notice_id not in requested:
                continue
            notice = db.get(Notice, notice_id)
            if notice is None:
                raise RuntimeError(f"pilot notice missing: {notice_id}")
            grounded = processor.ground_external_structured(notice, structured)
            processor.persist_structured(notice, grounded, list(notice.content_links or []))
            db.commit()
            db.expire_all()
            saved = db.get(Notice, notice_id)
            print({
                "noticeId": notice_id,
                "title": saved.title,
                "taskUnits": [unit.task.task_key for unit in saved.task_units],
            })
    finally:
        db.close()


if __name__ == "__main__":
    main()
