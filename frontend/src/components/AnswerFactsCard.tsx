import { CalendarClock, CheckCircle2 } from 'lucide-react'
import type { AnswerFact } from '../types'

const URL_PATTERN = /(https?:\/\/[^\s]+)/g

function linkedValue(value: string) {
  return value.split(URL_PATTERN).map((part, index) => {
    if (!part.startsWith('http://') && !part.startsWith('https://')) return part
    const match = part.match(/^(.+?)([.,;!?)]*)$/)
    const url = match?.[1] ?? part
    const suffix = match?.[2] ?? ''
    try {
      const parsed = new URL(url)
      if (!['http:', 'https:'].includes(parsed.protocol)) return part
    } catch {
      return part
    }
    return <span key={`${url}-${index}`}><a href={url} target="_blank" rel="noreferrer" title="새 창에서 열기" className="break-all font-extrabold text-brand-700 underline decoration-brand-300 underline-offset-2 transition hover:text-brand-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600">{url}</a>{suffix}</span>
  })
}

export function AnswerFactsCard({ facts, notes }: { facts: AnswerFact[]; notes: string[] }) {
  const primaryFacts = facts.slice(0, 3)
  const additionalFacts = facts.slice(3)
  const rows = (values: AnswerFact[]) => values.map((fact, index) => <div key={`${fact.label}-${index}`} className="grid gap-1 px-4 py-3 sm:grid-cols-[9rem_1fr] sm:gap-3">
    <dt className="flex items-center gap-1.5 text-xs font-extrabold text-brand-700"><CalendarClock size={15}/>{fact.label}</dt>
    <dd className="text-sm font-semibold leading-6 text-slate-800">{linkedValue(fact.value)}</dd>
  </div>)
  return <section aria-label="답변 핵심 정보" className="mt-4 overflow-hidden rounded-xl border border-brand-100 bg-brand-50/50">
    {primaryFacts.length > 0 && <dl className="divide-y divide-brand-100">{rows(primaryFacts)}</dl>}
    {additionalFacts.length > 0 && <details className="border-t border-brand-100 bg-white/60">
      <summary className="cursor-pointer px-4 py-3 text-xs font-extrabold text-brand-700">추가 정보 {additionalFacts.length}개 보기</summary>
      <dl className="divide-y divide-brand-100 border-t border-brand-100">{rows(additionalFacts)}</dl>
    </details>}
    {notes.length > 0 && <div className="border-t border-brand-100 bg-white/70 px-4 py-3">
      <p className="flex items-center gap-1.5 text-xs font-extrabold text-slate-700"><CheckCircle2 size={15}/>함께 확인할 사항</p>
      <ul className="mt-2 space-y-1.5 text-sm leading-6 text-slate-600">{notes.map(note=><li key={note} className="flex gap-2"><span aria-hidden="true" className="text-brand-500">•</span><span>{note}</span></li>)}</ul>
    </div>}
  </section>
}
