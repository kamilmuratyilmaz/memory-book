import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { useDesign } from '../App'
import { pageDims, themeOf, type AssetInfo, type Design, type MemoryBook } from '../model'
import { Page } from '../render/Page'
import { PX_PER_MM } from '../render/ScaledPage'
import { Link } from '../router'
import { spreadsFor, spreadOfPage, type Spread } from './spreads'

const TURN_MS = 760

export function Reader({ bookId }: { bookId: string }) {
  const design = useDesign()
  const [data, setData] = useState<{ book: MemoryBook; assets: Record<string, AssetInfo> } | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    api.book(bookId).then(d => setData({ book: d.book, assets: d.assets })).catch(e => setError(e.message))
  }, [bookId])
  const message = 'fixed inset-0 grid place-content-center gap-2 bg-cloth p-6 text-center text-on-cloth-muted'
  if (error) return <div data-reader className={message} role="alert">{error} <Link href="/" className="underline">Back to library</Link></div>
  if (!data) return <div data-reader className={message} aria-busy="true">Opening the book…</div>
  return <BookView book={data.book} assets={data.assets} design={design} />
}

function useViewport(ref: React.RefObject<HTMLElement | null>) {
  const [size, setSize] = useState({ w: 1000, h: 700 })
  useLayoutEffect(() => {
    const node = ref.current
    if (!node) return
    const update = () => setSize({ w: node.clientWidth, h: node.clientHeight })
    update()
    const ro = new ResizeObserver(update)
    ro.observe(node)
    return () => ro.disconnect()
  }, [ref])
  return size
}

const prefersReducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

function BookView({ book, assets, design }: { book: MemoryBook; assets: Record<string, AssetInfo>; design: Design }) {
  const root = useRef<HTMLDivElement>(null)
  const stage = useRef<HTMLDivElement>(null)
  const vp = useViewport(stage)
  const [pw, ph] = pageDims(book, design)
  const aspect = pw / ph
  const [immersive, setImmersive] = useState(false)
  const [chrome, setChrome] = useState(true)
  const chromeTimer = useRef<number | undefined>(undefined)

  // spread view whenever the stage is wide enough for two pages side by side (desktops and landscape phones);
  // single page on portrait screens
  const spreadMode = vp.w >= 480 && vp.w / vp.h > aspect * 1.25
  const pad = immersive ? 8 : 24 // in full screen the book uses (almost) the whole screen
  const pageH = Math.max(120, spreadMode ? Math.min(vp.h - pad, (vp.w - pad * 2) / 2 / aspect) : Math.min(vp.h - pad, (vp.w - pad) / aspect))
  const pageW = pageH * aspect
  const scale = pageW / (pw * PX_PER_MM)

  const spreads = useMemo(() => spreadsFor(book.pages.length, spreadMode), [book.pages.length, spreadMode])
  const [pos, setPos] = useState(() => {
    const n = Number(new URLSearchParams(location.search).get('page'))
    return Number.isFinite(n) && n > 0 ? n : 0 // remembered as a page index; mapped to a spread below
  })
  const current = spreadOfPage(spreads, pos)
  const [turn, setTurn] = useState<{ from: number; to: number; dir: 1 | -1 } | null>(null)
  const turnTimer = useRef<number | undefined>(undefined)

  const go = useCallback((target: number) => {
    if (turn) return
    const to = Math.max(0, Math.min(spreads.length - 1, target))
    if (to === current) return
    const firstPage = (s: Spread) => (s.left ?? s.right)!
    if (prefersReducedMotion() || Math.abs(to - current) > 1) {
      setPos(firstPage(spreads[to]))
      return
    }
    setTurn({ from: current, to, dir: to > current ? 1 : -1 })
    window.clearTimeout(turnTimer.current)
    turnTimer.current = window.setTimeout(() => { setPos(firstPage(spreads[to])); setTurn(null) }, TURN_MS)
  }, [current, spreads, turn])

  const next = useCallback(() => go(current + 1), [current, go])
  const prev = useCallback(() => go(current - 1), [current, go])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === 'INPUT') return
      if (['ArrowRight', 'PageDown', ' '].includes(e.key)) { e.preventDefault(); next() }
      else if (['ArrowLeft', 'PageUp'].includes(e.key)) { e.preventDefault(); prev() }
      else if (e.key === 'Home') go(0)
      else if (e.key === 'End') go(spreads.length - 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, next, prev, spreads.length])

  useEffect(() => {
    const spread = spreads[current]
    const page = spread.left ?? spread.right ?? 0
    history.replaceState(null, '', page ? `?page=${page}` : location.pathname)
  }, [current, spreads])

  // swipe
  const swipe = useRef<{ x: number; y: number } | null>(null)
  const onPointerDown = (e: React.PointerEvent) => { swipe.current = { x: e.clientX, y: e.clientY } }
  const onPointerUp = (e: React.PointerEvent) => {
    const s = swipe.current
    swipe.current = null
    if (!s) return
    const dx = e.clientX - s.x, dy = e.clientY - s.y
    if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy) * 1.2) { if (dx < 0) next(); else prev() }
    else if (Math.abs(dx) < 6 && Math.abs(dy) < 6 && e.pointerType === 'mouse') {
      const rect = stage.current!.getBoundingClientRect()
      if (e.clientX > rect.left + rect.width / 2) next(); else prev()
    }
  }

  // Trackpad: a horizontal two-finger swipe turns one page per gesture (momentum events are ignored until it settles)
  const wheel = useRef({ dx: 0, last: 0, fired: false })
  const onWheel = (e: React.WheelEvent) => {
    if (Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return
    const w = wheel.current
    const now = performance.now()
    if (now - w.last > 250) { w.dx = 0; w.fired = false }
    w.last = now
    w.dx += e.deltaX
    if (!w.fired && Math.abs(w.dx) > 60) {
      w.fired = true
      if (w.dx > 0) next(); else prev()
    }
  }

  // Full screen: the Fullscreen API hides all browser and system bars (navigationUI: Android). iPhone Safari has no
  // element full screen, so there the immersive layout fills the window; only the home-screen app (see
  // public/manifest.webmanifest) has no browser toolbar at all.
  const nativeFullscreen = typeof document.documentElement.requestFullscreen === 'function' && document.fullscreenEnabled
  const homeScreenApp = window.matchMedia?.('(display-mode: fullscreen), (display-mode: standalone)').matches
    || (navigator as { standalone?: boolean }).standalone === true
  const toggleFullscreen = () => {
    if (nativeFullscreen) {
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
      else root.current?.requestFullscreen({ navigationUI: 'hide' }).catch(() => setImmersive(true))
    } else setImmersive(v => !v)
  }
  // the reader draws edge to edge, under the notch and home indicator; the safe-area padding keeps controls clear
  useEffect(() => {
    const meta = document.querySelector<HTMLMetaElement>('meta[name=viewport]')
    if (!meta) return
    const before = meta.content
    meta.content = `${before}, viewport-fit=cover`
    return () => { meta.content = before }
  }, [])
  useEffect(() => {
    const sync = () => setImmersive(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', sync)
    return () => document.removeEventListener('fullscreenchange', sync)
  }, [])
  useEffect(() => {
    if (!immersive || nativeFullscreen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setImmersive(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [immersive, nativeFullscreen])
  // in full screen the bars fade out and come back while the pointer moves or the reader taps
  const wake = () => {
    setChrome(true)
    window.clearTimeout(chromeTimer.current)
    chromeTimer.current = window.setTimeout(() => setChrome(false), 2500)
  }
  useEffect(() => {
    if (!immersive) { window.clearTimeout(chromeTimer.current); setChrome(true); return }
    wake()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [immersive])

  const PAGE_SIDE = {
    left: 'rounded-l-[3px]', right: 'rounded-r-[3px]', single: 'rounded-[3px]',
  } as const
  const GUTTER = {  // the shadow of the spine on each half of an open book
    left: 'right-0 bg-linear-to-l', right: 'left-0 bg-linear-to-r', single: 'hidden',
  } as const
  const renderPage = (index: number | null, side: 'left' | 'right' | 'single') => {
    if (index === null) return <div className="absolute inset-0" />
    return (
      <div className={`absolute inset-0 overflow-hidden bg-white shadow-[0_2px_3px_rgba(0,0,0,.25),0_24px_50px_-12px_rgba(0,0,0,.55)] ${PAGE_SIDE[side]}`}
        role="group" aria-roledescription="page" aria-label={index === 0 ? 'Cover' : `Page ${index}`}>
        <div className="absolute top-0 left-0 origin-top-left" style={{ transform: `scale(${scale})` }}>
          <Page book={book} page={book.pages[index]} index={index} design={design} assets={assets} mode="read" />
        </div>
        <div className={`pointer-events-none absolute inset-y-0 w-[7%] from-black/20 via-black/5 via-35% to-transparent ${GUTTER[side]}`} aria-hidden="true" />
      </div>
    )
  }

  const shown = turn ? spreads[turn.from] : spreads[current]
  const target = turn ? spreads[turn.to] : null
  // base layers during a turn: the pages revealed underneath the moving leaf
  const baseLeft = turn ? (turn.dir === 1 ? shown.left : target!.left) : shown.left
  const baseRight = turn ? (turn.dir === 1 ? target!.right : shown.right) : shown.right
  const settled = turn ? spreads[turn.to] : shown
  // centre a closed book (cover alone on the right, or a final page alone on the left)
  const shift = spreadMode ? (settled.left === null ? -pageW / 2 : settled.right === null ? pageW / 2 : 0) : 0
  const paper = themeOf(design, book.themeId).colors.paper
  const label = (s: Spread) => [s.left, s.right].filter(x => x !== null).map(i => (i === 0 ? 'Cover' : `page ${i}`)).join(' and ')

  // a turning leaf and its two faces; the shading darkens the leaf as it passes the vertical
  const leaf = 'absolute top-0 z-3 h-full transform-3d will-change-transform'
  const face = `absolute inset-0 backface-hidden after:pointer-events-none after:absolute after:inset-0 after:bg-linear-to-r
    after:from-black/25 after:to-transparent after:to-60% after:opacity-0 after:animate-leaf-shade`
  const bar = immersive
    ? `absolute right-[env(safe-area-inset-right)] left-[env(safe-area-inset-left)] z-5 transition-opacity duration-300 ${chrome ? '' : 'pointer-events-none opacity-0'}`
    : ''

  return (
    <div data-reader ref={root}
      className={`fixed inset-0 grid grid-cols-[minmax(0,1fr)] overflow-hidden text-on-cloth
        pt-[env(safe-area-inset-top)] pr-[env(safe-area-inset-right)] pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)]
        bg-[radial-gradient(ellipse_at_50%_42%,var(--color-cloth-light),var(--color-cloth)_45%,var(--color-cloth-deep)_100%)]
        ${immersive ? 'grid-rows-[minmax(0,1fr)]' : 'grid-rows-[auto_minmax(0,1fr)_auto]'} ${immersive && !chrome ? 'cursor-none' : ''}`}
      onPointerMove={immersive ? wake : undefined} onPointerDown={immersive ? wake : undefined}>
      <header className={`flex items-center gap-2 px-3 py-2.5 sm:gap-3 sm:px-4 ${bar} ${immersive ? 'top-[env(safe-area-inset-top)] bg-linear-to-b from-[rgba(16,24,21,.85)] to-transparent' : ''}`}>
        <Link className="btn btn-ghost btn-sm shrink-0" href={`/books/${book.id}`}>← Edit<span className="hidden sm:inline">&nbsp;book</span></Link>
        <h1 className="min-w-0 flex-1 truncate text-center font-display text-lg font-medium sm:text-xl">{book.title}</h1>
        <button className="btn btn-ghost btn-sm shrink-0" onClick={toggleFullscreen} aria-pressed={immersive}>
          {immersive ? 'Exit full screen' : 'Full screen'}
        </button>
      </header>
      <div className="relative grid touch-pan-y place-items-center overflow-hidden select-none" ref={stage}
        onPointerDown={onPointerDown} onPointerUp={onPointerUp} onWheel={onWheel}
        role="region" aria-roledescription="book" aria-label={book.title}>
        <div className={`relative transition-transform duration-700 ease-[cubic-bezier(.45,.05,.25,1)] ${spreadMode ? 'grid grid-cols-2' : ''}`}
          style={{ width: spreadMode ? pageW * 2 : pageW, height: pageH, transform: `translateX(${shift}px)`,
            perspective: `${(spreadMode ? pageW * 2 : pageW) * 2.4}px`, ['--turn-ms' as string]: `${TURN_MS}ms` }}>
          {spreadMode ? (
            <>
              <div className="relative">{baseLeft !== null && renderPage(baseLeft, 'left')}</div>
              <div className="relative">{baseRight !== null && renderPage(baseRight, 'right')}</div>
              {turn && (
                <div className={`${leaf} w-1/2 ${turn.dir === 1 ? 'left-1/2 origin-left animate-turn-next' : 'left-0 origin-right animate-turn-prev'}`}>
                  <div className={face}>{renderPage(turn.dir === 1 ? shown.right : shown.left, turn.dir === 1 ? 'right' : 'left')}</div>
                  <div className={`${face} [transform:rotateY(180deg)]`}>{renderPage(turn.dir === 1 ? target!.left : target!.right, turn.dir === 1 ? 'left' : 'right')}</div>
                </div>
              )}
            </>
          ) : (
            <>
              {/* one page at a time, turned like the desktop leaf: forward lifts the current page off the spine,
                  back lays the previous page down over it */}
              <div className="absolute inset-0">{renderPage(turn ? (turn.dir === 1 ? target!.right : shown.right) : settled.right, 'single')}</div>
              {turn && (
                <div className={`${leaf} left-0 w-full origin-left ${turn.dir === 1 ? 'animate-turn-next' : 'animate-turn-in [transform:rotateY(-180deg)]'}`}>
                  <div className={face}>{renderPage(turn.dir === 1 ? shown.right : target!.right, 'single')}</div>
                  <div className={`${face} [transform:rotateY(180deg)]`}>
                    <div className="absolute inset-0 rounded-[3px] shadow-[inset_-24px_0_30px_-18px_rgba(0,0,0,.25)]" style={{ background: paper }} />
                  </div>
                </div>
              )}
            </>
          )}
        </div>
        {/* preload the next spread's photos */}
        <div className="pointer-events-none absolute size-px overflow-hidden opacity-0" aria-hidden="true">
          {[spreads[current + 1]?.left, spreads[current + 1]?.right].filter((i): i is number => i != null).map(i => (
            <div key={i} className="absolute top-0 left-0 origin-top-left" style={{ transform: `scale(${scale})` }}>
              <Page book={book} page={book.pages[i]} index={i} design={design} assets={assets} mode="read" />
            </div>
          ))}
        </div>
      </div>
      <nav className={`flex flex-wrap items-center justify-center gap-2 px-4 pt-2.5 pb-4 md:gap-3.5 ${bar} ${immersive ? 'bottom-[env(safe-area-inset-bottom)] bg-linear-to-t from-[rgba(16,24,21,.85)] to-transparent' : ''}`}
        aria-label="Page navigation">
        <button className="btn btn-ghost" onClick={prev} disabled={current === 0} aria-label="Previous page">‹ Previous</button>
        <label className="hidden flex-[0_1_360px] md:block">
          <span className="sr-only">Go to page</span>
          <input type="range" className="w-full accent-brass-light" min={0} max={spreads.length - 1} value={turn ? turn.to : current}
            onChange={e => go(Number(e.target.value))} aria-valuetext={label(spreads[current])} />
        </label>
        <span className="hidden min-w-[170px] text-center text-[13px] text-on-cloth-muted md:block" aria-live="polite">
          {label(spreads[current])} · {book.pages.length - 1} pages
        </span>
        <button className="btn btn-ghost" onClick={next} disabled={current === spreads.length - 1} aria-label="Next page">Next ›</button>
        {immersive && !nativeFullscreen && !homeScreenApp && (
          <p className="basis-full text-center text-xs text-on-cloth-muted">For full screen without the browser bar, tap Share → Add to Home Screen and open the book from there.</p>
        )}
      </nav>
    </div>
  )
}
