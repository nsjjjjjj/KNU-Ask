import { Bot } from 'lucide-react'
import type { ChatMessageModel } from '../types'
import { DepartmentCard } from './DepartmentCard'
import { NoticeCard } from './NoticeCard'
import { AnswerStatus } from './AnswerStatus'
import { FeedbackBar } from './FeedbackBar'
import { NextActionCard } from './NextActionCard'
import { ActionGuideCard } from './ActionGuideCard'
import { AnswerFactsCard } from './AnswerFactsCard'

export function ChatMessage({ message }: { message: ChatMessageModel }) {
  const assistant = message.role === 'assistant'
  return <article className={`flex gap-2.5 ${assistant?'justify-start':'justify-end'}`} aria-label={assistant?'AI 답변':'내 질문'}>
    {assistant && <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-600 text-white"><Bot size={19}/></span>}
    <div className={`max-w-[88%] rounded-2xl px-4 py-3 shadow-sm md:max-w-[76%] ${assistant?'rounded-tl-sm border border-slate-200 bg-white text-slate-700':'rounded-tr-sm bg-brand-600 text-white'}`}>
      {assistant && message.status && <AnswerStatus status={message.status} scope={message.searchScope}/>} 
      <p className="whitespace-pre-wrap text-[15px] leading-7">{message.content}</p>
      {assistant && ((message.answerFacts?.length ?? 0) > 0 || (message.answerNotes?.length ?? 0) > 0) && <AnswerFactsCard facts={message.answerFacts ?? []} notes={message.answerNotes ?? []}/>} 
      {assistant && message.verifiedAt && <p className="mt-2 text-[11px] text-slate-400">확인 기준 {new Intl.DateTimeFormat('ko-KR',{dateStyle:'medium',timeStyle:'short'}).format(new Date(message.verifiedAt))}</p>}
      {assistant && message.warnings?.map(warning=><p key={warning} className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm font-semibold text-amber-950">{warning}</p>)}
      {assistant && message.actionGuide && <ActionGuideCard guide={message.actionGuide}/>} 
      {assistant && !message.actionGuide && message.nextAction && <NextActionCard action={message.nextAction}/>} 
      {assistant && message.department && <DepartmentCard department={message.department}/>} 
      {assistant && message.notices?.map(notice => <NoticeCard key={notice.id} notice={notice}/>)}
      {assistant && message.answerId && message.status && <FeedbackBar answerId={message.answerId} sourceIds={message.notices?.map(notice=>notice.id)||[]} status={message.status}/>} 
    </div>
  </article>
}
