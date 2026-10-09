import { Move, Play, Plus, Search } from 'lucide-react'
import { useMemo, useState, type DragEvent, type ReactNode } from 'react'
import { useApi } from '@/api/context'
import { useCatalog } from '@/api/hooks'
import type { Catalog, CatalogEntry } from '@/api/types'
import { Chip, Input, Segmented } from '@/components/ui'
import { cn } from '@/lib/cn'
import { curvePath } from '@/lib/easing'
import { titleCase } from '@/lib/format'
import { sceneAt, sceneSlots } from '@/lib/timeline'
import { useProject } from '@/store/project'
import { addAction, addCameraMove, addSfx } from '../studio/ops'

type Kind = 'actions' | 'camera' | 'sounds' | 'backgrounds' | 'characters' | 'props' | 'captions' | 'transitions' | 'easings'

const KINDS: { value: Kind; label: string }[] = [
  { value: 'actions', label: 'Actions' },
  { value: 'backgrounds', label: 'Backgrounds' },
  { value: 'characters', label: 'Characters' },
  { value: 'props', label: 'Props' },
  { value: 'sounds', label: 'Sounds' },
  { value: 'camera', label: 'Camera' },
  { value: 'transitions', label: 'Transitions' },
  { value: 'captions', label: 'Captions' },
  { value: 'easings', label: 'Easings' },
]

function Draggable({ item, children, className, enabled = true }: { item: { kind: string; name: string }; children: ReactNode; className?: string; enabled?: boolean }) {
  if (!enabled) return <div className={className}>{children}</div>
  return (
    <div draggable onDragStart={(e: DragEvent) => (e.dataTransfer.setData('application/x-reel-item', JSON.stringify(item)), (e.dataTransfer.effectAllowed = 'copy'))} className={cn('cursor-grab active:cursor-grabbing', className)}>
      {children}
    </div>
  )
}

/** Add a library item at the playhead (for the selected character, or the first one in the scene). */
function useAddToScene() {
  const edit = useProject((s) => s.edit)
  const select = useProject((s) => s.select)
  const catalog = useCatalog().data
  return (kind: 'action' | 'camera' | 'sfx', name: string) => {
    const st = useProject.getState()
    edit((d) => {
      const t = st.playhead
      let sel = null
      if (kind === 'action') {
        const s = st.selection
        let who: string | undefined
        if (s.kind === 'character') who = s.id
        else if ('layer' in s) who = d.scenes[s.scene]?.layers[s.layer]?.character
        if (!who) {
          const slot = sceneAt(sceneSlots(d), t)
          who = (slot ? d.scenes[slot.index].layers[0]?.character : undefined) ?? d.characters[0]?.id
        }
        if (who) sel = addAction(d, catalog, who, name, t)
      } else if (kind === 'camera') sel = addCameraMove(d, name, t)
      else sel = addSfx(d, name, t)
      if (sel) select(sel)
    })
  }
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mb-4">
      <div className="eyebrow mb-1.5 px-1">{title}</div>
      {children}
    </div>
  )
}

function ActionRow({ a, onAdd }: { a: CatalogEntry; onAdd: (() => void) | null }) {
  return (
    <Draggable item={{ kind: 'action', name: a.name }} enabled={!!onAdd} className="group flex items-start gap-2 rounded-ctl px-2 py-1.5 hover:bg-hover">
      {onAdd && <Move className="mt-1 size-3 shrink-0 text-faint" aria-hidden />}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="font-medium">{a.name}</span>
          {a.moves_root && (
            <Chip tone="info" className="h-4 px-1 text-[10px]">
              moves
            </Chip>
          )}
        </div>
        <div className="line-clamp-2 text-[11.5px] leading-snug text-muted">{a.summary}</div>
        <div className="mt-0.5 text-[10.5px] text-faint tabular">
          min {a.min_duration ?? 0.2}s · typical {a.default_duration ?? 1.5}s
        </div>
      </div>
      {onAdd && (
        <button onClick={onAdd} aria-label={`Add ${a.name} at the playhead`} title="Add at the playhead" className="mt-0.5 rounded p-1 text-faint opacity-0 hover:bg-raised hover:text-fg focus-visible:opacity-100 group-hover:opacity-100">
          <Plus className="size-3.5" />
        </button>
      )}
    </Draggable>
  )
}

/** `browse` shows the catalogue on its own (no project open): nothing can be added or dragged. */
export function LibraryBrowser({ compact = false, browse = false, styleName }: { compact?: boolean; browse?: boolean; styleName?: string }) {
  const api = useApi()
  const catalog = useCatalog().data
  const [kind, setKind] = useState<Kind>('actions')
  const [q, setQ] = useState('')
  const add = useAddToScene()
  const projectStyle = useProject((s) => s.spec?.meta.style ?? 'flat_vector')
  const style = styleName ?? projectStyle
  const needle = q.trim().toLowerCase()
  const match = (e: CatalogEntry | { name: string; summary?: string }) => !needle || `${e.name} ${e.summary ?? ''}`.toLowerCase().includes(needle)
  const kinds = compact ? KINDS.filter((k) => ['actions', 'sounds', 'camera'].includes(k.value)) : KINDS

  const body = useMemo(() => {
    if (!catalog) return null
    return renderKind(kind, catalog, { api, match, add: browse ? null : add, style, compact })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, catalog, needle, style, compact, browse])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className={cn('flex flex-col gap-2.5 border-b border-line p-3', !compact && 'px-0 pt-0')}>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" aria-hidden />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${kind}…`} aria-label="Search the library" className="pl-8" />
        </div>
        {compact ? <Segmented label="Library section" size="sm" value={kind} onChange={setKind} options={kinds} className="self-start" /> : (
          <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Library sections">
            {kinds.map((k) => (
              <button key={k.value} role="tab" aria-selected={kind === k.value} onClick={() => setKind(k.value)} className={cn('rounded-full border px-3 py-1 text-[12px] font-medium transition-colors', kind === k.value ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:text-fg')}>
                {k.label}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className={cn('min-h-0 flex-1 overflow-y-auto', compact ? 'p-2' : 'pt-4')}>{body}</div>
    </div>
  )
}

interface Ctx {
  api: ReturnType<typeof useApi>
  match: (e: { name: string; summary?: string }) => boolean
  add: ReturnType<typeof useAddToScene> | null
  style: string
  compact: boolean
}

function renderKind(kind: Kind, catalog: Catalog, { api, match, add, style, compact }: Ctx): ReactNode {
  const grid = compact ? 'grid-cols-2' : 'grid-cols-[repeat(auto-fill,minmax(210px,1fr))]'
  const empty = <p className="px-2 py-8 text-center text-muted">Nothing matches.</p>
  switch (kind) {
    case 'actions': {
      const by: Record<string, CatalogEntry[]> = {}
      for (const a of catalog.actions.filter(match)) (by[a.category ?? 'other'] ??= []).push(a)
      const groups = Object.entries(by)
      if (!groups.length) return empty
      return groups.map(([cat, list]) => (
        <Group key={cat} title={`${titleCase(cat)} · ${list.length}`}>
          <div className={cn(compact ? '' : 'grid gap-1 md:grid-cols-2 xl:grid-cols-3')}>
            {list.map((a) => (
              <ActionRow key={a.name} a={a} onAdd={add ? () => add('action', a.name) : null} />
            ))}
          </div>
        </Group>
      ))
    }
    case 'camera': {
      const list = catalog.camera_moves.filter(match)
      return list.length ? (
        <div className="grid gap-1">
          {list.map((m) => (
            <Draggable key={m.name} item={{ kind: 'camera', name: m.name }} enabled={!!add} className="group flex items-start gap-2 rounded-ctl px-2 py-1.5 hover:bg-hover">
              <div className="min-w-0 flex-1">
                <div className="font-medium">{titleCase(m.name)}</div>
                <div className="text-[11.5px] leading-snug text-muted">{m.summary}</div>
              </div>
              {add && (
                <button onClick={() => add('camera', m.name)} aria-label={`Add ${m.name} at the playhead`} className="rounded p-1 text-faint hover:bg-raised hover:text-fg">
                  <Plus className="size-3.5" />
                </button>
              )}
            </Draggable>
          ))}
        </div>
      ) : (
        empty
      )
    }
    case 'sounds': {
      const list = catalog.sfx.filter(match)
      return list.length ? (
        <div className={cn('grid gap-1', compact ? 'grid-cols-2' : 'grid-cols-[repeat(auto-fill,minmax(160px,1fr))]')}>
          {list.map((s) => (
            <Draggable key={s.name} item={{ kind: 'sfx', name: s.name }} enabled={!!add} className="group flex items-center rounded-ctl hover:bg-hover">
              {add ? (
                <button onClick={() => add('sfx', s.name)} title="Add at the playhead" className="min-w-0 flex-1 truncate px-2 py-1.5 text-left">
                  {s.name}
                </button>
              ) : (
                <span className="min-w-0 flex-1 truncate px-2 py-1.5">{s.name}</span>
              )}
              <button aria-label={`Listen to ${s.name}`} onClick={() => void new Audio(api.sfxUrl(s.name)).play().catch(() => undefined)} className="px-1.5 text-faint hover:text-fg">
                <Play className="size-3.5" />
              </button>
            </Draggable>
          ))}
        </div>
      ) : (
        empty
      )
    }
    case 'backgrounds':
      return (
        <div className={cn('grid gap-4', 'grid-cols-[repeat(auto-fill,minmax(280px,1fr))]')}>
          {catalog.backgrounds.filter(match).map((b) => (
            <article key={b.name} className="overflow-hidden rounded-card border border-line bg-panel">
              <div className="grid grid-cols-3 gap-px bg-line">
                {['day', 'dusk', 'night'].map((t) => (
                  <img key={t} src={api.libraryThumbUrl('background', b.name, t, style)} alt={`${b.name} at ${t}`} loading="lazy" className="aspect-[9/14] w-full object-cover" />
                ))}
              </div>
              <div className="p-3">
                <div className="flex items-center justify-between">
                  <h3 className="font-semibold">{b.name}</h3>
                  <span className="text-[11px] text-faint">{Object.keys(b.slots ?? {}).filter((s) => !s.startsWith('off_')).length} places</span>
                </div>
                <p className="mt-1 text-[12px] leading-snug text-muted">{b.summary}</p>
              </div>
            </article>
          ))}
        </div>
      )
    case 'characters':
      return (
        <div className={cn('grid gap-4', 'grid-cols-[repeat(auto-fill,minmax(180px,1fr))]')}>
          {catalog.archetypes.filter(match).map((a) => (
            <article key={a.name} className="overflow-hidden rounded-card border border-line bg-panel">
              <img src={api.libraryThumbUrl('archetype', a.name, 'day', style)} alt={`${a.name} archetype`} loading="lazy" className="aspect-[9/12] w-full object-cover" />
              <div className="p-3">
                <h3 className="font-semibold capitalize">{a.name}</h3>
                <p className="mt-1 text-[12px] leading-snug text-muted">{a.summary}</p>
                <div className="mt-2 flex gap-1">
                  {Object.entries(a.palette ?? {})
                    .slice(0, 6)
                    .map(([role, c]) => (
                      <span key={role} title={`${role} ${c}`} className="size-4 rounded-full border border-black/10" style={{ background: c }} />
                    ))}
                </div>
              </div>
            </article>
          ))}
        </div>
      )
    case 'props':
      return (
        <div className={cn('grid gap-2', grid)}>
          {catalog.props.filter(match).map((p) => (
            <div key={p.name} className="rounded-card border border-line bg-panel p-3">
              <div className="font-medium">{p.name}</div>
              <div className="text-[12px] text-muted">{p.summary}</div>
            </div>
          ))}
        </div>
      )
    case 'transitions':
    case 'captions': {
      const list = (kind === 'transitions' ? catalog.transitions : catalog.caption_styles).filter(match)
      return (
        <div className={cn('grid gap-2', grid)}>
          {list.map((t) => (
            <div key={t.name} className="rounded-card border border-line bg-panel p-3">
              <div className="font-medium">{titleCase(t.name)}</div>
              <div className="mt-0.5 text-[12px] leading-snug text-muted">{t.summary}</div>
              {t.params && t.params.length > 0 && <div className="mt-1.5 text-[11px] text-faint">settings: {t.params.map((p) => p.name).join(', ')}</div>}
            </div>
          ))}
        </div>
      )
    }
    case 'easings':
      return (
        <div className={cn('grid gap-2', 'grid-cols-[repeat(auto-fill,minmax(150px,1fr))]')}>
          {catalog.easings.filter(match).map((e) => (
            <div key={e.name} className="flex items-center gap-2 rounded-card border border-line bg-panel p-2">
              <svg viewBox="0 0 44 22" className="h-8 w-14 shrink-0 rounded-ctl bg-raised" aria-hidden>
                <path d={curvePath(e.name)} fill="none" stroke="var(--accent)" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
              <span className="truncate text-[12px]">{titleCase(e.name)}</span>
            </div>
          ))}
        </div>
      )
  }
}
