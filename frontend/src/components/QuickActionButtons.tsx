import { CircleHelp, LayoutGrid } from 'lucide-react'
import type { Panel } from '../types'

export function QuickActionButtons({ panel, onSelect }: { panel: Panel; onSelect: (panel: Panel) => void }) {
  const items = [{ key:'category' as const, label:'문의 분야 선택', icon:LayoutGrid }, { key:'faq' as const, label:'자주 묻는 질문', icon:CircleHelp }]
  return <div className="grid grid-cols-2 gap-2 border-b border-slate-200 bg-white px-4 py-3 md:px-6"><div className="col-span-2 mx-auto grid w-full max-w-5xl grid-cols-2 gap-2">
    {items.map(({key,label,icon:Icon}) => <button key={key} onClick={() => onSelect(panel === key ? null : key)} aria-expanded={panel === key} className={`flex min-h-12 items-center justify-center gap-2 rounded-xl border px-3 text-sm font-bold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 ${panel===key?'border-brand-600 bg-brand-50 text-brand-700':'border-slate-200 bg-white text-slate-700 hover:border-brand-200 hover:bg-brand-50/60'}`}><Icon size={18}/>{label}</button>)}
  </div></div>
}
