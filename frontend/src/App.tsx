import { ChatPage } from './pages/ChatPage'
import { NoticeDetailPage } from './pages/NoticeDetailPage'

export default function App(){
  const match=window.location.pathname.match(/^\/notices\/(\d+)\/?$/)
  return match ? <NoticeDetailPage noticeId={Number(match[1])}/> : <ChatPage/>
}
