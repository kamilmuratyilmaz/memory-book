import { memo, useState } from 'react'
import type { AssetInfo, Design, MemoryBook } from '../model'
import { ScaledPage } from '../render/ScaledPage'

interface Props {
  /** shown as a bottom sheet on small screens */
  open: boolean
  /** an AI run is applying changes */
  locked: boolean
  book: MemoryBook
  design: Design
  assets: Record<string, AssetInfo>
  currentId: string
  busy: boolean
  onSelect: (id: string) => void
  onMove: (from: number, to: number) => void
  onAdd: (template: string | null) => void
  onDuplicate: () => void
  onDelete: () => void
}

export function PageList(p: Props) {
  const [dragFrom, setDragFrom] = useState<number | null>(null)
  const [dropAt, setDropAt] = useState<number | null>(null)
  const [adding, setAdding] = useState(false)
  const current = p.book.pages.findIndex(pg => pg.id === p.currentId)
  const chapters = new Map(p.book.chapters.map(c => [c.id, c.title]))
  const isCover = current === 0

  return (
    <nav className={`row-start-5 min-h-0 flex-col border-line bg-panel lg:col-start-1 lg:flex lg:border-r ${p.open ? 'flex' : 'hidden'} max-lg:fixed max-lg:inset-x-0 max-lg:bottom-0 max-lg:z-20 max-lg:max-h-[62dvh] max-lg:overflow-hidden max-lg:rounded-t-2xl max-lg:border-t max-lg:border-line max-lg:shadow-[0_-12px_40px_rgba(0,0,0,.2)] ${p.locked ? 'pointer-events-none' : ''}`} aria-label="Pages">
      <ol className="grid flex-1 content-start justify-items-center gap-1.5 overflow-y-auto py-3 max-lg:auto-cols-max max-lg:grid-flow-col max-lg:justify-start max-lg:overflow-x-auto max-lg:overflow-y-hidden max-lg:px-3">
        {p.book.pages.map((page, i) => {
          const chapterStart = page.chapterId && page.chapterId !== p.book.pages[i - 1]?.chapterId
          return (
            <li key={page.id} className={`grid justify-items-center gap-[3px] rounded-md px-1.5 pt-1 pb-0.5 [contain-intrinsic-size:auto_160px] [content-visibility:auto] ${dropAt === i ? 'shadow-[inset_0_3px_0_var(--color-brass)]' : ''}`}
              draggable={i > 0}
              onDragStart={e => { setDragFrom(i); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', page.id) }}
              onDragOver={e => { if (dragFrom !== null && i > 0) { e.preventDefault(); setDropAt(i) } }}
              onDragLeave={() => setDropAt(null)}
              onDrop={e => { e.preventDefault(); if (dragFrom !== null) p.onMove(dragFrom, i); setDragFrom(null); setDropAt(null) }}
              onDragEnd={() => { setDragFrom(null); setDropAt(null) }}>
              {chapterStart && <div className="mt-1.5 mb-0.5 line-clamp-2 max-w-[120px] text-center text-[11px] leading-tight text-muted">{chapters.get(page.chapterId!)}</div>}
              <button className={`cursor-pointer rounded-[1px] ${page.id === p.currentId ? 'shadow-[0_0_0_2px_var(--color-brass),0_3px_8px_rgba(0,0,0,.15)]' : 'shadow-[0_0_0_1px_rgba(0,0,0,.08),0_2px_5px_rgba(0,0,0,.1)]'}`} onClick={() => p.onSelect(page.id)} aria-current={page.id === p.currentId ? 'page' : undefined}
                aria-label={`${i === 0 ? 'Cover' : `Page ${i}`}${i > 0 ? '. Alt plus arrow keys to move.' : ''}`}
                onKeyDown={e => {
                  if (!e.altKey || i === 0) return
                  if (e.key === 'ArrowUp' && i > 1) { e.preventDefault(); p.onMove(i, i - 1) }
                  if (e.key === 'ArrowDown' && i < p.book.pages.length - 1) { e.preventDefault(); p.onMove(i, i + 1) }
                }}>
                <Thumb book={p.book} index={i} design={p.design} assets={p.assets} />
              </button>
              <span className="text-[11.5px] text-muted">{i === 0 ? 'Cover' : i}</span>
            </li>
          )
        })}
      </ol>
      <div className="grid gap-1.5 border-t border-line p-2.5">
        {adding ? (
          <div className="grid max-h-[50vh] overflow-y-auto rounded-md border border-line bg-white" role="menu" aria-label="Choose a layout for the new page">
            <button role="menuitem" className="w-full cursor-pointer border-b border-mist px-2.5 py-2 text-left text-[13px] hover:bg-mist focus-visible:bg-mist" onClick={() => { setAdding(false); p.onAdd(null) }}>Blank page</button>
            {p.design.templates.filter(t => !t.cover).map(t => (
              <button role="menuitem" className="w-full cursor-pointer border-b border-mist px-2.5 py-2 text-left text-[13px] hover:bg-mist focus-visible:bg-mist" key={t.id} onClick={() => { setAdding(false); p.onAdd(t.id) }}>{t.name}</button>
            ))}
            <button role="menuitem" className="w-full cursor-pointer border-b border-mist px-2.5 py-2 text-left text-[13px] hover:bg-mist focus-visible:bg-mist text-muted" onClick={() => setAdding(false)}>Cancel</button>
          </div>
        ) : (
          <>
            <button className="btn btn-brass btn-sm" disabled={p.busy} onClick={() => setAdding(true)}>+ Add page</button>
            <div className="flex gap-1.5 [&>.btn]:flex-1 [&>.btn]:px-1.5">
              <button className="btn btn-quiet btn-sm" disabled={isCover} onClick={p.onDuplicate}>Duplicate</button>
              <button className="btn btn-quiet btn-sm btn-danger-text" disabled={isCover} onClick={p.onDelete}>Delete</button>
            </div>
          </>
        )}
      </div>
    </nav>
  )
}

const Thumb = memo(function Thumb({ book, index, design, assets }: { book: MemoryBook; index: number; design: Design; assets: Record<string, AssetInfo> }) {
  return <ScaledPage width={book.orientation === 'landscape' ? 132 : 96} book={book} page={book.pages[index]} index={index}
    design={design} assets={assets} variant="thumb" />
}, (a, b) => a.book.pages[a.index] === b.book.pages[b.index] && a.book.themeId === b.book.themeId && a.index === b.index
  && a.assets === b.assets && a.book.pageSize === b.book.pageSize)
