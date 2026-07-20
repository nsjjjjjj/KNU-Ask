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
}
