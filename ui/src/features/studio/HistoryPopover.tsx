import { History } from 'lucide-react'
import { useMemo, useState } from 'react'
import { IconButton, Popover, Tip } from '@/components/ui'
import { cn } from '@/lib/cn'
import { describeChange } from '@/lib/describe'
import { MOD } from '@/lib/hotkeys'
import { useProject } from '@/store/project'

/** How many steps either side of the present get a name when the list opens (naming compares two specs, so it is not free). */
const WINDOW = 60

/** The undo history as a list: every step named by what it changed, newest first; clicking one goes there (back or forward). */
export function HistoryButton() {
  const past = useProject((s) => s.past)
  const spec = useProject((s) => s.spec)
  const future = useProject((s) => s.future)
  const jumpTo = useProject((s) => s.jumpTo)
  const [open, setOpen] = useState(false)
  const steps = past.length + future.length
  const rows = useMemo(() => {
    if (!open || !spec) return []
    const all = [...past, spec, ...future]
    const now = past.length
    const out: { index: number; label: string; state: 'now' | 'past' | 'undone' }[] = []
    for (let k = all.length - 1; k >= 0; k--) {
      const near = Math.abs(k - now) <= WINDOW
      out.push({ index: k, label: k === 0 ? 'Start of the history' : near ? describeChange(all[k - 1], all[k]) : 'Earlier edit', state: k === now ? 'now' : k < now ? 'past' : 'undone' })
    }
    return out
  }, [open, past, spec, future])

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      align="end"
      className="w-[320px] p-0"
      trigger={
        <span className="hidden sm:inline-flex">
          <Tip label="Undo history" shortcut={`${MOD}Z`}>
            <IconButton label="Undo history" disabled={steps === 0}>
              <History className="size-4" />
            </IconButton>
          </Tip>
        </span>
      }
    >
      <div className="border-b border-line px-3 py-2.5">
        <div className="text-[13px] font-semibold">Undo history</div>
        <p className="m-0 mt-0.5 text-[12px] text-muted">Click a step to go back to it. Steps you went back past stay here until you edit again.</p>
      </div>
      <ol aria-label="Steps" className="m-0 max-h-[320px] list-none overflow-y-auto p-1">
        {rows.map((r) => (
          <li key={r.index}>
            <button
              onClick={() => {
                jumpTo(r.index)
                setOpen(false)
              }}
              aria-current={r.state === 'now' ? 'step' : undefined}
              className={cn('flex w-full items-center gap-2 rounded-ctl px-2.5 py-1.5 text-left text-[13px] hover:bg-hover', r.state === 'now' && 'bg-accent-soft font-medium text-accent', r.state === 'undone' && 'text-faint')}
            >
              <span className="min-w-0 flex-1 truncate">{r.label}</span>
              {r.state === 'now' && <span className="shrink-0 text-[11px]">now</span>}
              {r.state === 'undone' && <span className="shrink-0 text-[11px]">undone</span>}
            </button>
          </li>
        ))}
      </ol>
    </Popover>
  )
}
