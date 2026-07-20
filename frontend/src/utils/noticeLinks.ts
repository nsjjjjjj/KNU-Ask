import type { Notice } from '../types'

export function isPlaceholderUrl(url?: string | null): boolean {
  if (!url) return false
  try {
    return new URL(url, window.location.origin).hostname.endsWith('.invalid')
  } catch {
    return false
  }
}

export function normalizeNotice(notice: Notice): Notice {
  if (!isPlaceholderUrl(notice.sourceUrl)) return notice
  return {
    ...notice,
    sourceUrl: `/notices/${notice.id}`,
    isSample: true,
  }
}

