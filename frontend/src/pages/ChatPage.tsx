import { useEffect, useState } from 'react'
import { api } from '../services/api'
import { FALLBACK_FAQS } from '../data/fallback'
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
    clarificationOptions:result.clarificationOptions,
    status:result.status,verifiedAt:result.verifiedAt,searchScope:result.searchScope,warnings:result.warnings,
    nextAction,actionGuide,department:result.department,
    taskResults:result.taskResults,
    notices,media:result.media,hasData:result.hasData,
  }
}

export function ChatPage() {
  const [messages,setMessages]=useState<ChatMessageModel[]>([initialMessage])
  const [panel,setPanel]=useState<Panel>(null)
  const [faqs,setFaqs]=useState<FAQ[]>(FALLBACK_FAQS.map((question,index)=>({id:index+1,question,category:'기타'})))
  const [loading,setLoading]=useState(false)
  const [exitOpen,setExitOpen]=useState(false)
  const [sessionId,setSessionId]=useState<string>()

  useEffect(()=>{api.faqs().then(setFaqs).catch(()=>{})},[])

  const send=async(message:string)=>{
    if(loading)return
    setPanel(null);setMessages(prev=>[...prev,{id:id(),role:'user',content:message}]);setLoading(true)
    try{
      const result=await api.chat(message,sessionId)
      setSessionId(result.sessionId);setMessages(prev=>[...prev,responseMessage(result)])
    }catch(error){
      setMessages(prev=>[...prev,{id:id(),role:'assistant',content:error instanceof Error?error.message:'요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.',status:'service_error',answerMode:'department_handoff',hasData:false}])
    }finally{setLoading(false)}
  }

  const confirmExit=async()=>{if(sessionId)await api.endSession(sessionId).catch(()=>{});setMessages([initialMessage]);setSessionId(undefined);setPanel(null);setExitOpen(false)}

  return <div className="flex h-dvh min-h-[560px] flex-col bg-slate-50 text-slate-900">
    <a href="#chat-main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[60] focus:rounded-lg focus:bg-white focus:px-4 focus:py-2">채팅 내용으로 이동</a>
    <ChatHeader onExit={()=>setExitOpen(true)}/>
    <QuickActionButtons panel={panel} onSelect={setPanel}/>
    {panel==='category'&&<CategoryPanel onSelect={send}/>}
    {panel==='faq'&&<FAQPanel faqs={faqs} onSelect={faq=>send(faq.question)}/>} 
    {messages.length===1&&!panel&&<SuggestedQuestions onSelect={send}/>} 
    <ChatMessageList messages={messages} loading={loading} onClarificationSelect={send}/>
    <ChatInput onSend={send} disabled={loading}/>
    <ExitModal open={exitOpen} onConfirm={confirmExit} onCancel={()=>setExitOpen(false)}/>
  </div>
}
