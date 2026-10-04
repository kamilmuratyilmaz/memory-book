// Reading order: the cover sits alone on the right, then pages pair up like a printed book.

export interface Spread { left: number | null; right: number | null }

export function spreadsFor(pageCount: number, spreadMode: boolean): Spread[] {
  if (pageCount === 0) return [{ left: null, right: null }]
  if (!spreadMode) return Array.from({ length: pageCount }, (_, i) => ({ left: null, right: i }))
  const spreads: Spread[] = [{ left: null, right: 0 }]
  for (let i = 1; i < pageCount; i += 2) spreads.push({ left: i, right: i + 1 < pageCount ? i + 1 : null })
  return spreads
}

export function spreadOfPage(spreads: Spread[], page: number): number {
  const i = spreads.findIndex(s => s.left === page || s.right === page)
  return i < 0 ? (page > 0 ? spreads.length - 1 : 0) : i
}
