// TypeScript mirror of src/memory_book/model.py — the MemoryBook document model.
// Geometry is millimetres on a fixed physical page; font sizes are points.

export type PageSize = 'A5' | 'A4' | 'Square'
export type Orientation = 'portrait' | 'landscape'
export type TextRole = 'title' | 'subtitle' | 'heading' | 'body' | 'caption' | 'quote' | 'meta'
export type Align = 'left' | 'center' | 'right' | 'justify'

export interface Box { x: number; y: number; w: number; h: number; rotation: number }

export interface TextStyle {
  fontFamily?: string | null
  fontSize?: number | null
  color?: string | null
  align?: Align | null
  bold?: boolean | null
  italic?: boolean | null
  lineHeight?: number | null
  letterSpacing?: number | null
  uppercase?: boolean | null
}

export interface TextElement {
  type: 'text'; id: string; box: Box; role: TextRole; text: string; style: TextStyle
  valign: 'top' | 'middle' | 'bottom'; slot?: string | null
}
export interface ImageCrop { x: number; y: number; zoom: number }
export interface ImageElement {
  type: 'image'; id: string; box: Box; assetId: string | null; crop: ImageCrop
  frame: 'theme' | 'none' | 'mat' | 'polaroid'; alt: string; slot?: string | null
}
export interface ShapeElement {
  type: 'shape'; id: string; box: Box; kind: 'line' | 'rect' | 'dot' | 'diamond' | 'map'
  color: string | null; opacity: number; slot?: string | null
}
export type PageElement = TextElement | ImageElement | ShapeElement

export interface MemoryPage {
  id: string; kind: 'cover' | 'page'; chapterId: string | null; template: string | null
  elements: PageElement[]; background: { color: string | null }; showNumber: boolean
}
export interface Chapter { id: string; title: string; summary: string }
export interface MemoryBook {
  schemaVersion: number; id: string; title: string; subtitle: string; author: string
  pageSize: PageSize; orientation: Orientation; themeId: string
  chapters: Chapter[]; pages: MemoryPage[]
  metadata: { createdAt: string; updatedAt: string; generatedBy: string; mood: string; language: string;
    assetIds: string[]; notice: string }
}

export interface AssetInfo { id: string; filename: string; width: number; height: number; takenAt: string | null; description: string }

// --- design (served by /api/design from static/themes.json) ---
export interface RoleStyle {
  fontFamily: string; fontSize: number; bold?: boolean; italic?: boolean; lineHeight?: number
  letterSpacing?: number; uppercase?: boolean; align?: Align; color?: string
}
export interface Theme {
  name: string; description: string
  colors: Record<'paper' | 'text' | 'muted' | 'accent' | 'rule', string>
  texture: boolean
  margins: { top: number; side: number; bottom: number }
  image: { frame: 'none' | 'mat' | 'polaroid'; radius: number; tone: 'none' | 'sepia' | 'mono'; tilt: number; tape: boolean }
  ornament: string
  text: Record<TextRole, RoleStyle>
  pageNumber: { fontFamily: string; fontSize: number; color: string }
}
export interface Design {
  fonts: { name: string; file: string; italic: boolean }[]
  themes: Record<string, Theme>
  templates: { id: string; name: string; photos: number; cover: boolean }[]
  pageSizes: Record<PageSize, [number, number]>
  ai: string
}

export function pageDims(book: Pick<MemoryBook, 'pageSize' | 'orientation'>, design: Design): [number, number] {
  const [w, h] = design.pageSizes[book.pageSize]
  return book.orientation === 'landscape' ? [h, w] : [w, h]
}

export const sizeScale = (w: number, h: number) => Math.min(w, h) / 148

export function themeOf(design: Design, id: string): Theme {
  return design.themes[id] ?? design.themes.editorial
}

export function color(theme: Theme, value: string | null | undefined, fallback = 'text'): string {
  const v = value || fallback
  return (theme.colors as Record<string, string>)[v] ?? v
}

export interface ResolvedText {
  fontFamily: string; fontSize: number; bold: boolean; italic: boolean; lineHeight: number
  letterSpacing: number; uppercase: boolean; align: Align; color: string
}

/** Mirrors design.resolve_text() in Python. */
export function resolveText(theme: Theme, role: TextRole, style: TextStyle, scale: number): ResolvedText {
  const base = theme.text[role]
  const pick = <K extends keyof TextStyle & keyof RoleStyle>(k: K, d: RoleStyle[K]) =>
    (style[k] ?? base[k] ?? d) as NonNullable<RoleStyle[K]>
  return {
    fontFamily: pick('fontFamily', 'Lora'),
    fontSize: style.fontSize ?? Math.round(base.fontSize * scale * 100) / 100,
    bold: !!pick('bold', false),
    italic: !!pick('italic', false),
    lineHeight: pick('lineHeight', 1.4),
    letterSpacing: pick('letterSpacing', 0),
    uppercase: !!pick('uppercase', false),
    align: pick('align', 'left'),
    color: color(theme, style.color ?? base.color),
  }
}

/** Mirrors model.crop_rect(): the source rect (px) a cover-fit box shows. */
export function cropRect(imgW: number, imgH: number, boxW: number, boxH: number, crop: ImageCrop) {
  const boxAr = boxW / boxH, imgAr = imgW / imgH
  let sw: number, sh: number
  if (imgAr > boxAr) { sh = imgH; sw = imgH * boxAr } else { sw = imgW; sh = imgW / boxAr }
  sw /= crop.zoom; sh /= crop.zoom
  const sx = Math.min(Math.max(crop.x * imgW - sw / 2, 0), imgW - sw)
  const sy = Math.min(Math.max(crop.y * imgH - sh / 2, 0), imgH - sh)
  return { sx, sy, sw, sh }
}

/** Mirrors pdf.frame_insets(): (left, top, right, bottom) in mm. */
export function frameInsets(frame: string, w: number, h: number): [number, number, number, number] {
  if (frame === 'mat') { const p = Math.min(Math.max(Math.min(w, h) * 0.035, 1.2), 4); return [p, p, p, p] }
  if (frame === 'polaroid') { const p = Math.min(Math.max(Math.min(w, h) * 0.05, 1.5), 5); return [p, p, p, p * 3.2] }
  return [0, 0, 0, 0]
}

export function resolveFrame(el: ImageElement, theme: Theme): string {
  const frame = el.frame === 'theme' ? theme.image.frame : el.frame
  return Math.min(el.box.w, el.box.h) >= 12 ? frame : 'none'
}

export const uid = (prefix: string) => `${prefix}_${crypto.randomUUID().replace(/-/g, '').slice(0, 10)}`

export const newBox = (x: number, y: number, w: number, h: number): Box => ({ x, y, w, h, rotation: 0 })
