import { ExternalLink } from 'lucide-react'
import type { AnswerMedia } from '../types'

export function AnswerMediaCard({ media }: { media: AnswerMedia }) {
  return <figure className="mt-4 overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
    <a href={media.url} target="_blank" rel="noreferrer" className="block bg-white">
      <img
        src={media.url}
        alt={media.alt}
        loading="lazy"
        className="h-auto w-full"
      />
    </a>
    <figcaption className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs text-slate-600">
      <span><strong className="font-semibold text-slate-700">{media.caption || media.alt}</strong><span className="mt-0.5 block text-[11px] text-slate-500">이미지를 누르면 원본 크기로 볼 수 있습니다.</span></span>
      <a href={media.sourceUrl} target="_blank" rel="noreferrer" className="inline-flex shrink-0 items-center gap-1 font-bold text-brand-700 hover:underline">
        공식 원문 <ExternalLink size={13}/>
      </a>
    </figcaption>
  </figure>
}
