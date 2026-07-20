from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import Notice


KST = ZoneInfo("Asia/Seoul")
ACTIONABLE_TYPES = {"신청", "제출", "납부", "참석", "수강", "발급"}


def _period_status(start: datetime | None, end: datetime | None, now: datetime) -> str:
    if start and start.astimezone(KST) > now:
        return "upcoming"
    if end and end.astimezone(KST) < now:
        return "expired"
    # 종료일을 모르는 시작일 하나만으로는 현재도 신청 가능한지 보장할 수 없다.
    # 반대로 마감일이 아직 지나지 않았다면 현재 신청 가능 구간으로 볼 수 있다.
    if end:
        return "active"
    return "unknown"


def effective_status(notice: Notice, now: datetime | None = None) -> str:
    """학생이 실제로 신청 가능한 기간을 행사 수행 기간보다 우선한다."""
    now = now or datetime.now(KST)
    meta = notice.metadata_record
    if not meta:
        return notice.notice_status
    if meta.application_start or meta.application_end:
        return _period_status(meta.application_start, meta.application_end, now)
    if meta.action_type in ACTIONABLE_TYPES or notice.action_guide:
        return "always" if notice.notice_status == "always" else "unknown"
    if meta.event_start or meta.event_end:
        return _period_status(meta.event_start, meta.event_end, now)
    return notice.notice_status if notice.notice_status in {"active", "always"} else "unknown"


def effective_status_label(notice: Notice, status: str | None = None) -> str:
    status = status or effective_status(notice)
    meta = notice.metadata_record
    action_type = meta.action_type if meta else "기타"
    noun = {
        "신청": "신청", "제출": "제출", "납부": "납부", "수강": "수강신청",
        "발급": "발급", "참석": "모집",
    }.get(action_type, "모집" if meta and (meta.application_start or meta.application_end) else "진행")
    labels = {
        "upcoming": f"{noun} 예정",
        "active": f"{noun} 중",
        "expired": f"{noun} 마감",
        "always": "상시",
        "unknown": f"{noun} 기간 확인 필요" if action_type in ACTIONABLE_TYPES else "확인 필요",
    }
    return labels.get(status, "확인 필요")
