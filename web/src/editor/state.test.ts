import { describe, expect, it } from 'vitest'
import type { MemoryBook, MemoryPage, TextElement } from '../model'
import {
  addElement, blankPage, commit, deletePage, duplicatePage, initHistory, insertPages, movePage, redo, removeElement,
  replacePage, undo, updateElement,
} from './state'

const text = (id: string): TextElement => ({ type: 'text', id, box: { x: 0, y: 0, w: 10, h: 10, rotation: 0 }, role: 'body', text: id, style: {}, valign: 'top' })
const page = (id: string, kind: MemoryPage['kind'] = 'page'): MemoryPage => ({ ...blankPage(), id, kind, elements: [text(`${id}-t`)] })
const book = (): MemoryBook => ({
  schemaVersion: 1, id: 'b', title: 'T', subtitle: '', author: '', pageSize: 'A5', orientation: 'portrait', themeId: 'editorial',
  chapters: [], pages: [page('cover', 'cover'), page('p1'), page('p2'), page('p3')],
  metadata: { createdAt: '', updatedAt: '', generatedBy: '', mood: '', language: 'en', assetIds: [], notice: '' },
})
const ids = (b: MemoryBook) => b.pages.map(p => p.id)

describe('document operations', () => {
  it('adds, deletes, duplicates and reorders pages; the cover stays first', () => {
    let b = insertPages(book(), 2, [page('new')])
    expect(ids(b)).toEqual(['cover', 'p1', 'new', 'p2', 'p3'])
    b = deletePage(b, 'new')
    expect(deletePage(b, 'cover')).toBe(b)
    b = duplicatePage(b, 'p1')
    expect(b.pages).toHaveLength(5)
    expect(b.pages[2].id).not.toBe('p1')
    expect(b.pages[2].elements[0].id).not.toBe('p1-t')
    expect(ids(movePage(book(), 3, 1))).toEqual(['cover', 'p3', 'p1', 'p2'])
    expect(ids(movePage(book(), 1, 0))).toEqual(ids(book()))
    expect(ids(replacePage(book(), 'p2', [page('a'), page('b')]))).toEqual(['cover', 'p1', 'a', 'b', 'p3'])
  })

  it('adds, modifies and removes elements without mutating the original', () => {
    const original = book()
    let b = addElement(original, 'p1', text('x'))
    b = updateElement(b, 'p1', 'x', { text: 'changed' } as Partial<TextElement>)
    expect((b.pages[1].elements[1] as TextElement).text).toBe('changed')
    expect(original.pages[1].elements).toHaveLength(1)
    b = removeElement(b, 'p1', 'x')
    expect(b.pages[1].elements.map(e => e.id)).toEqual(['p1-t'])
  })

  it('serializes and deserializes losslessly', () => {
    const b = addElement(book(), 'p2', text('y'))
    expect(JSON.parse(JSON.stringify(b))).toEqual(b)
  })
})

describe('history', () => {
  it('undoes and redoes, and coalesces continuous edits', () => {
    let h = initHistory(book())
    h = commit(h, deletePage(h.present, 'p1'))
    h = commit(h, updateElement(h.present, 'p2', 'p2-t', { text: 'a' } as Partial<TextElement>), 'drag', 1000)
    h = commit(h, updateElement(h.present, 'p2', 'p2-t', { text: 'ab' } as Partial<TextElement>), 'drag', 1100)
    expect(h.past).toHaveLength(2) // the two drag steps are one undo step
    h = undo(h)
    expect((h.present.pages[1].elements[0] as TextElement).text).toBe('p2-t')
    h = undo(h)
    expect(ids(h.present)).toContain('p1')
    h = redo(redo(h))
    expect((h.present.pages[1].elements[0] as TextElement).text).toBe('ab')
    expect(redo(h)).toBe(h)
    h = commit(undo(h), book())
    expect(h.future).toHaveLength(0) // a new edit clears redo
  })
})
