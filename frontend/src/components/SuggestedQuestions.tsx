import { ArrowUpRight } from 'lucide-react'

const questions = [
  '2026학년도 2학기 수강신청 일정 알려줘',
  '등록금 납부 기간이 언제야?',
  '휴학 신청 방법 알려줘',
  '예비군 신고는 어디에 제출해?',
]

export function SuggestedQuestions({ onSelect }: { onSelect: (question: string) => void }) {
  return <section aria-labelledby="suggested-title" className="border-b border-slate-200 bg-white px-4 py-4 md:px-6">
    <div className="mx-auto max-w-5xl">
      <div className="mb-3 flex items-end justify-between gap-3">
        <div><p className="text-xs font-bold uppercase tracking-[0.14em] text-brand-600">지금 많이 찾는 안내</p><h2 id="suggested-title" className="mt-1 text-base font-extrabold text-slate-900">이런 질문으로 시작해 보세요</h2></div>
        <span className="hidden text-xs text-slate-500 sm:inline">공개 공지 기준</span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {questions.map(question => <button key={question} onClick={() => onSelect(question)} className="group flex min-h-12 cursor-pointer items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-left text-sm font-semibold text-slate-700 transition duration-200 hover:border-brand-300 hover:bg-brand-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600">
          <span>{question}</span><ArrowUpRight size={17} className="shrink-0 text-slate-400 transition group-hover:text-brand-600"/>
        </button>)}
      </div>
    </div>
  </section>
}
