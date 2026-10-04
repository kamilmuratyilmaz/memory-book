import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MemoryBook } from '../model'
import { readDraft, useAutosave } from './autosave'

const book = (title: string) => ({ id: 'bk', title, pages: [] }) as unknown as MemoryBook

describe('autosave', () => {
  beforeEach(() => { vi.useFakeTimers(); localStorage.clear() })
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

  it('keeps a local draft immediately and batches rapid edits into one save', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ version: 2 }), { headers: { 'content-type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    const first = book('a')
    const { result, rerender } = renderHook(({ b }) => useAutosave(b, 1), { initialProps: { b: first } })
    rerender({ b: book('ab') })
    rerender({ b: book('abc') })
    expect(readDraft('bk')?.book.title).toBe('abc') // recoverable after a refresh
    expect(result.current.status).toBe('pending')
    await act(async () => { await vi.advanceTimersByTimeAsync(1300) })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(JSON.parse((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body as string).book.title).toBe('abc')
    expect(result.current.status).toBe('saved')
    expect(readDraft('bk')).toBeNull()
  })

  it('reports conflicts instead of overwriting, and keeps the draft', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ conflict: true }), { status: 409, headers: { 'content-type': 'application/json' } })))
    const { result, rerender } = renderHook(({ b }) => useAutosave(b, 1), { initialProps: { b: book('a') } })
    rerender({ b: book('b') })
    await act(async () => { await vi.advanceTimersByTimeAsync(1300) })
    expect(result.current.status).toBe('conflict')
    expect(readDraft('bk')?.book.title).toBe('b')
  })

  it('goes offline without losing edits', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network') }))
    const { result, rerender } = renderHook(({ b }) => useAutosave(b, 1), { initialProps: { b: book('a') } })
    rerender({ b: book('b') })
    await act(async () => { await vi.advanceTimersByTimeAsync(1300) })
    expect(result.current.status).toBe('offline')
    expect(readDraft('bk')?.book.title).toBe('b')
  })
})
