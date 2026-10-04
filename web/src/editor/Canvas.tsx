import { useCallback, useLayoutEffect, useRef, useState, type PointerEvent as RPointerEvent } from 'react'
import {
  cropRect, frameInsets, pageDims, resolveFrame, sizeScale, themeOf, type AssetInfo, type Box, type Design,
  type ImageCrop, type ImageElement, type MemoryBook, type MemoryPage,
} from '../model'
import { Page } from '../render/Page'
import { PX_PER_MM } from '../render/ScaledPage'

const HANDLES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'] as const
type Handle = (typeof HANDLES)[number]
const HANDLE_POS: Record<Handle, string> = {  // position on the selection box + resize cursor
  nw: 'left-0 top-0 cursor-nwse-resize', n: 'left-1/2 top-0 cursor-ns-resize', ne: 'left-full top-0 cursor-nesw-resize',
  e: 'left-full top-1/2 cursor-ew-resize', se: 'left-full top-full cursor-nwse-resize', s: 'left-1/2 top-full cursor-ns-resize',
  sw: 'left-0 top-full cursor-nesw-resize', w: 'left-0 top-1/2 cursor-ew-resize',
}
const SNAP = 1.5 // mm

interface Props {
  /** an AI run is applying changes: no direct editing */
  locked: boolean
  book: MemoryBook
  page: MemoryPage
  index: number
  design: Design
  assets: Record<string, AssetInfo>
  selectedId: string | null
  editingId: string | null
  cropId: string | null
  onSelect: (id: string | null) => void
  onStartEdit: (id: string) => void
  onCommitText: (id: string, text: string) => void
  onToggleCrop: (id: string | null) => void
  onChangeBox: (id: string, box: Box, key: string) => void
  onChangeCrop: (id: string, crop: ImageCrop, key: string) => void
  onDropAsset: (assetId: string, targetId: string | null, at: { x: number; y: number }) => void
}

type Drag =
  | { kind: 'move'; id: string; startX: number; startY: number; box: Box; key: string }
  | { kind: 'resize'; id: string; handle: Handle; startX: number; startY: number; box: Box; key: string }
  | { kind: 'pan'; id: string; startX: number; startY: number; crop: ImageCrop; key: string }

export function Canvas(props: Props) {
  const { book, page, design, selectedId, editingId, cropId } = props
  const [pw, ph] = pageDims(book, design)
  const theme = themeOf(design, book.themeId)
  const stage = useRef<HTMLDivElement>(null)
  const layer = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(0.5)
  const drag = useRef<Drag | null>(null)
  const [guides, setGuides] = useState<{ x: number | null; y: number | null }>({ x: null, y: null })
  const overflowRef = useRef(new Set<string>())
  const [overflowing, setOverflowing] = useState<string[]>([])

  useLayoutEffect(() => {
    const node = stage.current
    if (!node) return
    const fit = () => {
      const pad = window.innerWidth < 700 ? 16 : 56
      const s = Math.min((node.clientWidth - pad) / (pw * PX_PER_MM), (node.clientHeight - pad) / (ph * PX_PER_MM))
      setScale(Math.max(0.1, s))
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(node)
    return () => ro.disconnect()
  }, [pw, ph])

  const onOverflow = useCallback((id: string, over: boolean) => {
    const set = overflowRef.current
    if (over === set.has(id)) return
    if (over) set.add(id); else set.delete(id)
    queueMicrotask(() => setOverflowing([...set]))
  }, [])

  const mm = (px: number) => px / (scale * PX_PER_MM)
  const selected = page.elements.find(e => e.id === selectedId)
  const s = sizeScale(pw, ph)
  const m = { l: theme.margins.side * s, t: theme.margins.top * s, r: pw - theme.margins.side * s, b: ph - theme.margins.bottom * s }

  function snapMove(box: Box): Box {
    const xs = [m.l, pw / 2, m.r], ys = [m.t, ph / 2, m.b]
    let gx: number | null = null, gy: number | null = null
    let { x, y } = box
    for (const [edge, off] of [[x, 0], [x + box.w / 2, box.w / 2], [x + box.w, box.w]] as const) {
      const hit = xs.find(g => Math.abs(g - edge) < SNAP)
      if (hit !== undefined && gx === null) { x = hit - off; gx = hit }
    }
    for (const [edge, off] of [[y, 0], [y + box.h / 2, box.h / 2], [y + box.h, box.h]] as const) {
      const hit = ys.find(g => Math.abs(g - edge) < SNAP)
      if (hit !== undefined && gy === null) { y = hit - off; gy = hit }
    }
    setGuides({ x: gx, y: gy })
    return { ...box, x, y }
  }

  function onPointerDown(e: RPointerEvent) {
    if (e.button !== 0) return
    const target = e.target as HTMLElement
    if (editingId && target.closest('.mb-text-edit')) return
    const handle = target.closest<HTMLElement>('[data-handle]')?.dataset.handle as Handle | undefined
    const elNode = target.closest<HTMLElement>('[data-element-id]')
    const key = `${Date.now()}`
    if (handle && selected) {
      drag.current = { kind: 'resize', id: selected.id, handle, startX: e.clientX, startY: e.clientY, box: selected.box, key }
    } else if (elNode) {
      const id = elNode.dataset.elementId!
      const el = page.elements.find(x => x.id === id)!
      props.onSelect(id)
      if (cropId === id && el.type === 'image') drag.current = { kind: 'pan', id, startX: e.clientX, startY: e.clientY, crop: el.crop, key }
      else if (editingId !== id) drag.current = { kind: 'move', id, startX: e.clientX, startY: e.clientY, box: el.box, key }
    } else {
      props.onSelect(null)
      return
    }
    layer.current?.setPointerCapture(e.pointerId)
  }

  function onPointerMove(e: RPointerEvent) {
    const d = drag.current
    if (!d) return
    const dx = mm(e.clientX - d.startX), dy = mm(e.clientY - d.startY)
    if (Math.abs(dx) + Math.abs(dy) < 0.3) return
    if (d.kind === 'move') {
      const box = e.altKey ? { ...d.box, x: d.box.x + dx, y: d.box.y + dy } : snapMove({ ...d.box, x: d.box.x + dx, y: d.box.y + dy })
      props.onChangeBox(d.id, box, `move-${d.key}`)
    } else if (d.kind === 'resize') {
      const b = { ...d.box }
      const h = d.handle
      if (h.includes('e')) b.w = Math.max(4, d.box.w + dx)
      if (h.includes('s')) b.h = Math.max(4, d.box.h + dy)
      if (h.includes('w')) { b.w = Math.max(4, d.box.w - dx); b.x = d.box.x + d.box.w - b.w }
      if (h.includes('n')) { b.h = Math.max(4, d.box.h - dy); b.y = d.box.y + d.box.h - b.h }
      if (e.shiftKey && h.length === 2) { // keep proportions on corner drags
        const ratio = d.box.w / d.box.h
        b.h = b.w / ratio
        if (h.includes('n')) b.y = d.box.y + d.box.h - b.h
      }
      props.onChangeBox(d.id, b, `resize-${d.key}`)
    } else {
      const el = page.elements.find(x => x.id === d.id) as ImageElement | undefined
      const asset = el?.assetId ? props.assets[el.assetId] : undefined
      if (!el || !asset) return
      const [l, t, r, bt] = frameInsets(resolveFrame(el, theme), el.box.w, el.box.h)
      const { sw, sh } = cropRect(asset.width, asset.height, el.box.w - l - r, el.box.h - t - bt, d.crop)
      const k = (el.box.w - l - r) / sw // mm per source px
      const clamp = (v: number) => Math.min(1, Math.max(0, v))
      // keep the focal point inside the range that actually changes the view
      const fx = clamp(d.crop.x - dx / k / asset.width), fy = clamp(d.crop.y - dy / k / asset.height)
      const minX = sw / 2 / asset.width, minY = sh / 2 / asset.height
      props.onChangeCrop(d.id, { ...d.crop, x: Math.min(Math.max(fx, minX), 1 - minX), y: Math.min(Math.max(fy, minY), 1 - minY) }, `pan-${d.key}`)
    }
  }

  function onPointerUp() {
    drag.current = null
    setGuides({ x: null, y: null })
  }

  function onDoubleClick(e: React.MouseEvent) {
    // pointer capture retargets click events to the layer, so resolve the element under the pointer
    const elNode = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>('[data-element-id]')
    const el = elNode && page.elements.find(x => x.id === elNode.dataset.elementId)
    if (!el) return
    if (el.type === 'text') props.onStartEdit(el.id)
    else if (el.type === 'image' && el.assetId) props.onToggleCrop(cropId === el.id ? null : el.id)
  }

  function onDrop(e: React.DragEvent) {
    const assetId = e.dataTransfer.getData('application/x-asset-id')
    if (!assetId || !layer.current) return
    e.preventDefault()
    const rect = layer.current.getBoundingClientRect()
    const at = { x: (e.clientX - rect.left) / (scale * PX_PER_MM), y: (e.clientY - rect.top) / (scale * PX_PER_MM) }
    const hit = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>('[data-element-id]')
    const target = hit && page.elements.find(x => x.id === hit.dataset.elementId && x.type === 'image')
    props.onDropAsset(assetId, target ? target.id : null, at)
  }

  const handlePx = 9 / scale
  return (
    <div className={`grid min-h-0 flex-1 place-items-center overflow-hidden ${props.locked ? 'pointer-events-none opacity-90' : ''}`} ref={stage}>
      <div className="relative shadow-[0_1px_2px_rgba(0,0,0,.12),0_12px_30px_-6px_rgba(0,0,0,.25)]" style={{ width: pw * PX_PER_MM * scale, height: ph * PX_PER_MM * scale }}>
        <div ref={layer} className={`absolute top-0 left-0 origin-top-left touch-none select-none [&_.mb-el]:cursor-move [&_.mb-text]:overflow-visible [&_.mb-text-edit]:cursor-text [&_.mb-text-edit]:select-text [&_.mb-text.is-empty]:before:pointer-events-none [&_.mb-text.is-empty]:before:absolute [&_.mb-text.is-empty]:before:opacity-35 [&_.mb-text.is-empty]:before:content-[attr(data-placeholder)] [&_.mb-image-inner.is-empty]:grid [&_.mb-image-inner.is-empty]:place-items-center [&_.mb-image-inner.is-empty]:bg-[repeating-linear-gradient(135deg,rgba(0,0,0,.05)_0_2mm,rgba(0,0,0,.085)_2mm_4mm)] ${cropId ? '[&_.mb-image]:cursor-grab' : ''}`}
          style={{ transform: `scale(${scale})`, width: `${pw}mm`, height: `${ph}mm` }}
          onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
          onDoubleClick={onDoubleClick} onDragOver={e => { if (e.dataTransfer.types.includes('application/x-asset-id')) e.preventDefault() }}
          onDrop={onDrop}>
          <Page book={book} page={page} index={props.index} design={design} assets={props.assets} mode="edit"
            editingId={editingId} onTextCommit={props.onCommitText} onOverflow={onOverflow} />
          <div className="pointer-events-none absolute border-dashed border-brass/30" aria-hidden="true"
            style={{ left: `${m.l}mm`, top: `${m.t}mm`, width: `${m.r - m.l}mm`, height: `${m.b - m.t}mm`, borderWidth: 1 / scale }} />
          {overflowing.map(id => {
            const el = page.elements.find(e => e.id === id)
            return el && (
              <div key={id} className="pointer-events-none absolute border-dashed border-danger" title="This text doesn’t fit. Make the box bigger or the text shorter."
                style={{ left: `${el.box.x}mm`, top: `${el.box.y}mm`, width: `${el.box.w}mm`, height: `${el.box.h}mm`, borderWidth: 1.5 / scale }}>
                <span className="absolute right-0 bottom-full rounded-t-[3px] bg-danger px-[.5em] py-[.15em] font-sans whitespace-nowrap text-white" style={{ fontSize: 11 / scale }}>Text doesn’t fit</span>
              </div>
            )
          })}
          {selected && !editingId && (
            <div className="pointer-events-none absolute outline-brass outline-solid" style={{
              left: `${selected.box.x}mm`, top: `${selected.box.y}mm`, width: `${selected.box.w}mm`, height: `${selected.box.h}mm`,
              transform: selected.box.rotation ? `rotate(${selected.box.rotation}deg)` : undefined,
              outlineWidth: 1.5 / scale,
            }}>
              {cropId !== selected.id && HANDLES.map(h => (
                <span key={h} data-handle={h} className={`pointer-events-auto absolute rounded-[2px] border-solid border-brass bg-white ${HANDLE_POS[h]}`}
                  style={{ width: handlePx, height: handlePx, margin: -handlePx / 2, borderWidth: 1.5 / scale }} />
              ))}
              {cropId === selected.id && <span className="absolute top-full left-0 mt-[2mm] rounded bg-ink px-[.7em] py-[.4em] font-sans whitespace-nowrap text-white" style={{ fontSize: 11 / scale }}>Drag to reposition · double-click to finish</span>}
            </div>
          )}
          {guides.x !== null && <div className="pointer-events-none absolute inset-y-0 bg-[#d24f8a]" style={{ left: `${guides.x}mm`, width: 1 / scale }} />}
          {guides.y !== null && <div className="pointer-events-none absolute inset-x-0 bg-[#d24f8a]" style={{ top: `${guides.y}mm`, height: 1 / scale }} />}
        </div>
      </div>
    </div>
  )
}
