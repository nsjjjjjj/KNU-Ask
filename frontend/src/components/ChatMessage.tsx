import { forwardRef } from 'react'
import { Bot } from 'lucide-react'
import type { ChatMessageModel } from '../types'
import { DepartmentCard } from './DepartmentCard'
import { NoticeCard } from './NoticeCard'
import { AnswerStatus } from './AnswerStatus'
import { FeedbackBar } from './FeedbackBar'
import { NextActionCard } from './NextActionCard'
import { ActionGuideCard } from './ActionGuideCard'
import { AnswerFactsCard } from './AnswerFactsCard'
import { AnswerMediaCard } from './AnswerMediaCard'
import { TaskResultCard } from './TaskResultCard'
import { noticeStatusPresentation } from '../utils/noticeStatus'

const DEPARTMENT_HIDDEN_STATUSES = new Set(['out_of_scope', 'safety_refusal', 'service_error', 'clarification_required', 'no_result'])
const STATUS_HIDDEN_STATUSES = new Set(['out_of_scope', 'safety_refusal'])
const EXPIRY_COPY = /(신청\s*(?:이|은|는)?.{0,30}마감|마감(?:된|됐|되었|되었습니다)|현재\s*신청할\s*수\s*없|종료된\s*(?:모집|신청))/

function hasDepartmentDetails(message: ChatMessageModel) {
  const department = message.department
  if (!department || (message.status && DEPARTMENT_HIDDEN_STATUSES.has(message.status))) return false
  if ((message.media?.length ?? 0) > 0) return false
  return Boolean(
    department.name || department.contactPerson || department.phone || department.email
    || department.officeLocation || department.officeHours,
  )
}

function isExpiryFact(label: string) {
  return /(신청|접수|지원|모집).*(기간|기한|마감)|본 신청 기간/.test(label)
}

export const ChatMessage = forwardRef<HTMLElement, {
  message: ChatMessageModel
  onClarificationSelect?: (value: string) => void
  disabled?: boolean
}>(function ChatMessage({ message, onClarificationSelect, disabled = false }, ref) {
  const assistant = message.role === 'assistant'
  const primaryNoticeStatus = message.notices?.length === 1 ? noticeStatusPresentation(message.notices[0]) : null
  const showPrimaryNoticeStatus = Boolean(
    assistant
    && message.status === 'success'
    && message.notices?.length === 1
    && ['active', 'upcoming'].includes(message.notices[0].noticeStatus)
    && primaryNoticeStatus?.label !== '상시 안내',
  )
  const noticeUrls = new Set(message.notices?.map(notice => notice.sourceUrl.replace(/\/$/, '')) ?? [])
  const showNextAction = Boolean(
    message.nextAction
    && !message.actionGuide
    && (!message.nextAction.url || !noticeUrls.has(message.nextAction.url.replace(/\/$/, '')))
  )
  const showDepartment = assistant && !message.taskResults?.length && hasDepartmentDetails(message)
  const rawParagraphs = message.content.trim().split(/\n{2,}/).filter(Boolean)
  const nonExpiryParagraphs = message.status === 'stale_only'
    ? rawParagraphs.filter(paragraph => !EXPIRY_COPY.test(paragraph))
    : rawParagraphs
  const candidateParagraphs = nonExpiryParagraphs.length > 0 ? nonExpiryParagraphs : rawParagraphs
  const departmentOnlyAnswer = Boolean(
    showDepartment
    && message.department?.phone
    && candidateParagraphs.length === 1
    && candidateParagraphs[0].startsWith('담당 부서는')
    && candidateParagraphs[0].includes(message.department.phone),
  )
  const contentParagraphs = departmentOnlyAnswer ? [] : candidateParagraphs
  const deadlineFact = message.answerFacts?.find(fact => isExpiryFact(fact.label))
  const deadline = message.actionGuide?.period.end || deadlineFact?.value
  const visibleFacts = message.status === 'stale_only'
    ? (message.answerFacts ?? []).filter(fact => !isExpiryFact(fact.label))
    : (message.answerFacts ?? [])
  const visibleWarnings = (message.warnings ?? []).filter(warning => (
    message.status !== 'stale_only' || !EXPIRY_COPY.test(warning)
  ))

  return <article ref={ref} className={`flex ${assistant?'justify-start sm:gap-2.5':'justify-end'}`} aria-label={assistant?'AI 답변':'내 질문'}>
    {assistant && <span className="hidden h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-600 text-white sm:grid"><Bot size={19}/></span>}
    <div className={`${assistant?'w-full max-w-full rounded-tl-sm border border-slate-200 bg-white text-slate-700 sm:w-auto sm:max-w-[88%] md:max-w-[76%]':'max-w-[88%] rounded-tr-sm bg-brand-600 text-white'} rounded-2xl px-4 py-3 shadow-sm`}>
      {assistant && message.status && !STATUS_HIDDEN_STATUSES.has(message.status) && <AnswerStatus status={message.status} deadline={deadline}/>}
      {contentParagraphs.length > 0 && <div>{contentParagraphs.map((paragraph, index)=><p key={`${index}-${paragraph.slice(0, 20)}`} className={`${index > 0 ? 'mt-3 ' : ''}whitespace-pre-wrap text-[15px] leading-7`}>{paragraph}</p>)}</div>}
      {assistant && (message.clarificationOptions?.length ?? 0) > 0 && <div className="mt-3 flex flex-wrap gap-2" aria-label="질문 의도 선택">
        {message.clarificationOptions?.map(option=><button key={option} type="button" disabled={disabled} onClick={()=>onClarificationSelect?.(option)} className="rounded-lg border border-brand-200 bg-brand-50 px-3 py-2 text-sm font-bold text-brand-700 transition hover:border-brand-400 hover:bg-brand-100 disabled:cursor-not-allowed disabled:opacity-50">{option}</button>)}
      </div>}
      {showPrimaryNoticeStatus && primaryNoticeStatus && <p className="mt-3"><span className={`inline-flex rounded-md px-2.5 py-1 text-xs font-extrabold ${primaryNoticeStatus.className}`}>{primaryNoticeStatus.label}</span></p>}
      {assistant && (visibleFacts.length > 0 || (message.answerNotes?.length ?? 0) > 0) && <AnswerFactsCard facts={visibleFacts} notes={message.answerNotes ?? []}/>}
      {assistant && visibleWarnings.map(warning=><p key={warning} className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm font-semibold text-amber-950">{warning}</p>)}
      {assistant && message.media?.map(media => <AnswerMediaCard key={`${media.noticeId}-${media.url}`} media={media}/>) }
      {assistant && message.taskResults?.map(result => <TaskResultCard key={result.taskKey} result={result}/>) }
      {assistant && !message.taskResults?.length && message.actionGuide && <ActionGuideCard guide={message.actionGuide} status={message.status} answerText={contentParagraphs.join(' ')}/>}
      {assistant && !message.taskResults?.length && showNextAction && message.nextAction && <NextActionCard action={message.nextAction}/>}
      {showDepartment && message.department && <DepartmentCard department={message.department}/>}
      {assistant && (message.notices?.length ?? 0) > 0 && <details className="mt-4 rounded-xl border border-slate-200 bg-slate-50"><summary className="cursor-pointer px-3 py-3 text-sm font-extrabold text-slate-700">공식 근거와 원문 {message.notices?.length}건</summary><div className="border-t border-slate-200 p-2">{message.notices?.map(notice => <NoticeCard key={notice.id} notice={notice}/>)}{message.searchScope && <p className="px-3 pb-2 text-[11px] leading-5 text-slate-500">확인 범위: {message.searchScope.description}</p>}</div></details>}
      {assistant && message.answerId && message.status && <FeedbackBar answerId={message.answerId} sourceIds={message.notices?.map(notice=>notice.id)||[]} status={message.status}/>} 
    </div>
  </article>
})
