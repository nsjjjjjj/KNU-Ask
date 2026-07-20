import { ChevronRight } from 'lucide-react'
import type { FAQ } from '../types'

export function FAQPanel({ faqs, onSelect }: { faqs: FAQ[]; onSelect: (faq: FAQ) => void }) {
  return <section aria-labelledby="faq-title" className="animate-enter border-b border-slate-200 bg-brand-50/70 px-4 py-4 md:px-6"><div className="mx-auto max-w-5xl"><h2 id="faq-title" className="mb-3 text-sm font-bold text-slate-800">자주 묻는 질문</h2><div className="grid gap-2 sm:grid-cols-2">{faqs.map(faq => <button key={faq.id} onClick={() => onSelect(faq)} className="flex min-h-12 items-center justify-between rounded-xl border border-brand-100 bg-white px-4 text-left text-sm font-medium text-slate-700 transition hover:border-brand-300 hover:text-brand-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"><span>{faq.question}</span><ChevronRight size={17}/></button>)}</div></div></section>
}
