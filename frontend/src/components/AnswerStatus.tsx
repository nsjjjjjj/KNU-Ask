import { AlertTriangle, Info } from 'lucide-react'
import type { ChatStatus } from '../types'

const labels: Record<ChatStatus, string> = {
  success: '공식 공지에서 근거를 확인했습니다.',
  no_result: '확인한 범위에서 관련 근거를 찾지 못했습니다.',
  constraint_mismatch: '조건에 맞는 공지를 찾지 못했습니다.',
  insufficient_evidence: '관련 자료는 있지만 답할 근거가 부족합니다.',
  conflicting_evidence: '공식 자료의 내용이 서로 다릅니다.',
  stale_only: '가장 관련 높은 공지는 신청이 마감되었습니다.',
  out_of_scope: '공개 공지만으로 확인할 수 없는 질문입니다.',
  clarification_required: '정확한 검색을 위해 조건이 더 필요합니다.',
  safety_refusal: '위험한 행동을 돕는 요청은 처리하지 않습니다.',
  service_error: '일시적인 시스템 오류가 발생했습니다.',
}

function deadlineLabel(value?: string | null) {
  if (!value) return null
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    const extracted = value.match(/20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}(?:\s+\d{1,2}:\d{2})?/)
    return extracted?.[0] ?? null
  }
  const parts = new Intl.DateTimeFormat('ko-KR', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(parsed)
  const valueOf = (type: Intl.DateTimeFormatPartTypes) => parts.find(part => part.type === type)?.value
  return `${valueOf('year')}.${valueOf('month')}.${valueOf('day')} ${valueOf('hour')}:${valueOf('minute')}`
}

export function AnswerStatus({ status, deadline }: { status: ChatStatus; deadline?: string | null }) {
  if (status === 'success') return null
  const caution = ['stale_only','conflicting_evidence','insufficient_evidence'].includes(status)
  const Icon = caution ? AlertTriangle : Info
  const style = caution ? 'border-amber-200 bg-amber-50 text-amber-950' : 'border-blue-200 bg-blue-50 text-blue-950'
  const formattedDeadline = status === 'stale_only' ? deadlineLabel(deadline) : null
  const label = formattedDeadline ? `신청 마감 · ${formattedDeadline}` : labels[status]
  return <div className={`mb-3 rounded-xl border p-3 text-sm ${style}`} role={status === 'service_error' ? 'alert' : 'status'}>
    <div className="flex items-start gap-2"><Icon size={17} className="mt-0.5 shrink-0"/><span className="font-semibold">{label}</span></div>
  </div>
}
