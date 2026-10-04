import { useRef, useState } from 'react'
import { assetUrl } from '../api'
import {
  color, resolveText, sizeScale, pageDims, themeOf, type Align, type AssetInfo, type Box, type Design, type ImageElement,
  type MemoryBook, type MemoryPage, type PageElement, type ShapeElement, type TextElement, type TextStyle, type Theme,
} from '../model'

export type Tab = 'chat' | 'design' | 'photos' | 'book'

interface Props {
  /** shown as a bottom sheet on small screens */
  open: boolean
  tab: Tab
  setTab: (t: Tab) => void
  book: MemoryBook
  page: MemoryPage
  design: Design
  assets: Record<string, AssetInfo>
  selected: PageElement | null
  busy: boolean
  onElement: (patch: Partial<PageElement> | ((el: PageElement) => PageElement), key?: string) => void
  onElementAction: (action: 'delete' | 'duplicate' | 'front' | 'back') => void
  onPage: (patch: Partial<MemoryPage>, key?: string) => void
  onTemplate: (template: string) => void
  onBook: (patch: Partial<MemoryBook>, key?: string) => void
  onUsePhoto: (assetId: string) => void
  onUpload: (files: File[]) => void
  chat: React.ReactNode
}

export function Inspector(p: Props) {
  return (
    <aside className={`row-start-5 min-h-0 flex-col border-line bg-panel lg:col-start-3 lg:flex lg:border-l ${p.open ? 'flex' : 'hidden'} max-lg:fixed max-lg:inset-x-0 max-lg:bottom-0 max-lg:z-20 max-lg:max-h-[62dvh] max-lg:overflow-hidden max-lg:rounded-t-2xl max-lg:border-t max-lg:border-line max-lg:shadow-[0_-12px_40px_rgba(0,0,0,.2)]`} aria-label="Properties">
      <div className="flex border-b border-line bg-white" role="tablist">
        {(['chat', 'design', 'photos', 'book'] as Tab[]).map(t => (
          <button key={t} role="tab" aria-selected={p.tab === t} className={`flex-1 cursor-pointer border-b-2 px-1.5 py-2.5 text-[13.5px] font-semibold ${p.tab === t ? 'border-brass text-ink' : 'border-transparent text-muted'}`} onClick={() => p.setTab(t)}>
            {t === 'chat' ? 'Chat' : t === 'design' ? (p.selected ? 'Selection' : 'Page') : t === 'photos' ? 'Photos' : 'Book'}
          </button>
        ))}
      </div>
      {/* the chat stays mounted so a running edit survives switching tabs */}
      <div className="flex min-h-0 flex-1 [&[hidden]]:hidden" hidden={p.tab !== 'chat'}>{p.chat}</div>
      <div className="overflow-y-auto px-4 pt-1 pb-6 [&[hidden]]:hidden" hidden={p.tab === 'chat'}>
        {p.tab === 'design' && (p.selected ? <ElementPanel {...p} el={p.selected} /> : <PagePanel {...p} />)}
        {p.tab === 'photos' && <PhotosPanel {...p} />}
        {p.tab === 'book' && <BookPanel {...p} />}
      </div>
    </aside>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="grid gap-1.5 border-b border-line py-3.5 last:border-b-0 [&>.btn]:justify-self-start"><h3 className="mb-1 text-[13px] font-bold">{title}</h3>{children}</section>
}

const SWATCH = 'relative size-7 cursor-pointer rounded-full border border-black/20 bg-white p-0'
const SWATCH_ON = 'shadow-[0_0_0_2px_var(--color-panel),0_0_0_4px_var(--color-brass)]'

function Swatches({ theme, value, onChange, allowNone }: { theme: Theme; value: string | null | undefined; onChange: (v: string | null) => void; allowNone?: string }) {
  const keys = ['paper', 'text', 'muted', 'accent', 'rule'] as const
  const custom = value && !(value in theme.colors) ? value : ''
  return (
    <div className="flex flex-wrap gap-1.5">
      {allowNone && <button className={`${SWATCH} bg-[linear-gradient(135deg,#fff_45%,var(--color-danger)_46%_54%,#fff_55%)] ${!value ? SWATCH_ON : ''}`} onClick={() => onChange(null)} aria-label={allowNone} title={allowNone} />}
      {keys.map(k => (
        <button key={k} className={`${SWATCH} ${value === k ? SWATCH_ON : ''}`} style={{ background: theme.colors[k] }}
          onClick={() => onChange(k)} aria-label={`Theme ${k} colour`} title={`Theme ${k}`} />
      ))}
      <label className={`${SWATCH} overflow-hidden bg-[conic-gradient(#e66,#ec6,#6c6,#6cc,#66e,#c6c,#e66)] ${custom ? SWATCH_ON : ''}`} title="Custom colour" style={custom ? { background: custom } : undefined}>
        <input type="color" className="absolute inset-0 cursor-pointer opacity-0" value={custom || color(theme, value)} onChange={e => onChange(e.target.value)} aria-label="Custom colour" />
      </label>
    </div>
  )
}

function ElementPanel(p: Props & { el: PageElement }) {
  const theme = themeOf(p.design, p.book.themeId)
  const { el } = p
  const label = el.type === 'text' ? 'Text' : el.type === 'image' ? 'Photo' : 'Decoration'
  return (
    <>
      {el.type === 'text' && <TextPanel {...p} el={el} theme={theme} />}
      {el.type === 'image' && <ImagePanel {...p} el={el} />}
      {el.type === 'shape' && <ShapePanel {...p} el={el} theme={theme} />}
      <Section title="Position & size">
        <BoxFields box={el.box} onChange={(box, key) => p.onElement({ box }, key)} />
      </Section>
      <Section title={label}>
        <div className="flex flex-wrap items-center gap-1.5">
          <button className="btn btn-quiet btn-sm" onClick={() => p.onElementAction('front')}>Bring to front</button>
          <button className="btn btn-quiet btn-sm" onClick={() => p.onElementAction('back')}>Send to back</button>
          <button className="btn btn-quiet btn-sm" onClick={() => p.onElementAction('duplicate')}>Duplicate</button>
          <button className="btn btn-quiet btn-sm btn-danger-text" onClick={() => p.onElementAction('delete')}>Delete</button>
        </div>
      </Section>
    </>
  )
}

function TextPanel(p: Props & { el: TextElement; theme: Theme }) {
  const { el, theme } = p
  const [w, h] = pageDims(p.book, p.design)
  const rt = resolveText(theme, el.role, el.style, sizeScale(w, h))
  const setStyle = (patch: Partial<TextStyle>, key?: string) =>
    p.onElement(e => ({ ...(e as TextElement), style: { ...(e as TextElement).style, ...patch } }), key)
  return (
    <>
      <Section title="Text">
        <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Content</span>
          <textarea className="input" rows={4} value={el.text} onChange={e => p.onElement({ text: e.target.value } as Partial<TextElement>, `text-${el.id}`)} />
        </label>
        <p className="hint">Tip: double-click text on the page to type directly.</p>
      </Section>
      <Section title="Typography">
        <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Style</span>
          <select className="input" value={el.role} onChange={e => p.onElement({ role: e.target.value } as Partial<TextElement>)}>
            {(['title', 'subtitle', 'heading', 'body', 'caption', 'quote', 'meta'] as const).map(r => (
              <option key={r} value={r}>{{ title: 'Title', subtitle: 'Subtitle', heading: 'Heading', body: 'Story text', caption: 'Caption', quote: 'Quote', meta: 'Small label' }[r]}</option>
            ))}
          </select>
        </label>
        <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Typeface</span>
          <select className="input" value={rt.fontFamily} onChange={e => setStyle({ fontFamily: e.target.value })}>
            {p.design.fonts.map(f => <option key={f.name} value={f.name} style={{ fontFamily: `"${f.name}"` }}>{f.name}</option>)}
          </select>
        </label>
        <div className="flex flex-wrap items-end gap-2">
          <label className="mb-3 flex min-w-0 flex-col gap-1 mb-2 [&_input]:max-w-[92px]"><span className="label">Size (pt)</span>
            <input className="input" type="number" min={4} max={200} step={0.5} value={Math.round(rt.fontSize * 10) / 10}
              onChange={e => e.target.value && setStyle({ fontSize: Math.max(4, Number(e.target.value)) }, `size-${el.id}`)} />
          </label>
          <div className="toggles" role="group" aria-label="Emphasis">
            <button className="toggle" aria-pressed={rt.bold} onClick={() => setStyle({ bold: !rt.bold })}><b>B</b></button>
            <button className="toggle" aria-pressed={rt.italic} onClick={() => setStyle({ italic: !rt.italic })}><i>I</i></button>
            <button className="toggle" aria-pressed={rt.uppercase} onClick={() => setStyle({ uppercase: !rt.uppercase })} title="Capitals">Aa</button>
          </div>
        </div>
        <div className="toggles" role="group" aria-label="Alignment">
          {(['left', 'center', 'right', 'justify'] as Align[]).map(a => (
            <button key={a} className="toggle" aria-pressed={rt.align === a} onClick={() => setStyle({ align: a })}>
              {{ left: 'Left', center: 'Centre', right: 'Right', justify: 'Justify' }[a]}
            </button>
          ))}
        </div>
        <div className="toggles" role="group" aria-label="Vertical position">
          {(['top', 'middle', 'bottom'] as const).map(v => (
            <button key={v} className="toggle" aria-pressed={el.valign === v}
              onClick={() => p.onElement({ valign: v } as Partial<TextElement>)}>{v[0].toUpperCase() + v.slice(1)}</button>
          ))}
        </div>
        <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Line spacing · {rt.lineHeight.toFixed(2)}</span>
          <input type="range" className="w-full" min={0.9} max={2.4} step={0.05} value={rt.lineHeight}
            onChange={e => setStyle({ lineHeight: Number(e.target.value) }, `lh-${el.id}`)} />
        </label>
        <div className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Colour</span>
          <Swatches theme={theme} value={el.style.color ?? theme.text[el.role].color} onChange={v => setStyle({ color: v })} />
        </div>
        <button className="btn btn-quiet btn-sm" onClick={() => p.onElement({ style: {} } as Partial<TextElement>)}>Reset to theme style</button>
      </Section>
    </>
  )
}

function ImagePanel(p: Props & { el: ImageElement }) {
  const { el } = p
  const asset = el.assetId ? p.assets[el.assetId] : undefined
  const setCrop = (patch: Partial<ImageElement['crop']>, key: string) =>
    p.onElement(e => ({ ...(e as ImageElement), crop: { ...(e as ImageElement).crop, ...patch } }), key)
  return (
    <Section title="Photo">
      {asset ? <p className="hint">{asset.filename} · {asset.width}×{asset.height}px</p> :
        <p className="hint">{el.assetId ? 'This photo is no longer available.' : 'Empty photo frame.'} Pick one in the Photos tab, or drag a photo onto it.</p>}
      <button className="btn btn-quiet btn-sm" onClick={() => p.setTab('photos')}>{asset ? 'Replace photo' : 'Choose photo'}</button>
      {asset && (
        <>
          <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Zoom · {el.crop.zoom.toFixed(2)}×</span>
            <input type="range" className="w-full" min={1} max={4} step={0.01} value={el.crop.zoom} onChange={e => setCrop({ zoom: Number(e.target.value) }, `zoom-${el.id}`)} />
          </label>
          <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Horizontal focus</span>
            <input type="range" className="w-full" min={0} max={1} step={0.01} value={el.crop.x} onChange={e => setCrop({ x: Number(e.target.value) }, `fx-${el.id}`)} />
          </label>
          <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Vertical focus</span>
            <input type="range" className="w-full" min={0} max={1} step={0.01} value={el.crop.y} onChange={e => setCrop({ y: Number(e.target.value) }, `fy-${el.id}`)} />
          </label>
          <p className="hint">Double-click the photo on the page to drag it into place.</p>
          <button className="btn btn-quiet btn-sm" onClick={() => setCrop({ x: 0.5, y: 0.5, zoom: 1 }, `reset-${Date.now()}`)}>Reset crop</button>
          <button className="btn btn-quiet btn-sm" onClick={() => p.onElement(e => {
            const img = e as ImageElement
            return { ...img, box: { ...img.box, h: img.box.w * (asset.height / asset.width) } }
          })}>Fit frame to photo</button>
        </>
      )}
      <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Frame</span>
        <select className="input" value={el.frame} onChange={e => p.onElement({ frame: e.target.value } as Partial<ImageElement>)}>
          <option value="theme">Theme default</option><option value="none">No frame</option>
          <option value="mat">White border</option><option value="polaroid">Instant photo</option>
        </select>
      </label>
      <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Description (for screen readers)</span>
        <input className="input" value={el.alt} placeholder={asset?.description || 'Describe the photo'} onChange={e => p.onElement({ alt: e.target.value } as Partial<ImageElement>, `alt-${el.id}`)} />
      </label>
    </Section>
  )
}

function ShapePanel(p: Props & { el: ShapeElement; theme: Theme }) {
  return (
    <Section title="Decoration">
      <div className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Colour</span>
        <Swatches theme={p.theme} value={p.el.color} onChange={v => p.onElement({ color: v } as Partial<ShapeElement>)} />
      </div>
      <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Opacity · {Math.round(p.el.opacity * 100)}%</span>
        <input type="range" className="w-full" min={0.05} max={1} step={0.05} value={p.el.opacity}
          onChange={e => p.onElement({ opacity: Number(e.target.value) } as Partial<ShapeElement>, `op-${p.el.id}`)} />
      </label>
    </Section>
  )
}

function BoxFields({ box, onChange }: { box: Box; onChange: (b: Box, key: string) => void }) {
  const field = (k: keyof Box, label: string) => (
    <label className="mb-3 flex min-w-0 flex-col gap-1 mb-2 [&_input]:max-w-[92px]"><span className="label">{label}</span>
      <input className="input" type="number" step={0.5} value={Math.round(box[k] * 10) / 10}
        onChange={e => e.target.value !== '' && onChange({ ...box, [k]: k === 'w' || k === 'h' ? Math.max(3, Number(e.target.value)) : Number(e.target.value) }, `box-${k}`)} />
    </label>
  )
  return (
    <div className="grid grid-cols-2 gap-x-2.5">
      {field('x', 'Left mm')}{field('y', 'Top mm')}{field('w', 'Width mm')}{field('h', 'Height mm')}{field('rotation', 'Rotate °')}
    </div>
  )
}

function PagePanel(p: Props) {
  const theme = themeOf(p.design, p.book.themeId)
  const isCover = p.page.kind === 'cover'
  const templates = p.design.templates.filter(t => t.cover === isCover)
  return (
    <>
      <Section title="Layout">
        <p className="hint">Choosing a layout rearranges this page. Nothing is lost — extra photos or text move to a new page.</p>
        <div className="grid grid-cols-3 gap-2">
          {templates.map(t => (
            <button key={t.id} className={`grid cursor-pointer justify-items-center gap-1 rounded-md border bg-white px-1 pt-2 pb-1.5 text-center text-[11px] leading-tight hover:not-disabled:border-brass disabled:opacity-50 ${p.page.template === t.id ? 'border-brass shadow-[inset_0_0_0_1px_var(--color-brass)]' : 'border-line'}`} disabled={p.busy}
              onClick={() => p.onTemplate(t.id)} aria-pressed={p.page.template === t.id}>
              <TemplateIcon id={t.id} />
              <span>{t.name}</span>
            </button>
          ))}
        </div>
      </Section>
      <Section title="Background">
        <Swatches theme={theme} value={p.page.background.color} allowNone="Theme paper"
          onChange={v => p.onPage({ background: { color: v } })} />
      </Section>
      {!isCover && (
        <Section title="Page number">
          <label className="flex cursor-pointer items-center gap-2.5"><input type="checkbox" className="accent-brass" checked={p.page.showNumber} onChange={e => p.onPage({ showNumber: e.target.checked })} /> Show page number</label>
        </Section>
      )}
    </>
  )
}

/** Tiny schematic of each layout, drawn from the same idea as the template slots. */
function TemplateIcon({ id }: { id: string }) {
  const P = (x: number, y: number, w: number, h: number, k: number) => <rect key={k} x={x} y={y} width={w} height={h} rx={0.6} className="fill-[#8fa79a]" />
  const T = (x: number, y: number, w: number, k: number, h = 1.4) => <rect key={k} x={x} y={y} width={w} height={h} rx={0.5} className="fill-[#6f7a74]" />
  const shapes: Record<string, React.ReactNode[]> = {
    'cover': [P(3, 3, 18, 19, 1), T(3, 24, 14, 2, 2.2), T(3, 27.5, 9, 3)],
    'cover-full': [P(0, 0, 24, 32, 1), <rect key="s" x={0} y={22} width={24} height={10} className="fill-[#f4f1ea] stroke-[#cfd5cd] stroke-[0.5]" />, T(3, 24, 14, 2, 2.2), T(3, 27.5, 9, 3)],
    'cover-text': [T(5, 12, 14, 1, 2.6), T(7, 16, 10, 2)],
    'full-photo': [P(0, 0, 24, 32, 1)],
    'photo-text': [P(3, 3, 18, 16, 1), T(3, 21, 12, 2, 2), T(3, 24.5, 18, 3), T(3, 27, 16, 4)],
    'two-photos': [P(3, 3, 18, 12, 1), P(3, 16, 18, 11, 2), T(3, 28.5, 12, 3)],
    'two-portraits': [P(3, 4, 8.7, 17, 1), P(12.3, 8, 8.7, 17, 2), T(3, 27, 12, 3)],
    'three-collage': [P(3, 3, 18, 14, 1), P(3, 18, 8.7, 9, 2), P(12.3, 18, 8.7, 9, 3), T(3, 28.5, 12, 4)],
    'four-grid': [P(3, 3, 8.7, 12, 1), P(12.3, 3, 8.7, 12, 2), P(3, 16, 8.7, 12, 3), P(12.3, 16, 8.7, 12, 4)],
    'quote': [T(10, 11, 4, 1, 0.6), T(4, 14, 16, 2, 2), T(6, 17.5, 12, 3, 2), T(9, 21, 6, 4)],
    'timeline': [T(3, 3, 12, 1, 2), <rect key="l" x={3.7} y={7} width={0.4} height={22} className="fill-[#6f7a74]" />, T(6, 8, 14, 2), T(6, 14, 12, 3), T(6, 20, 14, 4)],
    'travel': [T(3, 3, 6, 1, 1), T(3, 5, 14, 2, 2), P(3, 8.5, 18, 13, 3), T(3, 23.5, 11, 4), T(3, 26, 10, 5), T(16, 23.5, 5, 6)],
    'chapter-opener': [T(9, 11, 6, 1, 1), T(4, 13.5, 16, 2, 3), T(10, 18, 4, 3, 0.5), T(6, 20, 12, 4)],
    'chapter-photo': [P(0, 0, 24, 15, 1), T(6, 18, 12, 2, 2.4), T(7, 23, 10, 3)],
    'minimal-text': [T(4, 4, 4, 1, 0.5), T(4, 6, 12, 2, 2), T(4, 10, 16, 3), T(4, 13, 15, 4), T(4, 16, 16, 5), T(4, 19, 12, 6)],
    'closing': [P(7, 3, 10, 11, 1), T(7, 17, 10, 2, 2), T(5, 21, 14, 3)],
    'map': [<rect key="m" x={3} y={3} width={18} height={14} className="fill-[#c9d6cf]" />, <circle key="c" cx={12} cy={9} r={1.4} className="fill-brass" />, T(3, 19, 12, 2, 2.2), T(3, 23, 18, 3)],
  }
  return <svg className="h-12 w-9" viewBox="0 0 24 32" aria-hidden="true"><rect width={24} height={32} className="fill-[#f4f1ea] stroke-[#cfd5cd] stroke-[0.5]" />{shapes[id]}</svg>
}

function PhotosPanel(p: Props) {
  const input = useRef<HTMLInputElement>(null)
  const used = new Set(p.book.pages.flatMap(pg => pg.elements.flatMap(e => (e.type === 'image' && e.assetId ? [e.assetId] : []))))
  const library = [...new Set([...p.book.metadata.assetIds, ...used])]
  const selectedImage = p.selected?.type === 'image'
  return (
    <Section title={`Your photos · ${library.length}`}>
      <p className="hint">{selectedImage ? 'Click a photo to put it in the selected frame.' : 'Click a photo to add it to this page, or drag it onto a photo frame.'}</p>
      <div className="mb-2 grid grid-cols-3 gap-1.5 sm:grid-cols-4 lg:grid-cols-3">
        {library.map(id => {
          const a = p.assets[id]
          return (
            <button key={id} className="relative aspect-square cursor-pointer overflow-hidden rounded-[3px] bg-mist hover:[&_img]:opacity-85" draggable={!!a} disabled={!a}
              onDragStart={e => { e.dataTransfer.setData('application/x-asset-id', id); e.dataTransfer.effectAllowed = 'copy' }}
              onClick={() => p.onUsePhoto(id)} aria-label={a ? `${selectedImage ? 'Use' : 'Add'} ${a.filename}${used.has(id) ? ' (in book)' : ''}` : 'Missing photo'}>
              {a ? <img src={assetUrl(id, 'thumb')} alt="" loading="lazy" className="size-full object-cover" /> : <span className="text-xs text-muted">Missing</span>}
              {used.has(id) && <span className="absolute bottom-1 left-1 rounded-[3px] bg-ink/80 px-1.5 py-px text-[10px] text-white" aria-hidden="true">In book</span>}
            </button>
          )
        })}
      </div>
      <button className="btn btn-quiet btn-sm" onClick={() => input.current?.click()}>Upload more photos</button>
      <input ref={input} type="file" accept="image/*" multiple hidden onChange={e => { p.onUpload([...(e.target.files ?? [])]); e.target.value = '' }} />
    </Section>
  )
}

function BookPanel(p: Props) {
  const [confirmSize, setConfirmSize] = useState<string | null>(null)
  return (
    <>
      <Section title="Cover text">
        <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Title</span>
          <input className="input" value={p.book.title} onChange={e => p.onBook({ title: e.target.value }, 'title')} /></label>
        <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Subtitle</span>
          <input className="input" value={p.book.subtitle} onChange={e => p.onBook({ subtitle: e.target.value }, 'subtitle')} /></label>
        <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Made by</span>
          <input className="input" value={p.book.author} onChange={e => p.onBook({ author: e.target.value }, 'author')} /></label>
      </Section>
      <Section title="Theme">
        <div className="grid gap-1.5">
          {Object.entries(p.design.themes).map(([id, t]) => (
            <button key={id} className={`flex cursor-pointer items-center gap-2.5 rounded-md border bg-white p-1.5 text-left ${p.book.themeId === id ? 'border-brass shadow-[inset_0_0_0_1px_var(--color-brass)]' : 'border-line'}`} aria-pressed={p.book.themeId === id}
              onClick={() => p.onBook({ themeId: id })}>
              <span className="relative grid size-[46px] flex-none place-items-center rounded-[3px] border border-black/10 text-xl" style={{ background: t.colors.paper, color: t.colors.text, fontFamily: `"${t.text.title.fontFamily}"` }}>
                Aa<i className="absolute inset-x-2 bottom-[7px] h-0.5" style={{ background: t.colors.accent }} />
              </span>
              <span><strong className="block text-[13.5px]">{t.name}</strong><small className="block text-xs leading-snug text-muted">{t.description}</small></span>
            </button>
          ))}
        </div>
      </Section>
      <Section title="Book size">
        <div className="segmented">
          {(['A5', 'A4', 'Square'] as const).map(s => (
            <label key={s} className="segment"><input type="radio" className="sr-only" name="book-size" checked={p.book.pageSize === s}
              onChange={() => setConfirmSize(s)} />{s}</label>
          ))}
        </div>
        {confirmSize && confirmSize !== p.book.pageSize && (
          <div className="notice">
            <p>Resize every page to {confirmSize}? Everything is scaled to fit; you can undo this.</p>
            <div className="flex flex-wrap items-center gap-1.5">
              <button className="btn btn-brass btn-sm" onClick={() => { p.onBook({ pageSize: confirmSize as MemoryBook['pageSize'] }); setConfirmSize(null) }}>Resize book</button>
              <button className="btn btn-quiet btn-sm" onClick={() => setConfirmSize(null)}>Cancel</button>
            </div>
          </div>
        )}
      </Section>
    </>
  )
}
