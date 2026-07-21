from __future__ import annotations

from dataclasses import dataclass
import re


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").lower())


@dataclass(frozen=True)
class TaskIntent:
    key: str
    name: str
    parent: str
    category: str
    aliases: tuple[str, ...]
    required_groups: tuple[tuple[str, ...], ...] = ()
    excluded_title_terms: tuple[str, ...] = ()


TASKS: tuple[TaskIntent, ...] = (
    TaskIntent("graduation.early", "조기졸업", "graduation", "학사", ("조기졸업",), (("조기졸업",),)),
    TaskIntent("graduation.certification", "졸업인증제", "graduation", "학사", ("졸업인증제", "졸업인증"), (("졸업인증",),)),
    TaskIntent("graduation.assessment", "졸업종합평가", "graduation", "학사", ("졸업종합평가", "졸업평가"), (("졸업종합평가", "졸업평가"),)),
    TaskIntent("graduation.defer", "학사학위취득유예", "graduation", "학사", ("학사학위취득유예", "졸업유예"), (("학사학위취득유예", "졸업유예"),)),
    TaskIntent(
        "graduation.requirements", "일반 졸업요건", "graduation", "학사",
        ("졸업요건", "졸업 요건", "졸업이수학점", "졸업 이수학점", "일반졸업"),
        (("졸업요건", "졸업 요건", "졸업이수", "졸업 이수"),),
        (
            "조기졸업", "졸업인증", "졸업종합평가", "졸업유예", "학사학위취득유예",
            "예비졸업", "졸업자 학점포기", "졸업 기념사진",
        ),
    ),
    TaskIntent("leave.startup", "창업휴학", "leave", "창업교육안내", ("창업휴학",), (("창업휴학",),)),
    TaskIntent("leave.military", "군입대휴학", "leave", "학사", ("군입대휴학", "입대휴학", "군휴학"), (("군입대", "입대휴학", "군휴학"),)),
    TaskIntent("leave.medical", "질병휴학", "leave", "학사", ("질병휴학", "질병 휴학"), (("질병휴학", "질병 휴학"),)),
    TaskIntent("leave.childcare", "육아휴학", "leave", "학사", ("육아휴학", "임신휴학", "출산휴학"), (("육아휴학", "임신휴학", "출산휴학"),)),
    TaskIntent("leave.cancel", "휴학 취소", "leave", "학사", ("휴학취소", "휴학 취소"), (("휴학",), ("취소",))),
    TaskIntent(
        "leave.general", "일반휴학", "leave", "학사", ("일반휴학", "휴학", "학교를잠시쉬"), (("휴학",),),
        ("창업휴학", "군입대", "입대휴학", "질병휴학", "육아휴학", "임신휴학", "출산휴학", "휴학취소", "휴학 취소"),
    ),
    TaskIntent("return.general", "복학", "return", "학사", ("복학",), (("복학",),)),
    TaskIntent("course.change", "수강신청 변경", "course", "학사", ("수강신청변경", "수강변경", "정정기간"), (("수강",), ("변경", "정정"))),
    TaskIntent(
        "course.registration", "수강신청", "course", "학사",
        ("수강신청", "예비수강신청"), (("수강신청",),),
        ("계절수업", "계절학기", "수강포기", "수강취소", "수강교과목 포기"),
    ),
    TaskIntent("tuition.refund", "등록금 반환", "tuition", "등록", ("등록금반환", "등록금환불", "등록금 반환", "등록금 환불"), (("등록금",), ("반환", "환불"))),
    TaskIntent("tuition.credit.payment", "학점등록대상자 등록금 납부", "tuition.credit", "등록", ("학점등록대상자등록금납부", "학점등록금납부", "학점등록납부"), (("학점등록",), ("납부",))),
    TaskIntent("tuition.credit.course", "학점등록대상자 수강신청", "tuition.credit", "학사", ("학점등록대상자수강신청", "학점등록수강신청"), (("학점등록",), ("수강신청",))),
    TaskIntent("tuition.credit", "학점등록대상자 등록", "tuition", "등록", ("학점등록대상자", "학점등록"), (("학점등록",),)),
    TaskIntent(
        "tuition.payment", "일반 등록금 납부", "tuition", "등록",
        ("등록금납부", "등록금 납부"), (("등록금",), ("납부",)),
        ("학점등록대상자", "등록금지원", "장학", "계절수업", "계절학기", "수강료"),
    ),
    TaskIntent("reserve.transfer", "학생예비군 편성·전입", "reserve", "병무", ("예비군전입", "전입신고", "학생예비군편성"), (("예비군",), ("전입", "편성"))),
    TaskIntent(
        "reserve.defer", "예비군 훈련 연기·신고", "reserve", "병무",
        ("예비군훈련연기", "교육훈련연기", "훈련연기", "예비군신고", "예비군 신고", "근거서류직접신고"),
        (("예비군",), ("연기", "신고")),
    ),
    TaskIntent("reserve.training", "예비군 교육훈련", "reserve", "병무", ("예비군훈련", "교육훈련"), (("예비군",), ("훈련",))),
    TaskIntent("certificate.issue", "증명서 발급", "certificate", "학사", ("증명서발급", "성적증명서", "재학증명서", "졸업증명서", "제증명"), (("증명서", "제증명"),)),
    TaskIntent("major.multiple", "다전공", "major", "학사", ("다전공", "복수전공", "부전공"), (("다전공", "복수전공", "부전공"),)),
    TaskIntent("credit.exchange", "학점교류", "credit", "학사", ("학점교류", "타대학수강"), (("학점교류", "타대학수강"),)),
    TaskIntent(
        "grade.appeal", "성적 이의신청", "grade", "학사",
        ("성적이의신청", "성적 이의신청", "성적정정", "성적 정정"),
        (("성적",), ("이의", "정정")),
    ),
    TaskIntent(
        "grade.check", "성적 확인", "grade", "학사",
        ("성적확인", "성적 확인", "성적조회", "성적 조회", "성적열람", "성적 열람"),
        (("성적",), ("확인", "조회", "열람")),
        ("성적우수장학", "성적장학", "성적 포기", "학점포기"),
    ),
    TaskIntent("scholarship.merit", "성적우수장학금", "scholarship", "장학", ("성적우수장학금", "성적장학금", "성적우수 장학금"), (("성적",), ("장학",))),
    TaskIntent("scholarship.national", "국가장학금", "scholarship", "장학", ("국가장학금",), (("국가장학금",),)),
    TaskIntent("scholarship.loan", "학자금대출", "scholarship", "장학", ("학자금대출", "학자금 대출"), (("학자금",), ("대출",))),
    TaskIntent(
        "scholarship.general", "장학금 안내", "scholarship", "장학",
        ("교내장학", "교외장학", "장학제도"), (("장학",),),
        ("국가장학금", "성적우수장학금", "성적장학금", "학자금대출"),
    ),
    TaskIntent("dormitory.apply", "기숙사 입사 신청", "dormitory", "대학생활안내", ("기숙사신청", "기숙사 신청", "생활관신청", "입사신청", "심전생활관", "생활관"), (("기숙사", "생활관", "입사"),)),
    TaskIntent("exchange.outgoing", "교환학생 신청", "international", "대학생활안내", ("교환학생", "해외파견", "복수학위"), (("교환학생", "해외파견", "복수학위"),)),
    TaskIntent("career.internship", "현장실습 신청", "career", "취업", ("현장실습", "인턴십"), (("현장실습", "인턴십"),)),
    TaskIntent("counseling.apply", "상담·심리검사 신청", "counseling", "대학생활안내", ("심리상담", "상담신청", "심리검사"), (("상담", "심리검사"),)),
    TaskIntent("shuttle.info", "무료셔틀 이용", "transport", "대학생활안내", ("무료셔틀", "셔틀버스", "학교셔틀"), (("셔틀",),)),
    TaskIntent("event.camp", "캠프·프로그램 신청", "event", "기타", ("캠프", "집중캠프", "부트캠프"), (("캠프",),)),
)


TASK_BY_KEY = {task.key: task for task in TASKS}


def visible_student_step(step) -> bool:
    """학생의 제출을 끝낸 뒤 진행되는 내부 승인·결재 단계를 숨긴다."""
    actor = getattr(step, "actor", "student")
    required = getattr(step, "student_action_required", True)
    text = re.sub(r"\s+", " ", str(
        f"{getattr(step, 'title', '')} "
        f"{getattr(step, 'description', step if isinstance(step, str) else '')}"
    )).strip()
    if actor != "student" or not required:
        return False
    internal_terms = (
        "학과장 승인", "학과장·전공주임 승인", "전공주임 승인", "교학팀 결재",
        "교무팀 결재", "담당부서 검토", "위원회 심의", "내부 승인", "최종 승인",
        "결재 상태 확인", "결재완료 여부 확인", "처리 상태 확인",
        "합격 통보 확인", "선발 결과 확인", "결과 발표 확인",
    )
    direct_student_markers = ("직접", "서명 받아", "승인을 받아", "방문하여 승인")
    return not (
        any(term in text for term in internal_terms)
        and not any(marker in text for marker in direct_student_markers)
    )


def detect_task(text: str) -> TaskIntent | None:
    compact = _compact(text)
    if "성적" in compact and any(term in compact for term in ("이의신청", "성적정정")):
        return TASK_BY_KEY["grade.appeal"]
    # 한 문장에서 수강신청과 등록금 납부를 함께 묻는 경우에는 어느 한
    # 자식 업무만 고르지 않고 두 단계를 묶은 학점등록 업무를 선택한다.
    if (
        "학점등록" in compact
        and "수강신청" in compact
        and ("등록금납부" in compact or ("등록금" in compact and "납부" in compact))
    ):
        return TASK_BY_KEY["tuition.credit"]
    # 학교 상시안내에서 일반적인 "예비군 신고"는 훈련 연기 사유와
    # 근거서류를 직접 신고하는 절차를 가리킨다. 전입·편성이 명시되면
    # 위의 reserve.transfer 별칭이 아래 일반 매칭에서 우선한다.
    if (
        "예비군" in compact and "신고" in compact
        and "전입" not in compact and "편성" not in compact
    ):
        return TASK_BY_KEY["reserve.defer"]
    # 여러 업무 표현이 한 제목에 함께 있으면 먼저 나온 단어가 아니라 더
    # 구체적인(긴) 표현을 우선한다. 예: "학점등록대상자 수강신청"은
    # 일반 수강신청이 아닌 학점등록 업무다.
    matches: list[tuple[int, int, TaskIntent]] = []
    for order, task in enumerate(TASKS):
        lengths = [len(_compact(alias)) for alias in task.aliases if _compact(alias) in compact]
        if lengths:
            matches.append((max(lengths), -order, task))
    if "예비군" in compact and not any(task.parent == "reserve" for _, _, task in matches):
        matches.append((len("예비군"), -len(TASKS), TASK_BY_KEY["reserve.training"]))
    if (
        "졸업" in compact
        and "졸업생" not in compact
        and "졸업예정" not in compact
        and "예비졸업" not in compact
        and not any(task.parent == "graduation" for _, _, task in matches)
    ):
        matches.append((len("졸업"), -len(TASKS) - 1, TASK_BY_KEY["graduation.requirements"]))
    return max(matches, default=(0, 0, None), key=lambda item: (item[0], item[1]))[2]


def detect_tasks(text: str) -> list[TaskIntent]:
    """복수 업무 질문을 서로 독립적인 검색 단위로 보존한다."""
    compact = _compact(text)
    if (
        "학점등록" in compact
        and "수강신청" in compact
        and ("등록금납부" in compact or ("등록금" in compact and "납부" in compact))
    ):
        return [TASK_BY_KEY["tuition.credit"]]

    matches: list[TaskIntent] = []
    for task in TASKS:
        if any(_compact(alias) in compact for alias in task.aliases):
            matches.append(task)
    if "성적" in compact and any(term in compact for term in ("이의신청", "성적정정")):
        matches.append(TASK_BY_KEY["grade.appeal"])
    if "예비군" in compact and "신고" in compact and "전입" not in compact and "편성" not in compact:
        matches = [task for task in matches if task.parent != "reserve"]
        matches.append(TASK_BY_KEY["reserve.defer"])
    if (
        "졸업" in compact and "졸업생" not in compact and "졸업예정" not in compact
        and "예비졸업" not in compact and not any(task.parent == "graduation" for task in matches)
    ):
        matches.append(TASK_BY_KEY["graduation.requirements"])

    # 일반 업무 별칭이 더 구체적인 자식 업무의 일부로만 등장한 경우에는
    # 중복 검색하지 않는다. 비교를 명시한 문장은 양쪽 표현이 독립적으로
    # 등장하므로 그대로 유지한다.
    unique: list[TaskIntent] = []
    for task in matches:
        if task not in unique:
            unique.append(task)
    if len(unique) > 1 and not any(marker in compact for marker in ("차이", "비교", "둘다", "모두", "그리고", "와", "과")):
        primary = detect_task(text)
        return [primary] if primary else []
    return unique or ([detect_task(text)] if detect_task(text) else [])


def task_key_for(text: str, fallback: str | None = None) -> str | None:
    task = detect_task(text)
    return task.key if task else fallback


def candidate_task_score(
    query_text: str,
    *,
    title: str,
    content: str,
    task_key: str | None = None,
    aliases: list[str] | None = None,
) -> tuple[bool, float, str | None]:
    """필수 업무가 다른 후보를 임베딩 점수보다 먼저 차단한다."""
    wanted = detect_task(query_text)
    if not wanted:
        return True, 0.0, None

    title_compact = _compact(title)
    content_compact = _compact(content)
    candidate_text = f"{title_compact}{content_compact}{_compact(' '.join(aliases or []))}"
    candidate_task = task_key or task_key_for(title)

    # TaskUnit에 Codex가 확정해 둔 업무키가 있으면 의미 유사도보다 그
    # 경계를 우선한다. 서로 다른 업무키의 본문에 우연히 질문 단어가
    # 등장해도 다른 절차를 내보내지 않는다.
    if task_key and task_key != wanted.key:
        other = TASK_BY_KEY.get(task_key)
        return False, 0.0, f"다른 세부 업무: {other.name if other else task_key}"

    for group in wanted.required_groups:
        if not any(_compact(term) in candidate_text for term in group):
            return False, 0.0, f"필수 업무 표현 누락: {'/'.join(group)}"

    if any(_compact(term) in title_compact for term in wanted.excluded_title_terms):
        return False, 0.0, "질문과 다른 세부 업무 제목"

    # 같은 상위업무의 다른 세부업무는 강하게 배제한다. 다만 수강신청
    # 종합 공지처럼 본문 구간이 정확히 맞는 경우는 허용한다.
    if candidate_task and candidate_task != wanted.key:
        other = TASK_BY_KEY.get(candidate_task)
        if other and other.category != wanted.category:
            return False, 0.0, f"다른 업무 분류: {other.category}"
        if other and other.parent == wanted.parent:
            exact_in_content = all(
                any(_compact(term) in content_compact for term in group)
                for group in wanted.required_groups
            )
            if not exact_in_content:
                return False, 0.0, f"다른 세부 업무: {other.name}"

    bonus = 0.0
    if candidate_task == wanted.key:
        bonus += 0.45
    if any(_compact(alias) in title_compact for alias in wanted.aliases):
        bonus += 0.35
    elif all(any(_compact(term) in content_compact for term in group) for group in wanted.required_groups):
        bonus += 0.12
    return True, bonus, None


def guide_matches_query(query_text: str, task_name: str | None) -> bool:
    wanted = detect_task(query_text)
    if not wanted or not task_name:
        return bool(task_name)
    eligible, _, _ = candidate_task_score(
        query_text,
        title=task_name,
        content=task_name,
        task_key=task_key_for(task_name),
    )
    return eligible
