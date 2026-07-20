import { useEffect, useRef } from 'react'
import type { ChatMessageModel } from '../types'
import { ChatMessage } from './ChatMessage'
import { LoadingBubble } from './LoadingBubble'

export function ChatMessageList({ messages, loading }: { messages: ChatMessageModel[]; loading: boolean }) {
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => { const reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;endRef.current?.scrollIntoView?.({ behavior:reduced?'auto':'smooth' }) }, [messages, loading])
  return <main id="chat-main" aria-live="polite" aria-busy={loading} className="flex-1 overflow-y-auto px-4 py-6 md:px-6"><div className="mx-auto flex max-w-5xl flex-col gap-5">{messages.map(message => <ChatMessage key={message.id} message={message}/>)}{loading&&<LoadingBubble/>}<div ref={endRef}/></div></main>
}
