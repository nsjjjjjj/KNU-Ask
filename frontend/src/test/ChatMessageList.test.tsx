import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatMessageList } from '../components/ChatMessageList'

const originalScroll = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView')
const originalOffsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight')
const originalClientHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight')

afterEach(() => {
  if (originalScroll) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', originalScroll)
  else delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView
  if (originalOffsetHeight) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', originalOffsetHeight)
  if (originalClientHeight) Object.defineProperty(HTMLElement.prototype, 'clientHeight', originalClientHeight)
})

describe('긴 답변 자동 스크롤', () => {
  it('답변 맨 아래가 아니라 새 AI 답변의 시작 위치를 보여준다', () => {
    const scrollIntoView = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable:true, value:scrollIntoView })
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
      configurable:true,
      get() { return this.getAttribute('aria-label') === 'AI 답변' && this.textContent?.includes('긴 답변') ? 900 : 80 },
    })
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
      configurable:true,
      get() { return this.tagName === 'MAIN' ? 500 : 0 },
    })

    render(<ChatMessageList
      messages={[
        {id:'welcome',role:'assistant',content:'안녕하세요.'},
        {id:'question',role:'user',content:'신청 절차 알려줘'},
        {id:'answer',role:'assistant',content:'긴 답변 '.repeat(100)},
      ]}
      loading={false}
      onClarificationSelect={()=>{}}
    />)

    expect(scrollIntoView).toHaveBeenLastCalledWith({behavior:'smooth',block:'start'})
  })
})
