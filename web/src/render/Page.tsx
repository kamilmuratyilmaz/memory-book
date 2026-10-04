// The web renderer for one MemoryPage. Used by the editor canvas, page thumbnails and the reader.
// Lays out in physical units (mm / pt) exactly like src/memory_book/pdf.py; callers scale with CSS transforms.

import { memo, useEffect, useRef, useState, type CSSProperties } from 'react'
import { assetUrl } from '../api'
import {
  color, cropRect, frameInsets, resolveFrame, resolveText, sizeScale, type AssetInfo, type Design, type ImageElement,
  type MemoryBook, type MemoryPage, type ShapeElement, type TextElement, type Theme,
} from '../model'
import { pageDims, themeOf } from '../model'

const PT_TO_MM = 25.4 / 72

export interface PageProps {
  book: MemoryBook
  page: MemoryPage
  index: number
  design: Design
  assets: Record<string, AssetInfo>
  variant?: 'thumb' | 'preview'
  mode?: 'read' | 'edit'
  editingId?: string | null
  onTextCommit?: (id: string, text: string) => void
  onOverflow?: (id: string, overflowing: boolean) => void
}

export const Page = memo(function Page(props: PageProps) {
  const { book, page, index, design } = props
  const [w, h] = pageDims(book, design)
  const theme = themeOf(design, book.themeId)
  const scale = sizeScale(w, h)
  const style: CSSProperties = {
    width: `${w}mm`, height: `${h}mm`,
    backgroundColor: color(theme, page.background.color, 'paper'),
    backgroundImage: theme.texture ? 'url(/static/paper.png)' : undefined,
    backgroundSize: '60mm 60mm',
  }
  return (
    <div className="mb-page" style={style} data-page-id={page.id}>
      {page.elements.map(el =>
        el.type === 'text' ? <TextView key={el.id} el={el} theme={theme} scale={scale} {...props} /> :
        el.type === 'image' ? <ImageView key={el.id} el={el} theme={theme} scale={scale} {...props} /> :
        <ShapeView key={el.id} el={el} theme={theme} scale={scale} />)}
      {page.showNumber && page.kind !== 'cover' && index > 0 && <PageNumber theme={theme} scale={scale} h={h} n={index} />}
    </div>
  )
})

function boxStyle(b: { x: number; y: number; w: number; h: number; rotation: number }): CSSProperties {
  return {
    left: `${b.x}mm`, top: `${b.y}mm`, width: `${b.w}mm`, height: `${b.h}mm`,
    transform: b.rotation ? `rotate(${b.rotation}deg)` : undefined,
  }
}

function TextView({ el, theme, scale, mode, editingId, onTextCommit, onOverflow }: PageProps & { el: TextElement; theme: Theme; scale: number }) {
  const rt = resolveText(theme, el.role, el.style, scale)
  const ref = useRef<HTMLDivElement>(null)
  const editing = mode === 'edit' && editingId === el.id
  useEffect(() => {
    if (!onOverflow || !ref.current) return
    const node = ref.current
    onOverflow(el.id, node.offsetHeight > (node.parentElement?.clientHeight ?? Infinity) + 1)
  })
  useEffect(() => {
    if (!editing || !ref.current) return
    const node = ref.current
    node.focus()
    const range = document.createRange()
    range.selectNodeContents(node)
    range.collapse(false)
    getSelection()?.removeAllRanges()
    getSelection()?.addRange(range)
  }, [editing])
  const style: CSSProperties = {
    ...boxStyle(el.box),
    fontFamily: `"${rt.fontFamily}"`, fontSize: `${rt.fontSize}pt`, lineHeight: rt.lineHeight,
    fontWeight: rt.bold ? 700 : 400, fontStyle: rt.italic ? 'italic' : 'normal',
    letterSpacing: rt.letterSpacing ? `${rt.letterSpacing}em` : undefined,
    textTransform: rt.uppercase ? 'uppercase' : undefined,
    textAlign: rt.align, color: rt.color,
    justifyContent: el.valign === 'middle' ? 'center' : el.valign === 'bottom' ? 'flex-end' : 'flex-start',
  }
  const placeholder = mode === 'edit' && !el.text ? `Add ${el.role === 'body' ? 'text' : el.role}` : undefined
  return (
    <div className={`mb-el mb-text${placeholder ? ' is-empty' : ''}`} data-element-id={el.id} style={style}
      data-placeholder={placeholder}>
      {editing ? (
        <div ref={ref} className="mb-text-edit" contentEditable="plaintext-only" suppressContentEditableWarning
          role="textbox" aria-multiline="true" aria-label={`Edit ${el.role}`}
          onBlur={e => onTextCommit?.(el.id, e.currentTarget.innerText.replace(/\n$/, ''))}
          onKeyDown={e => { if (e.key === 'Escape') (e.currentTarget as HTMLElement).blur() }}>
          {el.text}
        </div>
      ) : <div ref={ref} className="mb-text-flow">{el.text}</div>}
    </div>
  )
}

function ImageView({ el, theme, mode, assets, variant = 'preview', scale }: PageProps & { el: ImageElement; theme: Theme; scale: number }) {
  const frame = resolveFrame(el, theme)
  const [l, t, r, b] = frameInsets(frame, el.box.w, el.box.h)
  const iw = el.box.w - l - r, ih = el.box.h - t - b
  const asset = el.assetId ? assets[el.assetId] : undefined
  const [failed, setFailed] = useState(false)
  let img = null
  if (asset && !failed) {
    const { sx, sy, sw } = cropRect(asset.width, asset.height, iw, ih, el.crop)
    const k = iw / sw // mm per source pixel
    img = (
      <img src={assetUrl(asset.id, variant)} alt={el.alt || asset.description || ''} draggable={false}
        loading="lazy" decoding="async" onError={() => setFailed(true)}
        style={{
          width: `${asset.width * k}mm`, height: `${asset.height * k}mm`, left: `${-sx * k}mm`, top: `${-sy * k}mm`,
          filter: theme.image.tone === 'sepia' ? 'sepia(0.45)' : theme.image.tone === 'mono' ? 'grayscale(1)' : undefined,
        }} />
    )
  }
  const missing = el.assetId && !img
  const tapeW = Math.min(el.box.w * 0.38, 24 * scale)
  return (
    <div className={`mb-el mb-image frame-${frame}`} data-element-id={el.id} style={boxStyle(el.box)}>
      <div className={`mb-image-inner${img ? '' : ' is-empty'}${missing ? ' is-missing' : ''}`}
        style={{ left: `${l}mm`, top: `${t}mm`, width: `${iw}mm`, height: `${ih}mm`,
          borderRadius: frame === 'none' && theme.image.radius ? `${theme.image.radius}mm` : undefined }}>
        {img}
        {mode === 'edit' && !img && <span className="mb-image-hint">{missing ? 'Photo unavailable — replace it' : 'Add a photo'}</span>}
        {mode !== 'edit' && missing && <span className="mb-image-hint" role="img" aria-label="Photo unavailable">Photo unavailable</span>}
      </div>
      {theme.image.tape && frame !== 'none' && (
        <div className="mb-tape" style={{ width: `${tapeW}mm`, left: `${(el.box.w - tapeW) / 2}mm`,
          transform: `rotate(${el.box.rotation <= 0 ? -4 : 4}deg)` }} />
      )}
    </div>
  )
}

function ShapeView({ el, theme, scale }: { el: ShapeElement; theme: Theme; scale: number }) {
  const fill = color(theme, el.color, 'accent')
  const { w, h } = el.box
  if (el.kind === 'rect' || el.kind === 'line') {
    return <div className="mb-el" data-element-id={el.id} style={{ ...boxStyle(el.box), background: fill, opacity: el.opacity }} />
  }
  let body
  if (el.kind === 'dot') body = <circle cx={w / 2} cy={h / 2} r={Math.min(w, h) / 2} fill={fill} opacity={el.opacity} />
  else if (el.kind === 'diamond') body = <polygon points={`${w / 2},0 ${w},${h / 2} ${w / 2},${h} 0,${h / 2}`} fill={fill} opacity={el.opacity} />
  else {
    const step = 8 * scale, r = 2.6 * scale, cx = w / 2, cy = h / 2
    const lines = []
    for (let x = step; x < w; x += step) lines.push(<line key={`x${x}`} x1={x} y1={0} x2={x} y2={h} />)
    for (let y = step; y < h; y += step) lines.push(<line key={`y${y}`} x1={0} y1={y} x2={w} y2={y} />)
    body = (
      <>
        <rect width={w} height={h} fill={fill} opacity={el.opacity} />
        <g stroke={fill} strokeOpacity={Math.min(1, el.opacity * 2.2)} strokeWidth={0.25}>{lines}</g>
        <polygon points={`${cx},${cy} ${cx - r * 0.75},${cy - r * 1.6} ${cx + r * 0.75},${cy - r * 1.6}`} fill={fill} />
        <circle cx={cx} cy={cy - r * 2.1} r={r} fill={fill} />
        <circle cx={cx} cy={cy - r * 2.1} r={r * 0.4} fill="#fff" />
      </>
    )
  }
  return (
    <svg className="mb-el" data-element-id={el.id} style={boxStyle(el.box)} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      {body}
    </svg>
  )
}

function PageNumber({ theme, scale, h, n }: { theme: Theme; scale: number; h: number; n: number }) {
  const pn = theme.pageNumber
  const size = pn.fontSize * scale
  const lineMm = size * PT_TO_MM
  const center = h - (theme.margins.bottom * scale) / 2
  return (
    <div className="mb-page-number" aria-hidden="true"
      style={{ top: `${center - lineMm / 2}mm`, height: `${lineMm}mm`, lineHeight: `${lineMm}mm`,
        fontFamily: `"${pn.fontFamily}"`, fontSize: `${size}pt`, color: color(theme, pn.color) }}>
      {n}
    </div>
  )
}

/** Inject @font-face rules for the shared TTF files (same files the PDF embeds). */
export function installFonts(design: Design) {
  if (document.getElementById('mb-fonts')) return
  const variants: [string, number, string][] = [['Regular', 400, 'normal'], ['Bold', 700, 'normal'], ['Italic', 400, 'italic'], ['BoldItalic', 700, 'italic']]
  const css = design.fonts.flatMap(f => variants
    .filter(([, , style]) => style === 'normal' || f.italic)
    .map(([suffix, weight, style]) =>
      `@font-face{font-family:"${f.name}";src:url(/static/fonts/${f.file}-${suffix}.ttf) format("truetype");font-weight:${weight};font-style:${style};font-display:block}`))
  const tag = document.createElement('style')
  tag.id = 'mb-fonts'
  tag.textContent = css.join('\n')
  document.head.appendChild(tag)
}
