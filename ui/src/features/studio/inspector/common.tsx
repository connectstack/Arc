import { ChevronRight } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { ReelSpec } from '@/api/types'
import { cn } from '@/lib/cn'
import { useProject } from '@/store/project'

export function Section({ title, children, action, defaultOpen = true, className }: { title: string; children: ReactNode; action?: ReactNode; defaultOpen?: boolean; className?: string }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className={cn('border-b border-line px-4 py-3.5', className)}>
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <button className="eyebrow flex items-center gap-1 hover:text-fg" onClick={() => setOpen(!open)} aria-expanded={open}>
          <ChevronRight className={cn('size-3 transition-transform', open && 'rotate-90')} aria-hidden />
          {title}
        </button>
        {action}
      </div>
      {open && <div className="flex flex-col gap-3">{children}</div>}
    </section>
  )
}

export function Row({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('grid grid-cols-2 gap-2.5', className)}>{children}</div>
}

/** How long a number may sit after its last change before the edit counts as finished (a dragged slider says so itself, when it is let go). */
const SETTLE_MS = 1200

/**
 * Edits that stream in (a slider being dragged, a number being typed) as one undo step: `live` changes the spec without a history entry
 * of its own and `commit` closes the step. A step nobody commits (a number typed into its box) closes by itself a moment after the last
 * change, and when the inspector goes away, so it can never be left open under the next edit.
 */
export function useLive() {
  const edit = useProject((s) => s.edit)
  const begin = useProject((s) => s.beginGesture)
  const end = useProject((s) => s.endGesture)
  const active = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const commit = () => {
    clearTimeout(timer.current)
    if (active.current) {
      end()
      active.current = false
    }
  }
  const latest = useRef(commit)
  latest.current = commit
  useEffect(() => () => latest.current(), [])
  return {
    live: (fn: (d: ReelSpec) => void) => {
      if (!active.current) {
        begin()
        active.current = true
      }
      edit(fn, { live: true })
      clearTimeout(timer.current)
      timer.current = setTimeout(() => latest.current(), SETTLE_MS)
    },
    commit,
  }
}
