import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, waitForJob } from '../api'
import { useDesign } from '../App'
import { newBox, pageDims, photoCrop, uid, type AssetInfo, type ImageElement, type MemoryBook, type PageElement, type TextElement } from '../model'
import { Link } from '../router'
import { clearDraft, readDraft, useAutosave, type SaveStatus } from './autosave'
import { Canvas } from './Canvas'

import { Inspector, type Tab } from './Inspector'
import { PageList } from './PageList'
import {
  addElement, blankPage, commit, deletePage, duplicatePage, initHistory, insertPages, movePage, redo, removeElement,
  reorderElement, replacePage, undo, updateElement, updatePage, type History,
} from './state'

// the AG-UI client is sizeable; load it only with the editor's chat
const ChatPanel = lazy(() => import('./ChatPanel'))

const STATUS_DOT: Record<SaveStatus, string> = {
  saved: 'before:bg-[#7da38b]', pending: 'before:bg-brass-light', saving: 'before:bg-brass-light',
  offline: 'before:bg-danger', conflict: 'before:bg-danger', error: 'before:bg-danger',
}

const STATUS_TEXT: Record<SaveStatus, string> = {
  saved: 'All changes saved', pending: 'Unsaved changes', saving: 'Saving…',
  offline: 'Offline — changes kept on this device', conflict: 'Changed elsewhere', error: 'Couldn’t save — retrying',
}

export function Editor({ bookId }: { bookId: string }) {
  const design = useDesign()
  const [hist, setHist] = useState<History | null>(null)
  const [assets, setAssets] = useState<Record<string, AssetInfo>>({})
  const [pageId, setPageId] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [cropId, setCropId] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('chat')
  const [agentRunning, setAgentRunning] = useState(false)
  const sendToChat = useRef<((prompt: string) => void) | null>(null)
  const [loadError, setLoadError] = useState('')
  const [toast, setToast] = useState<{ text: string; undo?: boolean; error?: boolean } | null>(null)
  const [recovery, setRecovery] = useState<{ book: MemoryBook; savedAt: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [mobilePanel, setMobilePanel] = useState<'pages' | 'inspector' | null>(null)
  const book = hist?.present ?? null
  const autosave = useAutosave(book, 0)

  const load = useCallback(async () => {
    try {
      const data = await api.book(bookId)
      setAssets(data.assets)
      autosave.reset(data.book, data.version)
      let h = initHistory(data.book)
      const draft = readDraft(bookId)
      if (draft && JSON.stringify(draft.book) !== JSON.stringify(data.book)) {
        if (draft.baseVersion === data.version) {
          h = commit(h, draft.book) // unsaved edits from last session: restore, undo-able
          setToast({ text: 'Restored changes that hadn’t been saved yet.', undo: true })
        } else {
          setRecovery({ book: draft.book, savedAt: draft.savedAt })
        }
      }
      setHist(h)
      setPageId(id => (h.present.pages.some(p => p.id === id) ? id : h.present.pages[0]?.id ?? ''))
    } catch (e) {
      setLoadError((e as Error).message)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookId])

  useEffect(() => { load() }, [load])

  const apply = useCallback((fn: (b: MemoryBook) => MemoryBook, key?: string) => {
    setHist(h => (h ? commit(h, fn(h.present), key) : h))
  }, [])

  const pageIndex = book ? Math.max(0, book.pages.findIndex(p => p.id === pageId)) : 0
  const page = book?.pages[pageIndex]
  const selected = page?.elements.find(e => e.id === selectedId) ?? null

  // keep the current page valid after undo/redo/AI changes
  useEffect(() => {
    if (book && !book.pages.some(p => p.id === pageId)) setPageId(book.pages[Math.min(pageIndex, book.pages.length - 1)]?.id ?? '')
  }, [book, pageId, pageIndex])

  const goToPage = useCallback((id: string) => {
    setPageId(id); setSelectedId(null); setEditingId(null); setCropId(null); setMobilePanel(null)
  }, [])

  // --- keyboard ---
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (agentRunning) return
      const t = e.target as HTMLElement
      const typing = t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)
      const mod = e.metaKey || e.ctrlKey
      if (mod && e.key.toLowerCase() === 'z' && !typing) {
        e.preventDefault()
        setHist(h => (h ? (e.shiftKey ? redo(h) : undo(h)) : h))
      } else if (mod && e.key.toLowerCase() === 'y' && !typing) {
        e.preventDefault(); setHist(h => (h ? redo(h) : h))
      } else if (typing) {
        return
      } else if (e.key === 'Escape') {
        setSelectedId(null); setCropId(null)
      } else if ((e.key === 'Delete' || e.key === 'Backspace') && selectedId && page) {
        e.preventDefault(); apply(b => removeElement(b, page.id, selectedId)); setSelectedId(null)
      } else if (e.key === 'Enter' && selected?.type === 'text') {
        e.preventDefault(); setEditingId(selected.id)
      } else if (e.key.startsWith('Arrow') && selected && page) {
        e.preventDefault()
        const d = e.shiftKey ? 5 : 1
        const dx = e.key === 'ArrowLeft' ? -d : e.key === 'ArrowRight' ? d : 0
        const dy = e.key === 'ArrowUp' ? -d : e.key === 'ArrowDown' ? d : 0
        apply(b => updateElement(b, page.id, selected.id, el => ({ ...el, box: { ...el.box, x: el.box.x + dx, y: el.box.y + dy } })), `nudge-${selected.id}`)
      } else if ((e.key === 'PageDown' || e.key === 'PageUp') && book) {
        const next = book.pages[pageIndex + (e.key === 'PageDown' ? 1 : -1)]
        if (next) goToPage(next.id)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [agentRunning, apply, book, goToPage, page, pageIndex, selected, selectedId])

  // --- element & page actions ---
  const onElement = useCallback((patch: Partial<PageElement> | ((el: PageElement) => PageElement), key?: string) => {
    if (!page || !selectedId) return
    apply(b => updateElement(b, page.id, selectedId, patch), key)
  }, [apply, page, selectedId])

  function onElementAction(action: 'delete' | 'duplicate' | 'front' | 'back') {
    if (!page || !selected) return
    if (action === 'delete') { apply(b => removeElement(b, page.id, selected.id)); setSelectedId(null) }
    else if (action === 'duplicate') {
      const copy = { ...structuredClone(selected), id: uid('el'), box: { ...selected.box, x: selected.box.x + 4, y: selected.box.y + 4 } }
      apply(b => addElement(b, page.id, copy)); setSelectedId(copy.id)
    } else apply(b => reorderElement(b, page.id, selected.id, action))
  }

  function addText() {
    if (!book || !page) return
    const [w, h] = pageDims(book, design)
    const el: TextElement = { type: 'text', id: uid('el'), box: newBox(w * 0.15, h * 0.4, w * 0.7, h * 0.12), role: 'body',
      text: 'Write something here', style: {}, valign: 'top', slot: null }
    apply(b => addElement(b, page.id, el)); setSelectedId(el.id); setEditingId(el.id)
  }

  function addPhoto(assetId: string | null, at?: { x: number; y: number }) {
    if (!book || !page) return
    const [w, h] = pageDims(book, design)
    const a = assetId ? assets[assetId] : undefined
    const bw = w * 0.6
    const bh = a ? Math.min(bw * (a.height / a.width), h * 0.6) : bw * 0.7
    const x = at ? at.x - bw / 2 : (w - bw) / 2, y = at ? at.y - bh / 2 : (h - bh) / 2
    const el: ImageElement = { type: 'image', id: uid('el'), box: newBox(x, y, bw, bh), assetId, crop: photoCrop(a?.focus), frame: 'theme', alt: '', slot: null }
    apply(b => addElement(b, page.id, el)); setSelectedId(el.id); setTab('design')
  }

  function addDivider() {
    if (!book || !page) return
    const [w, h] = pageDims(book, design)
    const el: PageElement = { type: 'shape', id: uid('el'), box: newBox(w * 0.4, h * 0.5, w * 0.2, 0.4), kind: 'line', color: 'rule', opacity: 1, slot: null }
    apply(b => addElement(b, page.id, el)); setSelectedId(el.id)
  }

  function usePhoto(assetId: string) {
    if (!page) return
    if (selected?.type === 'image') {
      apply(b => updateElement(b, page.id, selected.id, { assetId, crop: photoCrop(assets[assetId]?.focus) } as Partial<ImageElement>))
      setTab('design')
    } else addPhoto(assetId)
  }

  async function onUpload(files: File[]) {
    if (!files.length) return
    setToast({ text: `Uploading ${files.length} photo${files.length > 1 ? 's' : ''}…` })
    try {
      const res = await api.upload(files)
      setAssets(a => ({ ...a, ...Object.fromEntries(res.assets.map(x => [x.id, x])) }))
      apply(b => ({ ...b, metadata: { ...b.metadata, assetIds: [...b.metadata.assetIds, ...res.assets.map(x => x.id)] } }))
      setToast(res.errors.length ? { text: res.errors.join(' '), error: true } : { text: `Added ${res.assets.length} photo${res.assets.length > 1 ? 's' : ''} to your library.` })
    } catch (e) {
      setToast({ text: (e as Error).message, error: true })
    }
  }

  async function withBusy<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true)
    try { return await fn() } catch (e) { setToast({ text: (e as Error).message, error: true }) } finally { setBusy(false) }
  }

  async function onTemplate(template: string) {
    if (!book || !page) return
    await withBusy(async () => {
      const { pages } = await api.layout(book, template, page)
      apply(b => replacePage(b, page.id, pages))
      setSelectedId(null)
      if (pages.length > 1) setToast({ text: `Some content moved to ${pages.length - 1} new page${pages.length > 2 ? 's' : ''} so nothing was lost.`, undo: true })
    })
  }

  async function onAddPage(template: string | null) {
    if (!book) return
    await withBusy(async () => {
      const pages = template ? (await api.layout(book, template)).pages : [blankPage()]
      apply(b => insertPages(b, pageIndex + 1, pages.map(p => ({ ...p, chapterId: page?.chapterId ?? null }))))
      goToPage(pages[0].id)
    })
  }

  async function onBook(patch: Partial<MemoryBook>, key?: string) {
    if (!book) return
    if (patch.pageSize && patch.pageSize !== book.pageSize) {
      await withBusy(async () => {
        const res = await api.resize(book, patch.pageSize!)
        apply(() => res.book)
      })
      return
    }
    // keep cover text in sync with the book fields
    apply(b => {
      let next = { ...b, ...patch }
      for (const field of ['title', 'subtitle', 'author'] as const) {
        if (patch[field] === undefined) continue
        const cover = next.pages[0]
        const el = cover?.elements.find(e => e.type === 'text' && e.slot === field)
        if (el) next = updateElement(next, cover.id, el.id, { text: patch[field] } as Partial<TextElement>)
      }
      return next
    }, key)
  }

  function askChat(instruction: string) {
    if (!instruction.trim() || !sendToChat.current) return false
    sendToChat.current(instruction)
    setTab('chat')
    setMobilePanel('inspector')
    return true
  }

  if (loadError) return <div className="grid min-h-dvh place-content-center gap-2 p-6 text-center text-muted" role="alert">{loadError} <Link href="/">Back to library</Link></div>
  if (!book || !page) return <div className="grid min-h-dvh place-content-center gap-2 p-6 text-center text-muted" aria-busy="true">Opening your book…</div>

  return (
    <div className="grid h-dvh grid-cols-[minmax(0,1fr)] grid-rows-[auto_auto_auto_auto_minmax(0,1fr)] bg-mist lg:grid-cols-[156px_minmax(0,1fr)_312px]">
      <header className="col-span-full flex flex-wrap items-center gap-2 border-b border-line bg-white px-3.5 py-2 lg:flex-nowrap lg:gap-3">
        <Link href="/" className="btn btn-quiet btn-sm" aria-label="Back to library">← Library</Link>
        <input className="order-3 min-w-20 basis-full rounded-md border border-transparent bg-transparent px-2 py-1 font-display text-lg font-semibold hover:border-line focus:border-brass focus:outline-none lg:order-none lg:max-w-[460px] lg:flex-1 lg:basis-auto lg:text-[21px]" value={book.title} aria-label="Book title" onChange={e => onBook({ title: e.target.value }, 'title')} />
        <span className={`whitespace-nowrap text-[11.5px] text-muted before:mr-1.5 before:inline-block before:size-[7px] before:rounded-full before:align-[1px] before:content-[''] lg:text-[12.5px] ${STATUS_DOT[autosave.status]}`} role="status" aria-live="polite">{STATUS_TEXT[autosave.status]}</span>
        <div className="ml-auto flex gap-1.5">
          <button className="btn btn-quiet btn-sm" onClick={() => setHist(h => h && undo(h))} disabled={!hist?.past.length} aria-label="Undo" title="Undo (⌘Z)">↶</button>
          <button className="btn btn-quiet btn-sm" onClick={() => setHist(h => h && redo(h))} disabled={!hist?.future.length} aria-label="Redo" title="Redo (⇧⌘Z)">↷</button>
          <Link className="btn btn-quiet btn-sm" href={`/books/${book.id}/read`} onClick={() => { autosave.flush().catch(() => {}) }}>Read</Link>
          <button className="btn btn-brass btn-sm" onClick={() => setExporting(true)}>Export PDF</button>
        </div>
      </header>

      {autosave.status === 'conflict' && (
        <div className="col-span-full flex flex-wrap items-center gap-2.5 border-b px-3.5 py-2 text-[13.5px] border-[#ecc9bb] bg-[#fbefe9]" role="alert">
          This book was changed in another window or device.
          <button className="btn btn-sm btn-brass" onClick={() => autosave.keepMine()}>Keep my version</button>
          <button className="btn btn-sm btn-quiet" onClick={() => { clearDraft(book.id); load() }}>Load the other version</button>
        </div>
      )}
      {recovery && (
        <div className="col-span-full flex flex-wrap items-center gap-2.5 border-b px-3.5 py-2 text-[13.5px] border-[#e6d9b8] bg-sand" role="alert">
          You have unsaved changes from {new Date(recovery.savedAt).toLocaleString()}, but the book was saved elsewhere since.
          <button className="btn btn-sm btn-brass" onClick={() => { apply(() => ({ ...recovery.book })); setRecovery(null) }}>Restore my changes</button>
          <button className="btn btn-sm btn-quiet" onClick={() => { clearDraft(book.id); setRecovery(null) }}>Keep the saved version</button>
        </div>
      )}
      {book.metadata.notice && <NoticeBanner bookId={book.id} text={book.metadata.notice} />}

      <PageList open={mobilePanel === 'pages'} locked={agentRunning} book={book} design={design} assets={assets} currentId={page.id} busy={busy} onSelect={goToPage}
        onMove={(from, to) => apply(b => movePage(b, from, to))} onAdd={onAddPage}
        onDuplicate={() => apply(b => duplicatePage(b, page.id))}
        onDelete={() => { apply(b => deletePage(b, page.id)); setToast({ text: 'Page deleted.', undo: true }) }} />

      <main className="relative row-start-5 flex min-h-0 min-w-0 flex-col bg-desk lg:col-start-2">
        <div className={`flex flex-wrap items-center gap-1.5 px-3.5 py-2 ${agentRunning ? 'pointer-events-none' : ''}`} role="toolbar" aria-label="Add to page">
          <button className="btn btn-quiet btn-sm" onClick={addText}>+ Text</button>
          <button className="btn btn-quiet btn-sm" onClick={() => { setTab('photos'); setMobilePanel('inspector') }}>+ Photo</button>
          <button className="btn btn-quiet btn-sm" onClick={addDivider}>+ Divider</button>
          <span className="text-[12.5px] text-muted lg:ml-auto">{pageIndex === 0 ? 'Cover' : `Page ${pageIndex} of ${book.pages.length - 1}`}</span>
          <button className="btn btn-quiet btn-sm lg:hidden" onClick={() => setMobilePanel(m => (m === 'pages' ? null : 'pages'))} aria-expanded={mobilePanel === 'pages'}>Pages</button>
          <button className="btn btn-quiet btn-sm lg:hidden" onClick={() => setMobilePanel(m => (m === 'inspector' ? null : 'inspector'))} aria-expanded={mobilePanel === 'inspector'}>Edit</button>
        </div>
        {agentRunning && <div className="absolute top-13 left-1/2 z-6 -translate-x-1/2 rounded-full bg-ink px-3.5 py-1.5 text-[13px] text-white" role="status">Making your changes…</div>}
        <Canvas key={page.id} locked={agentRunning} book={book} page={page} index={pageIndex} design={design} assets={assets}
          selectedId={selectedId} editingId={editingId} cropId={cropId}
          onSelect={id => { setSelectedId(id); if (id !== editingId) setEditingId(null); if (id !== cropId) setCropId(null); if (id) setTab('design') }}
          onStartEdit={id => { setSelectedId(id); setEditingId(id) }}
          onCommitText={(id, text) => { apply(b => updateElement(b, page.id, id, { text } as Partial<TextElement>)); setEditingId(null) }}
          onToggleCrop={setCropId}
          onChangeBox={(id, box, key) => apply(b => updateElement(b, page.id, id, { box }), key)}
          onChangeCrop={(id, crop, key) => apply(b => updateElement(b, page.id, id, { crop } as Partial<ImageElement>), key)}
          onDropAsset={(assetId, targetId, at) => {
            if (targetId) apply(b => updateElement(b, page.id, targetId, { assetId, crop: photoCrop(assets[assetId]?.focus) } as Partial<ImageElement>))
            else addPhoto(assetId, at)
          }} />
        <AiBar busy={busy || agentRunning} onSubmit={async t => askChat(t)} scope={selected ? 'the selected item' : pageIndex === 0 ? 'the cover' : `page ${pageIndex}`} ai={design.ai} />
      </main>

      <Inspector open={mobilePanel === 'inspector'} tab={tab} setTab={setTab} book={book} page={page} design={design} assets={assets} selected={selected} busy={busy}
        onElement={onElement} onElementAction={onElementAction}
        onPage={(patch, key) => apply(b => updatePage(b, page.id, patch), key)}
        onTemplate={onTemplate} onBook={onBook} onUsePhoto={usePhoto} onUpload={onUpload}
        chat={<Suspense fallback={<p className="hint">Loading chat…</p>}><ChatPanel book={book} pageId={page.id} elementId={selectedId} sendRef={sendToChat}
          aiMode={design.ai}
          onRunStart={() => { setAgentRunning(true); setSelectedId(null); setEditingId(null); setCropId(null) }}
          onBook={(next, runId) => setHist(h => (h ? commit(h, next, `agent:${runId}`, Date.now(), Infinity) : h))}
          onRunEnd={() => setAgentRunning(false)}
          onShowPage={id => { if (book.pages.some(p => p.id === id)) setPageId(id) }}
          canUndo={runId => hist?.coalesce?.key === `agent:${runId}`}
          onUndo={() => setHist(h => h && undo(h))} /></Suspense>} />

      {toast && <Toast {...toast} onUndo={() => { setHist(h => h && undo(h)); setToast(null) }} onClose={() => setToast(null)} />}
      {exporting && <ExportDialog book={book} flush={autosave.flush} onClose={() => setExporting(false)} />}
    </div>
  )
}

function NoticeBanner({ bookId, text }: { bookId: string; text: string }) {
  const key = `memory-book:notice:${bookId}`
  const [hidden, setHidden] = useState(() => { try { return !!localStorage.getItem(key) } catch { return false } })
  if (hidden) return null
  return (
    <div className="col-span-full flex flex-wrap items-center gap-2.5 border-b px-3.5 py-2 text-[13.5px] border-[#e6d9b8] bg-sand" role="status">
      {text}
      <button className="btn btn-sm btn-quiet" onClick={() => { try { localStorage.setItem(key, '1') } catch { /* ignore */ } setHidden(true) }}>Got it</button>
    </div>
  )
}

function Toast({ text, undo, error, onUndo, onClose }: { text: string; undo?: boolean; error?: boolean; onUndo: () => void; onClose: () => void }) {
  useEffect(() => {
    const t = window.setTimeout(onClose, error ? 9000 : 6000)
    return () => window.clearTimeout(t)
  }, [text, error, onClose])
  return (
    <div className={`fixed bottom-22 left-1/2 z-30 flex max-w-[min(560px,calc(100vw-32px))] -translate-x-1/2 items-center gap-2.5 rounded-xl py-2.5 pr-2.5 pl-4 text-sm text-white shadow-[0_12px_30px_rgba(0,0,0,.25)] ${error ? 'bg-[#6e2416]' : 'bg-ink'}`} role={error ? 'alert' : 'status'}>
      <span>{text}</span>
      {undo && <button className="btn btn-sm border-white/30 text-white hover:bg-white/10" onClick={onUndo}>Undo</button>}
      <button className="cursor-pointer px-1.5 text-xl text-white opacity-70 hover:opacity-100" onClick={onClose} aria-label="Dismiss">×</button>
    </div>
  )
}

function AiBar({ busy, onSubmit, scope, ai }: { busy: boolean; onSubmit: (s: string) => Promise<boolean>; scope: string; ai: string }) {
  const [text, setText] = useState('')
  async function submit(value: string) {
    if (await onSubmit(value)) setText('')
  }
  return (
    <form className={`relative m-2 flex items-center gap-2 rounded-full border border-line py-1.5 pr-1.5 pl-3.5 shadow-[0_4px_14px_rgba(26,35,32,.08)] lg:mx-3.5 lg:mt-2.5 lg:mb-3.5 ${busy ? 'animate-shimmer bg-linear-to-r from-white via-sand to-white bg-size-[200%_100%]' : 'bg-white'}`} onSubmit={e => { e.preventDefault(); submit(text) }}>
      <label className="sr-only" htmlFor="ai-input">Ask for a change</label>
      <input id="ai-input" value={text} disabled={busy} onChange={e => setText(e.target.value)}
        placeholder={busy ? 'Working on your change…' : `Ask for a change to ${scope} or the whole book`} />
      <button className="btn btn-brass btn-sm rounded-full" disabled={busy || !text.trim()}>{busy ? 'Working…' : 'Ask'}</button>
      {ai === 'local' && <span className="hidden pr-2 text-[11.5px] whitespace-nowrap text-muted lg:inline" title="Configure an AI model (MEMORY_BOOK_MODEL and its API key) on the server to enable writing changes">Offline mode</span>}
    </form>
  )
}

function ExportDialog({ book, flush, onClose }: { book: MemoryBook; flush: () => Promise<void>; onClose: () => void }) {
  const [bleed, setBleed] = useState(false)
  const [state, setState] = useState<'idle' | 'saving' | 'rendering' | 'done' | 'error'>('idle')
  const [result, setResult] = useState<{ file: string; warnings: string[]; pages: number } | null>(null)
  const [error, setError] = useState('')
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  const photoCount = useMemo(() => new Set(book.pages.flatMap(p => p.elements.flatMap(e => (e.type === 'image' && e.assetId ? [e.assetId] : [])))).size, [book])

  async function run() {
    setError('')
    try {
      setState('saving')
      await flush()
      setState('rendering')
      const { jobId } = await api.exportPdf(book.id, bleed)
      const res = await waitForJob<{ file: string; warnings: string[]; pages: number }>(jobId)
      setResult(res)
      setState('done')
    } catch (e) {
      setError((e as Error).message)
      setState('error')
    }
  }

  return (
    <dialog ref={dialog} className="m-auto w-[min(480px,calc(100vw-32px))] gap-3.5 rounded-xl p-6 shadow-[0_30px_80px_rgba(0,0,0,.3)] backdrop:bg-ink/55 open:grid" onClose={onClose} aria-labelledby="export-title">
      <h2 id="export-title" className="font-display text-[28px] font-semibold">Export as PDF</h2>
      <p className="text-muted">{book.pages.length} pages · {photoCount} photos · {book.pageSize} {book.orientation}</p>
      {state === 'done' && result ? (
        <>
          <p>Your PDF is ready: {result.pages} pages at full print resolution.</p>
          {result.warnings.length > 0 && (
            <div className="notice"><strong>Worth checking before you print:</strong>
              <ul className="list-disc pl-4.5">{[...new Set(result.warnings)].slice(0, 8).map(w => <li key={w}>{w}</li>)}</ul></div>
          )}
          <div className="flex flex-wrap items-center gap-1.5">
            <a className="btn btn-brass" href={`/api/exports/${result.file}`} download>Download PDF</a>
            <button className="btn btn-quiet" onClick={() => dialog.current?.close()}>Close</button>
          </div>
        </>
      ) : (
        <>
          <fieldset className="grid gap-2.5" disabled={state === 'saving' || state === 'rendering'}>
            <label className="flex cursor-pointer items-start gap-2.5 rounded-md border border-line p-3 has-checked:border-brass has-checked:bg-cream"><input type="radio" className="mt-1 accent-brass" name="bleed" checked={!bleed} onChange={() => setBleed(false)} />
              <span><strong>For reading and sharing</strong><small className="block text-muted">Exact page size, ideal for screens and home printers.</small></span></label>
            <label className="flex cursor-pointer items-start gap-2.5 rounded-md border border-line p-3 has-checked:border-brass has-checked:bg-cream"><input type="radio" className="mt-1 accent-brass" name="bleed" checked={bleed} onChange={() => setBleed(true)} />
              <span><strong>For a print shop</strong><small className="block text-muted">Adds 3 mm bleed on every edge and marks the trim size for the printer.</small></span></label>
          </fieldset>
          {error && <p className="notice notice-error" role="alert">{error}</p>}
          <div className="flex flex-wrap items-center gap-1.5">
            <button className="btn btn-brass" onClick={run} disabled={state === 'saving' || state === 'rendering'}>
              {state === 'saving' ? 'Saving your changes…' : state === 'rendering' ? 'Creating PDF…' : 'Create PDF'}
            </button>
            <button className="btn btn-quiet" onClick={() => dialog.current?.close()}>Cancel</button>
          </div>
        </>
      )}
    </dialog>
  )
}
