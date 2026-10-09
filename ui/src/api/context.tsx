import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import type { Api } from './client'
import { createHttpApi } from './http'

const Ctx = createContext<Api | null>(null)

/** `?mock` in the address bar or VITE_API=mock selects the in-memory adapter; everything else talks to `reel serve`. */
export function wantsMock(): boolean {
  return import.meta.env.VITE_API === 'mock' || new URLSearchParams(location.search).has('mock')
}

export function ApiProvider({ children, api: given }: { children: ReactNode; api?: Api }) {
  const [api, setApi] = useState<Api | null>(() => given ?? (wantsMock() ? null : createHttpApi()))
  useEffect(() => {
    if (api) return
    void import('./mock').then((m) => m.createMockApi()).then(setApi)
  }, [api])
  if (!api) return <div className="grid h-full place-items-center text-muted">Loading the demo data…</div>
  return <Ctx.Provider value={api}>{children}</Ctx.Provider>
}

export function useApi(): Api {
  const api = useContext(Ctx)
  if (!api) throw new Error('useApi outside ApiProvider')
  return api
}
