// Pure editor state: undo/redo history and immutable document operations.

import { uid, type Box, type MemoryBook, type MemoryPage, type PageElement } from '../model'

export interface History {
  past: MemoryBook[]
  present: MemoryBook
  future: MemoryBook[]
  /** Consecutive edits with the same key (a drag, a slider) collapse into one undo step. */
  coalesce: { key: string; at: number } | null
}

const LIMIT = 150

export const initHistory = (book: MemoryBook): History => ({ past: [], present: book, future: [], coalesce: null })

export function commit(h: History, next: MemoryBook, key?: string, now = Date.now(), windowMs = 1500): History {
  if (next === h.present) return h
  if (key && h.coalesce?.key === key && now - h.coalesce.at < windowMs) {
    return { ...h, present: next, future: [], coalesce: { key, at: now } }
  }
  return { past: [...h.past, h.present].slice(-LIMIT), present: next, future: [], coalesce: key ? { key, at: now } : null }
}

export function undo(h: History): History {
  if (!h.past.length) return h
  return { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future], coalesce: null }
}

export function redo(h: History): History {
  if (!h.future.length) return h
  return { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1), coalesce: null }
}

// --- document operations (return new books; never mutate) ---

const mapPage = (book: MemoryBook, pageId: string, fn: (p: MemoryPage) => MemoryPage): MemoryBook =>
  ({ ...book, pages: book.pages.map(p => (p.id === pageId ? fn(p) : p)) })

export function updatePage(book: MemoryBook, pageId: string, patch: Partial<MemoryPage>): MemoryBook {
  return mapPage(book, pageId, p => ({ ...p, ...patch }))
}

export function updateElement(book: MemoryBook, pageId: string, elementId: string,
  patch: Partial<PageElement> | ((el: PageElement) => PageElement)): MemoryBook {
  return mapPage(book, pageId, p => ({
    ...p,
    elements: p.elements.map(e => (e.id === elementId ? (typeof patch === 'function' ? patch(e) : ({ ...e, ...patch } as PageElement)) : e)),
  }))
}

export function addElement(book: MemoryBook, pageId: string, el: PageElement): MemoryBook {
  return mapPage(book, pageId, p => ({ ...p, elements: [...p.elements, el] }))
}

export function removeElement(book: MemoryBook, pageId: string, elementId: string): MemoryBook {
  return mapPage(book, pageId, p => ({ ...p, elements: p.elements.filter(e => e.id !== elementId) }))
}

export function reorderElement(book: MemoryBook, pageId: string, elementId: string, dir: 'front' | 'back'): MemoryBook {
  return mapPage(book, pageId, p => {
    const el = p.elements.find(e => e.id === elementId)
    if (!el) return p
    const rest = p.elements.filter(e => e.id !== elementId)
    return { ...p, elements: dir === 'front' ? [...rest, el] : [el, ...rest] }
  })
}

export function insertPages(book: MemoryBook, at: number, pages: MemoryPage[]): MemoryBook {
  const next = [...book.pages]
  next.splice(Math.max(1, Math.min(at, next.length)), 0, ...pages)
  return { ...book, pages: next }
}

/** Replace one page with one or more pages (template changes can spill content onto new pages). */
export function replacePage(book: MemoryBook, pageId: string, pages: MemoryPage[]): MemoryBook {
  const i = book.pages.findIndex(p => p.id === pageId)
  if (i < 0) return book
  const next = [...book.pages]
  next.splice(i, 1, ...pages)
  return { ...book, pages: next }
}

export function deletePage(book: MemoryBook, pageId: string): MemoryBook {
  const page = book.pages.find(p => p.id === pageId)
  if (!page || page.kind === 'cover') return book
  return { ...book, pages: book.pages.filter(p => p.id !== pageId) }
}

export function duplicatePage(book: MemoryBook, pageId: string): MemoryBook {
  const i = book.pages.findIndex(p => p.id === pageId)
  if (i < 0 || book.pages[i].kind === 'cover') return book
  const src = book.pages[i]
  const copy: MemoryPage = { ...structuredClone(src), id: uid('pg'), elements: src.elements.map(e => ({ ...structuredClone(e), id: uid('el') })) }
  return insertPages(book, i + 1, [copy])
}

/** Move a page to a new index. The cover always stays first. */
export function movePage(book: MemoryBook, from: number, to: number): MemoryBook {
  if (from === to || from < 1 || to < 1 || from >= book.pages.length || to >= book.pages.length) return book
  const next = [...book.pages]
  const [page] = next.splice(from, 1)
  next.splice(to, 0, page)
  return { ...book, pages: next }
}

export function blankPage(): MemoryPage {
  return { id: uid('pg'), kind: 'page', chapterId: null, template: null, elements: [], background: { color: null }, showNumber: true }
}

export const clampBox = (b: Box, pageW: number, pageH: number): Box => {
  const w = Math.max(3, Math.min(b.w, pageW * 2)), h = Math.max(3, Math.min(b.h, pageH * 2))
  return { ...b, w, h, x: Math.min(Math.max(b.x, -w + 3), pageW - 3), y: Math.min(Math.max(b.y, -h + 3), pageH - 3) }
}
