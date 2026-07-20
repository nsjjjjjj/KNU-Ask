import { LogOut } from 'lucide-react'
import { BrandMark } from './BrandMark'

export function ChatHeader({ onExit }: { onExit: () => void }) {
  return <header className="border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur md:px-6">
    <div className="mx-auto flex max-w-5xl items-center justify-between gap-3">
      <div className="flex min-w-0 items-center gap-3"><BrandMark/><div className="min-w-0"><h1 className="truncate text-lg font-extrabold tracking-tight text-slate-900">강냉이 에스크</h1><p className="truncate text-xs text-slate-500">강남대학교 AI 문의 길잡이</p></div></div>
      <button onClick={onExit} className="flex min-h-11 items-center gap-2 rounded-xl border border-slate-200 px-3 text-sm font-semibold text-slate-600 transition hover:border-red-200 hover:bg-red-50 hover:text-red-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600" aria-label="채팅 종료"><LogOut size={18}/><span className="hidden sm:inline">채팅 종료</span></button>
    </div>
  </header>
}
