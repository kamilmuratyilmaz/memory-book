import { useEffect, useState } from 'react'
import { api, type BookSummary } from '../api'
import { useDesign } from '../App'
import type { AssetInfo, MemoryBook } from '../model'
import { ScaledPage } from '../render/ScaledPage'
import { Link, navigate } from '../router'

export function Home() {
  const [books, setBooks] = useState<BookSummary[] | null>(null)
  const [assets, setAssets] = useState<Record<string, AssetInfo>>({})
  const [error, setError] = useState('')

  useEffect(() => {
    api.books().then(r => { setBooks(r.books); setAssets(r.assets) }).catch(e => setError(e.message))
  }, [])

  async function remove(b: BookSummary) {
    if (!confirm(`Delete “${b.title}”? This can’t be undone.`)) return
    try {
      await api.deleteBook(b.id)
      setBooks(list => list?.filter(x => x.id !== b.id) ?? null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="min-h-dvh bg-cloth text-on-cloth">
      <header className="mx-auto grid max-w-6xl justify-items-start gap-5 px-6 pt-14 pb-16 sm:pt-20">
        <span className="font-display text-[22px] font-bold text-brass-light">Memory Book</span>
        <h1 className="max-w-[15ch] font-display text-[clamp(40px,6.4vw,76px)] leading-[1.02] font-medium tracking-tight">Your photos and stories, made into a&nbsp;book.</h1>
        <p className="max-w-[54ch] text-[17px] text-on-cloth-muted">Add your photos and a few words about what happened. We write the story, lay out every page and design the
          cover — then you can change anything you like.</p>
        <Link className="btn btn-lg bg-brass-light text-cloth-deep hover:bg-[#e3c27f]" href="/new">Start a new book</Link>
      </header>

      <section className="min-h-[50vh] bg-cloth-deep px-6 pt-10 pb-20" aria-labelledby="shelf-title">
        <div className="mx-auto max-w-6xl">
        <h2 id="shelf-title" className="mb-6 font-display text-3xl font-medium">Your books</h2>
        {error && <p className="notice notice-error" role="alert">{error}</p>}
        {books === null && !error && <p className="text-on-cloth-muted">Loading your books…</p>}
        {books?.length === 0 && (
          <div className="rounded-md border border-dashed border-on-cloth/25 p-8 text-on-cloth-muted">
            <p>Nothing on the shelf yet. Your first book takes about a minute — start with a handful of photos.</p>
          </div>
        )}
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-x-7 gap-y-10 sm:grid-cols-[repeat(auto-fill,minmax(220px,1fr))]">
          {books?.map(b => <BookCard key={b.id} summary={b} assets={assets} onDelete={() => remove(b)} />)}
        </ul>
        </div>
      </section>
    </div>
  )
}

function BookCard({ summary, assets, onDelete }: { summary: BookSummary; assets: Record<string, AssetInfo>; onDelete: () => void }) {
  const design = useDesign()
  const book = {
    id: summary.id, title: summary.title, themeId: summary.themeId, pageSize: summary.pageSize,
    orientation: summary.orientation, pages: summary.coverPage ? [summary.coverPage] : [],
  } as unknown as MemoryBook
  const updated = new Date(summary.updatedAt).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
  return (
    <li className="grid content-start gap-3.5">
      <button className="relative cursor-pointer justify-self-start overflow-hidden rounded-[1px_3px_3px_1px] shadow-[0_1px_1px_rgba(0,0,0,.3),0_14px_28px_-8px_rgba(0,0,0,.55)] transition duration-300 after:pointer-events-none after:absolute after:inset-y-0 after:left-0 after:w-2.5 after:bg-linear-to-r after:from-black/30 after:via-white/10 after:to-transparent hover:-translate-y-1 hover:shadow-[0_1px_1px_rgba(0,0,0,.3),0_22px_36px_-10px_rgba(0,0,0,.6)]" onClick={() => navigate(`/books/${summary.id}`)} aria-label={`Open ${summary.title}`}>
        {summary.coverPage && (
          <ScaledPage width={summary.orientation === 'landscape' ? 260 : 190} book={book} page={summary.coverPage}
            index={0} design={design} assets={assets} variant="thumb" />
        )}
      </button>
      <div className="grid gap-1">
        <h3 className="font-display text-[21px] leading-tight font-semibold">{summary.title}</h3>
        <p className="text-[13px] text-on-cloth-muted">{summary.pageCount} pages · edited {updated}</p>
        <div className="mt-1.5 flex gap-1.5">
          <Link className="btn btn-ghost btn-sm" href={`/books/${summary.id}/read`}>Read</Link>
          <Link className="btn btn-ghost btn-sm" href={`/books/${summary.id}`}>Edit</Link>
          <button className="btn btn-ghost btn-sm text-[#e7a595]" onClick={onDelete}>Delete</button>
        </div>
      </div>
    </li>
  )
}
