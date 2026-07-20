import { Check, ThumbsDown, ThumbsUp } from 'lucide-react'
import { useState } from 'react'
import { api } from '../services/api'
import type { ChatStatus, FeedbackReason } from '../types'

export function FeedbackBar({ answerId, sourceIds, status }: { answerId: string; sourceIds: number[]; status: ChatStatus }) {
  const [sent,setSent]=useState(false)
  const [expanded,setExpanded]=useState(false)
  const submit=async(resolved:boolean,reason:FeedbackReason)=>{await api.feedback({answerId,resolved,reason,sourceIds,responseStatus:status});setSent(true);setExpanded(false)}
  if(sent)return <p className="mt-4 flex items-center gap-2 text-sm font-semibold text-emerald-700" role="status"><Check size={16}/>의견을 반영했습니다.</p>
  return <div className="mt-4 border-t border-slate-200 pt-3">
    <div className="flex flex-wrap items-center gap-2"><span className="mr-1 text-xs font-semibold text-slate-500">이 답변이 도움됐나요?</span><button onClick={()=>submit(true,'resolved')} className="inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded-lg border border-slate-200 px-3 text-sm font-semibold text-slate-700 transition hover:bg-emerald-50 hover:text-emerald-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"><ThumbsUp size={16}/>도움됐어요</button><button onClick={()=>setExpanded(v=>!v)} aria-expanded={expanded} className="inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded-lg border border-slate-200 px-3 text-sm font-semibold text-slate-700 transition hover:bg-red-50 hover:text-red-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"><ThumbsDown size={16}/>아쉬워요</button></div>
    {expanded&&<div className="mt-2 flex flex-wrap gap-2" aria-label="아쉬운 이유">{([['incorrect','내용이 달라요'],['outdated','정보가 오래됐어요'],['misunderstood','질문을 오해했어요'],['insufficient','근거가 부족해요'],['needs_staff','담당자 확인이 필요해요']] as [FeedbackReason,string][]).map(([reason,label])=><button key={reason} onClick={()=>submit(false,reason)} className="min-h-11 cursor-pointer rounded-lg bg-slate-100 px-3 text-xs font-semibold text-slate-700 transition hover:bg-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600">{label}</button>)}</div>}
  </div>
}
