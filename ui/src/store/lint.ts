import { useEffect } from 'react'
import { create } from 'zustand'
import { useApi } from '@/api/context'
import type { LintReport } from '@/api/types'
import { useProject } from './project'

interface LintState {
  report: LintReport | null
  busy: boolean
  set: (report: LintReport | null, busy: boolean) => void
}

export const useLint = create<LintState>((set) => ({ report: null, busy: false, set: (report, busy) => set({ report, busy }) }))

/** Lints the open spec shortly after each edit (the engine's linter: the same one `reel lint` runs). Mount once per project. */
export function LintRunner() {
  const api = useApi()
  const spec = useProject((s) => s.spec)
  useEffect(() => {
    if (!spec) return
    let cancelled = false
    const timer = setTimeout(() => {
      useLint.setState({ busy: true })
      api
        .lint(spec)
        .then((r) => !cancelled && useLint.setState({ report: r, busy: false }))
        .catch(() => !cancelled && useLint.setState({ busy: false }))
    }, 400)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [api, spec])
  useEffect(() => () => useLint.setState({ report: null, busy: false }), [])
  return null
}
