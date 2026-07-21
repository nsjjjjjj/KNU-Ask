import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AnswerFactsCard } from '../components/AnswerFactsCard'

describe('답변 핵심 정보 카드', () => {
  it('일정 외 근거도 추가 정보로 접어 표시한다', () => {
    render(<AnswerFactsCard facts={[
      { label:'행사 기간', value:'2026.08.03.~08.14.' },
      { label:'참여 대상', value:'인근 대학 재학생' },
      { label:'모집 인원', value:'대학별 5명 이내' },
      { label:'참가비', value:'35만원/인' },
    ]} notes={[]} />)

    expect(screen.getByText('추가 정보 1개 보기')).toBeInTheDocument()
    expect(screen.getByText('참가비')).toBeInTheDocument()
  })

  it('신청 장소 URL을 새 창에서 열 수 있는 링크로 표시한다', () => {
    render(<AnswerFactsCard facts={[
      { label:'신청 장소', value:'https://ai-boost.gdg-kangnam.site' },
    ]} notes={[]} />)

    const link = screen.getByRole('link', { name:'https://ai-boost.gdg-kangnam.site' })
    expect(link).toHaveAttribute('href', 'https://ai-boost.gdg-kangnam.site')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noreferrer')
  })
})
