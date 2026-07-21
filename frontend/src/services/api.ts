import type { ChatResponse, FAQ, FeedbackReason, Notice, NoticeDetail } from '../types'

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || '요청을 처리하지 못했습니다.')
  }
  return response.json()
}

export const api = {
  chat: (message: string, sessionId?: string, selectedCategory?: string) => request<ChatResponse>('/chat', { method: 'POST', body: JSON.stringify({ message, sessionId, selectedCategory }) }),
  categories: () => request<{ categories: string[] }>('/categories'),
  categoryNotices: (category: string) => request<{ category: string; notices: Notice[]; message: string }>(`/categories/${encodeURIComponent(category)}/notices`),
  noticeDetail: (noticeId: number) => request<NoticeDetail>(`/notices/${noticeId}`),
  faqs: () => request<FAQ[]>('/faqs'),
  endSession: (sessionId: string) => request(`/chat/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }),
  feedback: (payload: { answerId: string; resolved: boolean; reason: FeedbackReason; sourceIds: number[]; responseStatus: string }) =>
    request<{ status: 'accepted' }>('/feedback', { method: 'POST', body: JSON.stringify(payload) }),
  crawlerPreview: (mode: 'incremental' | 'daily' | 'pilot' | 'full', token: string) =>
    request<CrawlerPreview>(`/crawler/preview?mode=${mode}`, { headers: { 'X-Admin-Token': token } }),
  crawlerStatus: (token: string) =>
    request<CrawlerStatus>('/crawler/status', { headers: { 'X-Admin-Token': token } }),
}

export interface CrawlerPreview {
  mode: string
  maximumDocuments: number
  maximumAIJobs: number
  sources: string[]
  currentActiveDocuments: number
  estimatedNewOrChangedUpperBound: number
  estimatedAIJobsUpperBound: number
  estimatedInputTokensUpperBound: number
  unchangedDocumentsSkipAI: boolean
  publicIndexKeptUntilSuccess: boolean
}

export interface CrawlerStatus {
  id?: number | null
  status: string
  phase?: string | null
  totalFound?: number
  newCount?: number
  updatedCount?: number
  skippedCount?: number
  failedCount?: number
  processedCount?: number
  errorMessage?: string | null
}
