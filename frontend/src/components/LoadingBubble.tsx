import { useEffect, useState } from 'react'

const stages=[
  '질문 분석 중',
  '저장된 근거 확인 중',
  '학교 공식 자료 검색 중',
  '근거 검증 중',
  '첨부파일 추가 확인 중',
]
export function LoadingBubble() { const [stage,setStage]=useState(0);useEffect(()=>{const delays=[700,1800,4000,10000];const timers=delays.map((delay,index)=>window.setTimeout(()=>setStage(index+1),delay));return()=>timers.forEach(window.clearTimeout)},[]);return <div className="flex gap-2.5" role="status" aria-live="polite"><span className="grid h-9 w-9 place-items-center rounded-xl bg-brand-600 text-white font-bold">K</span><div className="flex min-h-12 items-center gap-3 rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600 shadow-sm"><span className="flex gap-1" aria-hidden="true"><i className="typing-dot"/><i className="typing-dot"/><i className="typing-dot"/></span>{stages[stage]}</div></div> }
