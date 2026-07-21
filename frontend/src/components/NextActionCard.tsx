import { ArrowRight, CalendarClock } from 'lucide-react'
import type { NextAction } from '../types'

export function NextActionCard({ action }: { action: NextAction }) {
  const deadline = action.deadline ? new Intl.DateTimeFormat('ko-KR', { dateStyle:'medium', timeStyle:'short', timeZone:'Asia/Seoul' }).format(new Date(action.deadline)) : null
  return <section aria-label="다음에 할 일" className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4">
    <p className="text-xs font-bold uppercase tracking-[0.12em] text-amber-800">다음에 할 일</p>
    <p className="mt-1 font-extrabold text-slate-900">{action.label}</p>
    {action.description && <p className="mt-1 text-sm leading-6 text-slate-600">{action.description}</p>}
    {deadline && <p className="mt-2 flex items-center gap-1.5 text-sm font-semibold text-amber-900"><CalendarClock size={16}/>마감 {deadline}</p>}
    {action.url && <a href={action.url} target="_blank" rel="noreferrer" className="mt-3 inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg bg-amber-800 px-4 text-sm font-bold text-white transition hover:bg-amber-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-900">{action.official?'공식 원문 확인':'샘플 공지 상세 보기'}<ArrowRight size={17}/></a>}
  </section>
}
