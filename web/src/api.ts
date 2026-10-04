import type { AssetInfo, Design, MemoryBook, MemoryPage, Orientation, PageSize } from './model'

export class ApiError extends Error {
  status: number
  body: unknown
  constructor(status: number, message: string, body?: unknown) {
    super(message)
    this.status = status
    this.body = body
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      ...init,
      headers: init?.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined,
    })
  } catch {
    throw new ApiError(0, 'You appear to be offline. Your work is kept on this device until the connection returns.')
  }
  const body = res.headers.get('content-type')?.includes('json') ? await res.json() : await res.text()
  if (!res.ok) {
    const detail = typeof body === 'object' && body && 'detail' in body ? String((body as { detail: unknown }).detail) : ''
    throw new ApiError(res.status, detail || `Request failed (${res.status})`, body)
  }
  return body as T
}

const json = (method: string, data: unknown): RequestInit => ({ method, body: JSON.stringify(data) })

export interface Job<R = Record<string, unknown>> {
  id: string; status: 'running' | 'done' | 'error'; stage: string | null; result: R | null; error: string | null
}

export interface BookSummary {
  id: string; title: string; subtitle: string; themeId: string; pageSize: PageSize; orientation: Orientation
  pageCount: number; coverAssetId: string | null; coverPage: MemoryPage | null; updatedAt: string
}

export interface GenerationInput {
  title?: string; author?: string; story?: string; date?: string; location?: string; people?: string; mood?: string
  themeId?: string; pageSize?: string; orientation?: string; targetPages?: number | null; photoIds: string[]
  notes?: { text: string; date?: string; location?: string }[]
}

export const api = {
  design: () => request<Design>('/api/design'),
  books: () => request<{ books: BookSummary[]; assets: Record<string, AssetInfo> }>('/api/books'),
  book: (id: string) => request<{ book: MemoryBook; version: number; assets: Record<string, AssetInfo> }>(`/api/books/${id}`),
  save: (book: MemoryBook, version: number | null) =>
    request<{ version: number }>(`/api/books/${book.id}`, json('PUT', { book, version })),
  deleteBook: (id: string) => request(`/api/books/${id}`, { method: 'DELETE' }),
  generate: (input: GenerationInput) =>
    request<{ jobId: string; stages: [string, string][] }>('/api/books/generate', json('POST', input)),
  job: <R>(id: string) => request<Job<R>>(`/api/jobs/${id}`),
  upload: (files: File[]) => {
    const form = new FormData()
    files.forEach(f => form.append('files', f))
    return request<{ assets: AssetInfo[]; errors: string[] }>('/api/assets', { method: 'POST', body: form })
  },
  layout: (book: MemoryBook, template: string, page?: MemoryPage) =>
    request<{ pages: MemoryPage[] }>('/api/layout', json('POST', { book, template, page })),
  resize: (book: MemoryBook, pageSize: string) => request<{ book: MemoryBook }>('/api/resize', json('POST', { book, pageSize })),
  exportPdf: (id: string, bleed: boolean) => request<{ jobId: string }>(`/api/books/${id}/export`, json('POST', { bleed })),
}

export const assetUrl = (id: string, variant: 'thumb' | 'preview' | 'original') => `/api/assets/${id}/${variant}`

/** Poll a background job until it finishes; reports each stage change. */
export async function waitForJob<R>(id: string, onStage?: (stage: string) => void, interval = 600): Promise<R> {
  let last: string | null = null
  for (;;) {
    const job = await api.job<R>(id)
    if (job.stage && job.stage !== last) onStage?.((last = job.stage))
    if (job.status === 'done') return job.result as R
    if (job.status === 'error') throw new ApiError(500, job.error || 'Something went wrong.')
    await new Promise(r => setTimeout(r, interval))
  }
}
