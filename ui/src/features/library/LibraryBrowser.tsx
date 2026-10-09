import { Move, Play, Plus, Search } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useApi } from '@/api/context'
import { useAssets, useCatalog } from '@/api/hooks'
import type { AssetInfo, Catalog, CatalogEntry } from '@/api/types'
import { Button, Chip, EmptyState, Input, Segmented } from '@/components/ui'
import { AddAssetDialog, type AddAssetInitial } from '@/features/assets/AddAssetDialog'
import { AssetDetail } from '@/features/assets/AssetDetail'
import { KIND_INFO } from '@/features/assets/assetFields'
import { cn } from '@/lib/cn'
import { curvePath } from '@/lib/easing'
import { titleCase } from '@/lib/format'
import { sceneAt, sceneSlots } from '@/lib/timeline'
import { useProject } from '@/store/project'
import { addAction, addCameraMove, addObject, addSfx } from '../studio/ops'
import { EngineBackgroundCard, EngineCharacterCard, KIND_ICON, LibraryCard, LibraryPlaceCard, ObjectRow } from './AssetCards'
import { Draggable } from './Draggable'
import { SECTION_KIND, fromMatches, isAssetSection, itemMatches, libraryItems, matchesQuery, type AssetSection, type From, type LibraryItem } from './libraryItems'

export type LibrarySection = 'actions' | 'camera' | 'sounds' | 'backgrounds' | 'characters' | 'objects' | 'props' | 'captions' | 'transitions' | 'easings'

const SECTIONS: { value: LibrarySection; label: string }[] = [
  { value: 'actions', label: 'Actions' },
  { value: 'backgrounds', label: 'Backgrounds' },
  { value: 'characters', label: 'Characters' },
  { value: 'objects', label: 'Objects' },
  { value: 'props', label: 'Props' },
  { value: 'sounds', label: 'Sounds' },
  { value: 'camera', label: 'Camera' },
  { value: 'transitions', label: 'Transitions' },
  { value: 'captions', label: 'Captions' },
  { value: 'easings', label: 'Easings' },
]

/** The studio's left panel is narrow: only what can be added to the scene is offered there. */
const COMPACT: LibrarySection[] = ['actions', 'objects', 'sounds', 'camera']

const FROM_OPTIONS: { value: From; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'builtin', label: 'Built in' },
  { value: 'user', label: 'Yours' },
]

type AddKind = 'action' | 'camera' | 'sfx' | 'object'

/** Add a library item at the playhead (for the selected character, or the first one in the scene; an object goes in the playhead's scene). */
function useAddToScene() {
  const edit = useProject((s) => s.edit)
  const select = useProject((s) => s.select)
  const catalog = useCatalog().data
  return (kind: AddKind, name: string) => {
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
      else if (kind === 'object') sel = addObject(d, catalog, name, t)
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

export interface LibraryBrowserProps {
  compact?: boolean
  /** shows the catalogue on its own (no project open): nothing can be added to a scene or dragged */
  browse?: boolean
  styleName?: string
  /** told which section is shown (the page offers "Add asset" for the kind being looked at) */
  onSectionChange?: (section: LibrarySection) => void
  /** "Add asset" from inside the browser (an empty "Yours" list, the studio's Objects list). Without it the browser opens the dialog itself. */
  onAddAsset?: (initial?: AddAssetInitial) => void
  /** Go to a section (and show everything in it, with no search): each new `key` is a request, so the same section can be asked for twice */
  reveal?: { section: LibrarySection; key: number }
}

export function LibraryBrowser({ compact = false, browse = false, styleName, onSectionChange, onAddAsset, reveal }: LibraryBrowserProps) {
  const api = useApi()
  const catalog = useCatalog().data
  const library = useAssets().data
  const [section, setSection] = useState<LibrarySection>('actions')
  const [q, setQ] = useState('')
  const [from, setFrom] = useState<From>('all')
  const [detail, setDetail] = useState<AssetInfo | null>(null)
  const [own, setOwn] = useState<AddAssetInitial | null>(null)
  const add = useAddToScene()
  const projectStyle = useProject((s) => s.spec?.meta.style ?? 'flat_vector')
  const style = styleName ?? projectStyle
  const sections = compact ? SECTIONS.filter((s) => COMPACT.includes(s.value)) : SECTIONS
  const showFrom = !compact && isAssetSection(section)

  const choose = (next: LibrarySection) => {
    setSection(next)
    onSectionChange?.(next)
  }
  // something was just added: show it where it belongs, and not behind a search or a filter
  const revealKey = reveal?.key
  useEffect(() => {
    if (!reveal) return
    setSection(reveal.section)
    setFrom('all')
    setQ('')
    onSectionChange?.(reveal.section)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealKey])
  const addAsset = (initial?: AddAssetInitial) => (onAddAsset ? onAddAsset(initial) : setOwn(initial ?? {}))
  const match = (e: { name: string; summary?: string; tags?: string[] }) => matchesQuery(e, q)

  const body = !catalog ? null : isAssetSection(section) ? (
    <AssetSection
      section={section}
      catalog={catalog}
      assets={library?.assets}
      folder={library?.folder}
      q={q}
      from={from}
      style={style}
      compact={compact}
      onOpen={setDetail}
      onAddAsset={addAsset}
      onAddToScene={browse ? null : (name) => add('object', name)}
    />
  ) : (
    renderKind(section, catalog, { api, match, add: browse ? null : add, compact })
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className={cn('flex flex-col gap-2.5 border-b border-line p-3', !compact && 'px-0 pt-0')}>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="relative min-w-[180px] flex-1">
            <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" aria-hidden />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${section}…`} aria-label="Search the library" className="pl-8" />
          </div>
          {showFrom && (
            <div className="flex items-center gap-2">
              <span className="eyebrow" aria-hidden>
                From
              </span>
              <Segmented<From> label="From" value={from} onChange={setFrom} options={FROM_OPTIONS} />
            </div>
          )}
        </div>
        {compact ? (
          // four sections in the narrow left panel: a little less padding in each, so they fit down to a panel of about 230 px
          <Segmented label="Library section" size="sm" value={section} onChange={choose} options={sections} className="max-w-full self-start [&>button]:px-1.5" />
        ) : (
          <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Library sections">
            {sections.map((k) => (
              <button key={k.value} role="tab" aria-selected={section === k.value} onClick={() => choose(k.value)} className={cn('rounded-full border px-3 py-1 text-[12px] font-medium transition-colors', section === k.value ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:text-fg')}>
                {k.label}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className={cn('min-h-0 flex-1 overflow-y-auto', compact ? 'p-2' : 'pt-4')}>{body}</div>

      {detail && (
        <AssetDetail
          asset={detail}
          open
          onOpenChange={(o) => !o && setDetail(null)}
          onUpdated={setDetail}
          onDeleted={() => setDetail(null)}
          onOverride={(a) => {
            setDetail(null)
            addAsset({ name: a.name, kind: a.kind, summary: a.summary, tags: a.tags, hint: `Your picture will be used instead of the built-in “${a.name}” in every reel.` })
          }}
        />
      )}
      {!onAddAsset && own && <AddAssetDialog open onOpenChange={(o) => !o && setOwn(null)} initial={own} />}
    </div>
  )
}

interface SectionProps {
  section: AssetSection
  catalog: Catalog
  assets: AssetInfo[] | undefined
  folder: string | undefined
  q: string
  from: From
  style: string
  compact: boolean
  onOpen: (asset: AssetInfo) => void
  onAddAsset: (initial?: AddAssetInitial) => void
  /** adds the object to the scene at the playhead (the studio's left panel); null when no project is open */
  onAddToScene: ((name: string) => void) | null
}

/** Characters, objects or places: the engine's own and the asset library's, filtered by where they come from and by the search. */
function AssetSection({ section, catalog, assets, folder, q, from, style, compact, onOpen, onAddAsset, onAddToScene }: SectionProps) {
  const kind = SECTION_KIND[section]
  const info = KIND_INFO[kind]
  const Icon = KIND_ICON[kind]
  const all = libraryItems(catalog, section, assets)
  const mine = all.filter((i) => i.origin === 'user').length
  const items = all.filter((i) => fromMatches(from, i.origin) && itemMatches(i, q))
  const addMine = () => onAddAsset({ kind })

  if (all.length === 0 || (from === 'user' && mine === 0)) {
    const none = all.length === 0
    return (
      <EmptyState
        art={<Icon className="size-9 text-accent" strokeWidth={1.4} aria-hidden />}
        title={none ? `The library has no ${info.plural} yet` : `You have not added any ${info.plural} yet`}
        action={
          <Button variant="primary" onClick={addMine}>
            <Plus className="size-4" aria-hidden /> Add asset
          </Button>
        }
      >
        {compact ? 'Choose a picture and it is ready to use in a scene.' : 'Drop a picture on this page, or press Add asset.'} SVG, PNG, JPG and WebP all work, and a picture with a plain background gets it removed automatically.
        {folder && !compact ? (
          <>
            {' '}
            Your assets are saved in <code className="break-all font-mono text-[12px] text-fg">{folder}</code>.
          </>
        ) : null}
      </EmptyState>
    )
  }
  if (items.length === 0) return <p className="px-2 py-8 text-center text-muted">Nothing matches.</p>

  const count = (
    <div className="sr-only" role="status">
      {items.length} {items.length === 1 ? info.label.toLowerCase() : info.plural} shown
    </div>
  )

  if (compact) {
    return (
      <>
        <div className="grid gap-0.5">
          {items.map((i) => (
            <ObjectRow key={i.entry.name} item={i} style={style} onAdd={onAddToScene ? () => onAddToScene(i.entry.name) : null} />
          ))}
        </div>
        <button type="button" onClick={addMine} className="mt-2 flex w-full items-center gap-2 rounded-ctl border-t border-line px-2 pb-1 pt-2.5 text-left text-accent hover:underline">
          <Plus className="size-3.5" aria-hidden /> Add your own…
        </button>
        {count}
      </>
    )
  }

  const grid = section === 'backgrounds' ? 'grid-cols-[repeat(auto-fill,minmax(280px,1fr))]' : 'grid-cols-[repeat(auto-fill,minmax(180px,1fr))]'
  return (
    <>
      <div className={cn('grid gap-4', grid)}>
        {items.map((i) => (
          <Entry key={i.entry.name} item={i} section={section} style={style} onOpen={onOpen} />
        ))}
      </div>
      {count}
    </>
  )
}

/** The engine's own characters and sets keep the renderer's thumbnails; everything from the asset library has its own card. */
function Entry({ item, section, style, onOpen }: { item: LibraryItem; section: AssetSection; style: string; onOpen: (a: AssetInfo) => void }) {
  if (item.origin === 'engine' && section === 'backgrounds') return <EngineBackgroundCard entry={item.entry} style={style} />
  if (item.origin === 'engine' && section === 'characters') return <EngineCharacterCard entry={item.entry} style={style} />
  return section === 'backgrounds' ? <LibraryPlaceCard item={item} style={style} onOpen={onOpen} /> : <LibraryCard item={item} style={style} onOpen={onOpen} />
}

interface Ctx {
  api: ReturnType<typeof useApi>
  match: (e: { name: string; summary?: string; tags?: string[] }) => boolean
  add: ReturnType<typeof useAddToScene> | null
  compact: boolean
}

/** The sections that are the engine's alone: actions, camera moves, sounds, props, transitions, caption styles and easings. */
function renderKind(kind: Exclude<LibrarySection, AssetSection>, catalog: Catalog, { api, match, add, compact }: Ctx): ReactNode {
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
