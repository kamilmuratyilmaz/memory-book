import { describe, expect, it } from 'vitest'
import { spreadOfPage, spreadsFor } from './spreads'

describe('reader spreads', () => {
  it('puts the cover alone, then pairs pages like a printed book', () => {
    expect(spreadsFor(6, true)).toEqual([
      { left: null, right: 0 }, { left: 1, right: 2 }, { left: 3, right: 4 }, { left: 5, right: null },
    ])
    expect(spreadsFor(1, true)).toEqual([{ left: null, right: 0 }])
    expect(spreadsFor(3, false)).toHaveLength(3)
  })

  it('finds the spread for a page, clamping out-of-range pages', () => {
    const s = spreadsFor(60, true)
    expect(spreadOfPage(s, 0)).toBe(0)
    expect(spreadOfPage(s, 4)).toBe(2)
    expect(spreadOfPage(s, 59)).toBe(s.length - 1)
    expect(spreadOfPage(s, 999)).toBe(s.length - 1)
  })
})
