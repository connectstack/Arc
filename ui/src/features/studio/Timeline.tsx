import { ArrowRightToLine, AudioLines, Blend, BookOpen, Camera, Film, Minus, Music, Plus, Scissors, Search, Sparkles, Type, Volume2, Zap } from 'lucide-react'
import { memo, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type DragEvent, type PointerEvent as RPointer, type ReactNode } from 'react'
import { useApi } from '@/api/context'
import { useCatalog } from '@/api/hooks'
import type { AudioResult, Catalog, ReelSpec } from '@/api/types'
import { Button, ContextMenu, Input, Menu, Popover, Tip, type MenuEntry } from '@/components/ui'
import { bgColor, rgba } from '@/lib/colors'
import { cn } from '@/lib/cn'
import { characterColor, characterName, sameSelection, type Selection } from '@/lib/spec'
import { budgetState, clamp, fitToDuration, ms, packRows, sceneSlots, snap, totalDuration, type Slot } from '@/lib/timeline'
import { useAudio } from '@/store/audio'
import { MOD } from '@/lib/hotkeys'
import { useNarrow } from '@/lib/useMedia'
import { useClipboard } from '@/store/clipboard'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { beginClipDrag } from './timeline/drag'
import { CLIP_H, GUTTER as GUTTER_WIDE, GUTTER_PHONE, MAX_ZOOM, MIN_ZOOM, PAD_RIGHT, ROW_PAD, RULER_H, rulerSteps, tickLabel } from './timeline/geometry'
import { copySelection, deleteSelected, duplicateSelected, ensureSelected, nudgeSelected, pasteAtPlayhead } from './clipboardActions'
import { baseOf, selectedClips, shiftClips } from './clips'
import { addAction, addCameraMove, addCaption, addSfx, addScene, clipOf, setClipTimes, splitAtPlayhead } from './ops'

/** The id a clip has in the lanes (`a-scene-layer-index` ...). */
const keyOf = (s: Selection): string => (s.kind === 'action' ? `a-${s.scene}-${s.layer}-${s.action}` : s.kind === 'caption' ? `c-${s.scene}-${s.caption}` : s.kind === 'camera' ? `m-${s.scene}-${s.move}` : s.kind === 'sfx' ? `s-${s.scene}-${s.sfx}` : '')

// ------------------------------------------------------------------------------- model
interface ClipItem {
  key: string
  sel: Selection
  scene: number
  t0: number
  t1: number
  g0: number
  g1: number
  label: string
  sub?: string
  color: string
  row: number
  overlap?: boolean
  kind: 'action' | 'caption' | 'camera' | 'sfx'
}

interface LaneModel {
  key: string
  kind: 'scenes' | 'camera' | 'char' | 'captions' | 'sfx' | 'audio'
  label: string
  charId?: string
  color?: string
  items: ClipItem[]
  rows: number
}

function buildLanes(spec: ReelSpec, catalog: Catalog | undefined, slots: Slot[]): LaneModel[] {
  const char = new Map<string, ClipItem[]>()
  for (const c of spec.characters) char.set(c.id, [])
  const camera: ClipItem[] = []
  const captions: ClipItem[] = []
  const sfx: ClipItem[] = []
  spec.scenes.forEach((sc, si) => {
    const base = slots[si].start
    sc.layers.forEach((l, li) => {
      const color = characterColor(spec, catalog, l.character)
      const sorted = l.actions.map((a, ai) => ({ a, ai })).sort((x, y) => x.a.t0 - y.a.t0)
      sorted.forEach(({ a, ai }, k) => {
        const next = sorted[k + 1]?.a
        const prev = sorted[k - 1]?.a
        const overlap = (next && next.t0 < a.t1 - 1e-6) || (prev && prev.t1 > a.t0 + 1e-6) || false
        const list = char.get(l.character) ?? []
        list.push({ key: `a-${si}-${li}-${ai}`, sel: { kind: 'action', scene: si, layer: li, action: ai }, scene: si, t0: a.t0, t1: a.t1, g0: base + a.t0, g1: base + a.t1, label: a.name, color, row: 0, overlap: !!overlap, kind: 'action' })
        char.set(l.character, list)
      })
    })
    sc.captions.forEach((c, ci) => {
      const color = c.speaker ? characterColor(spec, catalog, c.speaker) : '#94a3b8'
      captions.push({ key: `c-${si}-${ci}`, sel: { kind: 'caption', scene: si, caption: ci }, scene: si, t0: c.t0, t1: c.t1, g0: base + c.t0, g1: base + c.t1, label: c.text, sub: c.speaker ? characterName(spec, c.speaker) : undefined, color, row: 0, kind: 'caption' })
    })
    sc.camera.moves.forEach((m, mi) => {
      camera.push({ key: `m-${si}-${mi}`, sel: { kind: 'camera', scene: si, move: mi }, scene: si, t0: m.t0, t1: m.t1, g0: base + m.t0, g1: base + m.t1, label: m.type.replace('_', ' '), color: '#60a5fa', row: 0, kind: 'camera' })
    })
    sc.sfx.forEach((x, xi) => {
      sfx.push({ key: `s-${si}-${xi}`, sel: { kind: 'sfx', scene: si, sfx: xi }, scene: si, t0: x.t, t1: x.t, g0: base + x.t, g1: base + x.t, label: x.name, color: '#fbbf24', row: 0, kind: 'sfx' })
    })
  })
  const lane = (key: string, kind: LaneModel['kind'], label: string, items: ClipItem[], extra: Partial<LaneModel> = {}): LaneModel => {
    const rows = packRows(items.map((i) => ({ t0: i.g0, t1: Math.max(i.g1, i.g0 + 0.4) })))
    items.forEach((it, i) => (it.row = rows[i]))
    return { key, kind, label, items, rows: Math.max(1, ...rows.map((r) => r + 1)), ...extra }
  }
  return [
    { key: 'scenes', kind: 'scenes', label: 'Scenes', items: [], rows: 1 },
    lane('camera', 'camera', 'Camera', camera),
    ...[...char.entries()].map(([id, items]) => lane(`char-${id}`, 'char', characterName(spec, id), items, { charId: id, color: characterColor(spec, catalog, id) })),
    lane('captions', 'captions', 'Captions', captions),
    lane('sfx', 'sfx', 'Sound effects', sfx),
    { key: 'audio', kind: 'audio', label: 'Audio', items: [], rows: 1 },
  ]
}

// ------------------------------------------------------------------------------- clips
const ClipView = memo(function ClipView({
  it,
  zoom,
  selected,
  onSelect,
  onToggle,
  onDrag,
  onKey,
  menu,
}: {
  it: ClipItem
  zoom: number
  selected: boolean
  onSelect: () => void
  /** Shift+click: add to or take out of the selection */
  onToggle: () => void
  onDrag: (e: RPointer, mode: 'move' | 'start' | 'end', it: ClipItem) => void
  onKey: (e: React.KeyboardEvent, it: ClipItem) => void
  menu: (it: ClipItem) => MenuEntry[]
}) {
  const isPoint = it.kind === 'sfx'
  const width = isPoint ? 0 : Math.max(8, (it.g1 - it.g0) * zoom)
  const style: CSSProperties = { left: it.g0 * zoom, top: ROW_PAD + it.row * (CLIP_H + 4), height: CLIP_H, width: isPoint ? undefined : width }
  const label = `${it.label}${it.sub ? `, ${it.sub}` : ''}, ${it.t0.toFixed(2)}${isPoint ? '' : ` to ${it.t1.toFixed(2)}`} seconds`
  const body = (
    <div
      role="button"
      tabIndex={0}
      aria-label={label}
      aria-pressed={selected}
      data-clip={it.key}
      onPointerDown={(e) => {
        if (e.shiftKey && e.button === 0) {
          e.preventDefault()
          return onToggle()
        }
        onSelect()
        onDrag(e, 'move', it)
      }}
      onKeyDown={(e) => onKey(e, it)}
      onClick={(e) => e.stopPropagation()}
      className={cn(
        'group absolute flex cursor-grab items-center overflow-hidden text-[12px] font-medium outline-none active:cursor-grabbing',
        isPoint ? '-translate-x-1/2 gap-1 rounded-chip px-1.5' : 'rounded-[6px]',
        selected ? 'z-10 ring-2 ring-accent' : 'ring-1 ring-black/10 hover:brightness-110 focus-visible:ring-2 focus-visible:ring-accent',
      )}
      style={{ ...style, background: rgba(it.color, selected ? 0.5 : 0.3) }}
      title={label}
    >
      {!isPoint && <span className="absolute inset-y-0 left-0 w-[3px]" style={{ background: it.color }} />}
      {it.overlap && <span className="stripes pointer-events-none absolute inset-0 opacity-60" />}
      {isPoint ? (
        <>
          <Volume2 className="size-3" style={{ color: it.color }} aria-hidden />
          <span className="max-w-[110px] truncate text-fg">{it.label}</span>
        </>
      ) : (
        <span className="relative min-w-0 truncate pl-2.5 pr-1 text-fg">
          {it.label}
          {it.sub && width > 120 && <span className="ml-1.5 text-muted">{it.sub}</span>}
        </span>
      )}
      {!isPoint && (
        <>
          <span onPointerDown={(e) => onDrag(e, 'start', it)} className="absolute inset-y-0 left-0 z-10 w-1.5 cursor-ew-resize" aria-hidden />
          <span onPointerDown={(e) => onDrag(e, 'end', it)} className="absolute inset-y-0 right-0 z-10 w-1.5 cursor-ew-resize" aria-hidden />
        </>
      )}
    </div>
  )
  return <ContextMenu entries={menu(it)}>{body}</ContextMenu>
})

// ------------------------------------------------------------------------------- pickers
function ActionPicker({ onPick, catalog }: { onPick: (name: string) => void; catalog: Catalog | undefined }) {
  const [q, setQ] = useState('')
  const groups = useMemo(() => {
    const needle = q.toLowerCase()
    const by: Record<string, { name: string; summary: string }[]> = {}
    for (const a of catalog?.actions ?? []) {
      if (needle && !`${a.name} ${a.summary}`.toLowerCase().includes(needle)) continue
      ;(by[a.category ?? 'other'] ??= []).push(a)
    }
    return by
  }, [catalog, q])
  return (
    <div className="w-[300px]">
      <div className="relative mb-2">
        <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" aria-hidden />
        <Input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find an action…" aria-label="Find an action" className="pl-8" />
      </div>
      <div className="max-h-[320px] overflow-y-auto">
        {Object.entries(groups).map(([cat, list]) => (
          <div key={cat}>
            <div className="eyebrow px-1 pb-1 pt-2">{cat}</div>
            {list.map((a) => (
              <button key={a.name} onClick={() => onPick(a.name)} className="flex w-full flex-col rounded-ctl px-2 py-1.5 text-left hover:bg-hover">
                <span className="font-medium">{a.name}</span>
                <span className="line-clamp-1 text-[11.5px] text-muted">{a.summary}</span>
              </button>
            ))}
          </div>
        ))}
        {Object.keys(groups).length === 0 && <p className="px-2 py-4 text-center text-muted">No action matches.</p>}
      </div>
    </div>
  )
}

function SfxPicker({ onPick, catalog }: { onPick: (name: string) => void; catalog: Catalog | undefined }) {
  const api = useApi()
  const [q, setQ] = useState('')
  const list = (catalog?.sfx ?? []).filter((s) => s.name.includes(q.toLowerCase().replace(/\s+/g, '_')))
  const audition = (name: string) => void new Audio(api.sfxUrl(name)).play().catch(() => undefined)
  return (
    <div className="w-[260px]">
      <Input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a sound…" aria-label="Find a sound effect" className="mb-2" />
      <div className="grid max-h-[300px] grid-cols-2 gap-1 overflow-y-auto">
        {list.map((s) => (
          <div key={s.name} className="flex items-center rounded-ctl hover:bg-hover">
            <button onClick={() => onPick(s.name)} className="min-w-0 flex-1 truncate px-2 py-1.5 text-left">
              {s.name}
            </button>
            <button aria-label={`Listen to ${s.name}`} onClick={() => audition(s.name)} className="px-1.5 text-faint hover:text-fg">
              <Volume2 className="size-3.5" />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------- budget bar
function BudgetBar({ spec }: { spec: ReelSpec }) {
  const catalog = useCatalog().data
  const total = totalDuration(spec)
  const min = catalog?.limits.min_total_sec ?? 45
  const max = catalog?.limits.max_total_sec ?? 60
  const state = budgetState(total, min, max)
  const replace = useProject((s) => s.replace)
  const scale = Math.max(max + 5, total + 3)
  const pct = (v: number) => `${(v / scale) * 100}%`
  const tone = state === 'ok' ? 'bg-success' : state === 'near' ? 'bg-warning' : 'bg-danger'
  const text = state === 'ok' ? 'within the budget' : state === 'near' ? 'close to the limit' : total < min ? `${(min - total).toFixed(1)} s too short` : `${(total - max).toFixed(1)} s too long`
  return (
    <div className="flex items-center gap-3">
      <div className="flex items-baseline gap-1.5 text-[12px]" title={`A reel must be ${min}-${max} s long: scene time minus the overlaps of transitions`}>
        <span className="font-mono text-[13px] font-semibold tabular">{total.toFixed(1)} s</span>
        <span className={cn('hidden @3xl:inline', state === 'ok' ? 'text-muted' : state === 'near' ? 'text-warning' : 'text-danger')}>{text}</span>
      </div>
      <div className="relative h-2 w-20 rounded-full bg-hover @2xl:w-32 @4xl:w-48" role="img" aria-label={`Reel length ${total.toFixed(1)} seconds of an allowed ${min} to ${max}`}>
        <div className="absolute inset-y-0 rounded-full bg-success/20" style={{ left: pct(min), width: `calc(${pct(max)} - ${pct(min)})` }} />
        <div className={cn('absolute inset-y-0 left-0 rounded-full', tone)} style={{ width: pct(Math.min(total, scale)) }} />
        {[min, max].map((m) => (
          <span key={m} className="absolute -top-0.5 h-3 w-px bg-fg/50" style={{ left: pct(m) }} />
        ))}
      </div>
      {state !== 'ok' && (
        <Button size="sm" variant={state === 'out' ? 'primary' : 'secondary'} onClick={() => replace(fitToDuration(spec, 50, catalog?.limits.max_scene_sec ?? 30))} title="Scale every scene (and what happens inside it) so the reel is 50 s long">
          Fit to 50 s
        </Button>
      )}
    </div>
  )
}

// ------------------------------------------------------------------------------- the timeline
export function Timeline() {
  const spec = useProject((s) => s.spec) as ReelSpec
  const api = useApi()
  const catalog = useCatalog().data
  const playhead = useProject((s) => s.playhead)
  const setPlayhead = useProject((s) => s.setPlayhead)
  const selection = useProject((s) => s.selection)
  const extra = useProject((s) => s.extra)
  const select = useProject((s) => s.select)
  const toggleSelect = useProject((s) => s.toggleSelect)
  const edit = useProject((s) => s.edit)
  const board = useClipboard((s) => s.board)
  const zoom = useStudio((s) => s.zoom)
  const setStudio = useStudio((s) => s.set)
  const playing = useStudio((s) => s.playing)
  const loop = useStudio((s) => s.loop)
  const audio = useAudio((s) => s.result)
  const scroller = useRef<HTMLDivElement>(null)
  const gutter = useNarrow() ? GUTTER_PHONE : GUTTER_WIDE
  const [announce, setAnnounce] = useState('')
  const [viewW, setViewW] = useState(900)

  const slots = useMemo(() => sceneSlots(spec), [spec])
  const lanes = useMemo(() => buildLanes(spec, catalog, slots), [spec, catalog, slots])
  const total = slots.length ? slots[slots.length - 1].end : 0
  const contentW = Math.max(viewW - gutter, total * zoom + PAD_RIGHT)

  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const ro = new ResizeObserver(() => setViewW(el.clientWidth))
    ro.observe(el)
    setViewW(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  // zoom with Cmd/Ctrl + wheel, anchored at the pointer
  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return
      e.preventDefault()
      const z = useStudio.getState().zoom
      const next = clamp(z * Math.exp(-e.deltaY * 0.004), MIN_ZOOM, MAX_ZOOM)
      const rect = el.getBoundingClientRect()
      const x = e.clientX - rect.left - gutter + el.scrollLeft
      const t = x / z
      setStudio({ zoom: next })
      requestAnimationFrame(() => (el.scrollLeft = t * next - (e.clientX - rect.left - gutter)))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [setStudio, gutter])

  // keep the playhead in view while playing
  useEffect(() => {
    const el = scroller.current
    if (!el || !playing) return
    const x = playhead * zoom
    if (x > el.scrollLeft + el.clientWidth - gutter - 80 || x < el.scrollLeft) el.scrollLeft = Math.max(0, x - 120)
  }, [playhead, playing, zoom, gutter])

  const timeAt = (clientX: number, areaEl: Element): number => (clientX - areaEl.getBoundingClientRect().left) / zoom

  const snapPointsFor = useCallback(
    (sceneIdx: number, skip: ReadonlySet<string>) => {
      const cur = useProject.getState().spec as ReelSpec
      const sl = sceneSlots(cur)
      const pts = [useProject.getState().playhead, ...sl.flatMap((s) => [s.start, s.end])]
      const sc = cur.scenes[sceneIdx]
      const base = sl[sceneIdx]?.start ?? 0
      sc?.layers.forEach((l, li) => l.actions.forEach((a, ai) => !skip.has(`a-${sceneIdx}-${li}-${ai}`) && pts.push(base + a.t0, base + a.t1)))
      sc?.captions.forEach((c, ci) => !skip.has(`c-${sceneIdx}-${ci}`) && pts.push(base + c.t0, base + c.t1))
      sc?.camera.moves.forEach((m, mi) => !skip.has(`m-${sceneIdx}-${mi}`) && pts.push(base + m.t0, base + m.t1))
      sc?.sfx.forEach((x, xi) => !skip.has(`s-${sceneIdx}-${xi}`) && pts.push(base + x.t))
      return pts
    },
    [],
  )

  const onDrag = useCallback(
    (e: RPointer, mode: 'move' | 'start' | 'end', it: ClipItem) => {
      const st = useProject.getState()
      const cur = st.spec as ReelSpec
      const sl = sceneSlots(cur)[it.scene]
      // dragging one clip of a selection moves them all by the same step
      const sels = selectedClips(st.selection, st.extra)
      const together = mode === 'move' && sels.length > 1 && sels.some((x) => sameSelection(x, it.sel))
      const group = together ? baseOf(cur, sels) : null
      const skip = new Set(together ? sels.map(keyOf) : [it.key])
      beginClipDrag(e, mode, {
        t0: it.t0,
        t1: it.t1,
        sceneDuration: cur.scenes[it.scene].duration_sec,
        sceneStart: sl.start,
        zoom: useStudio.getState().zoom,
        point: it.kind === 'sfx',
        snapPoints: () => snapPointsFor(it.scene, skip),
        apply: (t0, t1) =>
          edit(
            (d) => {
              if (group) shiftClips(d, group, t0 - it.t0)
              else if (it.sel.kind === 'sfx') {
                const x = d.scenes[it.sel.scene]?.sfx[it.sel.sfx]
                if (x) x.t = t0
              } else setClipTimes(d, it.sel, t0, t1)
            },
            { live: true },
          ),
        onTap: () => {
          // a click on a clip that belongs to a selection narrows the selection to it
          if (sels.length > 1) st.select(it.sel)
          // a click on a sound effect plays it, at the volume it has in the reel
          if (it.sel.kind === 'sfx') {
            const x = (useProject.getState().spec as ReelSpec).scenes[it.scene]?.sfx[it.sel.sfx]
            const a = new Audio(api.sfxUrl(it.label))
            a.volume = Math.max(0, Math.min(1, x?.volume ?? 1))
            void a.play().catch(() => undefined)
          }
        },
      })
    },
    [api, edit, snapPointsFor],
  )

  const onKey = useCallback(
    (e: React.KeyboardEvent, it: ClipItem) => {
      const st = useProject.getState()
      const fps = st.spec?.meta.fps ?? 30
      const say = (m: string) => setAnnounce(m)
      const many = selectedClips(st.selection, st.extra)
      const together = many.length > 1 && many.some((x) => sameSelection(x, it.sel))
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault()
        const dir = e.key === 'ArrowLeft' ? -1 : 1
        const dt = e.altKey ? 1 / fps : e.shiftKey ? 1 : 0.1
        if (!together) st.select(it.sel)
        nudgeSelected(dir * dt)
        const c = clipOf(useProject.getState().spec as ReelSpec, it.sel)
        say(together ? `Moved ${many.length} clips` : c ? `Moved ${it.label} to ${c.t0.toFixed(2)}–${c.t1.toFixed(2)} s` : `Moved ${it.label}`)
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault()
        if (!together) st.select(it.sel)
        deleteSelected()
        say(together ? `Deleted ${many.length} clips` : `Deleted ${it.label}`)
      } else if (e.key === 'Enter') {
        e.preventDefault()
        st.select(it.sel)
        setStudio({ rightTab: 'inspector', ...(window.matchMedia('(min-width: 1280px)').matches ? {} : { drawer: 'right' as const }) })
        requestAnimationFrame(() => document.getElementById('inspector')?.querySelector<HTMLElement>('input,textarea,button,[role=combobox]')?.focus())
      }
    },
    [setStudio],
  )

  const clipMenu = useCallback(
    (it: ClipItem) => {
      const st = useProject.getState
      const lane = it.sel.kind === 'action' ? st().spec?.scenes[it.sel.scene]?.layers[it.sel.layer]?.character : undefined
      const sels = selectedClips(st().selection, st().extra)
      const many = sels.length > 1 && sels.some((x) => sameSelection(x, it.sel))
      return [
        { label: many ? `Copy ${sels.length} clips` : 'Copy', shortcut: `${MOD}C`, onSelect: () => (ensureSelected(it.sel), void copySelection()) },
        { label: many ? `Cut ${sels.length} clips` : 'Cut', shortcut: `${MOD}X`, onSelect: () => (ensureSelected(it.sel), void copySelection(true)) },
        { label: lane ? `Paste onto ${characterName(st().spec as ReelSpec, lane)} at the playhead` : 'Paste at the playhead', shortcut: `${MOD}V`, disabled: !useClipboard.getState().board, onSelect: () => void pasteAtPlayhead(lane) },
        { separator: true },
        { label: many ? `Duplicate ${sels.length} clips` : 'Duplicate', shortcut: `${MOD}D`, onSelect: () => (ensureSelected(it.sel), duplicateSelected()) },
        { label: 'Split at playhead', shortcut: 'S', disabled: it.kind === 'sfx' || it.kind === 'camera', onSelect: () => st().edit((d) => st().select(splitAtPlayhead(d, it.sel, st().playhead))) },
        { separator: true },
        { label: many ? `Delete ${sels.length} clips` : 'Delete', danger: true, onSelect: () => (ensureSelected(it.sel), deleteSelected()) },
      ]
    },
    // the clipboard changes what Paste offers: read when the menu is built, so a new copy is seen next time
    [board],
  )

  const addAt = (fn: (d: ReelSpec) => Selection | null) =>
    edit((d) => {
      const added = fn(d)
      if (added) select(added)
    })

  const drop = (e: DragEvent, lane: LaneModel) => {
    const raw = e.dataTransfer.getData('application/x-reel-item')
    if (!raw) return
    e.preventDefault()
    const item = JSON.parse(raw) as { kind: string; name: string }
    const t = timeAt(e.clientX, e.currentTarget)
    if (lane.kind === 'char' && item.kind === 'action' && lane.charId) addAt((d) => addAction(d, catalog, lane.charId as string, item.name, t))
    else if (lane.kind === 'camera' && item.kind === 'camera') addAt((d) => addCameraMove(d, item.name, t))
    else if (lane.kind === 'sfx' && item.kind === 'sfx') addAt((d) => addSfx(d, item.name, t))
  }
  const acceptsDrop = (e: DragEvent, lane: LaneModel) => {
    const types = [...e.dataTransfer.types]
    if (types.includes('application/x-reel-item') && ['char', 'camera', 'sfx'].includes(lane.kind)) e.preventDefault()
  }

  // ---------------------------------------------------------------- rulers and tick marks
  const { major, minor } = rulerSteps(zoom)
  const ticks = useMemo(() => {
    const out: { t: number; major: boolean }[] = []
    const end = contentW / zoom
    for (let t = 0; t <= end; t = Math.round((t + minor) * 1000) / 1000) out.push({ t, major: Math.abs(t / major - Math.round(t / major)) < 1e-6 })
    return out
  }, [contentW, zoom, major, minor])

  const scrub = (e: RPointer) => {
    const area = e.currentTarget as HTMLElement
    area.setPointerCapture(e.pointerId)
    const move = (ev: PointerEvent) => setPlayhead(clamp((ev.clientX - area.getBoundingClientRect().left) / zoom, 0, Math.max(0, total - 0.001)))
    move(e.nativeEvent)
    const up = () => {
      area.removeEventListener('pointermove', move)
      area.removeEventListener('pointerup', up)
    }
    area.addEventListener('pointermove', move)
    area.addEventListener('pointerup', up)
  }

  // resizing a scene by its right edge
  const resizeScene = (e: RPointer, slot: Slot) => {
    e.stopPropagation()
    e.preventDefault()
    const el = e.currentTarget as HTMLElement
    el.setPointerCapture(e.pointerId)
    const startX = e.clientX
    const start = slot.duration
    const st = useProject.getState()
    let moved = false
    const maxScene = catalog?.limits.max_scene_sec ?? 30
    const move = (ev: PointerEvent) => {
      if (!moved && Math.abs(ev.clientX - startX) < 3) return
      if (!moved) {
        moved = true
        st.beginGesture()
      }
      const raw = start + (ev.clientX - startX) / zoom
      const pts = ev.altKey ? [] : [useProject.getState().playhead - slot.start]
      const s = snap(raw, pts, Math.max(0.05, 7 / zoom))
      const next = clamp(s.to !== null ? s.value : Math.round(raw * 10) / 10, 0.5, maxScene)
      useProject.getState().edit((d) => void (d.scenes[slot.index].duration_sec = ms(next)), { live: true })
    }
    const up = () => {
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerup', up)
      if (moved) st.endGesture()
    }
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerup', up)
  }

  const selectedKey = (it: ClipItem) => sameSelection(selection, it.sel) || extra.some((x) => sameSelection(x, it.sel))
  const loopStyle = loop ? { left: loop[0] * zoom, width: Math.max(2, (loop[1] - loop[0]) * zoom) } : null

  return (
    <section aria-label="Timeline" className="flex min-h-0 flex-1 flex-col bg-panel">
      <div className="@container flex h-11 shrink-0 items-center gap-2 overflow-hidden border-b border-line px-2 @2xl:gap-4 @2xl:px-3">
        <BudgetBar spec={spec} />
        <div className="flex-1" />
        <Tip label="Zoom out">
          <Button variant="ghost" size="icon-sm" aria-label="Zoom out" onClick={() => setStudio({ zoom: clamp(zoom / 1.3, MIN_ZOOM, MAX_ZOOM) })}>
            <Minus className="size-4" />
          </Button>
        </Tip>
        <input type="range" aria-label="Timeline zoom" min={Math.log(MIN_ZOOM)} max={Math.log(MAX_ZOOM)} step="0.01" value={Math.log(zoom)} onChange={(e) => setStudio({ zoom: Math.exp(Number(e.target.value)) })} className="hidden h-1 w-24 accent-[var(--accent)] @2xl:block" />
        <Tip label="Zoom in">
          <Button variant="ghost" size="icon-sm" aria-label="Zoom in" onClick={() => setStudio({ zoom: clamp(zoom * 1.3, MIN_ZOOM, MAX_ZOOM) })}>
            <Plus className="size-4" />
          </Button>
        </Tip>
        <Button size="sm" variant="ghost" onClick={() => setStudio({ zoom: clamp((viewW - gutter - 40) / Math.max(1, total), MIN_ZOOM, MAX_ZOOM) })}>
          Fit all
        </Button>
      </div>

      <div ref={scroller} className="relative min-h-0 flex-1 overflow-auto">
        <div className="relative" style={{ width: gutter + contentW }}>
          {/* ruler */}
          <div className="sticky top-0 z-20 flex border-b border-line bg-panel" style={{ height: RULER_H }}>
            <div className="sticky left-0 z-30 flex shrink-0 items-center border-r border-line bg-panel px-3 text-[11px] text-faint" style={{ width: gutter }}>
              Seconds
            </div>
            <div className="relative cursor-col-resize" style={{ width: contentW }} onPointerDown={scrub}>
              {ticks.map(({ t, major: isMajor }) => (
                <div key={t} className="absolute bottom-0" style={{ left: t * zoom }}>
                  <div className={cn('w-px bg-line-strong', isMajor ? 'h-3' : 'h-1.5')} />
                  {isMajor && <span className="absolute -top-[17px] left-1 whitespace-nowrap font-mono text-[10.5px] text-muted tabular">{tickLabel(t)}</span>}
                </div>
              ))}
              {loopStyle && <div className="absolute inset-y-0 bg-accent/20" style={loopStyle} />}
            </div>
          </div>

          {lanes.map((lane) => {
            const h = lane.rows * (CLIP_H + 4) + ROW_PAD * 2 - 4
            return (
              <div key={lane.key} className="flex border-b border-line" style={{ height: lane.kind === 'scenes' ? 44 : lane.kind === 'audio' ? 60 : Math.max(h, 34) }}>
                <div className="sticky left-0 z-10 flex shrink-0 items-center gap-1 border-r border-line bg-panel px-2 sm:gap-2 sm:px-3" style={{ width: gutter }}>
                  <LaneLabel lane={lane} spec={spec} catalog={catalog} addAt={addAt} />
                </div>
                <div
                  className="relative isolate"
                  style={{ width: contentW }}
                  onPointerDown={(e) => {
                    if (e.target === e.currentTarget) {
                      select({ kind: 'reel' })
                      setPlayhead(clamp(timeAt(e.clientX, e.currentTarget), 0, Math.max(0, total - 0.001)))
                    }
                  }}
                  onDragOver={(e) => acceptsDrop(e, lane)}
                  onDrop={(e) => drop(e, lane)}
                >
                  {lane.kind === 'scenes' &&
                    slots.map((slot) => {
                      const sc = spec.scenes[slot.index]
                      const color = bgColor(sc.background.template)
                      const sel = selection.kind === 'scene' && selection.scene === slot.index
                      return (
                        <div key={slot.id + slot.index} className="absolute inset-y-1" style={{ left: slot.start * zoom, width: slot.duration * zoom }}>
                          <div
                            role="button"
                            tabIndex={0}
                            aria-pressed={sel}
                            onPointerDown={(e) => {
                              select({ kind: 'scene', scene: slot.index })
                              setPlayhead(clamp(slot.start + timeAt(e.clientX, e.currentTarget.parentElement as Element), slot.start, slot.end - 0.001))
                            }}
                            onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), select({ kind: 'scene', scene: slot.index }), setPlayhead(slot.start + 0.001))}
                            className={cn('flex h-full cursor-pointer items-center overflow-hidden rounded-[8px] pl-3 pr-2 outline-none', sel ? 'ring-2 ring-accent' : 'ring-1 ring-black/10 hover:brightness-110 focus-visible:ring-2 focus-visible:ring-accent')}
                            style={{ background: rgba(color, sel ? 0.42 : 0.26) }}
                          >
                            <span className="absolute inset-y-0 left-0 w-1" style={{ background: color }} />
                            <span className="min-w-0 truncate text-[12px] font-semibold">
                              <span className="sr-only">Scene </span>
                              {slot.index + 1}. {sc.id}
                              <span className="ml-2 font-normal text-fg/75 tabular">{slot.duration.toFixed(1)} s</span>
                              <span className="ml-2 font-normal text-fg/65">{sc.background.template}</span>
                            </span>
                          </div>
                          {slot.overlap > 0 && <div className="stripes pointer-events-none absolute inset-y-0 right-0 rounded-r-[8px] opacity-70" style={{ width: slot.overlap * zoom }} />}
                          <span onPointerDown={(e) => resizeScene(e, slot)} className="absolute -right-1 inset-y-0 z-10 w-2 cursor-ew-resize rounded hover:bg-accent/60" aria-hidden title="Drag to change the scene's length" />
                          {slot.index < slots.length - 1 && (
                            <button
                              onClick={() => select({ kind: 'transition', scene: slot.index })}
                              className={cn('absolute top-1/2 z-20 grid h-5 min-w-5 -translate-y-1/2 place-items-center rounded-full border bg-panel px-1 text-[10px] font-semibold', selection.kind === 'transition' && selection.scene === slot.index ? 'border-accent text-accent' : 'border-line-strong text-muted hover:text-fg')}
                              style={{ right: (slot.overlap * zoom) / 2 - 11 }}
                              title={`Transition: ${sc.transition_out.type}${sc.transition_out.duration ? ` (${sc.transition_out.duration}s)` : ''}`}
                              aria-label={`Transition after scene ${slot.index + 1}: ${sc.transition_out.type}`}
                            >
                              <TransitionGlyph type={sc.transition_out.type} />
                            </button>
                          )}
                        </div>
                      )
                    })}
                  {lane.kind === 'audio' && <AudioLane spec={spec} zoom={zoom} audio={audio} />}
                  {lane.items.map((it) => (
                    <ClipView
                      key={it.key}
                      it={it}
                      zoom={zoom}
                      selected={selectedKey(it)}
                      onSelect={() => {
                        // pressing a clip that is already in a selection keeps the selection: it may be about to be dragged along
                        const st = useProject.getState()
                        if (!(st.extra.length > 0 && selectedKey(it))) select(it.sel)
                      }}
                      onToggle={() => toggleSelect(it.sel)}
                      onDrag={onDrag}
                      onKey={onKey}
                      menu={clipMenu}
                    />
                  ))}
                  {lane.kind === 'char' && lane.items.length === 0 && <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[11.5px] text-faint">No actions yet: add one with +, or drop it from the Library</span>}
                </div>
              </div>
            )
          })}

          {/* playhead */}
          <div className="pointer-events-none absolute bottom-0 top-0 z-[15]" style={{ left: gutter + playhead * zoom }}>
            <div className="absolute inset-y-0 w-px -translate-x-1/2 bg-accent" />
            <div className="absolute left-0 top-0 -translate-x-1/2 rounded-b-md bg-solid px-1.5 py-0.5 font-mono text-[10.5px] font-medium text-accent-fg tabular">{playhead.toFixed(2)}</div>
          </div>
        </div>
      </div>
      <div className="sr-only" aria-live="polite">
        {announce}
      </div>
    </section>
  )
}

/** The mark on the seam between two scenes: a picture, so the label for screen readers is the whole name. */
function TransitionGlyph({ type }: { type: string }) {
  const Icon = { cut: Scissors, crossfade: Blend, wipe: ArrowRightToLine, page_flip: BookOpen }[type] ?? Sparkles
  return <Icon className="size-3" aria-hidden />
}

// ------------------------------------------------------------------------------- lane labels and the audio lane
function LaneLabel({ lane, spec, catalog, addAt }: { lane: LaneModel; spec: ReelSpec; catalog: Catalog | undefined; addAt: (fn: (d: ReelSpec) => Selection | null) => void }) {
  const board = useClipboard((s) => s.board)
  const select = useProject((s) => s.select)
  const edit = useProject((s) => s.edit)
  const playhead = useProject((s) => s.playhead)
  const icon = { scenes: <Film className="size-3.5" />, camera: <Camera className="size-3.5" />, captions: <Type className="size-3.5" />, sfx: <Zap className="size-3.5" />, audio: <AudioLines className="size-3.5" />, char: null }[lane.kind]
  const label = (
    <span className="flex min-w-0 flex-1 items-center gap-2">
      {lane.kind === 'char' ? <span className="size-2.5 shrink-0 rounded-full" style={{ background: lane.color }} aria-hidden /> : <span className="text-muted">{icon}</span>}
      <button className="min-w-0 truncate text-left text-[12px] font-medium" onClick={() => lane.charId && select({ kind: 'character', id: lane.charId })} title={lane.label}>
        {lane.label}
      </button>
    </span>
  )
  const plus = (title: string) => (
    <Button variant="ghost" size="icon-sm" aria-label={title} title={title} className="size-6">
      <Plus className="size-3.5" />
    </Button>
  )
  let adder: ReactNode = null
  if (lane.kind === 'scenes')
    adder = (
      <Menu
        align="start"
        trigger={plus('Add a scene')}
        entries={[{ heading: 'Add a scene with…' }, ...(catalog?.backgrounds ?? []).map((b) => ({ label: b.name, onSelect: () => edit((d) => select(addScene(d, spec.scenes.length - 1, b.name))) }))]}
      />
    )
  else if (lane.kind === 'camera')
    adder = <Menu align="start" trigger={plus('Add a camera move at the playhead')} entries={[{ heading: 'At the playhead' }, ...(catalog?.camera_moves ?? []).map((m) => ({ label: m.name.replace('_', ' '), onSelect: () => addAt((d) => addCameraMove(d, m.name, playhead)) }))]} />
  else if (lane.kind === 'captions') adder = <span onClick={() => addAt((d) => addCaption(d, playhead))}>{plus('Add a caption at the playhead')}</span>
  else if (lane.kind === 'sfx') adder = <Popover trigger={plus('Add a sound effect at the playhead')} className="p-2.5"><SfxPicker catalog={catalog} onPick={(name) => addAt((d) => addSfx(d, name, playhead))} /></Popover>
  else if (lane.kind === 'char' && lane.charId) {
    const id = lane.charId
    adder = (
      <Popover trigger={plus(`Add an action for ${lane.label} at the playhead`)} className="p-2.5">
        <ActionPicker catalog={catalog} onPick={(name) => addAt((d) => addAction(d, catalog, id, name, playhead))} />
      </Popover>
    )
  }
  // right-click a lane's name to paste onto it (a character's lane takes actions; the others take what belongs to them)
  const pasteEntries: MenuEntry[] =
    lane.kind === 'scenes' || lane.kind === 'audio'
      ? []
      : [
          {
            label: lane.kind === 'char' ? `Paste onto ${lane.label} at the playhead` : 'Paste at the playhead',
            shortcut: `${MOD}V`,
            disabled: !board,
            onSelect: () => void pasteAtPlayhead(lane.charId),
          },
        ]
  return (
    <>
      {pasteEntries.length ? <ContextMenu entries={pasteEntries}>{label}</ContextMenu> : label}
      {adder}
    </>
  )
}

function AudioLane({ spec, zoom, audio }: { spec: ReelSpec; zoom: number; audio: AudioResult | null }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const total = totalDuration(spec)
  const music = spec.audio.music
  useEffect(() => {
    const c = ref.current
    if (!c) return
    const dpr = window.devicePixelRatio || 1
    const w = Math.max(1, Math.round(total * zoom))
    const h = 52
    c.width = w * dpr
    c.height = h * dpr
    c.style.width = `${w}px`
    c.style.height = `${h}px`
    const ctx = c.getContext('2d')
    if (!ctx) return
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, w, h)
    if (!audio) return
    const css = getComputedStyle(document.documentElement)
    const rows: [keyof typeof audio.peaks, string, number][] = [
      ['voice', css.getPropertyValue('--accent').trim() || '#7c6cff', 0],
      ['music', css.getPropertyValue('--muted').trim() || '#999', 1],
      ['sfx', css.getPropertyValue('--warning').trim() || '#fbbf24', 2],
    ]
    const duck = audio.duck
    // the mixer's gain on the music at pixel x (1 = untouched); the bed is drawn as loud as it is after the dip
    const gainAt = (x: number): number => (duck ? (duck.gain[Math.min(duck.gain.length - 1, Math.floor((x / zoom) * duck.per_sec))] ?? 1) : 1)
    for (const [key, color, r] of rows) {
      const peaks = audio.peaks[key]
      const y0 = r * 17 + 8
      ctx.fillStyle = color
      for (let x = 0; x < w; x++) {
        const v = (peaks[Math.min(peaks.length - 1, Math.floor((x / w) * peaks.length))] ?? 0) * (key === 'music' ? gainAt(x) : 1)
        const amp = Math.min(8, Math.max(v > 0.004 ? 1 : 0, v * 22))
        if (amp > 0) ctx.fillRect(x, y0 - amp, 1, amp * 2)
      }
    }
    if (duck) {
      // the dip curve itself: level with the top of the music row while the bed is untouched, falling as it ducks under speech
      const top = 17 + 8 - 8
      ctx.strokeStyle = css.getPropertyValue('--text').trim() || '#e8ebf2'
      ctx.globalAlpha = 0.75
      ctx.lineWidth = 1.25
      ctx.beginPath()
      for (let x = 0; x <= w; x++) {
        const y = top + (1 - gainAt(Math.min(x, w - 1))) * 16
        if (x === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.stroke()
      ctx.globalAlpha = 1
    }
  }, [audio, zoom, total])
  return (
    <div className="absolute inset-0 flex flex-col justify-center">
      {audio ? (
        <canvas ref={ref} aria-label={`Waveforms: voice, music and sound effects${audio.duck ? '; the music dips under the voice' : ''}`} role="img" className="absolute left-0 top-1" />
      ) : (
        <div className="flex h-full items-center gap-2 pl-3 text-[11.5px] text-faint">
          <Music className="size-3.5" aria-hidden />
          {music ? `Music: ${music.replace('procedural', 'generated bed')}` : 'No music'} · generate the voice in Voice & Audio to see waveforms here
        </div>
      )}
    </div>
  )
}
