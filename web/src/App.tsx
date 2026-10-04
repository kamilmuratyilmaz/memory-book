import { createContext, useContext, useEffect, useState } from 'react'
import { api } from './api'
import type { Design } from './model'
import { installFonts } from './render/Page'
import { usePath } from './router'
import { Home } from './home/Home'
import { Create } from './create/Create'
import { Editor } from './editor/Editor'
import { Reader } from './reader/Reader'

const DesignContext = createContext<Design | null>(null)

export function useDesign(): Design {
  const d = useContext(DesignContext)
  if (!d) throw new Error('design not loaded')
  return d
}

export default function App() {
  const [design, setDesign] = useState<Design | null>(null)
  const [error, setError] = useState('')
  const path = usePath()

  useEffect(() => {
    api.design().then(d => { installFonts(d); setDesign(d) }).catch(() => setError('We can’t reach the server. Check that it’s running, then reload this page.'))
  }, [])

  useEffect(() => { window.scrollTo(0, 0) }, [path])

  if (error) return <div className="grid min-h-dvh place-content-center gap-2 p-6 text-center text-muted" role="alert">{error}</div>
  if (!design) return <div className="grid min-h-dvh place-content-center gap-2 p-6 text-center text-muted" aria-busy="true">Opening your library…</div>

  const read = path.match(/^\/books\/([\w-]+)\/read\/?$/)
  const edit = path.match(/^\/books\/([\w-]+)\/?$/)
  return (
    <DesignContext.Provider value={design}>
      {path === '/new' ? <Create /> :
        read ? <Reader key={read[1]} bookId={read[1]} /> :
        edit ? <Editor key={edit[1]} bookId={edit[1]} /> :
        <Home />}
    </DesignContext.Provider>
  )
}
