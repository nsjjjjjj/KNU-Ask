import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../pages/ChatPage'
import { DepartmentCard } from '../components/DepartmentCard'

const jsonResponse=(body:unknown)=>Promise.resolve({ok:true,json:()=>Promise.resolve(body)}) as Promise<Response>

beforeEach(()=>{
  vi.stubGlobal('fetch',vi.fn((input:RequestInfo|URL,init?:RequestInit)=>{
    const url=String(input)
    if(url.endsWith('/faqs'))return jsonResponse([{id:1,question:'휴학 신청 방법',category:'학사'}])
    if(url.endsWith('/chat')&&init?.method==='POST')return jsonResponse({answerId:'answer-1',answer:'휴학 신청 절차를 정리했습니다.',status:'success',answerMode:'action_guide',matchedNotices:[{id:1,title:'휴학 신청 안내',category:'학사',publishedAt:'2026-07-10T09:00:00+09:00',noticeStatus:'active',sourceUrl:'https://example.invalid/1'}],sources:[{noticeId:1,title:'휴학 신청 안내',publishedAt:'2026-07-10T09:00:00+09:00',effectiveStatus:'active',evidenceExcerpt:'휴학 신청 기간은 8월 14일까지입니다.',url:'https://example.invalid/1'}],department:{name:'학사지원팀',phone:'031-000-0000',officeHours:'평일 09:00~18:00'},nextAction:{label:'공식 공지에서 신청 방법 확인',description:'마감 전에 제출하세요.',url:'https://example.invalid/1',deadline:'2026-08-14T18:00:00+09:00',official:true},actionGuide:{taskName:'휴학 신청',summary:'학사시스템에서 휴학을 신청합니다.',targets:['재학생'],period:{start:'2026-08-01T09:00:00+09:00',end:'2026-08-14T18:00:00+09:00'},prerequisites:['지도교수 상담'],requiredDocuments:['휴학 신청서'],steps:[{order:1,title:'학사시스템 접속',description:'학사시스템에 로그인합니다.',actionType:'open_url',actionUrl:null,linkLabel:null,sourceType:'html',sourceLocator:'공지 본문',confidence:0.9},{order:2,title:'학적 메뉴 선택',description:'휴학 신청 메뉴로 이동합니다.',actionType:'navigate',actionUrl:null,linkLabel:null,sourceType:'html',sourceLocator:'공지 본문',confidence:0.9},{order:3,title:'신청서 제출',description:'내용을 확인하고 제출합니다.',actionType:'submit',actionUrl:null,linkLabel:null,sourceType:'html',sourceLocator:'공지 본문',confidence:0.9}],warnings:['마감 전에 제출하세요.'],applicationUrl:null,sourceUrl:'https://example.invalid/1',department:{name:'학사지원팀',phone:'031-000-0000',officeHours:'평일 09:00~18:00'},confidence:0.9,needsReview:false},warnings:[],originalUrl:'https://example.invalid/1',hasData:true,sessionId:'session-1',verifiedAt:'2026-07-20T10:00:00+09:00',searchScope:{sources:['official_notices','approved_faq'],noticeCount:5,description:'현재 수집된 강남대학교 공개 공지와 검수 FAQ'}})
    return jsonResponse({status:'success'})
  }))
})

describe('강냉이 에스크 채팅 화면',()=>{
  it('반복 문의 버튼과 검증 질문 패널을 연다',async()=>{render(<ChatPage/>);await userEvent.click(screen.getByRole('button',{name:'반복 문의 바로가기'}));expect(screen.getByRole('heading',{name:'반복 문의 바로가기'})).toBeInTheDocument();expect(screen.getByRole('button',{name:/수강신청/})).toBeInTheDocument();expect(screen.getByRole('button',{name:/국가장학금/})).toBeInTheDocument();expect(screen.queryByText('대학생활안내')).not.toBeInTheDocument()})
  it('반복 문의를 선택하면 검증용 질문을 바로 전송한다',async()=>{render(<ChatPage/>);await userEvent.click(screen.getByRole('button',{name:'반복 문의 바로가기'}));await userEvent.click(screen.getByRole('button',{name:/일반휴학/}));expect(screen.getByText('일반휴학 신청 방법 알려줘')).toBeInTheDocument();await waitFor(()=>expect(screen.getByText(/휴학 신청 절차/)).toBeInTheDocument())})
  it('FAQ 선택 시 질문을 전송하고 근거 답변을 표시한다',async()=>{render(<ChatPage/>);await userEvent.click(screen.getByRole('button',{name:'자주 묻는 질문'}));await userEvent.click(await screen.findByRole('button',{name:'휴학 신청 방법'}));expect(screen.getByText('휴학 신청 방법')).toBeInTheDocument();expect(await screen.findByText(/휴학 신청 절차/)).toBeInTheDocument()})
  it('채팅 입력 후 사용자 메시지를 표시한다',async()=>{render(<ChatPage/>);const input=screen.getByLabelText('질문 입력');await userEvent.type(input,'휴학 언제까지야?');fireEvent.keyDown(input,{key:'Enter',code:'Enter'});expect(screen.getByText('휴학 언제까지야?')).toBeInTheDocument();await screen.findByText(/휴학 신청 절차/)})
  it('셔틀 시간 질문의 공식 시간표 이미지를 표시한다',async()=>{
    const fetchMock=vi.mocked(fetch)
    fetchMock.mockImplementation((input:RequestInfo|URL,init?:RequestInit)=>{
      const url=String(input)
      if(url.endsWith('/faqs'))return jsonResponse([])
      if(url.endsWith('/chat')&&init?.method==='POST')return jsonResponse({answerId:'shuttle-1',answer:'무료 순환버스 시간표입니다.',status:'success',answerMode:'deterministic',matchedNotices:[],sources:[],media:[{type:'image',url:'/api/notices/1081/media/1',alt:'강남대학교 무료 순환버스 운행 시간표',caption:'공식 무료 순환버스 운행 시간표',sourceUrl:'https://web.kangnam.ac.kr/shuttle',noticeId:1081}],department:{name:null,phone:null,officeHours:null},warnings:[],originalUrl:'https://web.kangnam.ac.kr/shuttle',hasData:true,sessionId:'shuttle-session',verifiedAt:'2026-07-22T10:00:00+09:00',searchScope:{sources:['official_notices'],noticeCount:1,description:'공식 자료'}})
      return jsonResponse({status:'success'})
    })
    render(<ChatPage/>);const input=screen.getByLabelText('질문 입력');await userEvent.type(input,'셔틀버스 시간표 보여줘');await userEvent.click(screen.getByRole('button',{name:'질문 전송'}));const image=await screen.findByRole('img',{name:'강남대학교 무료 순환버스 운행 시간표'});expect(image).toHaveAttribute('src','/api/notices/1081/media/1');expect(screen.getByRole('link',{name:/공식 원문/})).toHaveAttribute('href','https://web.kangnam.ac.kr/shuttle')
  })
  it('AI 답변과 담당 부서 카드 및 샘플 공지 링크를 표시한다',async()=>{render(<ChatPage/>);const input=screen.getByLabelText('질문 입력');await userEvent.type(input,'휴학 언제까지야?');await userEvent.click(screen.getByRole('button',{name:'질문 전송'}));expect(await screen.findByText(/휴학 신청 절차/)).toBeInTheDocument();expect(screen.getAllByText('학사지원팀').length).toBeGreaterThan(0);expect(screen.getByText('휴학 신청 안내')).toBeInTheDocument();expect(screen.getByRole('link',{name:'샘플 상세 보기'})).toHaveAttribute('href','/notices/1');expect(screen.getByRole('link',{name:'공식 원문 보기'})).toHaveAttribute('href','/notices/1');expect(screen.queryByLabelText('다음에 할 일')).not.toBeInTheDocument();expect(screen.queryByText(/확인 기준/)).not.toBeInTheDocument()})
  it('공지에서 추출한 신청 단계를 순서대로 표시한다',async()=>{render(<ChatPage/>);const input=screen.getByLabelText('질문 입력');await userEvent.type(input,'휴학 신청 방법');await userEvent.click(screen.getByRole('button',{name:'질문 전송'}));expect(await screen.findByRole('heading',{name:'휴학 신청'})).toBeInTheDocument();expect(screen.getByLabelText('1단계')).toBeInTheDocument();expect(screen.getByLabelText('2단계')).toBeInTheDocument();expect(screen.getByLabelText('3단계')).toBeInTheDocument();expect(screen.getByText('휴학 신청서')).toBeInTheDocument()})
  it('애매한 질문의 선택지를 누르면 같은 대화에서 다시 질문한다',async()=>{
    const fetchMock=vi.mocked(fetch)
    fetchMock.mockImplementation((input:RequestInfo|URL,init?:RequestInit)=>{
      const url=String(input)
      if(url.endsWith('/faqs'))return jsonResponse([])
      if(url.endsWith('/chat')&&init?.method==='POST'){
        const body=JSON.parse(String(init.body))
        if(body.message==='휴학 기간')return jsonResponse({answerId:'clarify-1',answer:'휴학 신청 기간과 최대 휴학 가능 기간 중 어떤 것을 찾으시나요?',status:'clarification_required',answerMode:'deterministic',clarificationOptions:['휴학 신청 기간','최대 휴학 가능 기간'],matchedNotices:[],sources:[],department:{name:null,phone:null,officeHours:null},warnings:[],originalUrl:null,hasData:false,sessionId:'session-clarify',verifiedAt:'2026-07-22T10:00:00+09:00',searchScope:{sources:[],noticeCount:5,description:'질문 의도 확인 후 공식 자료 검색'}})
        return jsonResponse({answerId:'answer-2',answer:'휴학 신청 기간을 확인했습니다.',status:'success',answerMode:'deterministic',matchedNotices:[],sources:[],department:{name:null,phone:null,officeHours:null},warnings:[],originalUrl:null,hasData:false,sessionId:'session-clarify',verifiedAt:'2026-07-22T10:00:00+09:00',searchScope:{sources:[],noticeCount:5,description:'공식 자료'}})
      }
      return jsonResponse({status:'success'})
    })
    render(<ChatPage/>);const input=screen.getByLabelText('질문 입력');await userEvent.type(input,'휴학 기간');await userEvent.click(screen.getByRole('button',{name:'질문 전송'}));await userEvent.click(await screen.findByRole('button',{name:'휴학 신청 기간'}));expect(await screen.findByText('휴학 신청 기간을 확인했습니다.')).toBeInTheDocument();expect(fetchMock).toHaveBeenLastCalledWith(expect.stringContaining('/chat'),expect.objectContaining({body:expect.stringContaining('"sessionId":"session-clarify"')}))
  })
  it('담당 부서 카드에서 내부 연락처 보완 문구를 숨긴다',()=>{render(<DepartmentCard department={{name:'교무팀',phone:'031-280-3543',officeHours:null,contactSource:'강남대학교 공식 직원 연락처에서 보완',sourceUrl:'https://web.kangnam.ac.kr/directory'}}/>);expect(screen.getByText('교무팀')).toBeInTheDocument();expect(screen.getByText('031-280-3543')).toBeInTheDocument();expect(screen.queryByText(/공식 직원 연락처에서 보완/)).not.toBeInTheDocument()})
  it('문의 종료 모달을 열고 취소한다',async()=>{render(<ChatPage/>);await userEvent.click(screen.getByRole('button',{name:'채팅 종료'}));expect(screen.getByRole('dialog')).toBeInTheDocument();expect(screen.getByText('문의를 종료하시겠습니까?')).toBeInTheDocument();await userEvent.click(screen.getByRole('button',{name:'계속 문의하기'}));expect(screen.queryByRole('dialog')).not.toBeInTheDocument()})
})
