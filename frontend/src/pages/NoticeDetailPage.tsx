import { useEffect, useState } from 'react'
import { ArrowLeft, CalendarDays, FileText } from 'lucide-react'
import { api } from '../services/api'
import type { NoticeDetail } from '../types'
import { ActionGuideCard } from '../components/ActionGuideCard'
import { isPlaceholderUrl } from '../utils/noticeLinks'
import { noticeStatusPresentation } from '../utils/noticeStatus'

export function NoticeDetailPage({ noticeId }: { noticeId: number }) {
  const [notice, setNotice] = useState<NoticeDetail>()
  const [error, setError] = useState<string>()

  const actionGuide = notice?.actionGuide ? {
    ...notice.actionGuide,
    sourceUrl: isPlaceholderUrl(notice.actionGuide.sourceUrl) ? `/notices/${noticeId}` : notice.actionGuide.sourceUrl,
    applicationUrl: isPlaceholderUrl(notice.actionGuide.applicationUrl) ? null : notice.actionGuide.applicationUrl,
    steps: notice.actionGuide.steps.map(step => isPlaceholderUrl(step.actionUrl) ? {...step, actionUrl: null, linkLabel: null} : step),
  } : null
  const status = notice ? noticeStatusPresentation(notice) : null

  useEffect(() => {
    api.noticeDetail(noticeId).then(setNotice).catch(reason => {
      setError(reason instanceof Error ? reason.message : '공지를 불러오지 못했습니다.')
    })
  }, [noticeId])

  return <main className="min-h-dvh bg-slate-50 px-4 py-8 text-slate-900 md:py-12">
    <div className="mx-auto max-w-3xl">
      <a href="/" className="inline-flex min-h-11 items-center gap-2 rounded-lg px-3 text-sm font-bold text-brand-700 transition hover:bg-brand-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600">
        <ArrowLeft size={17}/>채팅으로 돌아가기
      </a>

      {error && <section className="mt-6 rounded-2xl border border-red-200 bg-white p-6" role="alert">
        <h1 className="text-xl font-extrabold">공지를 찾을 수 없습니다</h1>
        <p className="mt-2 text-sm text-slate-600">{error}</p>
      </section>}

      {!notice && !error && <section className="mt-6 rounded-2xl border border-slate-200 bg-white p-6" aria-live="polite">공지 내용을 불러오는 중입니다.</section>}

      {notice && <article className="mt-6 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <header className="border-b border-slate-200 p-6 md:p-8">
          <div className="mb-4 flex flex-wrap gap-2">
            <span className="rounded-full bg-brand-50 px-3 py-1 text-xs font-bold text-brand-700">{notice.metadata?.category || '기타'}</span>
            {status && <span className={`rounded-full px-3 py-1 text-xs font-bold ${status.className}`}>{status.label}</span>}
          </div>
          <h1 className="text-2xl font-black leading-tight md:text-3xl">{notice.title}</h1>
          <p className="mt-4 flex items-center gap-2 text-sm text-slate-500"><CalendarDays size={17}/>게시 {new Date(notice.publishedAt).toLocaleDateString('ko-KR')}</p>
        </header>

        <section className="p-6 md:p-8" aria-label="공지 내용">
          {actionGuide && <div className="mb-8"><ActionGuideCard guide={actionGuide}/></div>}
          <h2 className="flex items-center gap-2 text-lg font-extrabold"><FileText size={20}/>공지 내용</h2>
          <p className="mt-4 whitespace-pre-wrap text-[15px] leading-8 text-slate-700">{notice.content}</p>

          {notice.metadata && <dl className="mt-8 grid gap-4 rounded-xl bg-slate-50 p-5 text-sm sm:grid-cols-2">
            <div><dt className="font-bold text-slate-500">문의 분야</dt><dd className="mt-1 font-semibold">{notice.metadata.category}</dd></div>
            <div><dt className="font-bold text-slate-500">세부 분야</dt><dd className="mt-1 font-semibold">{notice.metadata.subCategory || '확인 필요'}</dd></div>
            <div><dt className="font-bold text-slate-500">담당 부서</dt><dd className="mt-1 font-semibold">{notice.metadata.department.name || '확인 필요'}</dd></div>
            <div><dt className="font-bold text-slate-500">담당자</dt><dd className="mt-1 font-semibold">{notice.metadata.department.contactPerson || '확인 필요'}</dd></div>
            <div><dt className="font-bold text-slate-500">전화번호</dt><dd className="mt-1 font-semibold">{notice.metadata.department.phone || '확인 필요'}</dd></div>
            <div><dt className="font-bold text-slate-500">운영시간</dt><dd className="mt-1 font-semibold">{notice.metadata.department.officeHours || '확인 필요'}</dd></div>
            {notice.metadata.applicationMethod && <div className="sm:col-span-2"><dt className="font-bold text-slate-500">신청 방법</dt><dd className="mt-1 font-semibold">{notice.metadata.applicationMethod}</dd></div>}
            {notice.metadata.applicationLocation && <div className="sm:col-span-2"><dt className="font-bold text-slate-500">신청 장소·경로</dt><dd className="mt-1 font-semibold">{notice.metadata.applicationLocation}</dd></div>}
            {(notice.metadata.eligibilityNotes?.length ?? 0) > 0 && <div className="sm:col-span-2"><dt className="font-bold text-slate-500">자격·제외 조건</dt><dd className="mt-1 font-semibold">{notice.metadata.eligibilityNotes?.join(', ')}</dd></div>}
            <div className="sm:col-span-2"><dt className="font-bold text-slate-500">필요 서류</dt><dd className="mt-1 font-semibold">{notice.metadata.requiredDocuments.length ? notice.metadata.requiredDocuments.join(', ') : '없음'}</dd></div>
            {notice.metadata.feeInformation && <div><dt className="font-bold text-slate-500">비용·환불</dt><dd className="mt-1 font-semibold">{notice.metadata.feeInformation}</dd></div>}
            {notice.metadata.capacity && <div><dt className="font-bold text-slate-500">모집 인원</dt><dd className="mt-1 font-semibold">{notice.metadata.capacity}</dd></div>}
            {notice.metadata.selectionMethod && <div><dt className="font-bold text-slate-500">선발 방식</dt><dd className="mt-1 font-semibold">{notice.metadata.selectionMethod}</dd></div>}
            {notice.metadata.resultAnnouncement && <div><dt className="font-bold text-slate-500">결과 발표</dt><dd className="mt-1 font-semibold">{notice.metadata.resultAnnouncement}</dd></div>}
            {notice.metadata.cancellationPolicy && <div className="sm:col-span-2"><dt className="font-bold text-slate-500">취소·변경 조건</dt><dd className="mt-1 font-semibold">{notice.metadata.cancellationPolicy}</dd></div>}
            {(notice.metadata.additionalFacts?.length ?? 0) > 0 && <div className="sm:col-span-2"><dt className="font-bold text-slate-500">기타 중요 정보</dt><dd className="mt-1"><ul className="list-disc space-y-1 pl-5 font-semibold">{notice.metadata.additionalFacts?.map(item => <li key={`${item.factType}-${item.label}`}>{item.label}: {item.value}</li>)}</ul></dd></div>}
          </dl>}
        </section>
      </article>}
    </div>
  </main>
}
