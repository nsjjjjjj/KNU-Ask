import { describe, expect, it } from 'vitest'
import { noticeStatusPresentation } from './noticeStatus'

describe('noticeStatusPresentation', () => {
  it('신청 가능한 공지를 신청받는 중으로 표시한다', () => {
    expect(noticeStatusPresentation({ noticeStatus: 'active', statusLabel: '신청 중' }).label).toBe('신청받는 중')
  })

  it('진행 중인 행사만 진행 중으로 표시한다', () => {
    expect(noticeStatusPresentation({ noticeStatus: 'active', statusLabel: '진행 중' }).label).toBe('진행 중')
  })

  it('예정·상시·기간 미확인 상태를 진행 중으로 오인하지 않는다', () => {
    expect(noticeStatusPresentation({ noticeStatus: 'upcoming', statusLabel: '진행 예정' }).label).toBe('진행 예정')
    expect(noticeStatusPresentation({ noticeStatus: 'always', statusLabel: '상시 안내' }).label).toBe('상시 안내')
    expect(noticeStatusPresentation({ noticeStatus: 'unknown', statusLabel: '확인 필요' }).label).toBe('기간 확인 필요')
  })

  it('직접 검색된 마감 공지를 종료로 표시한다', () => {
    expect(noticeStatusPresentation({ noticeStatus: 'expired', statusLabel: '신청 마감' }).label).toBe('종료')
  })
})
