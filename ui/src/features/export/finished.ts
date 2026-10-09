import type { QueryClient } from '@tanstack/react-query'
import type { NavigateFunction } from 'react-router-dom'
import type { RenderResult } from '@/api/types'
import { toast } from '@/components/ui'
import { useStudio } from '@/store/studio'

/** A render ended: refresh the history and, unless its dialog is open to say so, tell the user with a way to watch it. */
export function announceRender(qc: QueryClient, nav: NavigateFunction, projectId: string | undefined, result: Record<string, unknown>): void {
  void qc.invalidateQueries({ queryKey: ['renders'] })
  if (useStudio.getState().exportOpen) return // with the dialog open, its own result view is the notification
  const watch = () => {
    if (projectId) nav(`/p/${projectId}`)
    useStudio.getState().set({ exportOpen: !!projectId, exportShowResult: true })
  }
  toast.action('success', 'Render finished', projectId ? { label: 'Watch', onClick: watch } : undefined, (result as unknown as RenderResult).title)
}
