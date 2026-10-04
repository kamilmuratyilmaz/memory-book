// Chat editing over the AG-UI protocol. The server (src/memory_book/agui.py) streams the reply and the model's
// reasoning token by token, a checklist activity, JSON-Patch deltas of the book, and asks for approval
// (an AG-UI interrupt) before large or destructive changes. The thread itself is stored on the server.

import { HttpAgent, type ActivityMessage, type Interrupt, type Message, type TokenUsage, type Tool } from '@ag-ui/client'
import { useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { uid, type MemoryBook } from '../model'

const SHOW_PAGE: Tool = {
  name: 'show_page',
  description: 'Turn the editor to a page so the user can see what changed.',
  parameters: { type: 'object', properties: { pageId: { type: 'string' } }, required: ['pageId'] },
}

const SUGGESTIONS = [
  'Make this more emotional', 'Use fewer words', 'Make this page minimalist', 'Write a better caption',
  'Make the whole book feel more vintage', 'Add more photos', 'Turn this into a 10-page book',
]

const STEPS: Record<string, string> = { planning: 'Reading your book and planning the changes…', applying: 'Applying the changes…' }

// chat bubbles by message role
const MESSAGE_LOOK: Record<string, string> = {
  user: 'max-w-[92%] self-end rounded-[14px_14px_4px_14px] bg-cloth px-3 py-2 text-on-cloth',
  assistant: 'max-w-[92%] self-start [&>p]:rounded-[14px_14px_14px_4px] [&>p]:border [&>p]:border-line [&>p]:bg-white [&>p]:px-3 [&>p]:py-2',
  reasoning: 'max-w-[92%] self-start',
  activity: 'self-stretch',
}

// checklist marker per item status
const ITEM_LOOK: Record<string, string> = {
  pending: "before:text-muted before:content-['○']",
  applied: "before:text-[#3f7a55] before:content-['✓']",
  skipped: "text-muted line-through before:content-['–']",
  proposed: "before:text-brass before:content-['•']",
}

interface Checklist { status: 'proposed' | 'applying' | 'done'; done: number; total: number; items: { label: string; status: string }[] }

interface Props {
  book: MemoryBook
  pageId: string
  elementId: string | null
  /** Lets other controls (the bar under the canvas) send a prompt through this chat. */
  sendRef: React.RefObject<((prompt: string) => void) | null>
  onRunStart: () => void
  onBook: (book: MemoryBook, runId: string) => void
  onRunEnd: (runId: string, changed: boolean) => void
  onShowPage: (pageId: string) => void
  canUndo: (runId: string) => boolean
  onUndo: () => void
  aiMode: string
}

export default function ChatPanel(p: Props) {
  const threadId = `book-${p.book.id}`
  const [agent] = useState(() => new HttpAgent({ url: '/api/agent', threadId }))
  const [messages, setMessages] = useState<Message[]>([])
  const [loaded, setLoaded] = useState(false)
  const [interrupt, setInterrupt] = useState<Interrupt | null>(null)
  const [step, setStep] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [text, setText] = useState('')
  const [lastRun, setLastRun] = useState<{ id: string; changed: boolean; usage?: TokenUsage[] } | null>(null)
  const list = useRef<HTMLOListElement>(null)
  const latest = useRef(p)
  useEffect(() => { latest.current = p })

  // the thread lives on the server: load it (and any approval still waiting) once
  useEffect(() => {
    let live = true
    fetch(`/api/agent/threads/${threadId}`).then(r => r.json()).then((t: { messages: Message[]; interrupt: Interrupt | null }) => {
      if (!live) return
      agent.setMessages(t.messages)
      setMessages(t.messages)
      setInterrupt(t.interrupt)
    }).catch(() => setError('Your earlier conversation could not be loaded.')).finally(() => live && setLoaded(true))
    return () => { live = false }
  }, [agent, threadId])

  useEffect(() => { list.current?.scrollTo({ top: list.current.scrollHeight, behavior: 'smooth' }) }, [messages, step])

  const run = useCallback(async (start: (a: HttpAgent) => void, resume?: { interruptId: string; status: 'resolved' | 'cancelled'; payload?: unknown }) => {
    if (running) return
    const props = latest.current
    const runId = crypto.randomUUID()
    const shown: string[] = []
    let changed = false
    let usage: TokenUsage[] | undefined
    setError('')
    setRunning(true)
    setInterrupt(null)
    props.onRunStart()
    agent.setState({ book: props.book })
    start(agent)
    setMessages([...agent.messages])
    try {
      await agent.runAgent({
        runId,
        tools: [SHOW_PAGE],
        context: [{ description: 'editor-focus', value: JSON.stringify({ pageId: props.pageId, elementId: props.elementId }) }],
        ...(resume ? { resume: [resume] } : {}),
      }, {
        onMessagesChanged: ({ messages }) => setMessages([...messages]),
        onStepStartedEvent: ({ event }) => setStep(event.stepName),
        onStateChanged: ({ state }) => {
          const book = (state as { book?: MemoryBook }).book
          if (book) { changed = true; latest.current.onBook(book, runId) }
        },
        onToolCallEndEvent: ({ event, toolCallName, toolCallArgs }) => {
          if (toolCallName !== 'show_page') return
          latest.current.onShowPage(String(toolCallArgs.pageId))
          shown.push(event.toolCallId)
        },
        onRunFinishedEvent: (params) => {
          usage = params.event.usage ?? undefined
          if (params.outcome === 'interrupt') setInterrupt(params.interrupts[0] ?? null)
        },
        onRunErrorEvent: ({ event }) => setError(event.message),
        onRunFailed: ({ error }) => setError(error.message.includes('fetch') ? 'You appear to be offline. Try again when you’re connected.' : error.message),
      })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      // answer the frontend tool after the server's MESSAGES_SNAPSHOT, so the next run sends the result
      shown.forEach(id => agent.addMessage({ id: uid('msg'), role: 'tool', toolCallId: id, content: 'shown' }))
      setStep(null)
      setRunning(false)
      setMessages([...agent.messages])
      setLastRun({ id: runId, changed, usage })
      latest.current.onRunEnd(runId, changed)
    }
  }, [agent, running])

  const send = useCallback((prompt: string) => {
    if (!prompt.trim()) return
    run(a => a.addMessage({ id: uid('msg'), role: 'user', content: prompt.trim() }))
  }, [run])

  const decide = (approved: boolean) => interrupt && run(() => {}, {
    interruptId: interrupt.id, status: approved ? 'resolved' : 'cancelled', payload: { approved },
  })

  useImperativeHandle(p.sendRef, () => send, [send])

  const visible = messages.filter(m => m.role === 'user' || m.role === 'assistant' || m.role === 'reasoning' || m.role === 'activity')
  const lastAssistant = [...visible].reverse().find(m => m.role === 'assistant')
  const tokens = lastRun?.usage?.reduce((n, u) => n + (u.totalTokens ?? 0), 0) ?? 0

  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-label="Edit by chat">
      <ol className="flex flex-1 flex-col gap-2.5 overflow-y-auto px-4 py-3.5" ref={list} aria-live="polite">
        {loaded && visible.length === 0 && (
          <li className="grid gap-2 px-0.5 py-1.5 text-sm text-ink">
            <p>Tell me what to change and I’ll edit the book for you — no dragging or menus needed.</p>
            <p className="text-[13px] text-muted">I can rewrite text, change layouts and themes, add or remove pages, move photos and more. I ask before big changes, and every change can be undone.</p>
          </li>
        )}
        {visible.map(m => (
          <li key={m.id} className={`grid justify-items-start gap-1.5 text-sm leading-snug [&_p]:whitespace-pre-wrap ${MESSAGE_LOOK[m.role] ?? ''}`}>
            {m.role === 'reasoning' && (
              <details className="text-[13px] text-muted"><summary className="cursor-pointer italic">How I approached this</summary><p className="mt-1 border-l-2 border-line pl-2.5">{String(m.content)}</p></details>
            )}
            {m.role === 'activity' && <ChecklistView content={(m as ActivityMessage).content as unknown as Checklist} />}
            {(m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string' && m.content.trim() && <p>{m.content}</p>}
            {m === lastAssistant && !running && lastRun?.changed && p.canUndo(lastRun.id) && (
              <div className="flex items-center gap-2.5">
                <button className="btn btn-quiet btn-sm" onClick={p.onUndo}>Undo these changes</button>
                {tokens > 0 && <span className="text-[11.5px] text-muted" title="Model tokens used for this request">{tokens.toLocaleString()} tokens</span>}
              </div>
            )}
          </li>
        ))}
        {running && <li className="relative self-start pl-4.5 text-sm text-muted italic before:absolute before:top-[.55em] before:left-0.5 before:size-2 before:animate-pulse-soft before:rounded-full before:bg-brass-light" role="status">{STEPS[step ?? ''] ?? 'Thinking…'}</li>}
      </ol>
      {interrupt && !running && (
        <div className="mx-4 mb-2.5 grid gap-2 rounded-xl border border-brass-light bg-cream p-3 text-sm" role="alertdialog" aria-labelledby="approval-text">
          <p id="approval-text">{interrupt.message ?? 'Apply these changes?'}</p>
          <div className="flex flex-wrap items-center gap-1.5">
            <button className="btn btn-brass btn-sm" onClick={() => decide(true)}>Apply changes</button>
            <button className="btn btn-quiet btn-sm" onClick={() => decide(false)}>Keep the book as it is</button>
          </div>
        </div>
      )}
      {error && <p className="notice notice-error mx-4 mb-2.5" role="alert">{error}</p>}
      {!running && !interrupt && (
        <div className="flex flex-wrap gap-1.5 px-4 pb-2.5">
          {SUGGESTIONS.map(s => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}
        </div>
      )}
      <form className="flex items-end gap-2 border-t border-line bg-white px-4 pt-2.5 pb-3.5" onSubmit={e => { e.preventDefault(); send(text); setText('') }}>
        <label className="sr-only" htmlFor="chat-input">Message</label>
        <textarea id="chat-input" className="input min-h-10 flex-1 resize-none rounded-xl" rows={2} value={text} disabled={running || !loaded} placeholder="e.g. Make page 3 feel calmer"
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(text); setText('') } }} />
        {running
          ? <button type="button" className="btn btn-quiet btn-sm" onClick={() => agent.abortRun()}>Stop</button>
          : <button className="btn btn-brass btn-sm" disabled={!text.trim()}>Send</button>}
      </form>
      {p.aiMode === 'local' && <p className="hint mx-4 mb-2.5">Offline mode: structural changes only. Configure an AI model on the server for writing changes.</p>}
    </section>
  )
}

function ChecklistView({ content }: { content: Checklist }) {
  const title = content.status === 'proposed' ? 'Proposed changes' : content.status === 'done' ? 'Changes made' : 'Making changes'
  return (
    <div className={`grid w-full gap-1.5 rounded-xl border px-3 py-2 text-[13px] ${content.status === 'proposed' ? 'border-brass-light bg-cream' : 'border-line bg-white'}`}>
      <div className="flex items-baseline justify-between">
        <strong>{title}</strong>
        {content.status !== 'proposed' && <span className="text-muted">{content.done}/{content.total}</span>}
      </div>
      {content.status === 'applying' && <progress className="h-1 w-full accent-brass" max={content.total} value={content.done} aria-label="Progress" />}
      <ul className="grid gap-[3px]">
        {content.items.map((item, i) => (
          <li key={i} className={`relative pl-5 before:absolute before:left-0.5 ${ITEM_LOOK[item.status] ?? ITEM_LOOK.pending}`}>{item.label}{item.status === 'skipped' ? ' — skipped' : ''}</li>
        ))}
      </ul>
    </div>
  )
}
