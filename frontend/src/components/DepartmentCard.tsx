import { Building2, Clock3, Phone, UserRound } from 'lucide-react'
import type { Department } from '../types'

export function DepartmentCard({ department }: { department: Department }) {
  const hasDetails = Boolean(
    department.name || department.contactPerson || department.phone || department.email
    || department.officeLocation || department.officeHours,
  )
  if (!hasDetails) return null
  return <section aria-label="담당 부서 정보" className="mt-4 rounded-xl border border-blue-100 bg-blue-50/70 p-4"><div className="mb-3 flex items-center gap-2 text-sm font-bold text-slate-800"><Building2 size={17} className="text-brand-600"/>담당 부서 안내</div><dl className="grid gap-2 text-sm">{department.name&&<div className="flex gap-2"><dt className="w-20 shrink-0 text-slate-500">담당 부서</dt><dd className="font-semibold text-slate-800">{department.name}</dd></div>}{department.contactPerson&&<div className="flex gap-2"><dt className="flex w-20 shrink-0 items-center gap-1 text-slate-500"><UserRound size={13}/>담당자</dt><dd>{department.contactPerson}{department.contactRole ? ` (${department.contactRole})` : ''}</dd></div>}{department.contactDuty&&<div className="flex gap-2"><dt className="w-20 shrink-0 text-slate-500">담당 업무</dt><dd>{department.contactDuty}</dd></div>}{department.phone&&<div className="flex gap-2"><dt className="flex w-20 shrink-0 items-center gap-1 text-slate-500"><Phone size={13}/>{department.name ? '전화번호' : '문의 전화'}</dt><dd className="font-semibold text-slate-900">{department.phone}</dd></div>}{department.email&&<div className="flex gap-2"><dt className="w-20 shrink-0 text-slate-500">이메일</dt><dd className="break-all">{department.email}</dd></div>}{department.officeLocation&&<div className="flex gap-2"><dt className="w-20 shrink-0 text-slate-500">사무실</dt><dd>{department.officeLocation}</dd></div>}{department.officeHours&&<div className="flex gap-2"><dt className="flex w-20 shrink-0 items-center gap-1 text-slate-500"><Clock3 size={13}/>운영시간</dt><dd>{department.officeHours}</dd></div>}</dl></section>
}
