import { useRef, useState, type DragEvent } from 'react'
import { api, assetUrl, waitForJob, type GenerationInput } from '../api'
import { useDesign } from '../App'
import type { AssetInfo, Orientation, PageSize } from '../model'
import { Link, navigate } from '../router'

interface Moment { text: string; date: string; location: string }

const LENGTHS: { label: string; pages: number | null }[] = [
  { label: 'Let us decide', pages: null }, { label: 'Short · ~10 pages', pages: 10 },
  { label: 'Medium · ~20 pages', pages: 20 }, { label: 'Long · ~32 pages', pages: 32 },
]

export function Create() {
  const design = useDesign()
  const [photos, setPhotos] = useState<AssetInfo[]>([])
  const [uploading, setUploading] = useState(0)
  const [uploadErrors, setUploadErrors] = useState<string[]>([])
  const [title, setTitle] = useState('')
  const [story, setStory] = useState('')
  const [moments, setMoments] = useState<Moment[]>([])
  const [date, setDate] = useState('')
  const [location, setLocation] = useState('')
  const [people, setPeople] = useState('')
  const [author, setAuthor] = useState('')
  const [mood, setMood] = useState('')
  const [themeId, setThemeId] = useState('auto')
  const [pageSize, setPageSize] = useState<PageSize>('A5')
  const [orientation, setOrientation] = useState<Orientation>('portrait')
  const [targetPages, setTargetPages] = useState<number | null>(null)
  const [stages, setStages] = useState<[string, string][] | null>(null)
  const [stage, setStage] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  async function addFiles(files: File[]) {
    const images = files.filter(f => f.type.startsWith('image/') || /\.(jpe?g|png|webp|gif|tiff?|heic)$/i.test(f.name))
    if (!images.length) return
    setUploading(n => n + images.length)
    for (let i = 0; i < images.length; i += 4) {
      const batch = images.slice(i, i + 4)
      try {
        const res = await api.upload(batch)
        setPhotos(p => [...p, ...res.assets])
        if (res.errors.length) setUploadErrors(e => [...e, ...res.errors])
      } catch (e) {
        setUploadErrors(errs => [...errs, `${batch.map(f => f.name).join(', ')}: ${(e as Error).message}`])
      } finally {
        setUploading(n => n - batch.length)
      }
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    setDragging(false)
    addFiles([...e.dataTransfer.files])
  }

  const canCreate = (photos.length > 0 || story.trim().length > 0) && uploading === 0

  async function create() {
    setError('')
    const input: GenerationInput = {
      title, author, story, date, location, people, mood, themeId, pageSize, orientation, targetPages,
      photoIds: photos.map(p => p.id),
      notes: moments.filter(m => m.text.trim()),
    }
    try {
      const { jobId, stages } = await api.generate(input)
      setStages(stages)
      setStage(stages[0][0])
      const result = await waitForJob<{ bookId: string }>(jobId, setStage)
      navigate(`/books/${result.bookId}`, true)
    } catch (e) {
      setStages(null)
      setError((e as Error).message)
    }
  }

  if (stages) return <Progress stages={stages} current={stage} photos={photos} />

  return (
    <div className="mx-auto max-w-3xl px-4 pt-6 pb-24 sm:px-6">
      <nav className="mb-8"><Link href="/" className="btn btn-quiet btn-sm">← Library</Link></nav>
      <header className="mb-7">
        <h1 className="font-display text-[clamp(34px,5vw,50px)] leading-[1.05] font-medium">Create your memory book</h1>
        <p className="mt-2 text-base text-muted">Photos and a few honest words are all we need. Everything else is optional.</p>
      </header>

      <section className={`rounded-xl border-[1.5px] border-dashed transition-colors ${dragging ? 'border-brass bg-cream' : 'border-[#b9c3bb] bg-white'}`} onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)} onDrop={onDrop} aria-label="Photos">
        {photos.length === 0 && uploading === 0 ? (
          <button className="grid w-full cursor-pointer gap-1.5 px-6 py-14 text-center" onClick={() => fileInput.current?.click()}>
            <strong className="font-display text-[26px] font-semibold">Upload photos</strong>
            <span className="text-muted">Drag them here, or choose from your device. Originals are kept untouched for printing.</span>
          </button>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(84px,1fr))] gap-2.5 p-3.5 sm:grid-cols-[repeat(auto-fill,minmax(96px,1fr))]">
            {photos.map(p => (
              <figure key={p.id} className="relative m-0 aspect-square overflow-hidden rounded bg-mist">
                <img src={assetUrl(p.id, 'thumb')} alt={p.filename} loading="lazy" className="size-full object-cover" />
                <button className="absolute top-1 right-1 grid size-6 cursor-pointer place-items-center rounded-full bg-ink/75 leading-none text-white" aria-label={`Remove ${p.filename}`}
                  onClick={() => setPhotos(list => list.filter(x => x.id !== p.id))}>×</button>
              </figure>
            ))}
            {Array.from({ length: uploading }, (_, i) => <div key={`u${i}`} className="aspect-square animate-pulse-soft rounded bg-mist" aria-label="Uploading" />)}
            <button className="aspect-square cursor-pointer rounded border border-dashed border-line bg-panel text-muted" onClick={() => fileInput.current?.click()}>Add more</button>
          </div>
        )}
        <input ref={fileInput} type="file" accept="image/*" multiple hidden
          onChange={e => { addFiles([...(e.target.files ?? [])]); e.target.value = '' }} />
      </section>
      {uploadErrors.length > 0 && (
        <div className="notice notice-error" role="alert">
          {uploadErrors.map((m, i) => <p key={i}>{m}</p>)}
          <button className="btn btn-quiet btn-sm" onClick={() => setUploadErrors([])}>Dismiss</button>
        </div>
      )}

      <section className="mt-7">
        <label className="mb-3 flex min-w-0 flex-col gap-1">
          <span className="label">Tell us about this memory</span>
          <textarea className="input resize-y text-base leading-relaxed" rows={7} value={story} onChange={e => setStory(e.target.value)}
            placeholder="Where were you, who was there, what do you want to remember? Rough notes are fine — we’ll shape them into a story." />
        </label>
        {moments.map((m, i) => (
          <fieldset key={i} className="mb-3 grid gap-2 rounded-md border border-line bg-panel p-3">
            <legend className="label px-1">Another moment</legend>
            <textarea className="input resize-y leading-relaxed" rows={3} value={m.text} aria-label="What happened"
              onChange={e => setMoments(ms => ms.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
              placeholder="What happened next?" />
            <div className="flex flex-wrap items-end gap-2">
              <input className="input w-auto min-w-36 flex-1" value={m.date} type="date" aria-label="Date"
                onChange={e => setMoments(ms => ms.map((x, j) => (j === i ? { ...x, date: e.target.value } : x)))} />
              <input className="input w-auto min-w-36 flex-1" value={m.location} placeholder="Place" aria-label="Place"
                onChange={e => setMoments(ms => ms.map((x, j) => (j === i ? { ...x, location: e.target.value } : x)))} />
              <button className="btn btn-quiet btn-sm" onClick={() => setMoments(ms => ms.filter((_, j) => j !== i))}>Remove</button>
            </div>
          </fieldset>
        ))}
        <button className="btn btn-quiet" onClick={() => setMoments(ms => [...ms, { text: '', date: '', location: '' }])}>
          + Add another moment
        </button>
      </section>

      <details className="mt-7 border-t border-line pt-5">
        <summary className="mb-4 cursor-pointer text-base font-semibold">Details and style <span className="font-normal text-muted">— optional</span></summary>
        <div className="grid gap-x-4 sm:grid-cols-2">
          <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Title</span>
            <input className="input" value={title} onChange={e => setTitle(e.target.value)} placeholder="We’ll suggest one" /></label>
          <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Made by</span>
            <input className="input" value={author} onChange={e => setAuthor(e.target.value)} placeholder="Your name" /></label>
          <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">When</span>
            <input className="input" type="date" value={date} onChange={e => setDate(e.target.value)} /></label>
          <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Where</span>
            <input className="input" value={location} onChange={e => setLocation(e.target.value)} placeholder="City, country" /></label>
          <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Who was there</span>
            <input className="input" value={people} onChange={e => setPeople(e.target.value)} placeholder="Names" /></label>
          <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Mood</span>
            <input className="input" value={mood} onChange={e => setMood(e.target.value)} placeholder="e.g. nostalgic, joyful, calm" /></label>
        </div>

        <fieldset className="mb-3 flex min-w-0 flex-col gap-1">
          <legend className="label">Style</legend>
          <div className="mt-1.5 grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-2.5">
            <ThemeChip id="auto" name="Let us choose" selected={themeId === 'auto'} onSelect={setThemeId} />
            {Object.entries(design.themes).map(([id, t]) => (
              <ThemeChip key={id} id={id} name={t.name} selected={themeId === id} onSelect={setThemeId}
                swatch={[t.colors.paper, t.colors.text, t.colors.accent]} font={t.text.title.fontFamily} />
            ))}
          </div>
        </fieldset>

        <div className="grid gap-x-4 sm:grid-cols-2">
          <fieldset className="mb-3 flex min-w-0 flex-col gap-1 sm:col-span-2">
            <legend className="label">Book size</legend>
            <div className="segmented">
              {(['A5', 'A4', 'Square'] as PageSize[]).map(s => (
                <label key={s} className="segment"><input type="radio" className="sr-only" name="size" checked={pageSize === s} onChange={() => setPageSize(s)} />
                  {s} <small className="ml-1 opacity-70">{design.pageSizes[s].join(' × ')} mm</small></label>
              ))}
            </div>
          </fieldset>
          {pageSize !== 'Square' && (
            <fieldset className="mb-3 flex min-w-0 flex-col gap-1">
              <legend className="label">Orientation</legend>
              <div className="segmented">
                {(['portrait', 'landscape'] as Orientation[]).map(o => (
                  <label key={o} className="segment"><input type="radio" className="sr-only" name="orientation" checked={orientation === o} onChange={() => setOrientation(o)} />
                    {o === 'portrait' ? 'Portrait' : 'Landscape'}</label>
                ))}
              </div>
            </fieldset>
          )}
          <label className="mb-3 flex min-w-0 flex-col gap-1"><span className="label">Length</span>
            <select className="input" value={targetPages ?? ''} onChange={e => setTargetPages(e.target.value ? Number(e.target.value) : null)}>
              {LENGTHS.map(l => <option key={l.label} value={l.pages ?? ''}>{l.label}</option>)}
            </select>
          </label>
        </div>
      </details>

      {error && <p className="notice notice-error" role="alert">{error} Your photos and notes are still here.</p>}
      <div className="mt-8 flex flex-wrap items-center gap-4">
        <button className="btn btn-brass btn-lg" disabled={!canCreate} onClick={create}>Create my book</button>
        {!canCreate && <span className="text-muted">{uploading ? 'Waiting for photos to finish uploading…' : 'Add a photo or a few words to begin.'}</span>}
      </div>
    </div>
  )
}

function ThemeChip({ id, name, selected, onSelect, swatch, font }: {
  id: string; name: string; selected: boolean; onSelect: (id: string) => void; swatch?: string[]; font?: string
}) {
  return (
    <label className="relative grid cursor-pointer gap-1.5">
      <input type="radio" className="peer sr-only" name="theme" checked={selected} onChange={() => onSelect(id)} />
      <span className={`relative grid h-16 place-items-center rounded border border-line bg-panel text-[26px] peer-focus-visible:outline-2
        peer-focus-visible:outline-offset-2 peer-focus-visible:outline-brass-light ${selected ? 'outline-2 outline-offset-2 outline-brass' : ''}`}
        style={{ background: swatch?.[0], color: swatch?.[1], fontFamily: font ? `"${font}"` : undefined }}>
        {swatch ? 'Aa' : '✦'}
        {swatch && <i className="absolute inset-x-2.5 bottom-2 h-0.5" style={{ background: swatch[2] }} />}
      </span>
      <span className="text-center text-[13px]">{name}</span>
    </label>
  )
}

function Progress({ stages, current, photos }: { stages: [string, string][]; current: string | null; photos: AssetInfo[] }) {
  const idx = Math.max(0, stages.findIndex(([k]) => k === current))
  return (
    <div className="grid min-h-dvh place-content-center justify-items-center gap-5 bg-cloth p-6 text-center text-on-cloth" role="status" aria-live="polite">
      <div className="relative size-[150px]" aria-hidden="true">
        {photos.slice(0, 5).map((p, i) => (
          <img key={p.id} src={assetUrl(p.id, 'thumb')} alt=""
            className="absolute inset-3.5 size-[122px] animate-settle border-[5px] border-[#fffdf8] object-cover shadow-[0_8px_22px_rgba(0,0,0,.35)]"
            style={{ ['--i' as string]: i, transform: `rotate(${(i - 2) * 7}deg)`, animationDelay: `${i * 0.25}s` }} />
        ))}
      </div>
      <h1 className="font-display text-[clamp(30px,5vw,46px)] font-medium">{stages[idx][1]}…</h1>
      <ol className="grid gap-2 text-left">
        {stages.map(([k, label], i) => (
          <li key={k} className={`relative pl-6.5 before:absolute before:top-[.55em] before:left-1 before:size-[9px] before:rounded-full before:border-[1.5px]
            ${i < idx ? 'text-on-cloth before:border-brass-light before:bg-brass-light'
              : i === idx ? 'font-semibold text-on-cloth before:animate-pulse-soft before:border-brass-light' : 'text-on-cloth-muted before:border-current'}`}>{label}</li>
        ))}
      </ol>
      <p className="text-on-cloth-muted">This usually takes under a minute. You can keep this tab open.</p>
    </div>
  )
}
