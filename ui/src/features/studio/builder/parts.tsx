// What the steps of the Build panel share: the edit runner, the picture tiles, the search box and the list of what a scene holds already.
import { ArrowRight, Search, Trash2 } from 'lucide-react'
import { Children, type ReactNode } from 'react'
import { useCatalog } from '@/api/hooks'
import type { ReelSpec } from '@/api/types'
import { Button, IconButton, Input } from '@/components/ui'
import { cn } from '@/lib/cn'
import { sameSelection, type Selection } from '@/lib/spec'
import { sceneAt, sceneSlots, sceneVisibleFrom } from '@/lib/timeline'
import { useProject } from '@/store/project'
import { useStudio, type BuildStep } from '@/store/studio'

/** What every step is given: the spec and the index of the scene being built. */
export interface StepProps {
  spec: ReelSpec
  si: number
}

/**
 * Edits for the scene being built. `run` applies one (a single undo step), selects what it returns (the inspector then shows it) and keeps
 * the playhead inside the scene, so the stage shows what was just added.
 */
export function useBuild(si: number) {
  const edit = useProject((s) => s.edit)
  const select = useProject((s) => s.select)
  const catalog = useCatalog().data
  const run = (fn: (draft: ReelSpec) => Selection | null | void) => {
    edit((d) => {
      const sel = fn(d)
      if (sel) select(sel)
    })
    const { spec, playhead, setPlayhead } = useProject.getState()
    const slots = spec ? sceneSlots(spec) : []
    if (slots[si] && sceneAt(slots, playhead)?.index !== si) setPlayhead(sceneVisibleFrom(slots, si))
  }
  return { catalog, run }
}

/** `0.5` → `0.5 s`, `12` → `12 s`: a time as the panel says it. */
export const secs = (t: number): string => `${Number(t.toFixed(1))} s`

export function SearchBox({ value, onChange, label, placeholder }: { value: string; onChange: (v: string) => void; label: string; placeholder: string }) {
  return (
    <div className="relative">
      <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" aria-hidden />
      <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} aria-label={label} className="pl-8" />
    </div>
  )
}

/** A picture to choose from (a set, a character): its thumbnail as the engine draws it, the name over it. */
export function Tile({ src, label, selected, hint, onClick }: { src: string; label: string; selected?: boolean; hint?: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={selected} title={hint} className={cn('group relative overflow-hidden rounded-[10px] border text-left transition-colors', selected ? 'border-accent ring-2 ring-accent/40' : 'border-line hover:border-line-strong')}>
      <img src={src} alt="" loading="lazy" draggable={false} className="aspect-[9/12] w-full bg-hover object-cover" />
      <span className="absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-black/70 to-transparent px-1.5 pb-1 pt-4 text-[11px] font-medium capitalize text-white">{label}</span>
    </button>
  )
}

/** The things of one kind that the scene holds already, each to select (the inspector shows it) or to take out again. */
export function Contents({ title, empty, children }: { title: string; empty: string; children?: ReactNode }) {
  const rows = Children.toArray(children)
  return (
    <section aria-label={title} className="flex flex-col gap-1">
      <div className="eyebrow px-0.5">{title}</div>
      {rows.length ? <ul className="m-0 flex max-h-44 flex-col gap-1 overflow-y-auto p-0">{rows}</ul> : <p className="px-0.5 text-[12px] leading-snug text-faint">{empty}</p>}
    </section>
  )
}

export function ContentRow({ sel, title, detail, leading, removeLabel, onRemove }: { sel: Selection; title: string; detail?: string; leading?: ReactNode; removeLabel: string; onRemove: () => void }) {
  const select = useProject((s) => s.select)
  const on = useProject((s) => sameSelection(s.selection, sel))
  return (
    <li className="list-none">
      <div className={cn('flex items-center gap-1.5 rounded-ctl border py-0.5 pl-1.5 pr-0.5 transition-colors', on ? 'border-accent bg-accent-soft' : 'border-line bg-raised')}>
        {leading}
        <button type="button" aria-pressed={on} onClick={() => select(sel)} className="min-w-0 flex-1 rounded-ctl py-1 text-left">
          <span className="block truncate text-[12.5px] font-medium" dir="auto">
            {title}
          </span>
          {detail && <span className="block truncate text-[11px] text-muted">{detail}</span>}
        </button>
        <IconButton label={removeLabel} size="icon-sm" className="hover:text-danger" onClick={onRemove}>
          <Trash2 className="size-3.5" />
        </IconButton>
      </div>
    </li>
  )
}

/** The way on to the next step. */
export function NextStep({ to, label }: { to: BuildStep; label: string }) {
  const set = useStudio((s) => s.set)
  return (
    <div className="flex justify-end">
      <Button variant="ghost" size="sm" onClick={() => set({ buildStep: to })}>
        Next: {label} <ArrowRight className="size-3.5" aria-hidden />
      </Button>
    </div>
  )
}
