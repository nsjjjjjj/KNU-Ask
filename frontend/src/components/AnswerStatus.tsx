import { AlertTriangle, CheckCircle2, Info } from 'lucide-react'
import type { ChatStatus, SearchScope } from '../types'

const labels: Record<ChatStatus, string> = {
  success: '공식 공지에서 근거를 확인했습니다.',
  no_result: '확인한 범위에서 관련 근거를 찾지 못했습니다.',
  constraint_mismatch: '조건에 맞는 공지를 찾지 못했습니다.',
  insufficient_evidence: '관련 자료는 있지만 답할 근거가 부족합니다.',
  conflicting_evidence: '공식 자료의 내용이 서로 다릅니다.',
  stale_only: '가장 관련 높은 공지는 신청이 마감되었습니다.',
  out_of_scope: '공개 공지만으로 확인할 수 없는 질문입니다.',
  clarification_required: '정확한 검색을 위해 조건이 더 필요합니다.',
  service_error: '일시적인 시스템 오류가 발생했습니다.',
}

export function AnswerStatus({ status, scope }: { status: ChatStatus; scope?: SearchScope }) {
  const success = status === 'success'
  const caution = ['stale_only','conflicting_evidence','insufficient_evidence'].includes(status)
  const Icon = success ? CheckCircle2 : caution ? AlertTriangle : Info
  const style = success ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : caution ? 'border-amber-200 bg-amber-50 text-amber-950' : 'border-blue-200 bg-blue-50 text-blue-950'
  return <div className={`mb-3 rounded-xl border p-3 text-sm ${style}`} role={status === 'service_error' ? 'alert' : 'status'}>
    <div className="flex items-start gap-2"><Icon size={17} className="mt-0.5 shrink-0"/><span className="font-semibold">{labels[status]}</span></div>
    {status !== 'success' && scope && <p className="mt-1 pl-6 text-xs opacity-80">검색 범위: {scope.description} · 공지 {scope.noticeCount}건</p>}
  </div>
}
