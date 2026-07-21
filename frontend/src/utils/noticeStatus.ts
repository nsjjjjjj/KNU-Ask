import type { Notice } from '../types'

export type NoticeDisplayStatus = 'accepting' | 'ongoing' | 'scheduled' | 'always' | 'ended' | 'unknown'

export interface NoticeStatusPresentation {
  key: NoticeDisplayStatus
  label: '신청받는 중' | '진행 중' | '진행 예정' | '상시 안내' | '종료' | '기간 확인 필요'
  className: string
}

const presentations: Record<NoticeDisplayStatus, NoticeStatusPresentation> = {
  accepting: {
    key: 'accepting',
    label: '신청받는 중',
    className: 'border border-emerald-200 bg-emerald-50 text-emerald-800',
  },
  ongoing: {
    key: 'ongoing',
    label: '진행 중',
    className: 'border border-sky-200 bg-sky-50 text-sky-800',
  },
  scheduled: {
    key: 'scheduled',
    label: '진행 예정',
    className: 'border border-violet-200 bg-violet-50 text-violet-800',
  },
  always: {
    key: 'always',
    label: '상시 안내',
    className: 'border border-blue-200 bg-blue-50 text-blue-800',
  },
  ended: {
    key: 'ended',
    label: '종료',
    className: 'border border-slate-300 bg-slate-100 text-slate-600',
  },
  unknown: {
    key: 'unknown',
    label: '기간 확인 필요',
    className: 'border border-amber-200 bg-amber-50 text-amber-900',
  },
}

export function noticeStatusPresentation(notice: Pick<Notice, 'noticeStatus' | 'statusLabel'>): NoticeStatusPresentation {
  if (notice.noticeStatus === 'expired') return presentations.ended
  if (notice.noticeStatus === 'upcoming') return presentations.scheduled
  if (notice.noticeStatus === 'always') return presentations.always
  if (notice.noticeStatus !== 'active') return presentations.unknown

  // 같은 active 상태라도 신청 공지와 단순 조회·행사 안내를 구분한다.
  const statusLabel = notice.statusLabel || ''
  const acceptsApplications = notice.noticeStatus === 'active'
    && /(신청|모집|접수|제출|납부|수강).*(중)$/.test(statusLabel)

  return acceptsApplications ? presentations.accepting : presentations.ongoing
}
