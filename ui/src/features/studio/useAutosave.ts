import { useCallback, useEffect } from 'react'
import { useApi } from '@/api/context'
import { ApiError } from '@/api/types'
import type { ProjectDoc } from '@/api/types'
import { stripDefaults } from '@/lib/normalize'
import { useProject } from '@/store/project'

const DELAY = 700

/** Saves the open project shortly after the last edit (and on demand). A save from a stale tab becomes a conflict, never an overwrite. */
export function useAutosave(): { saveNow: () => Promise<void> } {
  const api = useApi()
  const save = useProject((s) => s.save)
  const spec = useProject((s) => s.spec)
  const script = useProject((s) => s.script)

  const saveNow = useCallback(async () => {
    const st = useProject.getState()
    if (!st.id || !st.spec || !st.etag || st.save === 'saving' || st.save === 'conflict') return
    st.setSaving()
    try {
      const doc = await api.saveProject(st.id, stripDefaults(st.spec) as unknown as typeof st.spec, st.script, st.etag)
      useProject.getState().markSaved(doc)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) useProject.getState().setConflict((e.extra.current as ProjectDoc) ?? null)
      else useProject.getState().setSaveError(e instanceof ApiError ? `${e.detail}${e.hint ? ` — ${e.hint}` : ''}` : String(e))
    }
  }, [api])

  useEffect(() => {
    if (save !== 'dirty') return
    const t = setTimeout(() => void saveNow(), DELAY)
    return () => clearTimeout(t)
  }, [save, spec, script, saveNow])

  useEffect(() => {
    const flush = () => document.visibilityState === 'hidden' && void saveNow()
    document.addEventListener('visibilitychange', flush)
    return () => {
      document.removeEventListener('visibilitychange', flush)
      void saveNow() // leaving the project: do not lose the last edits
    }
  }, [saveNow])

  return { saveNow }
}
