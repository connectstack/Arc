import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Copy, GripVertical, MoreHorizontal, Plus, Trash2, UserPlus } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useApi } from '@/api/context'
import { useCatalog } from '@/api/hooks'
import type { CatalogEntry, ReelSpec } from '@/api/types'
import { ApiError } from '@/api/types'
import { Banner, Button, Menu, StatusChip, Tabs, toast, type MenuEntry } from '@/components/ui'
import { LibraryBrowser } from '@/features/library/LibraryBrowser'
import { bgColor } from '@/lib/colors'
import { cn } from '@/lib/cn'
import { normalizeSpec } from '@/lib/normalize'
import { characterColor, characterName } from '@/lib/spec'
import { plural } from '@/lib/format'
import { sceneAt, sceneSlots, sceneVisibleFrom } from '@/lib/timeline'
import { useProject } from '@/store/project'
import { useStudio, type LeftTab } from '@/store/studio'
import { SceneObjects } from './SceneObjects'
import { addCharacter, addScene, deleteSelection, duplicateSelection, moveScene } from './ops'

// ------------------------------------------------------------------------------- scene thumbnails
function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

const sceneKey = (spec: ReelSpec, i: number) => JSON.stringify([spec.scenes[i], spec.characters, spec.meta.style])

/** A mid-scene frame per scene, drawn by the engine; a scene is re-drawn only when something in it (or the cast, or the style) changed. */
function useSceneThumbs(spec: ReelSpec): (i: number) => string | undefined {
  const api = useApi()
  const cache = useRef(new Map<string, string>())
  const [, bump] = useState(0)
  const settled = useDebounced(spec, 1200)
  useEffect(() => {
    let cancelled = false
    void (async () => {
      const missing = settled.scenes.map((_, i) => i).filter((i) => !cache.current.has(sceneKey(settled, i)))
      if (!missing.length) return
      try {
        const info = await api.createPreview(settled, 0.25)
        const slots = sceneSlots(settled)
        for (const i of missing) {
          if (cancelled) return
          const frame = Math.round((slots[i].start + slots[i].duration * 0.4) * info.fps)
          const blob = await api.frame(info.preview_id, frame)
          cache.current.set(sceneKey(settled, i), URL.createObjectURL(blob))
          bump((n) => n + 1)
        }
      } catch {
        /* an invalid spec has no thumbnails yet */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [api, settled])
  useEffect(() => {
    const urls = cache.current
    return () => urls.forEach((u) => URL.revokeObjectURL(u))
  }, [])
  return (i) => cache.current.get(sceneKey(spec, i))
}

// ------------------------------------------------------------------------------- scenes tab
function SceneCard({ spec, index, thumb }: { spec: ReelSpec; index: number; thumb?: string }) {
  const select = useProject((s) => s.select)
  const edit = useProject((s) => s.edit)
  const setPlayhead = useProject((s) => s.setPlayhead)
  const selected = useProject((s) => s.selection.kind === 'scene' && s.selection.scene === index)
  const playhead = useProject((s) => s.playhead)
  const sc = spec.scenes[index]
  const slots = useMemo(() => sceneSlots(spec), [spec])
  const here = sceneAt(slots, playhead)?.index === index
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: `${sc.id}#${index}` })
  return (
    <li ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition }} className={cn('list-none', isDragging && 'z-10 opacity-80')}>
      <div className={cn('group flex items-stretch gap-2 rounded-card border p-1.5 transition-colors', selected ? 'border-accent bg-accent-soft' : 'border-line bg-raised hover:border-line-strong')}>
        <button {...attributes} {...listeners} aria-label={`Reorder scene ${index + 1}`} className="grid w-4 shrink-0 cursor-grab place-items-center text-faint hover:text-fg active:cursor-grabbing">
          <GripVertical className="size-3.5" />
        </button>
        <button
          className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
          onClick={() => {
            select({ kind: 'scene', scene: index })
            setPlayhead(sceneVisibleFrom(slots, index))
          }}
          aria-current={here ? 'true' : undefined}
        >
          <span className="relative block h-[60px] w-[34px] shrink-0 overflow-hidden rounded-[6px] bg-hover">
            {thumb ? <img src={thumb} alt="" className="size-full object-cover" /> : <span className="absolute inset-0" style={{ background: `${bgColor(sc.background.template)}33` }} />}
            {here && <span className="absolute inset-x-0 bottom-0 h-0.5 bg-accent" />}
          </span>
          <span className="min-w-0">
            <span className="block truncate text-[12.5px] font-semibold">
              <span className="sr-only">Scene </span>
              {index + 1}. {sc.id}
            </span>
            <span className="flex items-center gap-1.5 text-[11.5px] text-muted">
              <span className="size-2 rounded-full" style={{ background: bgColor(sc.background.template) }} aria-hidden />
              {sc.background.template} · <span className="tabular">{sc.duration_sec.toFixed(1)} s</span>
            </span>
            <span className="block truncate text-[11px] text-faint">{sc.captions[0]?.text ?? 'no captions'}</span>
          </span>
        </button>
        <Menu
          trigger={
            <Button variant="ghost" size="icon-sm" aria-label={`Scene ${index + 1} actions`} className="self-start opacity-0 focus-visible:opacity-100 group-hover:opacity-100">
              <MoreHorizontal className="size-4" />
            </Button>
          }
          entries={[
            { label: 'Duplicate', icon: <Copy />, onSelect: () => edit((d) => select(duplicateSelection(d, { kind: 'scene', scene: index }))) },
            { label: 'Add a scene after', icon: <Plus />, onSelect: () => edit((d) => select(addScene(d, index))) },
            { separator: true },
            { label: 'Delete', icon: <Trash2 />, danger: true, disabled: spec.scenes.length <= 1, onSelect: () => edit((d) => select(deleteSelection(d, { kind: 'scene', scene: index }))) },
          ]}
        />
      </div>
    </li>
  )
}

function ScenesTab({ spec }: { spec: ReelSpec }) {
  const edit = useProject((s) => s.edit)
  const select = useProject((s) => s.select)
  const thumbs = useSceneThumbs(spec)
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }))
  const ids = spec.scenes.map((s, i) => `${s.id}#${i}`)
  const onEnd = (e: DragEndEvent) => {
    if (!e.over || e.active.id === e.over.id) return
    const from = ids.indexOf(String(e.active.id))
    const to = ids.indexOf(String(e.over.id))
    if (from >= 0 && to >= 0) edit((d) => moveScene(d, from, to))
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto p-2.5">
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onEnd}>
          <SortableContext items={ids} strategy={verticalListSortingStrategy}>
            <ul className="m-0 flex flex-col gap-1.5 p-0">
              {spec.scenes.map((_, i) => (
                <SceneCard key={ids[i]} spec={spec} index={i} thumb={thumbs(i)} />
              ))}
            </ul>
          </SortableContext>
        </DndContext>
      </div>
      <SceneObjects spec={spec} />
      <div className="border-t border-line p-2.5">
        <Button className="w-full" onClick={() => edit((d) => select(addScene(d)))}>
          <Plus className="size-4" /> Add a scene
        </Button>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------- cast tab
function CastTab({ spec }: { spec: ReelSpec }) {
  const api = useApi()
  const catalog = useCatalog().data
  const select = useProject((s) => s.select)
  const edit = useProject((s) => s.edit)
  const selection = useProject((s) => s.selection)
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto p-2.5">
        <ul className="m-0 flex flex-col gap-1.5 p-0">
          {spec.characters.map((c) => {
            const on = selection.kind === 'character' && selection.id === c.id
            const uses = spec.scenes.filter((s) => s.layers.some((l) => l.character === c.id)).length
            return (
              <li key={c.id} className="list-none">
                <button onClick={() => select({ kind: 'character', id: c.id })} aria-pressed={on} className={cn('flex w-full items-center gap-3 rounded-card border p-2 text-left transition-colors', on ? 'border-accent bg-accent-soft' : 'border-line bg-raised hover:border-line-strong')}>
                  <span className="relative h-[52px] w-10 shrink-0 overflow-hidden rounded-[8px] bg-hover">
                    <img src={api.libraryThumbUrl('archetype', c.archetype, 'day', spec.meta.style)} alt="" loading="lazy" className="size-full object-cover" />
                    <span className="absolute bottom-0 left-0 right-0 h-1" style={{ background: characterColor(spec, catalog, c.id) }} />
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate font-semibold">{characterName(spec, c.id)}</span>
                    <span className="block text-[11.5px] text-muted">
                      {c.archetype} · {uses} {uses === 1 ? 'scene' : 'scenes'}
                    </span>
                    {c.props.length > 0 && <span className="block truncate text-[11px] text-faint">{c.props.join(', ')}</span>}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
        {spec.characters.length === 0 && <p className="px-2 py-8 text-center text-muted">Nobody is cast yet. Add a character, then give them actions on the timeline.</p>}
      </div>
      <div className="border-t border-line p-2.5">
        <Menu
          align="start"
          trigger={
            <Button className="w-full">
              <UserPlus className="size-4" /> Add a character
            </Button>
          }
          entries={bodyTypeEntries(catalog?.archetypes ?? [], (name) => edit((d) => select(addCharacter(d, name))))}
        />
      </div>
    </div>
  )
}

/** The "Add a character" menu: the engine's jointed bodies, then the pictures of the asset library (a long list, so it is sectioned). */
function bodyTypeEntries(archetypes: CatalogEntry[], add: (archetype: string) => void): MenuEntry[] {
  const entry = (a: CatalogEntry): MenuEntry => ({ label: a.name.replace(/_/g, ' '), onSelect: () => add(a.name) })
  const bodies = archetypes.filter((a) => !a.library)
  const pictures = archetypes.filter((a) => a.library)
  return [{ heading: 'Body type' }, ...bodies.map(entry), ...(pictures.length ? [{ heading: 'Pictures from the library' }, ...pictures.map(entry)] : [])]
}

// ------------------------------------------------------------------------------- script tab
/** Do the captions say what the script says? One click puts them back (words in order, speakers right, missing lines added). */
function ScriptCheck({ spec, script }: { spec: ReelSpec; script: string }) {
  const api = useApi()
  const replace = useProject((s) => s.replace)
  const [busy, setBusy] = useState(false)
  const dScript = useDebounced(script, 600)
  const dSpec = useDebounced(spec, 600)
  const check = useQuery({
    queryKey: ['script-check', dScript, JSON.stringify([dSpec.characters, dSpec.scenes.map((s) => s.captions)])],
    queryFn: () => api.scriptCheck(dScript, dSpec),
    enabled: dScript.trim().length > 0,
    staleTime: 5_000,
  })
  const c = check.data
  if (!script.trim() || (c && c.empty)) return null
  const fix = async () => {
    setBusy(true)
    try {
      const r = await api.scriptLock(script, spec)
      replace(normalizeSpec(r.spec))
      toast.success('The captions follow your script', r.notes[0] ?? 'Nothing needed changing.')
    } catch (e) {
      toast.error('Could not follow the script', e instanceof ApiError ? `${e.detail}${e.hint ? ` — ${e.hint}` : ''}` : String(e))
    } finally {
      setBusy(false)
    }
  }
  if (!c) return <p className="mb-3 text-[11.5px] text-faint">{check.isError ? 'The script could not be compared with the captions yet.' : 'Checking the captions against the script…'}</p>
  if (c.ok) {
    return (
      <div className="mb-3">
        <StatusChip tone="success">Every line of the script is in the captions, in order</StatusChip>
      </div>
    )
  }
  const missing = c.missing ?? []
  return (
    <div className="mb-3 flex flex-col gap-2">
      <Banner tone="warning" title={`The captions show ${Math.round((c.covered ?? 0) * 100)}% of your script`}>
        {[
          missing.length ? plural(missing.length, 'line') + ' of the script ' + (missing.length === 1 ? 'is' : 'are') + ' not in any caption' : '',
          c.labels ? plural(c.labels, 'caption') + (c.labels === 1 ? ' is' : ' are') + ' only a speaker’s name' : '',
          c.foreign ? plural(c.foreign, 'caption') + ' ' + (c.foreign === 1 ? 'has' : 'have') + ' words the script does not' : '',
        ]
          .filter(Boolean)
          .join('; ')}
        .
      </Banner>
      {missing.length > 0 && (
        <ul className="m-0 flex flex-col gap-0.5 p-0 text-[12px] text-muted" dir="auto">
          {missing.slice(0, 4).map((m, i) => (
            <li key={i} className="list-none truncate before:mr-1.5 before:text-faint before:content-['+']">
              {m.speaker ? `${m.speaker}: ` : ''}
              {m.text}
            </li>
          ))}
          {missing.length > 4 && <li className="list-none text-faint">and {missing.length - 4} more</li>}
        </ul>
      )}
      {c.fits === false ? (
        <p className="text-[12px] text-muted">This script takes about {Math.round(c.reading_sec ?? 0)} s to read and a reel holds about 55 s, so it cannot be shown word for word: shorten it, or let a planner condense it.</p>
      ) : (
        <Button variant="primary" size="sm" className="self-start" onClick={() => void fix()} disabled={busy}>
          Make the captions follow my script
        </Button>
      )}
    </div>
  )
}

function ScriptTab({ spec }: { spec: ReelSpec }) {
  const script = useProject((s) => s.script)
  const setScript = useProject((s) => s.setScript)
  const select = useProject((s) => s.select)
  const norm = (t: string) => t.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()
  const lines = script.split('\n')
  const links = useMemo(() => {
    const idx = new Map<string, { scene: number; caption: number }>()
    spec.scenes.forEach((sc, si) => sc.captions.forEach((c, ci) => idx.set(norm(c.text), { scene: si, caption: ci })))
    return lines.map((l) => {
      const n = norm(l.replace(/^[^:：]{1,24}[:：]/, ''))
      return n.length > 3 ? idx.get(n) : undefined
    })
  }, [spec, lines])
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <ScriptCheck spec={spec} script={script} />
        <p className="mb-2 text-[11.5px] leading-snug text-faint">The script this reel was planned from. Lines that became a caption link to it. Editing here changes the script; “Make the captions follow my script” applies it to the reel (one undo step).</p>
        {script.trim() ? (
          <ul className="m-0 mb-3 flex flex-col gap-0.5 p-0" dir="auto">
            {lines.map((l, i) =>
              l.trim() ? (
                <li key={i} className="list-none">
                  {links[i] ? (
                    <button className="w-full rounded-ctl px-2 py-1 text-left text-[12.5px] hover:bg-hover" onClick={() => select({ kind: 'caption', scene: links[i]!.scene, caption: links[i]!.caption })}>
                      {l} <span className="ml-1 text-[10.5px] text-accent">scene {links[i]!.scene + 1} →</span>
                    </button>
                  ) : (
                    <p className="px-2 py-1 text-[12.5px] text-muted">{l}</p>
                  )}
                </li>
              ) : null,
            )}
          </ul>
        ) : (
          <p className="mb-3 text-muted">This reel has no script (it was made by hand or from a spec file).</p>
        )}
        <label className="eyebrow mb-1 block">Edit the script text</label>
        <textarea dir="auto" value={script} onChange={(e) => setScript(e.target.value)} rows={8} aria-label="Script" className="w-full resize-y rounded-ctl border border-line bg-raised p-2.5 text-[12.5px] leading-relaxed outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/35" />
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------- the panel
export function LeftPanel() {
  const spec = useProject((s) => s.spec) as ReelSpec
  const tab = useStudio((s) => s.leftTab)
  const set = useStudio((s) => s.set)
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Tabs
        label="Panels"
        value={tab}
        onChange={(v) => set({ leftTab: v as LeftTab })}
        tabs={[
          { value: 'scenes', label: 'Scenes' },
          { value: 'cast', label: 'Cast' },
          { value: 'script', label: 'Script' },
          { value: 'library', label: 'Library' },
        ]}
      >
        {tab === 'scenes' && <ScenesTab spec={spec} />}
        {tab === 'cast' && <CastTab spec={spec} />}
        {tab === 'script' && <ScriptTab spec={spec} />}
        {tab === 'library' && <LibraryBrowser compact />}
      </Tabs>
    </div>
  )
}
