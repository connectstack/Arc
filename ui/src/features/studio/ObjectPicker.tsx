// Choosing a library object: a search box over the catalog's objects (thumbnail, name, size, a few words of what it is), used by the
// scene outline, the timeline, the inspector's asset field and the command palette. Search matches the name, the summary and the
// tags, so a script's own words work ("गाड़ी" finds the car).
import { Plus, Search } from 'lucide-react'
import { useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useAssets, useCatalog } from '@/api/hooks'
import type { AssetInfo, CatalogEntry } from '@/api/types'
import { Input, Popover } from '@/components/ui'
import { AssetThumb } from '@/features/assets/AssetThumb'
import { cn } from '@/lib/cn'
import { PERSON_HEIGHT } from '@/lib/spec'
import { useProject } from '@/store/project'

const norm = (s: string): string => s.normalize('NFC').toLowerCase()

/** The objects that answer `query`, best first (the library's own order among equals). Every word must match the name, a tag or the summary. */
export function matchObjects(list: CatalogEntry[], query: string): CatalogEntry[] {
  const words = norm(query).split(/\s+/).filter(Boolean)
  if (!words.length) return list
  const scored: { e: CatalogEntry; score: number; at: number }[] = []
  list.forEach((e, at) => {
    const name = norm(e.name.replace(/_/g, ' '))
    const tags = (e.tags ?? []).map(norm)
    const summary = norm(e.summary ?? '')
    let score = 0
    for (const w of words) {
      const best = name === w ? 100 : name.startsWith(w) ? 80 : tags.includes(w) ? 70 : name.includes(w) ? 60 : tags.some((t) => t.startsWith(w)) ? 50 : tags.some((t) => t.includes(w)) ? 35 : summary.includes(w) ? 20 : 0
      if (!best) return
      score += best
    }
    scored.push({ e, score, at })
  })
  return scored.sort((a, b) => b.score - a.score || a.at - b.at).map((x) => x.e)
}

/** How tall a library thing is, in words: `size` 1 is a person ("0.52× a person"). */
export function sizeText(size: number | undefined): string {
  if (size === undefined || !Number.isFinite(size)) return ''
  const v = size >= 10 ? Math.round(size) : size >= 1 ? Math.round(size * 10) / 10 : Math.round(size * 100) / 100
  return `${v}× a person`
}

/** What a thumbnail needs of an asset (its name and version) for a name, from the asset list; the bare name until the list is loaded. */
export function useAssetRef(): (name: string) => Pick<AssetInfo, 'name' | 'version'> {
  const assets = useAssets().data?.assets
  const byName = useMemo(() => new Map((assets ?? []).map((a) => [a.name, a])), [assets])
  return (name) => byName.get(name) ?? { name, version: '' }
}

export function ObjectPicker({ onPick, onAddOwn, current, className }: { onPick: (name: string) => void; onAddOwn?: () => void; current?: string; className?: string }) {
  const catalog = useCatalog().data
  const ref = useAssetRef()
  const style = useProject((s) => s.spec?.meta.style) ?? 'flat_vector'
  const [q, setQ] = useState('')
  const rows = useMemo(() => matchObjects(catalog?.objects ?? [], q), [catalog, q])
  const input = useRef<HTMLInputElement>(null)
  const items = useRef<(HTMLButtonElement | null)[]>([])

  const move = (e: KeyboardEvent, i: number) => {
    const to = e.key === 'ArrowDown' ? i + 1 : e.key === 'ArrowUp' ? i - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? rows.length - 1 : null
    if (to === null) return
    e.preventDefault()
    if (to < 0) input.current?.focus()
    else items.current[Math.min(rows.length - 1, to)]?.focus()
  }

  return (
    <div className={cn('flex w-[320px] max-w-full flex-col', className)}>
      <div className="relative mb-2">
        <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" aria-hidden />
        <Input
          ref={input}
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && rows[0]) {
              e.preventDefault()
              onPick(rows[0].name)
            } else if (e.key === 'ArrowDown' && rows.length) {
              e.preventDefault()
              items.current[0]?.focus()
            }
          }}
          placeholder="Find an object: car, tree, कार…"
          aria-label="Find an object"
          className="pl-8"
        />
      </div>
      <div role="list" aria-label="Library objects" className="max-h-[320px] overflow-y-auto">
        {rows.map((e, i) => (
          <div role="listitem" key={e.name}>
            <button
              ref={(el) => {
                items.current[i] = el
              }}
              type="button"
              onClick={() => onPick(e.name)}
              onKeyDown={(ev) => move(ev, i)}
              aria-current={e.name === current ? 'true' : undefined}
              className={cn('flex w-full items-center gap-2.5 rounded-ctl px-2 py-1.5 text-left hover:bg-hover focus-visible:bg-hover', e.name === current && 'bg-accent-soft')}
            >
              <span className="block w-8 shrink-0">
                <AssetThumb asset={ref(e.name)} style={style} className="rounded-[5px]" alt="" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline justify-between gap-2">
                  <span className="truncate font-medium">{e.name.replace(/_/g, ' ')}</span>
                  <span className="shrink-0 text-[11px] text-faint tabular">{sizeText(e.size ?? (e.height === undefined ? undefined : e.height / PERSON_HEIGHT))}</span>
                </span>
                <span className="line-clamp-1 text-[11.5px] text-muted">{e.summary}</span>
              </span>
            </button>
          </div>
        ))}
        {rows.length === 0 && (
          <p className="px-2 py-4 text-center text-muted">
            {(catalog?.objects ?? []).length === 0 ? 'The library has no objects yet.' : `No object matches “${q.trim()}”.`}
            {onAddOwn ? ' You can add your own.' : ''}
          </p>
        )}
      </div>
      <div className="sr-only" role="status">
        {q.trim() ? `${rows.length} ${rows.length === 1 ? 'object matches' : 'objects match'}` : ''}
      </div>
      {onAddOwn && (
        <button type="button" onClick={onAddOwn} className="mt-2 flex items-center gap-2 rounded-ctl border-t border-line px-2 pb-1 pt-2.5 text-left text-accent hover:underline">
          <Plus className="size-3.5" aria-hidden /> Add your own…
        </button>
      )}
    </div>
  )
}

/** The picker in a popover that closes after a choice. `trigger` is the button that opens it. */
export function ObjectPickerPopover({ trigger, onPick, onAddOwn, current, align = 'start' }: { trigger: ReactNode; onPick: (name: string) => void; onAddOwn?: () => void; current?: string; align?: 'start' | 'center' | 'end' }) {
  const [open, setOpen] = useState(false)
  return (
    <Popover open={open} onOpenChange={setOpen} align={align} className="p-2.5" trigger={trigger} label="Choose an object">
      <ObjectPicker
        current={current}
        onPick={(name) => {
          setOpen(false)
          onPick(name)
        }}
        onAddOwn={
          onAddOwn
            ? () => {
                setOpen(false)
                onAddOwn()
              }
            : undefined
        }
      />
    </Popover>
  )
}
