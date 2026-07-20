import { CalendarClock, CheckCircle2 } from 'lucide-react'
import type { AnswerFact } from '../types'

export function AnswerFactsCard({ facts, notes }: { facts: AnswerFact[]; notes: string[] }) {
  return <section aria-label="답변 핵심 정보" className="mt-4 overflow-hidden rounded-xl border border-brand-100 bg-brand-50/50">
    {facts.length > 0 && <dl className="divide-y divide-brand-100">
      {facts.map((fact, index) => <div key={`${fact.label}-${index}`} className="grid gap-1 px-4 py-3 sm:grid-cols-[9rem_1fr] sm:gap-3">
        <dt className="flex items-center gap-1.5 text-xs font-extrabold text-brand-700"><CalendarClock size={15}/>{fact.label}</dt>
        <dd className="text-sm font-semibold leading-6 text-slate-800">{fact.value}</dd>
      </div>)}
    </dl>}
    {notes.length > 0 && <div className="border-t border-brand-100 bg-white/70 px-4 py-3">
      <p className="flex items-center gap-1.5 text-xs font-extrabold text-slate-700"><CheckCircle2 size={15}/>함께 확인할 사항</p>
      <ul className="mt-2 space-y-1.5 text-sm leading-6 text-slate-600">{notes.map(note=><li key={note} className="flex gap-2"><span aria-hidden="true" className="text-brand-500">•</span><span>{note}</span></li>)}</ul>
    </div>}
  </section>
}
