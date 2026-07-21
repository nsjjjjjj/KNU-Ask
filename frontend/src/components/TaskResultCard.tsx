import type { TaskAnswerResult } from '../types'
import { ActionGuideCard } from './ActionGuideCard'
import { AnswerFactsCard } from './AnswerFactsCard'
import { DepartmentCard } from './DepartmentCard'
import { NextActionCard } from './NextActionCard'

export function TaskResultCard({ result }: { result: TaskAnswerResult }) {
  const hasDepartment = Boolean(result.department?.name || result.department?.phone || result.department?.email)
  const brief = result.answer.length > 180 ? `${result.answer.slice(0, 180)}…` : result.answer
  const hasDetails = result.answer !== brief || result.answerFacts.length > 0 || Boolean(result.actionGuide || result.nextAction || hasDepartment)
  return <section className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-3" aria-label={`${result.taskName} 업무 안내`}>
    <h2 className="text-base font-black text-slate-900">{result.taskName}</h2>
    <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-slate-700">{brief}</p>
    {hasDetails && <details className="mt-3 rounded-xl border border-slate-200 bg-white">
      <summary className="cursor-pointer px-3 py-3 text-sm font-extrabold text-brand-700">세부 안내 보기</summary>
      <div className="border-t border-slate-200 p-3">
        {result.answer !== brief && <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{result.answer}</p>}
        {result.answerFacts.length > 0 && <AnswerFactsCard facts={result.answerFacts} notes={[]}/>}
        {result.actionGuide && <ActionGuideCard guide={result.actionGuide}/>}
        {result.nextAction && !result.actionGuide && <NextActionCard action={result.nextAction}/>}
        {hasDepartment && <DepartmentCard department={result.department}/>}
      </div>
    </details>}
  </section>
}
