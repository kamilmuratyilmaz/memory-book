import type { ReactNode } from 'react'
import { pageDims } from '../model'
import { Page, type PageProps } from './Page'

export const PX_PER_MM = 96 / 25.4

/** Renders a page at its physical size, scaled to a target width in CSS px. Logical geometry never changes. */
export function ScaledPage({ width, children, ...props }: PageProps & { width: number; children?: ReactNode }) {
  const [w, h] = pageDims(props.book, props.design)
  const scale = width / (w * PX_PER_MM)
  return (
    <div className="mb-scaled" style={{ width, height: h * PX_PER_MM * scale }}>
      <div className="mb-scaled-inner" style={{ transform: `scale(${scale})` }}>
        <Page {...props} />
        {children}
      </div>
    </div>
  )
}
