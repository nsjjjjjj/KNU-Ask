import { AlertTriangle, ArrowUpRight, CalendarDays, CheckCircle2, ClipboardList, ExternalLink, FileCheck2, Info, MapPin, Users } from 'lucide-react'
import type { ActionGuide, ChatStatus } from '../types'

function periodLabel(start?: string | null, end?: string | null) {
  const format = (value: string) => new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  if (start && end) return `${format(start)} ~ ${format(end)}`
  if (end) return `${format(end)}까지`
  if (start) return `${format(start)}부터`
  return '원문에서 확인'
}

function Chips({ values }: { values: string[] }) {
  if (!values.length) return null
  return <div className="mt-2 flex flex-wrap gap-1.5">{values.map(value => <span key={value} className="rounded-full bg-white px-2.5 py-1 text-xs font-bold text-slate-700 ring-1 ring-slate-200">{value}</span>)}</div>
}

const comparable = (value?: string | null) => (value || '').replace(/[\s'‘’“”".,:：()·•\-]/g, '').toLowerCase()
const normalizedUrl = (value?: string | null) => value?.replace(/\/$/, '') || null

function usefulDescription(title: string, description: string) {
  const titleText = comparable(title)
  const descriptionText = comparable(description)
  return Boolean(
    descriptionText
    && descriptionText !== titleText
    && descriptionText !== `${titleText}단계를진행합니다`
    && descriptionText !== `${titleText}진행합니다`,
  )
}

export function ActionGuideCard({ guide, status, answerText = '' }: { guide: ActionGuide; status?: ChatStatus; answerText?: string }) {
  const expired = status === 'stale_only'
  const answerComparable = comparable(answerText)
  const showSummary = Boolean(guide.summary && !answerComparable.includes(comparable(guide.summary)))
  const methodParts = (guide.applicationMethod || '').split('→').map(comparable).filter(Boolean)
  const stepTitles = guide.steps.map(step => comparable(step.title))
  const methodDuplicatesSteps = methodParts.length > 1 && methodParts.every(part => stepTitles.some(title => title.includes(part) || part.includes(title)))
  const firstOrderByUrl = new Map<string, number>()
  guide.steps.forEach(step => {
    const url = normalizedUrl(step.actionUrl)
    if (url && !firstOrderByUrl.has(url)) firstOrderByUrl.set(url, step.order)
  })
  const stepHasApplicationUrl = Boolean(
    normalizedUrl(guide.applicationUrl) && firstOrderByUrl.has(normalizedUrl(guide.applicationUrl) as string),
  )
  const sourceRepeatedByStep = Boolean(
    normalizedUrl(guide.sourceUrl) && firstOrderByUrl.has(normalizedUrl(guide.sourceUrl) as string),
  )
  const showApplicationCta = Boolean(!expired && guide.applicationUrl && !stepHasApplicationUrl)
  const showSourceCta = expired || !sourceRepeatedByStep
  const showPeriod = Boolean(!expired && (guide.period.start || guide.period.end))
  const hasGuideDetails = Boolean(
    showPeriod
    || guide.targets.length
    || (guide.applicationMethod && !methodDuplicatesSteps)
    || guide.applicationLocation
    || guide.prerequisites.length
    || guide.eligibilityNotes?.length
    || guide.requiredDocuments.length
    || guide.benefits?.length
    || guide.creditsOrHours
    || guide.feeInformation
    || guide.capacity
    || guide.selectionMethod
    || guide.resultAnnouncement
    || guide.cancellationPolicy
    || guide.importantDates?.length
    || guide.additionalFacts?.some(item => item.studentActionable)
  )
  const primarySteps = guide.steps.slice(0, 3)
  const remainingSteps = guide.steps.slice(3)
  const renderStep = (step: ActionGuide['steps'][number]) => {
    const url = normalizedUrl(step.actionUrl)
    const showLink = Boolean(url && firstOrderByUrl.get(url as string) === step.order)
    return <li key={`${step.order}-${step.title}`} className="flex gap-3 rounded-xl bg-white p-3 shadow-sm ring-1 ring-slate-200">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brand-600 text-sm font-black text-white" aria-label={`${step.order}단계`}>{step.order}</span>
      <div className="min-w-0 flex-1">
        <h3 className="font-extrabold text-slate-900">{step.title}</h3>
        {usefulDescription(step.title, step.description) && <p className="mt-1 text-sm leading-6 text-slate-600">{step.description}</p>}
        {showLink && <a href={step.actionUrl || undefined} target="_blank" rel="noreferrer" className="mt-2 inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-brand-50 px-3 text-sm font-extrabold text-brand-700 transition hover:bg-brand-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600">{step.linkLabel || '이 단계 바로가기'}<ArrowUpRight size={15}/></a>}
      </div>
    </li>
  }

  return <section className="mt-4 overflow-hidden rounded-2xl border border-brand-200 bg-brand-50/60" aria-label={`${guide.taskName} 절차`}>
    <header className="border-b border-brand-100 bg-white p-4">
      <p className="flex items-center gap-2 text-xs font-extrabold text-brand-700"><ClipboardList size={16}/>신청 가이드</p>
      <h2 className="mt-2 text-lg font-black text-slate-900">{guide.taskName}</h2>
      {showSummary && <p className="mt-1 text-sm leading-6 text-slate-600">{guide.summary}</p>}
    </header>

    {hasGuideDetails && <div className="grid gap-3 border-b border-brand-100 p-4 text-sm sm:grid-cols-2">
      {showPeriod && <div><p className="flex items-center gap-1.5 font-extrabold text-slate-700"><CalendarDays size={16}/>신청 기간</p><p className="mt-1 text-slate-600">{periodLabel(guide.period.start, guide.period.end)}</p></div>}
      {guide.targets.length > 0 && <div><p className="flex items-center gap-1.5 font-extrabold text-slate-700"><Users size={16}/>신청 대상</p><Chips values={guide.targets}/></div>}
      {guide.applicationMethod && !methodDuplicatesSteps && <div className="sm:col-span-2"><p className="font-extrabold text-slate-700">신청 방법</p><p className="mt-1 text-slate-600">{guide.applicationMethod}</p></div>}
      {guide.applicationLocation && <div className="sm:col-span-2"><p className="flex items-center gap-1.5 font-extrabold text-slate-700"><MapPin size={16}/>신청 장소·경로</p><p className="mt-1 text-slate-600">{guide.applicationLocation}</p></div>}
      {guide.prerequisites.length > 0 && <div className="sm:col-span-2"><p className="flex items-center gap-1.5 font-extrabold text-slate-700"><CheckCircle2 size={16}/>미리 확인할 것</p><Chips values={guide.prerequisites}/></div>}
      {(guide.eligibilityNotes?.length ?? 0) > 0 && <div className="sm:col-span-2"><p className="flex items-center gap-1.5 font-extrabold text-slate-700"><Info size={16}/>자격·제외 조건</p><Chips values={guide.eligibilityNotes ?? []}/></div>}
      {guide.requiredDocuments.length > 0 && <div className="sm:col-span-2"><p className="flex items-center gap-1.5 font-extrabold text-slate-700"><FileCheck2 size={16}/>준비 서류</p><Chips values={guide.requiredDocuments}/></div>}
      {(guide.benefits?.length ?? 0) > 0 && <div className="sm:col-span-2"><p className="font-extrabold text-slate-700">혜택·지원</p><Chips values={guide.benefits ?? []}/></div>}
      {guide.creditsOrHours && <div><p className="font-extrabold text-slate-700">인정 학점·시간</p><p className="mt-1 text-slate-600">{guide.creditsOrHours}</p></div>}
      {([['비용·환불', guide.feeInformation], ['모집 인원', guide.capacity], ['선발 방식', guide.selectionMethod], ['결과 발표', guide.resultAnnouncement], ['취소·변경', guide.cancellationPolicy]] as const).map(([label, value]) => value && <div key={label}><p className="font-extrabold text-slate-700">{label}</p><p className="mt-1 text-slate-600">{value}</p></div>)}
      {(guide.importantDates?.length ?? 0) > 0 && <div className="sm:col-span-2"><p className="font-extrabold text-slate-700">기타 중요 일정</p><ul className="mt-1 list-disc space-y-1 pl-5 text-slate-600">{guide.importantDates?.map(item=><li key={`${item.label}-${item.start}-${item.end}`}><strong>{item.label}</strong>: {(item.start || item.end) && `${periodLabel(item.start, item.end)} · `}{item.description || '세부 내용은 공식 근거에서 확인'}</li>)}</ul></div>}
      {(guide.additionalFacts?.filter(item => item.studentActionable).length ?? 0) > 0 && <div className="sm:col-span-2"><p className="font-extrabold text-slate-700">추가로 확인할 사항</p><ul className="mt-1 list-disc space-y-1 pl-5 text-slate-600">{guide.additionalFacts?.filter(item => item.studentActionable).map(item=><li key={`${item.factType}-${item.label}`}>{item.label}: {item.value}</li>)}</ul></div>}
    </div>}

    <ol className="space-y-3 p-4" aria-label="순서별 신청 방법">
      {primarySteps.map(renderStep)}
    </ol>
    {remainingSteps.length > 0 && <details className="mx-4 mb-4 rounded-xl border border-brand-100 bg-white/70">
      <summary className="cursor-pointer px-3 py-3 text-sm font-extrabold text-brand-700">나머지 {remainingSteps.length}단계 보기</summary>
      <ol className="space-y-3 border-t border-brand-100 p-3">{remainingSteps.map(renderStep)}</ol>
    </details>}

    {guide.warnings.length > 0 && <div className="mx-4 mb-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950"><p className="flex items-center gap-1.5 font-extrabold"><AlertTriangle size={16}/>주의사항</p><ul className="mt-2 list-disc space-y-1 pl-5">{guide.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></div>}
    {guide.needsReview && <p className="mx-4 mb-4 text-xs font-semibold text-amber-800">일부 항목은 자동 추출 신뢰도가 낮아 원문 확인이 필요합니다.</p>}

    {(showApplicationCta || showSourceCta) && <footer className="flex flex-wrap gap-2 border-t border-brand-100 bg-white p-4">
      {showApplicationCta && <a href={guide.applicationUrl || undefined} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-brand-600 px-4 text-sm font-extrabold text-white transition hover:bg-brand-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600">신청 페이지 열기<ArrowUpRight size={16}/></a>}
      {showSourceCta && <a href={guide.sourceUrl} target={guide.sourceUrl.startsWith('/') ? undefined : '_blank'} rel="noreferrer" className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-slate-300 bg-white px-4 text-sm font-extrabold text-slate-700 transition hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600">{expired ? '추가 모집 여부 확인' : '공식 원문 보기'}<ExternalLink size={16}/></a>}
    </footer>}
  </section>
}
