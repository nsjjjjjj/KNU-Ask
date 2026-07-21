import hashlib
import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo


TAG_PATTERN = re.compile(r"<[^>]+>")
SPACE_PATTERN = re.compile(r"\s+")
PHONE_PATTERN = re.compile(r"0\d{1,2}-\d{3,4}-\d{4}")
CONTACT_PHONE_PATTERN = re.compile(r"(?<!\d)(0\d{1,2})\s*[-)]\s*(\d{3,4})\s*-\s*(\d{4})(?!\d)")
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
STUDENT_ID_PATTERN = re.compile(r"(?<!\d)(?:20)?\d{8,10}(?!\d)")
ACCOUNT_PATTERN = re.compile(r"(?<!\d)\d{3,6}-\d{2,6}-\d{3,7}(?!\d)")
NAMED_PERSON_PATTERN = re.compile(r"(?:이름|성명|본인)\s*[:：은는]?\s*[가-힣]{2,4}(?:입니다|이에요|예요)?")
PERSONAL_GRADE_PATTERN = re.compile(
    r"(?:내|제|본인)?\s*(?:성적|평점|학점)\s*[:：은는]?\s*(?:[0-4](?:\.\d{1,2})?|[A-F][+-]?)\b",
    re.I,
)
DANGEROUS_ACTION_PATTERN = re.compile(
    r"(?:"
    r"불\s*(?:을\s*)?지르|불태우|방화(?:하|를\s*하)|화염병|"
    r"폭탄|폭발물|사제\s*폭발|테러(?:하|를\s*하)|"
    r"(?:사람|학생|교직원|선생|교수|친구|누군가|놈|새끼)?\s*(?:을|를)?\s*"
    r"(?:죽이|살해하|살인(?:하|을\s*하)?|찌르|다치게\s*하|공격하)|"
    r"강간(?:하|을\s*하)?|성폭행(?:하|을\s*하)?|성추행(?:하|을\s*하)?|"
    r"총\s*(?:을\s*)?쏘|독극물|독\s*(?:을\s*)?타"
    r")",
    re.I,
)
DANGEROUS_INTENT_PATTERN = re.compile(
    r"(?:"
    r"하고\s*싶|하\s*고싶|하려고|하려면|할까|하자|하겠|해버리|해\s*줘|"
    r"어디(?:에|서|가)?|어느\s*(?:곳|건물|장소)|방법|요령|순서|준비물|추천|"
    r"(?:가장|제일)?\s*(?:불\s*(?:을\s*)?지르|불태우|공격하|찌르|죽이|살인하|강간하|성폭행하|성추행하)[^.!?]{0,20}(?:좋|적당|쉬운|최적)|"
    r"잘\s*(?:퍼지|번지|타|죽)|빠르게|효과적|최대한|피해를?\s*(?:크게|많이)|"
    r"몰래|들키지|안\s*들[키켜]|안\s*걸리|추적을?\s*피하|증거를?\s*(?:없애|숨기)|"
    r"만드는\s*법|제조|설치"
    r")",
    re.I,
)
BENIGN_SAFETY_CONTEXT_PATTERN = re.compile(
    r"(?:예방|대피|진압|소화|안전\s*교육|신고(?:해야|하는|할|해|하려|방법)|"
    r"어떻게\s*(?:막|말리)|구조|응급|피해\s*복구|법적\s*처벌|"
    r"범죄\s*사례|사건\s*(?:기사|개요)|역사적\s*사건|뜻이\s*뭐)",
    re.I,
)
HARM_REPORTING_CONTEXT_PATTERN = re.compile(
    r"(?:당했|당한|피해(?:를|자|가)?|목격|신고|위협|협박|실제\s*위험|"
    r"누군가\s*(?:하려|하겠)|친구가\s*(?:하려|당)|도움\s*(?:요청|받))",
    re.I,
)
URL_PATTERN = re.compile(r"https?://[^\s<>\"]+")
YEAR_PATTERN = re.compile(r"(20\d{2})\s*(?:학년도|년도|(?=-\s*[12]\s*학기))")
ADMISSION_YEAR_PATTERN = re.compile(r"(20\d{2})\s*(?:(?:학년도|년도)\s*)?(?:입학|입학생|학번)")
SEMESTER_PATTERN = re.compile(r"([12])\s*학기")
DATE_PATTERN = re.compile(r"(20\d{2})[.\-/년 ]+(\d{1,2})[.\-/월 ]+(\d{1,2})")
FLEX_DATE_PATTERN = re.compile(
    r"(?:(20\d{2})\s*[.\-/년]\s*)?(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?"
    r"(?:\s*\([^)]+\))?(?:\s*[.]?\s*(오전|오후)?\s*(\d{1,2})(?::|시)\s*(\d{1,2})?\s*분?)?"
)
APPLICATION_LABEL_PATTERN = re.compile(
    r"(?:신청|모집|접수|지원|등록|제출|납부)(?:\s*(?:기간|기한|일정|마감일?))?\s*[:：]?"
)
KST = ZoneInfo("Asia/Seoul")


def normalize_text(value: str) -> str:
    value = html.unescape(TAG_PATTERN.sub(" ", (value or "").replace("\x00", " ")))
    return SPACE_PATTERN.sub(" ", value).strip()


def strip_nul(value):
    """PostgreSQL text/JSONB가 허용하지 않는 NUL만 재귀적으로 제거한다."""
    if isinstance(value, str):
        return value.replace("\x00", " ")
    if isinstance(value, list):
        return [strip_nul(item) for item in value]
    if isinstance(value, tuple):
        return tuple(strip_nul(item) for item in value)
    if isinstance(value, dict):
        return {strip_nul(key): strip_nul(item) for key, item in value.items()}
    return value


def content_hash(title: str, content: str, attachment_text: str, published_at: str, resource_manifest: str = "") -> str:
    normalized = "\n".join(map(normalize_text, [title, content, attachment_text, published_at, resource_manifest]))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def rule_extract(text: str) -> dict:
    text = normalize_text(text)
    admission_year = ADMISSION_YEAR_PATTERN.search(text)
    year = next((match for match in YEAR_PATTERN.finditer(text)
                 if not re.search(r"(?:입학|입학생|학번)", text[match.end():match.end() + 12])), None)
    semester = SEMESTER_PATTERN.search(text)
    return {
        "phones": sorted(set(PHONE_PATTERN.findall(text))),
        "emails": sorted(set(EMAIL_PATTERN.findall(text))),
        "urls": sorted(set(URL_PATTERN.findall(text))),
        "dates": [match.group(0) for match in DATE_PATTERN.finditer(text)],
        "academic_year": int(year.group(1)) if year else None,
        "admission_year": int(admission_year.group(1)) if admission_year else None,
        "semester": int(semester.group(1)) if semester else None,
    }


def extract_notice_email(text: str, department_name: str | None = None) -> str | None:
    """신청자 이메일 예시가 아니라 문의·담당 문맥의 이메일을 고른다."""
    normalized = normalize_text(text)
    candidates: list[tuple[int, str]] = []
    for match in EMAIL_PATTERN.finditer(normalized):
        context = normalized[max(0, match.start() - 100):match.end() + 60]
        nearby_prefix = normalized[max(0, match.start() - 45):match.start()]
        immediate_prefix = normalized[max(0, match.start() - 22):match.start()]
        score = 0
        if any(token in context for token in ("문의", "담당", "연락", "이메일", "E-mail", "email")):
            score += 3
        if department_name and department_name in context:
            score += 2
        if any(token in nearby_prefix for token in ("문의", "담당", "연락")):
            score += 4
        if department_name and department_name in nearby_prefix:
            score += 3
        if any(token in immediate_prefix for token in ("작성", "입력", "본인", "신청자")):
            score -= 8
        candidates.append((score, match.group(0)))
    if not candidates:
        return None
    score, email = max(candidates, key=lambda item: item[0])
    return email if score >= 2 else None


def extract_notice_contact(text: str, department_name: str | None = None) -> tuple[str | None, str | None]:
    """공지의 문의·담당 문맥에서 담당자 이름과 대표 연락처를 고른다."""
    normalized = normalize_text(text)
    person = None
    person_patterns = [
        re.compile(r"담당자\s*및\s*업무\s*[:：]\s*([가-힣]{2,4})"),
        re.compile(r"(?:업무\s*)?담당(?:자)?\s*[:：]\s*([가-힣]{2,4})\s*(주무관|교수|선생님|직원)?"),
        re.compile(r"업무\s+담당\s+([가-힣]{2,4})\s*(주무관|교수|선생님|직원)?"),
        re.compile(r"담당자\s+([가-힣]{2,4})\s+(주무관|교수|선생님|직원)"),
        re.compile(r"([가-힣]{2,4})\s*(교수|주무관)\s*(?=0\d{1,2}\s*[-)])"),
    ]
    excluded_names = {"부서", "문의", "전화", "연락처", "메일", "이메일", "선발", "신청", "업무"}
    for pattern in person_patterns:
        for match in pattern.finditer(normalized):
            name = match.group(1)
            if name in excluded_names:
                continue
            role = match.group(2) if match.lastindex and match.lastindex >= 2 else None
            person = f"{name} {role}".strip() if role else name
            break
        if person:
            break

    candidates = []
    for match in CONTACT_PHONE_PATTERN.finditer(normalized):
        phone = "-".join(match.groups())
        context = normalized[max(0, match.start() - 100):match.end() + 60]
        score = 0
        if re.search(r"문의|담당|연락처|전화|상담", context):
            score += 4
        if phone.startswith("031-280-"):
            score += 3
        if department_name and department_name in context:
            score += 2
        before_phone = normalized[max(0, match.start() - 70):match.start()]
        nearby_departments = re.findall(r"[가-힣A-Za-z0-9·]+(?:팀|센터|대대|부서)", before_phone)
        nearest_department = nearby_departments[-1] if nearby_departments else None
        if department_name and nearest_department and nearest_department != department_name:
            # 학사안내 안의 장학·회계 유의사항 번호처럼 다른 부서 번호를
            # 현재 문서의 대표 연락처로 잘못 가져오지 않는다.
            score = -100
        if re.search(r"FAX|팩스", context, re.IGNORECASE):
            score -= 5
        candidates.append((score, match.start(), phone))
    phone = max(candidates, key=lambda item: (item[0], -item[1]))[2] if candidates and max(c[0] for c in candidates) > 0 else None
    return person, phone


def extract_application_period(text: str, published_at: datetime) -> tuple[datetime | None, datetime | None]:
    """행사 수행일보다 모집·신청 문구 주변의 날짜를 우선 추출한다."""
    normalized = normalize_text(text)
    base_year = published_at.year
    candidates = []
    for label in APPLICATION_LABEL_PATTERN.finditer(normalized):
        fragment = normalized[label.start():label.end() + 240]
        dates = list(FLEX_DATE_PATTERN.finditer(fragment))
        if not dates:
            continue
        label_text = label.group(0)
        explicit_period = any(token in label_text for token in ("기간", "기한", "마감", "일정"))
        # 단순히 "신청" 뒤 멀리 떨어진 운영일자가 나온 경우 모집 기간으로 오인하지 않는다.
        if not explicit_period:
            before_date = fragment[len(label_text):dates[0].start()]
            if dates[0].start() > 25 or re.search(r"[.!?]", before_date):
                continue
        score = len(dates)
        if explicit_period:
            score += 4
        if any(token in fragment[:180] for token in ("~", "부터", "까지")):
            score += 2
        prefix_context = normalized[max(0, label.start() - 24):label.start()]
        # 행사 운영일·교육일정보다 "지원서 접수 기간"이 학생이
        # 실제로 지켜야 할 신청 마감이므로 명시적 서류 접수 표기를 우선한다.
        if (
            explicit_period
            and any(token in prefix_context for token in ("지원서", "신청서", "서류"))
            and any(token in label_text for token in ("접수", "제출", "지원", "신청"))
        ):
            score += 8
        candidates.append((score, label.start(), label_text, fragment, dates))
    if not candidates:
        return None, None

    _, _, label_text, fragment, matches = max(candidates, key=lambda item: (item[0], item[1]))

    def parsed(match, end_of_period: bool) -> datetime | None:
        try:
            year = int(match.group(1) or base_year)
            month = int(match.group(2))
            day = int(match.group(3))
            meridiem = match.group(4)
            hour = int(match.group(5) or (23 if end_of_period else 0))
            minute = int(match.group(6) or (59 if end_of_period and not match.group(5) else 0))
            if meridiem == "오후" and hour < 12:
                hour += 12
            if meridiem == "오전" and hour == 12:
                hour = 0
            return datetime(year, month, day, hour, minute, tzinfo=KST)
        except (TypeError, ValueError):
            return None

    matches = [match for match in matches if parsed(match, False) is not None]
    if not matches:
        return None, None

    first_prefix = fragment[len(label_text):matches[0].start()]
    if "~" in first_prefix:
        return None, parsed(matches[0], True)

    for index in range(len(matches) - 1):
        between = fragment[matches[index].end():matches[index + 1].start()]
        if "~" not in between and "부터" not in between:
            continue
        start = parsed(matches[index], False)
        end = parsed(matches[index + 1], True)
        if start and end and end >= start:
            return start, end

    only = matches[0]
    # "2026. 7. 7. ~ 13."처럼 같은 달의 종료일에서 연·월을 생략한 범위.
    short_end = re.match(
        r"\s*[.]?\s*(?:\([^)]+\))?\s*(?:~|[-–—]|부터)\s*(\d{1,2})\s*(?:[.일])?"
        r"(?:\s*\([^)]+\))?(?:\s*[.]?\s*(오전|오후)?\s*(\d{1,2})(?::|시)\s*(\d{1,2})?\s*분?)?",
        fragment[only.end():only.end() + 60],
    )
    if short_end:
        start = parsed(only, False)
        try:
            hour = int(short_end.group(3) or 23)
            minute = int(short_end.group(4) or (59 if not short_end.group(3) else 0))
            if short_end.group(2) == "오후" and hour < 12:
                hour += 12
            if short_end.group(2) == "오전" and hour == 12:
                hour = 0
            end = datetime(start.year, start.month, int(short_end.group(1)), hour, minute, tzinfo=KST)
            if end >= start:
                return start, end
        except (AttributeError, TypeError, ValueError):
            pass
    around = f"{label_text} {fragment[:only.end() + 20]}"
    date_prefix = fragment[len(label_text):only.start()]
    date_tail = fragment[only.end():only.end() + 8].lstrip()
    # "신청: ~ 7. 12."처럼 시작일을 생략한 표기는 마감일 하나만
    # 제시한 것이다. 이를 시작일로 저장하면 마감 후에도 계속 모집 중으로 보인다.
    if "~" in date_prefix or any(token in around for token in ("까지", "마감")) or date_tail.startswith("한"):
        return None, parsed(only, True)
    if "부터" in around:
        return parsed(only, False), None
    # 기간이라는 명시적 표지가 있어도 시작/종료 방향을 알 수 없으면 추측하지 않는다.
    return parsed(only, False), None


def sensitive_input_types(text: str) -> list[str]:
    """Return user-facing labels only; never return the matched sensitive values."""
    normalized = normalize_text(text)
    found = []
    if EMAIL_PATTERN.search(normalized):
        found.append("이메일")
    if PHONE_PATTERN.search(normalized):
        found.append("전화번호")
    if STUDENT_ID_PATTERN.search(normalized):
        found.append("학번 또는 개인 식별번호")
    if ACCOUNT_PATTERN.search(normalized):
        found.append("계좌번호")
    if NAMED_PERSON_PATTERN.search(normalized):
        found.append("이름")
    if PERSONAL_GRADE_PATTERN.search(normalized):
        found.append("개인 성적")
    return found


def is_dangerous_action_request(text: str) -> bool:
    """폭력·방화의 실행을 돕는 요청만 모델 호출 전에 보수적으로 차단한다."""
    normalized = normalize_text(text)
    if not DANGEROUS_ACTION_PATTERN.search(normalized):
        return False
    malicious_intent = DANGEROUS_INTENT_PATTERN.search(normalized)
    if not malicious_intent:
        return False
    # 예방·대피·신고 질문은 허용하되, 실행 최적화나 은폐 표현이 함께
    # 있으면 안전 문구를 끼워 넣은 우회 요청으로 보고 차단한다.
    if BENIGN_SAFETY_CONTEXT_PATTERN.search(normalized) and not re.search(
        r"(?:잘\s*(?:퍼지|번지)|효과적|최대한|피해를?\s*(?:크게|많이)|"
        r"몰래|들키지|안\s*걸리|추적을?\s*피하|증거를?\s*(?:없애|숨기))",
        normalized,
        re.I,
    ):
        return False
    return True


def is_harm_reporting_request(text: str) -> bool:
    """범죄 실행이 아니라 피해·위협의 신고와 도움을 구하는 질문을 구분한다."""
    normalized = normalize_text(text)
    return bool(
        DANGEROUS_ACTION_PATTERN.search(normalized)
        and HARM_REPORTING_CONTEXT_PATTERN.search(normalized)
    )
