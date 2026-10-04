import { describe, expect, it } from 'vitest'
import parity from '../../tests/fixtures/parity.json'
import themes from '../../src/memory_book/static/themes.json'
import { cropRect, resolveText, type ImageCrop, type TextRole, type TextStyle, type Theme } from './model'

describe('parity with the Python renderer', () => {
  it('crops images exactly like model.crop_rect', () => {
    for (const c of parity.crops) {
      const r = cropRect(c.img[0], c.img[1], c.box[0], c.box[1], c.crop as ImageCrop)
      expect([r.sx, r.sy, r.sw, r.sh].map(v => +v.toFixed(6))).toEqual(c.rect.map(v => +v.toFixed(6)))
    }
  })

  it('resolves text styles exactly like design.resolve_text', () => {
    for (const c of parity.texts) {
      const theme = (themes.themes as Record<string, unknown>)[c.theme] as Theme
      expect(resolveText(theme, c.role as TextRole, c.style as TextStyle, c.scale)).toEqual(c.resolved)
    }
  })
})
