import { ChatPage } from './pages/ChatPage'
import { NoticeDetailPage } from './pages/NoticeDetailPage'
import { AdminPage } from './pages/AdminPage'

export default function App(){
  if(window.location.pathname === '/admin' || window.location.pathname === '/admin/') return <AdminPage/>
  const match=window.location.pathname.match(/^\/notices\/(\d+)\/?$/)
  return match ? <NoticeDetailPage noticeId={Number(match[1])}/> : <ChatPage/>
}
