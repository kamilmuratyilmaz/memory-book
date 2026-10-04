// Debounced autosave with a local safety copy, offline retry and explicit conflict handling.

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { MemoryBook } from '../model'

export type SaveStatus = 'saved' | 'pending' | 'saving' | 'offline' | 'conflict' | 'error'

interface Draft { book: MemoryBook; baseVersion: number; savedAt: string }

const draftKey = (id: string) => `memory-book:draft:${id}`

export function readDraft(id: string): Draft | null {
  try {
    const raw = localStorage.getItem(draftKey(id))
    return raw ? (JSON.parse(raw) as Draft) : null
  } catch {
    return null
  }
}

function writeDraft(draft: Draft) {
  try {
    localStorage.setItem(draftKey(draft.book.id), JSON.stringify(draft))
  } catch { /* storage full or blocked: the server save still runs */ }
}

export function clearDraft(id: string) {
  try { localStorage.removeItem(draftKey(id)) } catch { /* ignore */ }
}

export function useAutosave(book: MemoryBook | null, initialVersion: number, delay = 1200) {
  const [status, setStatus] = useState<SaveStatus>('saved')
  const version = useRef(initialVersion)
  const saved = useRef<MemoryBook | null>(book)
  const latest = useRef(book)
  const timer = useRef<number | undefined>(undefined)
  const inflight = useRef<Promise<void> | null>(null)
  useLayoutEffect(() => { latest.current = book })

  const save = useCallback(async function run(force = false): Promise<void> {
    const current = latest.current
    if (!current || (current === saved.current && !force)) return
    if (inflight.current) { await inflight.current; return run(force) }
    setStatus('saving')
    const attempt = (async () => {
      try {
        const res = await api.save(current, force ? null : version.current)
        version.current = res.version
        saved.current = current
        if (latest.current === current) {
          clearDraft(current.id)
          setStatus('saved')
        } else {
          setStatus('pending')
        }
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) setStatus('conflict')
        else if (e instanceof ApiError && e.status === 0) setStatus('offline')
        else setStatus('error')
        throw e
      } finally {
        inflight.current = null
      }
    })()
    inflight.current = attempt
    return attempt
  }, [])

  // schedule a save after edits settle; keep a local copy immediately
  useEffect(() => {
    if (!book || book === saved.current) return
    writeDraft({ book, baseVersion: version.current, savedAt: new Date().toISOString() })
    setStatus(s => (s === 'conflict' ? s : 'pending'))
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => { save().catch(() => {}) }, delay)
    return () => window.clearTimeout(timer.current)
  }, [book, delay, save])

  // retry while offline / after transient errors
  useEffect(() => {
    if (status !== 'offline' && status !== 'error') return
    const t = window.setInterval(() => { save().catch(() => {}) }, 5000)
    const online = () => { save().catch(() => {}) }
    window.addEventListener('online', online)
    return () => { window.clearInterval(t); window.removeEventListener('online', online) }
  }, [status, save])

  // warn before leaving with unsaved edits
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (latest.current !== saved.current) e.preventDefault()
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  return {
    status,
    /** Save now (e.g. before export). */
    flush: () => save(),
    /** After a conflict: keep this device's version and overwrite the server copy. */
    keepMine: () => save(true),
    /** Mark a freshly loaded server version as the saved baseline. */
    reset: (b: MemoryBook, v: number) => { saved.current = b; version.current = v; setStatus('saved') },
  }
}
