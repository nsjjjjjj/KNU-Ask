import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ChatMessage } from '../components/ChatMessage'
import type { ActionGuide } from '../types'

const guide: ActionGuide = {
  taskName:'캠프·프로그램 신청',
  summary:'경기대와 크래프톤이 함께 주최하는 캠프입니다.',
  targets:[], period:{end:'2026-07-12T23:59:00+09:00'}, prerequisites:[], requiredDocuments:[], eligibilityNotes:[],
  applicationMethod:'링크 접속 → 외부회원 가입 → 프로그램 검색 → 지원서 업로드',
  applicationLocation:null, feeInformation:null, capacity:null, selectionMethod:null, resultAnnouncement:null,
  cancellationPolicy:null, benefits:[], creditsOrHours:null, importantDates:[], additionalFacts:[],
  steps:[
    {order:1,title:'링크 접속',description:'링크 접속 단계를 진행합니다.',actionType:'navigate',actionUrl:'https://barun.kyonggi.ac.kr/',linkLabel:'경기대 Barun 열기',sourceType:'pdf',confidence:0.75},
    {order:2,title:'외부회원 가입',description:'외부회원 가입 단계를 진행합니다.',actionType:'navigate',actionUrl:'https://barun.kyonggi.ac.kr/',linkLabel:'경기대 Barun 열기',sourceType:'pdf',confidence:0.75},
    {order:3,title:'프로그램 검색',description:'프로그램 검색 단계를 진행합니다.',actionType:'navigate',actionUrl:'https://barun.kyonggi.ac.kr/',linkLabel:'경기대 Barun 열기',sourceType:'pdf',confidence:0.75},
    {order:4,title:'지원서 업로드',description:'작성한 지원서를 업로드합니다.',actionType:'upload',actionUrl:'https://barun.kyonggi.ac.kr/',linkLabel:'경기대 Barun 열기',sourceType:'pdf',confidence:0.75},
  ],
  warnings:[], applicationUrl:'https://barun.kyonggi.ac.kr/', sourceUrl:'https://web.kangnam.ac.kr/camp',
  department:{name:null,phone:null,officeHours:null}, confidence:0.75, needsReview:false,
}

describe('채팅 답변 문단', () => {
  it('빈 줄로 구분된 개요와 마감 상태를 별도 문단으로 표시한다', () => {
    render(<ChatMessage message={{
      id: 'paragraph-answer',
      role: 'assistant',
      content: '경기대와 크래프톤이 함께 주최하는 캠프입니다.\n\n이 모집은 2026.07.12에 마감됐습니다.',
    }}/>)

    const answer = screen.getByLabelText('AI 답변')
    const paragraphs = answer.querySelectorAll('p')
    expect(paragraphs).toHaveLength(2)
    expect(within(answer).getByText(/함께 주최하는 캠프/)).toBeInTheDocument()
    expect(within(answer).getByText(/2026\.07\.12에 마감/)).toBeInTheDocument()
  })

  it('마감 상태·기간·경고를 한 상태 블록으로 통합한다', () => {
    render(<ChatMessage message={{
      id:'expired-answer', role:'assistant', status:'stale_only', answerMode:'action_guide',
      content:'경기대와 크래프톤이 함께 주최하는 캠프입니다.\n\n이 모집은 2026.07.12 23:59에 마감됐습니다.',
      answerFacts:[{label:'본 신청 기간',value:'2026.07.12 23:59까지'}],
      warnings:['가장 관련 높은 공지는 신청이 마감된 자료입니다.'], actionGuide:guide,
      notices:[{id:1,title:'캠프 모집',publishedAt:'2026-06-01T00:00:00Z',noticeStatus:'expired',sourceUrl:'https://web.kangnam.ac.kr/camp'}],
      department:{name:null,phone:null,officeHours:null},
    }}/>)

    expect(screen.getByRole('status')).toHaveTextContent('신청 마감 · 2026.07.12 23:59')
    expect(screen.queryByText('본 신청 기간')).not.toBeInTheDocument()
    expect(screen.queryByText('가장 관련 높은 공지는 신청이 마감된 자료입니다.')).not.toBeInTheDocument()
    expect(screen.queryByText(/이 모집은.*마감/)).not.toBeInTheDocument()
    expect(screen.getByText('종료').closest('details')).toBeInTheDocument()
  })

  it('동일한 단계 URL과 요약·형식적인 설명을 반복하지 않는다', () => {
    render(<ChatMessage message={{
      id:'guide-answer', role:'assistant', status:'stale_only',
      content:'경기대와 크래프톤이 함께 주최하는 캠프입니다.', actionGuide:guide,
      notices:[], department:{name:null,phone:null,officeHours:null},
    }}/>)

    expect(screen.getAllByRole('link',{name:'경기대 Barun 열기'})).toHaveLength(1)
    expect(screen.queryByRole('link',{name:'신청 페이지 열기'})).not.toBeInTheDocument()
    expect(screen.getByRole('link',{name:'추가 모집 여부 확인'})).toHaveAttribute('href','https://web.kangnam.ac.kr/camp')
    expect(screen.getAllByText('경기대와 크래프톤이 함께 주최하는 캠프입니다.')).toHaveLength(1)
    expect(screen.queryByText('링크 접속 단계를 진행합니다.')).not.toBeInTheDocument()
    expect(screen.getByText('나머지 1단계 보기')).toBeInTheDocument()
  })

  it('거절 답변에는 담당 부서 카드와 검색 범위를 표시하지 않는다', () => {
    render(<ChatMessage message={{
      id:'scope-answer', role:'assistant', status:'out_of_scope',
      content:'강남대학교 공식 안내와 관련된 질문만 도와드릴 수 있어요.',
      searchScope:{sources:[],noticeCount:0,description:'공식 자료 검색을 수행하지 않음'},
      department:{name:'교무팀',phone:'031-000-0000',officeHours:null},
    }}/>)

    expect(screen.queryByLabelText('담당 부서 정보')).not.toBeInTheDocument()
    expect(screen.queryByText(/검색 범위|확인 범위/)).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('단순 연락처 답변은 같은 전화번호를 본문과 카드에 반복하지 않는다', () => {
    render(<ChatMessage message={{
      id:'contact-answer', role:'assistant', status:'success',
      content:'담당 부서는 교무팀이며 조호성 담당자 전화번호는 031-280-3542입니다.',
      department:{name:'교무팀',contactPerson:'조호성',phone:'031-280-3542',officeHours:null},
    }}/>)

    expect(screen.getAllByText('031-280-3542')).toHaveLength(1)
    expect(screen.getByLabelText('담당 부서 정보')).toBeInTheDocument()
  })
})
