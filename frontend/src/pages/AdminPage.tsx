import { useState } from 'react'
import { api, type CrawlerPreview, type CrawlerStatus } from '../services/api'

type CrawlMode = 'incremental' | 'daily' | 'pilot' | 'full'

const number = new Intl.NumberFormat('ko-KR')

export function AdminPage(){
  const [token, setToken] = useState('')
  const [mode, setMode] = useState<CrawlMode>('pilot')
  const [preview, setPreview] = useState<CrawlerPreview | null>(null)
  const [status, setStatus] = useState<CrawlerStatus | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function load(){
    setLoading(true)
    setError('')
    try {
      const [nextPreview, nextStatus] = await Promise.all([
        api.crawlerPreview(mode, token), api.crawlerStatus(token),
      ])
      setPreview(nextPreview)
      setStatus(nextStatus)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '관리자 상태를 불러오지 못했습니다.')
    } finally {
      setLoading(false)
    }
  }

  return <main className="admin-page">
    <section className="admin-card">
      <div className="admin-heading">
        <div>
          <p className="admin-eyebrow">로컬 운영 도구</p>
          <h1>수집 전 예상량</h1>
          <p>실행 전 문서·AI 작업·입력 토큰의 보수적인 상한을 확인합니다.</p>
        </div>
        <a href="/">챗봇으로 돌아가기</a>
      </div>
      <div className="admin-controls">
        <label>수집 모드
          <select value={mode} onChange={event => setMode(event.target.value as CrawlMode)}>
            <option value="pilot">제한 시험 수집</option>
            <option value="incremental">시간별 증분 수집</option>
            <option value="daily">일일 학사일정</option>
            <option value="full">전체 확대 수집</option>
          </select>
        </label>
        <label>관리자 토큰
          <input type="password" autoComplete="off" value={token} onChange={event => setToken(event.target.value)} placeholder="브라우저에 저장하지 않습니다" />
        </label>
        <button type="button" onClick={load} disabled={!token || loading}>{loading ? '확인 중…' : '예상량 확인'}</button>
      </div>
      <p className="admin-security-note">토큰은 현재 화면의 메모리에만 두며 저장하지 않습니다. Cloudflare 외부 주소에서는 관리자 API가 차단됩니다.</p>
      {error && <p className="admin-error" role="alert">{error}</p>}
      {preview && <>
        <div className="admin-metrics">
          <article><span>현재 활성 원문</span><strong>{number.format(preview.currentActiveDocuments)}건</strong></article>
          <article><span>최대 확인 문서</span><strong>{number.format(preview.maximumDocuments)}건</strong></article>
          <article><span>최대 AI 작업</span><strong>{number.format(preview.estimatedAIJobsUpperBound)}건</strong></article>
          <article><span>예상 입력 상한</span><strong>{number.format(preview.estimatedInputTokensUpperBound)} tokens</strong></article>
        </div>
        <div className="admin-source-list"><strong>대상 출처</strong><p>{preview.sources.join(' · ')}</p></div>
        <ul className="admin-safety-list">
          <li>변경되지 않은 문서 AI 재처리: {preview.unchangedDocumentsSkipAI ? '건너뜀' : '확인 필요'}</li>
          <li>새 처리 실패 시 기존 공개 인덱스: {preview.publicIndexKeptUntilSuccess ? '유지' : '확인 필요'}</li>
        </ul>
      </>}
      {status && <div className="admin-status">
        <strong>최근 작업: {status.status}{status.phase ? ` · ${status.phase}` : ''}</strong>
        <p>발견 {number.format(status.totalFound || 0)} · 신규 {number.format(status.newCount || 0)} · 변경 {number.format(status.updatedCount || 0)} · 실패 {number.format(status.failedCount || 0)}</p>
        {status.errorMessage && <p className="admin-error">{status.errorMessage}</p>}
      </div>}
    </section>
  </main>
}
