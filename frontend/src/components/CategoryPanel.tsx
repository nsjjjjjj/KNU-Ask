import {
  Award, BookCheck, CalendarDays, ClipboardCheck, FileText,
  GraduationCap, RotateCcw, Shield, WalletCards, Waves,
} from 'lucide-react'

const now = new Date()
const semester = now.getMonth() + 1 <= 6 ? 1 : 2

export const inquiryShortcuts = [
  { label:'수강변경', description:`${semester}학기 변경 기간`, question:`${semester}학기 수강신청 변경 기간 알려줘`, icon:CalendarDays },
  { label:'수강신청', description:`${semester}학기 신청 일정`, question:`${semester}학기 수강신청 일정 알려줘`, icon:BookCheck },
  { label:'성적 확인', description:'조회·이의신청 기간', question:'이번 학기 성적 확인과 이의신청 기간 알려줘', icon:ClipboardCheck },
  { label:'일반휴학', description:'신청 방법과 절차', question:'일반휴학 신청 방법 알려줘', icon:Waves },
  { label:'복학', description:'신청 방법과 절차', question:'복학 신청 방법 알려줘', icon:RotateCcw },
  { label:'등록금', description:'고지서·납부 방법', question:'등록금 고지서 출력과 납부 방법 알려줘', icon:WalletCards },
  { label:'졸업요건', description:'2021~2024 입학자', question:'2021~2024학년도 입학생 졸업요건 알려줘', icon:GraduationCap },
  { label:'국가장학금', description:`${semester}학기 신청 안내`, question:`${semester}학기 국가장학금 신청 기간과 방법 알려줘`, icon:Award },
  { label:'증명서 발급', description:'재학증명서 발급', question:'재학증명서 발급 방법 알려줘', icon:FileText },
  { label:'예비군 신고', description:'훈련 연기 절차', question:'예비군 훈련 연기 신고 방법 알려줘', icon:Shield },
]

export function CategoryPanel({ onSelect }: { onSelect: (question: string) => void }) {
  return <section aria-labelledby="category-title" className="animate-enter border-b border-slate-200 bg-brand-50/70 px-4 py-4 md:px-6"><div className="mx-auto max-w-5xl">
    <div className="mb-3"><h2 id="category-title" className="text-sm font-bold text-slate-800">반복 문의 바로가기</h2><p className="mt-1 text-xs text-slate-600">버튼을 누르면 검증된 공식 근거를 바로 검색합니다.</p></div>
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">{inquiryShortcuts.map(({label,description,question,icon:Icon}) => <button key={label} onClick={() => onSelect(question)} className="flex min-h-24 flex-col items-center justify-center gap-1 rounded-xl border border-brand-100 bg-white p-3 text-center shadow-sm transition hover:-translate-y-0.5 hover:border-brand-300 hover:text-brand-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"><Icon size={21} className="mb-1 text-brand-600"/><span className="text-sm font-bold text-slate-800">{label}</span><span className="text-xs text-slate-500">{description}</span></button>)}</div>
  </div></section>
}
