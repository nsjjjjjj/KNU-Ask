import { useEffect, useRef } from 'react'
import type { ChatMessageModel } from '../types'
import { ChatMessage } from './ChatMessage'
import { LoadingBubble } from './LoadingBubble'

export function ChatMessageList({ messages, loading, onClarificationSelect }: { messages: ChatMessageModel[]; loading: boolean; onClarificationSelect: (value: string) => void }) {
  const mainRef = useRef<HTMLElement>(null)
  const latestAssistantRef = useRef<HTMLElement>(null)
  const loadingRef = useRef<HTMLDivElement>(null)
  const latestAssistantId = [...messages].reverse().find(message => message.role === 'assistant')?.id
  useEffect(() => {
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    const behavior = reduced ? 'auto' : 'smooth'
    if (loading) {
      loadingRef.current?.scrollIntoView?.({ behavior, block:'nearest' })
      return
    }
    const answer = latestAssistantRef.current
    const main = mainRef.current
    if (!answer || !main) return
    const longAnswer = answer.offsetHeight > main.clientHeight * 0.7
    answer.scrollIntoView?.({ behavior, block:longAnswer?'start':'nearest' })
  }, [messages, loading])
  return <main ref={mainRef} id="chat-main" aria-live="polite" aria-busy={loading} className="flex-1 overflow-y-auto px-4 py-5 md:px-6 md:py-6"><div className="mx-auto flex max-w-5xl flex-col gap-5">{messages.map(message => <ChatMessage ref={message.id===latestAssistantId?latestAssistantRef:undefined} key={message.id} message={message} onClarificationSelect={onClarificationSelect} disabled={loading}/>)}{loading&&<div ref={loadingRef}><LoadingBubble/></div>}</div></main>
}
