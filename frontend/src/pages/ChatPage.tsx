import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { api } from '../services/api'
import { FALLBACK_CATEGORIES, FALLBACK_FAQS } from '../data/fallback'
import type { ActionGuide, ChatMessageModel, ChatResponse, FAQ, Panel } from '../types'
import { ChatHeader } from '../components/ChatHeader'
import { QuickActionButtons } from '../components/QuickActionButtons'
import { CategoryPanel } from '../components/CategoryPanel'
import { FAQPanel } from '../components/FAQPanel'
import { ChatMessageList } from '../components/ChatMessageList'
import { ChatInput } from '../components/ChatInput'
import { ExitModal } from '../components/ExitModal'
import { SuggestedQuestions } from '../components/SuggestedQuestions'
import { isPlaceholderUrl, normalizeNotice } from '../utils/noticeLinks'

const initialMessage: ChatMessageModel = {
  id:'welcome',
  role:'assistant',
  content:'안녕하세요. 강남대학교 공개 공지를 근거로 학사 정보를 찾아드려요.\n답변에는 다음 행동과 담당 부서, 공식 원문을 함께 표시합니다.',
}
const id=()=>`${Date.now()}-${Math.random()}`

function responseMessage(result:ChatResponse):ChatMessageModel {
  const evidence=new Map(result.sources.map(source=>[source.noticeId,source.evidenceExcerpt]))
  const notices=result.matchedNotices.map(notice=>normalizeNotice({...notice,evidenceExcerpt:evidence.get(notice.id)}))
  const sampleNotice=notices.find(notice=>notice.isSample)
  const originalSampleUrl=result.matchedNotices.find(notice=>notice.id===sampleNotice?.id)?.sourceUrl
  const nextAction=result.nextAction && sampleNotice && isPlaceholderUrl(result.nextAction.url)
    ? {...result.nextAction,url:sampleNotice.sourceUrl,official:false}
    : result.nextAction
  const actionGuide: ActionGuide | null | undefined = result.actionGuide && sampleNotice && isPlaceholderUrl(result.actionGuide.sourceUrl)
    ? {
      ...result.actionGuide,
      sourceUrl: sampleNotice.sourceUrl,
      applicationUrl: isPlaceholderUrl(result.actionGuide.applicationUrl) ? null : result.actionGuide.applicationUrl,
      steps: result.actionGuide.steps.map(step => isPlaceholderUrl(step.actionUrl) ? {...step, actionUrl: null, linkLabel: null} : step),
    }
    : result.actionGuide
  const answer=sampleNotice && originalSampleUrl
    ? result.answer.split(originalSampleUrl).join('아래 샘플 공지 상세 보기')
    : result.answer
  return {
    id:id(),role:'assistant',content:answer,answerId:result.answerId,answerMode:result.answerMode,
    answerFacts:result.answerFacts,answerNotes:result.answerNotes,
    status:result.status,verifiedAt:result.verifiedAt,searchScope:result.searchScope,warnings:result.warnings,
    nextAction,actionGuide,department:result.department,
    notices,hasData:result.hasData,
  }
}

export function ChatPage() {
  const [messages,setMessages]=useState<ChatMessageModel[]>([initialMessage])
  const [panel,setPanel]=useState<Panel>(null)
  const [categories,setCategories]=useState(FALLBACK_CATEGORIES)
  const [faqs,setFaqs]=useState<FAQ[]>(FALLBACK_FAQS.map((question,index)=>({id:index+1,question,category:'기타'})))
  const [loading,setLoading]=useState(false)
  const [exitOpen,setExitOpen]=useState(false)
  const [sessionId,setSessionId]=useState<string>()
  const [selectedCategory,setSelectedCategory]=useState<string>()

  useEffect(()=>{api.categories().then(r=>setCategories(r.categories)).catch(()=>{});api.faqs().then(setFaqs).catch(()=>{})},[])

  const chooseCategory=async(category:string)=>{
    setPanel(null);setSelectedCategory(category);setLoading(true)
    setMessages(prev=>[...prev,{id:id(),role:'user',content:`${category} 분야 공지 보여줘`}])
    try{
      const result=await api.categoryNotices(category)
      setMessages(prev=>[...prev,{id:id(),role:'assistant',content:result.message,status:result.notices.length?'success':'no_result',answerMode:'search_results_only',notices:result.notices.map(normalizeNotice),hasData:Boolean(result.notices.length),searchScope:{sources:['official_notices'],noticeCount:result.notices.length,description:`${category} 분야의 수집된 공개 공지`}}])
    }catch(error){
      setMessages(prev=>[...prev,{id:id(),role:'assistant',content:error instanceof Error?error.message:'공지를 불러오지 못했습니다.',status:'service_error',answerMode:'search_results_only',hasData:false}])
    }finally{setLoading(false)}
  }

  const send=async(message:string)=>{
    if(loading)return
    setPanel(null);setMessages(prev=>[...prev,{id:id(),role:'user',content:message}]);setLoading(true)
    try{
      const result=await api.chat(message,sessionId,selectedCategory)
      setSessionId(result.sessionId);setMessages(prev=>[...prev,responseMessage(result)])
    }catch(error){
      setMessages(prev=>[...prev,{id:id(),role:'assistant',content:error instanceof Error?error.message:'요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.',status:'service_error',answerMode:'department_handoff',hasData:false}])
    }finally{setLoading(false)}
  }

  const confirmExit=async()=>{if(sessionId)await api.endSession(sessionId).catch(()=>{});setMessages([initialMessage]);setSessionId(undefined);setSelectedCategory(undefined);setPanel(null);setExitOpen(false)}

  return <div className="flex h-dvh min-h-[560px] flex-col bg-slate-50 text-slate-900">
    <a href="#chat-main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[60] focus:rounded-lg focus:bg-white focus:px-4 focus:py-2">채팅 내용으로 이동</a>
    <ChatHeader onExit={()=>setExitOpen(true)}/>
    <QuickActionButtons panel={panel} onSelect={setPanel}/>
    {selectedCategory&&<div className="border-b border-slate-200 bg-white px-4 py-2 md:px-6"><div className="mx-auto flex max-w-5xl items-center gap-2 text-sm"><span className="text-xs font-semibold text-slate-500">검색 분야</span><button onClick={()=>setSelectedCategory(undefined)} className="inline-flex min-h-11 cursor-pointer items-center gap-1 rounded-full bg-brand-50 px-3 font-bold text-brand-700 transition hover:bg-brand-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600">{selectedCategory}<X size={15}/><span className="sr-only">필터 해제</span></button></div></div>}
    {panel==='category'&&<CategoryPanel categories={categories} onSelect={chooseCategory}/>} 
    {panel==='faq'&&<FAQPanel faqs={faqs} onSelect={faq=>send(faq.question)}/>} 
    {messages.length===1&&!panel&&<SuggestedQuestions onSelect={send}/>} 
    <ChatMessageList messages={messages} loading={loading}/>
    <ChatInput onSend={send} disabled={loading}/>
    <ExitModal open={exitOpen} onConfirm={confirmExit} onCancel={()=>setExitOpen(false)}/>
  </div>
}
