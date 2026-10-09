import { Copy, Dice5, Play, Plus, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from '@/api/context'
import { useCatalog } from '@/api/hooks'
import type { Catalog, CatalogEntry, Character, FxSpec, Layer, PaletteRole, ReelSpec, Scene } from '@/api/types'
import { Banner, Button, Chip, ColorField, Field, Input, IconButton, NumberField, Segmented, SelectBox, SliderField, SwitchRow, Textarea, Toggle } from '@/components/ui'
import { cn } from '@/lib/cn'
import { curvePath } from '@/lib/easing'
import { titleCase } from '@/lib/format'
import { characterName, entry, pathLabel, type Selection } from '@/lib/spec'
import { MIN_CLIP, ms } from '@/lib/timeline'
import { useProject } from '@/store/project'
import { addCameraMove, deleteSelection, duplicateSelection, renameCharacterId } from '../ops'
import { Row, Section } from './common'
import { ParamForm } from './ParamForm'

// ------------------------------------------------------------------------------- shared bits
function useLive() {
  const edit = useProject((s) => s.edit)
  const begin = useProject((s) => s.beginGesture)
  const end = useProject((s) => s.endGesture)
  const active = useRef(false)
  return {
    live: (fn: (d: ReelSpec) => void) => {
      if (!active.current) {
        begin()
        active.current = true
      }
      edit(fn, { live: true })
    },
    commit: () => {
      if (active.current) {
        end()
        active.current = false
      }
    },
  }
}

function TimeFields({ t0, t1, onChange, min = MIN_CLIP, max }: { t0: number; t1: number; onChange: (t0: number, t1: number) => void; min?: number; max: number }) {
  return (
    <Row>
      <Field label="Start">
        <NumberField value={t0} onChange={(v) => onChange(ms(Math.min(v, t1 - min)), t1)} min={0} max={max} step={0.1} unit="s" aria-label="Start time" digits={2} />
      </Field>
      <Field label="End">
        <NumberField value={t1} onChange={(v) => onChange(t0, ms(Math.max(v, t0 + min)))} min={0} max={max} step={0.1} unit="s" aria-label="End time" digits={2} />
      </Field>
      <p className="col-span-2 -mt-1 text-[11.5px] text-faint">Length {(t1 - t0).toFixed(2)} s · times are seconds from the start of the scene</p>
    </Row>
  )
}

function Thumb({ src, alt, selected, label, onClick }: { src: string; alt: string; selected: boolean; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} aria-pressed={selected} aria-label={label} className={cn('group relative overflow-hidden rounded-[10px] border text-left transition-colors', selected ? 'border-accent ring-2 ring-accent/40' : 'border-line hover:border-line-strong')}>
      <img src={src} alt={alt} loading="lazy" className="aspect-[9/12] w-full object-cover" />
      <span className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent px-1.5 pb-1 pt-4 text-[11px] font-medium text-white">{label}</span>
    </button>
  )
}

function PanelHeader({ spec, sel, extra }: { spec: ReelSpec; sel: Selection; extra?: ReactNode }) {
  const edit = useProject((s) => s.edit)
  const select = useProject((s) => s.select)
  const canDup = ['action', 'caption', 'camera', 'sfx', 'layer', 'scene'].includes(sel.kind)
  const canDelete = sel.kind !== 'reel'
  const title = sel.kind === 'reel' ? 'Reel' : pathLabel(spec, pathOf(sel))
  return (
    <div className="flex items-center gap-1 border-b border-line px-4 py-2.5">
      <h2 className="min-w-0 flex-1 truncate text-[13px] font-semibold">{title}</h2>
      {extra}
      {canDup && (
        <IconButton label="Duplicate (⌘D)" size="icon-sm" onClick={() => edit((d) => select(duplicateSelection(d, sel)))}>
          <Copy className="size-3.5" />
        </IconButton>
      )}
      {canDelete && (
        <IconButton label="Delete (Del)" size="icon-sm" onClick={() => edit((d) => select(deleteSelection(d, sel)))} className="hover:text-danger">
          <Trash2 className="size-3.5" />
        </IconButton>
      )}
    </div>
  )
}

/** A selection as the lint-path form `pathLabel` understands. */
function pathOf(sel: Selection): string {
  switch (sel.kind) {
    case 'scene':
      return `scenes[${sel.scene}]`
    case 'layer':
      return `scenes[${sel.scene}].layers[${sel.layer}]`
    case 'action':
      return `scenes[${sel.scene}].layers[${sel.layer}].actions[${sel.action}]`
    case 'caption':
      return `scenes[${sel.scene}].captions[${sel.caption}]`
    case 'camera':
      return `scenes[${sel.scene}].camera.moves[${sel.move}]`
    case 'sfx':
      return `scenes[${sel.scene}].sfx[${sel.sfx}]`
    case 'transition':
      return `scenes[${sel.scene}].transition_out`
    case 'character':
      return `characters[0]`
    default:
      return 'meta'
  }
}

function namesFor(spec: ReelSpec, scene: Scene | undefined, catalog: Catalog | undefined): string[] {
  const slots = Object.keys(entry(catalog?.backgrounds, scene?.background.template ?? '')?.slots ?? {})
  // a set: every background already has 'left' and 'right' among its places
  return [...new Set([...slots, ...spec.characters.map((c) => c.id), 'camera', 'up', 'down', 'left', 'right'])]
}

// ------------------------------------------------------------------------------- reel
const FX: { key: keyof FxSpec; label: string; min: number; max: number; neutral: number; hint: string }[] = [
  { key: 'grain', label: 'Film grain', min: 0, max: 2, neutral: 1, hint: 'texture over the whole frame (it is also what makes the file bigger)' },
  { key: 'vignette', label: 'Vignette', min: 0, max: 2, neutral: 1, hint: 'darkens the corners' },
  { key: 'bloom', label: 'Bloom', min: 0, max: 2, neutral: 1, hint: 'a glow around bright areas' },
  { key: 'chromatic_aberration', label: 'Chromatic aberration', min: 0, max: 2, neutral: 1, hint: 'a subtle colour fringe towards the edges' },
  { key: 'saturation', label: 'Saturation', min: 0, max: 2, neutral: 1, hint: '1 is the style’s own' },
  { key: 'contrast', label: 'Contrast', min: 0, max: 2, neutral: 1, hint: '1 is the style’s own' },
  { key: 'warmth', label: 'Warmth', min: -1, max: 1, neutral: 0, hint: 'cool to warm colour grade' },
  { key: 'letterbox', label: 'Letterbox', min: 0, max: 0.3, neutral: 0.12, hint: 'black bars top and bottom (fraction of the height)' },
]

function ReelInspector({ spec }: { spec: ReelSpec }) {
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const { live, commit } = useLive()
  const sa = spec.meta.safe_area ?? { top: 0.1, bottom: 0.2, left: 0.07, right: 0.07 }
  return (
    <>
      <Section title="Reel">
        <Field label="Title">
          <Input value={spec.meta.title} aria-label="Title" onChange={(e) => edit((d) => void (d.meta.title = e.target.value), { live: true })} onBlur={commit} />
        </Field>
        <Field label="Style" hint={entry(catalog?.styles, spec.meta.style)?.summary}>
          <Segmented label="Style" value={spec.meta.style} onChange={(v) => edit((d) => void (d.meta.style = v))} options={(catalog?.styles ?? []).map((s) => ({ value: s.name, label: titleCase(s.name) }))} size="sm" className="flex-wrap" />
        </Field>
        <Row>
          <Field label="Frame rate">
            <NumberField value={spec.meta.fps} onChange={(v) => edit((d) => void (d.meta.fps = Math.round(v)))} min={12} max={60} step={1} unit="fps" aria-label="Frame rate" />
          </Field>
          <Field label="Seed" hint="Same spec + seed = the same video">
            <div className="flex gap-1.5">
              <NumberField value={spec.meta.seed} onChange={(v) => edit((d) => void (d.meta.seed = Math.round(v)))} step={1} aria-label="Seed" className="flex-1" />
              <IconButton label="Roll a new seed" variant="secondary" onClick={() => edit((d) => void (d.meta.seed = Math.floor(Math.random() * 9999)))}>
                <Dice5 className="size-4" />
              </IconButton>
            </div>
          </Field>
        </Row>
        <Field label="Target length" hint="A reel must be 45–60 s: see the bar above the timeline">
          <SliderField label="Target length" value={spec.meta.target_duration_sec} min={45} max={60} step={0.5} unit="s" onChange={(v) => live((d) => void (d.meta.target_duration_sec = v))} onCommit={commit} />
        </Field>
      </Section>
      <Section title="Look" defaultOpen={false}>
        <p className="-mt-1 text-[11.5px] text-faint">Each style has its own finishing. Override a strength to change it for this reel; “style default” leaves it to the style.</p>
        {FX.map((fx) => {
          const v = spec.meta.fx?.[fx.key]
          const on = typeof v === 'number'
          return (
            <div key={fx.key} className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[12px] font-medium">{fx.label}</span>
                <label className="flex items-center gap-2 text-[11.5px] text-muted">
                  {on ? 'overridden' : 'style default'}
                  <Toggle label={`Override ${fx.label}`} checked={on} onChange={(c) => edit((d) => void (((d.meta.fx ??= {})[fx.key] = c ? fx.neutral : null)))} />
                </label>
              </div>
              {on && <SliderField label={fx.label} value={v as number} min={fx.min} max={fx.max} step={0.01} onChange={(n) => live((d) => void (((d.meta.fx ??= {})[fx.key] = n)))} onCommit={commit} defaultValue={fx.neutral} />}
              <p className="text-[11.5px] text-faint">{fx.hint}</p>
            </div>
          )
        })}
      </Section>
      <Section title="Caption safe area" defaultOpen={false}>
        <p className="-mt-1 text-[11.5px] text-faint">Fractions of the frame kept clear of captions (platform buttons cover these). Show it on the stage with the safe-area button.</p>
        <Row>
          {(['top', 'bottom', 'left', 'right'] as const).map((side) => (
            <Field key={side} label={titleCase(side)}>
              <NumberField value={sa[side]} onChange={(v) => edit((d) => void ((d.meta.safe_area = { ...(d.meta.safe_area ?? sa), [side]: v })))} min={0} max={0.4} step={0.01} aria-label={`${side} margin`} />
            </Field>
          ))}
        </Row>
      </Section>
      <Section title="Sound" defaultOpen={false}>
        <p className="text-[12px] text-muted">
          Music: <b className="font-medium text-fg">{spec.audio.music ?? 'none'}</b> · voice-over: <b className="font-medium text-fg">{spec.audio.voiceover}</b>
        </p>
        <Link to="audio" className="text-accent hover:underline">
          Open Voice & Audio →
        </Link>
      </Section>
    </>
  )
}

// ------------------------------------------------------------------------------- scene
function TransitionFields({ spec, si, catalog }: { spec: ReelSpec; si: number; catalog: Catalog | undefined }) {
  const edit = useProject((s) => s.edit)
  const sc = spec.scenes[si]
  const tr = sc.transition_out
  const def = entry(catalog?.transitions, tr.type)
  const last = si === spec.scenes.length - 1
  return (
    <>
      {last && <p className="text-[12px] text-faint">The last scene has nothing to transition into, so this is ignored.</p>}
      <Field label="Type" hint={def?.summary}>
        <SelectBox label="Transition type" value={tr.type} onChange={(v) => edit((d) => void (d.scenes[si].transition_out = { type: v, duration: v === 'cut' ? 0 : Math.max(0.4, d.scenes[si].transition_out.duration || 0.6), params: {} }))} options={(catalog?.transitions ?? []).map((t) => ({ value: t.name, label: titleCase(t.name), hint: t.summary }))} />
      </Field>
      {tr.type !== 'cut' && (
        <Field label="Duration" hint="The next scene starts this long before this one ends, so the reel is shorter by the same amount">
          <SliderField label="Transition duration" value={tr.duration} min={0.1} max={3} step={0.05} unit="s" onChange={(v) => edit((d) => void (d.scenes[si].transition_out.duration = v), { live: true })} onCommit={() => undefined} />
        </Field>
      )}
      {def?.params && def.params.length > 0 && tr.type !== 'cut' && <ParamForm params={def.params} value={tr.params} ctx={{ names: [] }} onChange={(p) => edit((d) => void (d.scenes[si].transition_out.params = p))} />}
    </>
  )
}

function SceneInspector({ spec, si }: { spec: ReelSpec; si: number }) {
  const api = useApi()
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const select = useProject((s) => s.select)
  const { live, commit } = useLive()
  const sc = spec.scenes[si]
  if (!sc) return null
  const bgDef = entry(catalog?.backgrounds, sc.background.template)
  const tod = String(sc.background.params.time_of_day ?? 'day')
  return (
    <>
      <Section title="Scene">
        <Field label="Name" hint="Used in the timeline and in lint messages">
          <Input value={sc.id} aria-label="Scene name" onChange={(e) => edit((d) => void (d.scenes[si].id = e.target.value), { live: true })} onBlur={commit} />
        </Field>
        <Field label="Length" hint={`Up to ${catalog?.limits.max_scene_sec ?? 30} s; later scenes move along`}>
          <SliderField label="Scene length" value={sc.duration_sec} min={0.5} max={catalog?.limits.max_scene_sec ?? 30} step={0.1} unit="s" onChange={(v) => live((d) => void (d.scenes[si].duration_sec = ms(v)))} onCommit={commit} />
        </Field>
        <Field label="Director’s note" hint="Ignored by the renderer">
          <Textarea value={sc.notes ?? ''} aria-label="Director's note" rows={2} onChange={(e) => edit((d) => void (d.scenes[si].notes = e.target.value || null), { live: true })} onBlur={commit} />
        </Field>
      </Section>
      <Section title="Background">
        <div className="grid grid-cols-3 gap-2">
          {(catalog?.backgrounds ?? []).map((b) => (
            <Thumb key={b.name} src={api.libraryThumbUrl('background', b.name, tod, spec.meta.style)} alt={`${b.name} background`} label={b.name} selected={sc.background.template === b.name} onClick={() => edit((d) => void (d.scenes[si].background = { template: b.name, params: { ...(tod !== 'day' ? { time_of_day: tod } : {}) } }))} />
          ))}
        </div>
        {bgDef && <p className="text-[11.5px] leading-snug text-faint">{bgDef.summary}</p>}
        {bgDef?.params && <ParamForm params={bgDef.params} value={sc.background.params} ctx={{ names: [] }} onChange={(p) => edit((d) => void (d.scenes[si].background.params = p))} />}
      </Section>
      <Section title="Camera" action={<CameraAdder si={si} />} defaultOpen={sc.camera.moves.length > 0}>
        {sc.camera.moves.length === 0 && <p className="text-[12px] text-faint">A still camera. Add a pan, zoom, dolly, shake or rack focus.</p>}
        {sc.camera.moves.map((m, mi) => (
          <button key={mi} className="flex items-center justify-between rounded-ctl border border-line bg-raised px-2.5 py-2 text-left hover:border-line-strong" onClick={() => select({ kind: 'camera', scene: si, move: mi })}>
            <span className="font-medium">{titleCase(m.type)}</span>
            <span className="text-muted tabular">
              {m.t0.toFixed(1)}–{m.t1.toFixed(1)} s
            </span>
          </button>
        ))}
      </Section>
      <Section title="Transition into the next scene" defaultOpen={false}>
        <TransitionFields spec={spec} si={si} catalog={catalog} />
      </Section>
    </>
  )
}

function CameraAdder({ si }: { si: number }) {
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const select = useProject((s) => s.select)
  return (
    <SelectBox
      label="Add a camera move"
      value=""
      placeholder="Add…"
      className="h-7 w-24 text-[12px]"
      onChange={(type) =>
        edit((d) => {
          const sel = addCameraMove(d, type, useProject.getState().playhead, si)
          if (sel) select(sel)
        })
      }
      options={(catalog?.camera_moves ?? []).map((m) => ({ value: m.name, label: titleCase(m.name), hint: m.summary }))}
    />
  )
}

// ------------------------------------------------------------------------------- characters
function CharacterInspector({ spec, id }: { spec: ReelSpec; id: string }) {
  const api = useApi()
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const select = useProject((s) => s.select)
  const ch = spec.characters.find((c) => c.id === id)
  if (!ch) return null
  const arch = entry(catalog?.archetypes, ch.archetype)
  const roles = (catalog?.palette_roles ?? []) as PaletteRole[]
  const set = (fn: (c: Character) => void) =>
    edit((d) => {
      const c = d.characters.find((x) => x.id === id)
      if (c) fn(c)
    })
  return (
    <>
      <Section title="Character">
        <Row>
          <Field label="Name">
            <Input value={ch.name ?? ''} aria-label="Name" onChange={(e) => set((c) => void (c.name = e.target.value))} />
          </Field>
          <Field label="Id" hint="How scenes refer to them">
            <Input value={ch.id} aria-label="Character id" spellCheck={false} className="font-mono" onChange={(e) => edit((d) => void renameCharacterId(d, id, e.target.value.replace(/[^\w-]+/g, '_')))} onBlur={() => undefined} />
          </Field>
        </Row>
        <Field label="Voice" hint="A voice name or id for the speech engine; leave empty to let the engine choose a fitting one">
          <Input value={ch.voice ?? ''} aria-label="Voice" placeholder="Automatic" onChange={(e) => set((c) => void (c.voice = e.target.value || null))} />
        </Field>
      </Section>
      <Section title="Body">
        <div className="grid grid-cols-3 gap-2">
          {(catalog?.archetypes ?? []).map((a) => (
            <Thumb key={a.name} src={api.libraryThumbUrl('archetype', a.name, 'day', spec.meta.style)} alt={`${a.name} archetype`} label={a.name} selected={ch.archetype === a.name} onClick={() => set((c) => void (c.archetype = a.name))} />
          ))}
        </div>
        {arch && <p className="text-[11.5px] leading-snug text-faint">{arch.summary}</p>}
      </Section>
      <Section title="Colours">
        <div className="flex flex-col gap-2.5">
          {roles.map((r) => (
            <div key={r} className="grid grid-cols-[72px_1fr] items-center gap-2">
              <span className="text-[12px] text-muted">{titleCase(r)}</span>
              <ColorField label={r} value={ch.palette[r]} fallback={arch?.palette?.[r] ?? '#888888'} onChange={(v) => set((c) => void (v === undefined ? delete c.palette[r] : (c.palette[r] = v)))} />
            </div>
          ))}
        </div>
      </Section>
      <Section title="Props">
        <div className="flex flex-wrap gap-1.5">
          {(catalog?.props ?? []).map((p) => {
            const on = ch.props.includes(p.name)
            return (
              <button key={p.name} aria-pressed={on} title={p.summary} onClick={() => set((c) => void (c.props = on ? c.props.filter((x) => x !== p.name) : [...c.props, p.name]))} className={cn('rounded-chip border px-2 py-1 text-[12px] transition-colors', on ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:text-fg')}>
                {p.name}
              </button>
            )
          })}
        </div>
      </Section>
      <Section title="Where they appear" defaultOpen={false}>
        {spec.scenes.map((sc, si) =>
          sc.layers.some((l) => l.character === id) ? (
            <button key={si} className="flex items-center justify-between rounded-ctl px-2 py-1.5 text-left hover:bg-hover" onClick={() => select({ kind: 'layer', scene: si, layer: sc.layers.findIndex((l) => l.character === id) })}>
              <span>
                Scene {si + 1}: {sc.id}
              </span>
              <span className="text-muted">{sc.layers.filter((l) => l.character === id).reduce((n, l) => n + l.actions.length, 0)} actions</span>
            </button>
          ) : null,
        )}
      </Section>
    </>
  )
}

// ------------------------------------------------------------------------------- layers and actions
function LayerFields({ spec, si, li }: { spec: ReelSpec; si: number; li: number }) {
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const { live, commit } = useLive()
  const sc = spec.scenes[si]
  const layer = sc?.layers[li]
  if (!sc || !layer) return null
  const bg = entry(catalog?.backgrounds, sc.background.template)
  const slotNames = Object.keys(bg?.slots ?? {})
  const isSlot = typeof layer.position === 'string'
  const point = Array.isArray(layer.position) ? layer.position : [0.5, 0.74]
  const set = (fn: (l: Layer) => void) => edit((d) => void fn(d.scenes[si].layers[li]))
  return (
    <>
      <Field label="Character">
        <SelectBox label="Character" value={layer.character} onChange={(v) => set((l) => void (l.character = v))} options={spec.characters.map((c) => ({ value: c.id, label: characterName(spec, c.id) }))} />
      </Field>
      <Field label="Position" hint="Where their feet are: a named place on this set, or exact screen fractions. You can also drag them on the stage.">
        <Segmented label="Position mode" size="sm" value={isSlot ? 'slot' : 'point'} onChange={(m) => set((l) => void (l.position = m === 'slot' ? (slotNames.includes('center') ? 'center' : (slotNames[0] ?? 'center')) : [point[0], point[1]]))} options={[{ value: 'slot', label: 'Named place' }, { value: 'point', label: 'Exact point' }]} />
        {isSlot ? (
          <SelectBox label="Slot" value={layer.position as string} onChange={(v) => set((l) => void (l.position = v))} options={slotNames.map((n) => ({ value: n, label: n }))} />
        ) : (
          <Row>
            <NumberField value={point[0]} onChange={(x) => set((l) => void (l.position = [x, point[1]]))} step={0.01} min={-1} max={2} unit="x" aria-label="Position x" />
            <NumberField value={point[1]} onChange={(y) => set((l) => void (l.position = [point[0], y]))} step={0.01} min={0} max={1} unit="y" aria-label="Position y" />
          </Row>
        )}
      </Field>
      <Field label="Size">
        <SliderField label="Scale" value={layer.scale} min={0.2} max={4} step={0.01} onChange={(v) => live((d) => void (d.scenes[si].layers[li].scale = v))} onCommit={commit} defaultValue={1} />
      </Field>
      <Row>
        <Field label="Depth plane">
          <Segmented label="Depth" size="sm" value={layer.depth} onChange={(v) => set((l) => void (l.depth = v))} options={[{ value: 'background', label: 'Back' }, { value: 'mid', label: 'Mid' }, { value: 'foreground', label: 'Front' }]} />
        </Field>
        <Field label="Facing">
          <Segmented label="Facing" size="sm" value={layer.facing} onChange={(v) => set((l) => void (l.facing = v))} options={[{ value: 'auto', label: 'Auto' }, { value: 'left', label: '◀' }, { value: 'right', label: '▶' }]} />
        </Field>
      </Row>
    </>
  )
}

/** Parameters as raw JSON, for an action the catalog does not describe (so there is no form to generate). Valid objects apply as you type. */
function RawParams({ value, onChange }: { value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void }) {
  const shown = JSON.stringify(value, null, 2)
  const [text, setText] = useState(shown)
  const [error, setError] = useState<string | null>(null)
  const sent = useRef(JSON.stringify(value))
  useEffect(() => {
    // follow edits made elsewhere (undo, the JSON tab), but not our own: re-printing the text under the cursor would jump it
    if (JSON.stringify(value) !== sent.current) {
      setText(shown)
      setError(null)
    }
  }, [value, shown])
  return (
    <>
      <Textarea
        aria-label="Parameters as JSON"
        aria-invalid={!!error}
        spellCheck={false}
        rows={6}
        className="font-mono text-[12px]"
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          try {
            const v: unknown = JSON.parse(e.target.value.trim() || '{}')
            if (v === null || typeof v !== 'object' || Array.isArray(v)) return setError('The parameters must be a JSON object, like {"speed": 2}')
            setError(null)
            sent.current = JSON.stringify(v)
            onChange(v as Record<string, unknown>)
          } catch (err) {
            setError(`Not valid JSON yet: ${(err as Error).message}`)
          }
        }}
      />
      {error && (
        <p role="alert" className="m-0 text-[12px] text-danger">
          {error}
        </p>
      )}
    </>
  )
}

function ActionInspector({ spec, sel }: { spec: ReelSpec; sel: Extract<Selection, { kind: 'action' }> }) {
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const sc = spec.scenes[sel.scene]
  const a = sc?.layers[sel.layer]?.actions[sel.action]
  if (!sc || !a) return null
  const def = entry(catalog?.actions, a.name)
  const set = (fn: (x: typeof a) => void) => edit((d) => void fn(d.scenes[sel.scene].layers[sel.layer].actions[sel.action]))
  const tooShort = def?.min_duration !== undefined && a.t1 - a.t0 < def.min_duration - 1e-6
  return (
    <>
      <Section title="Action">
        <Field label="Action" hint={def?.summary}>
          <SelectBox label="Action" value={a.name} onChange={(v) => set((x) => void ((x.name = v), (x.params = {})))} options={[...(catalog?.actions ?? []).map((x) => ({ value: x.name, label: x.name, hint: x.summary, group: titleCase(x.category ?? 'other') })), ...(def || !catalog ? [] : [{ value: a.name, label: `${a.name} (custom)`, hint: 'not in this catalog', group: 'Custom' }])]} />
        </Field>
        <TimeFields t0={a.t0} t1={a.t1} max={sc.duration_sec} onChange={(t0, t1) => set((x) => void ((x.t0 = t0), (x.t1 = t1)))} min={Math.min(def?.min_duration ?? MIN_CLIP, MIN_CLIP)} />
        {tooShort && <Chip tone="warning">This action needs at least {def?.min_duration} s to look right</Chip>}
        {def?.moves_root && <Chip tone="info">Moves the character: its path shows on the stage</Chip>}
      </Section>
      {def?.params && (
        <Section title="Settings">
          <ParamForm params={def.params} value={a.params} ctx={{ names: namesFor(spec, sc, catalog) }} onChange={(p) => set((x) => void (x.params = p))} />
        </Section>
      )}
      {!def && catalog && (
        <Section title="Custom action">
          <Banner tone="warning" title={`“${a.name}” is not in this catalog`}>
            It probably comes from a plugin that is not loaded here. A lenient render (and the preview) shows <b>idle</b> in its place; a strict render stops.
          </Banner>
          <Field label="Parameters" hint="Written as the plugin expects them">
            <RawParams value={a.params} onChange={(p) => set((x) => void (x.params = p))} />
          </Field>
          <Button onClick={() => set((x) => void ((x.name = 'idle'), (x.params = {})))}>Replace with idle</Button>
        </Section>
      )}
      <Section title="Character in this scene" defaultOpen={false}>
        <LayerFields spec={spec} si={sel.scene} li={sel.layer} />
      </Section>
    </>
  )
}

function LayerInspector({ spec, sel }: { spec: ReelSpec; sel: Extract<Selection, { kind: 'layer' }> }) {
  const select = useProject((s) => s.select)
  const layer = spec.scenes[sel.scene]?.layers[sel.layer]
  if (!layer) return null
  return (
    <>
      <Section title="Character in this scene">
        <LayerFields spec={spec} si={sel.scene} li={sel.layer} />
      </Section>
      <Section title="Actions">
        {layer.actions.length === 0 && <p className="text-[12px] text-faint">No actions: they stand still. Add one from the timeline’s + or drag one in from the Library.</p>}
        {layer.actions.map((a, ai) => (
          <button key={ai} className="flex items-center justify-between rounded-ctl border border-line bg-raised px-2.5 py-2 text-left hover:border-line-strong" onClick={() => select({ kind: 'action', scene: sel.scene, layer: sel.layer, action: ai })}>
            <span className="font-medium">{a.name}</span>
            <span className="text-muted tabular">
              {a.t0.toFixed(1)}–{a.t1.toFixed(1)} s
            </span>
          </button>
        ))}
        <button className="flex items-center gap-1.5 text-accent hover:underline" onClick={() => select({ kind: 'character', id: layer.character })}>
          <Plus className="size-3.5" /> Edit {characterName(spec, layer.character)}
        </button>
      </Section>
    </>
  )
}

// ------------------------------------------------------------------------------- captions, camera, sfx
const CAPTION_LOOKS: Record<string, string> = { subtitle: 'text-[13px] font-extrabold', title: 'text-[15px] font-black uppercase tracking-wide', shout: 'text-[16px] font-black uppercase text-warning' }

function CaptionInspector({ spec, sel }: { spec: ReelSpec; sel: Extract<Selection, { kind: 'caption' }> }) {
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const { live, commit } = useLive()
  const sc = spec.scenes[sel.scene]
  const c = sc?.captions[sel.caption]
  if (!sc || !c) return null
  const set = (fn: (x: typeof c) => void, liveEdit = false) => edit((d) => void fn(d.scenes[sel.scene].captions[sel.caption]), liveEdit ? { live: true } : undefined)
  const speaks = c.speak ?? c.style === 'subtitle'
  return (
    <>
      <Section title="Caption">
        <Field label="Text" hint={`${c.text.length} characters · any script works: Hindi, Arabic, Thai, 中文, emoji`}>
          <Textarea value={c.text} rows={3} aria-label="Caption text" onChange={(e) => live((d) => void (d.scenes[sel.scene].captions[sel.caption].text = e.target.value))} onBlur={commit} />
        </Field>
        <Field label="Style">
          <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="Caption style">
            {(catalog?.caption_styles ?? []).map((s) => (
              <button key={s.name} role="radio" aria-checked={c.style === s.name} title={s.summary} onClick={() => set((x) => void (x.style = s.name))} className={cn('rounded-ctl border px-2 py-2.5 text-center transition-colors', c.style === s.name ? 'border-accent bg-accent-soft' : 'border-line hover:border-line-strong')}>
                <span className={cn('block', CAPTION_LOOKS[s.name] ?? 'text-[13px] font-bold')}>Aa</span>
                <span className="mt-0.5 block text-[11px] text-muted">{s.name}</span>
              </button>
            ))}
          </div>
        </Field>
        <Field label="Who says it" hint="The speaker’s mouth moves and the voice engine picks their voice">
          <SelectBox label="Speaker" value={c.speaker ?? '__none'} onChange={(v) => set((x) => void (x.speaker = v === '__none' ? null : v))} options={[{ value: '__none', label: 'Nobody (a title or a sign)' }, ...spec.characters.map((ch) => ({ value: ch.id, label: characterName(spec, ch.id) }))]} />
        </Field>
        <SwitchRow label="Read aloud" hint={c.speak === null || c.speak === undefined ? 'Default: subtitles are spoken' : undefined} checked={speaks} onChange={(v) => set((x) => void (x.speak = v === (x.style === 'subtitle') ? null : v))} />
        <Field label="Placement">
          <Segmented label="Anchor" size="sm" value={c.anchor} onChange={(v) => set((x) => void (x.anchor = v))} options={[{ value: 'auto', label: 'Auto' }, { value: 'top', label: 'Top' }, { value: 'center', label: 'Middle' }, { value: 'bottom', label: 'Bottom' }]} />
        </Field>
      </Section>
      <Section title="Timing">
        <TimeFields t0={c.t0} t1={c.t1} max={sc.duration_sec} onChange={(t0, t1) => set((x) => void ((x.t0 = t0), (x.t1 = t1)))} />
        <p className="text-[11.5px] text-faint">With a voice-over, the caption is stretched to stay up while it is spoken.</p>
      </Section>
    </>
  )
}

const CAMERA_FROM_TO: Record<string, 'vec2' | 'scale' | 'unit' | 'depth'> = { pan: 'vec2', zoom: 'scale', dolly: 'unit', shake: 'unit', rack_focus: 'depth' }

function MoveValue({ kind, label, value, onChange }: { kind: 'vec2' | 'scale' | 'unit' | 'depth'; label: string; value: unknown; onChange: (v: unknown) => void }) {
  if (kind === 'vec2') {
    const v = Array.isArray(value) ? (value as number[]) : [0, 0]
    return (
      <Field label={label}>
        <Row>
          <NumberField value={v[0]} onChange={(x) => onChange([x, v[1]])} step={0.01} min={-1} max={1} unit="x" aria-label={`${label} x`} />
          <NumberField value={v[1]} onChange={(y) => onChange([v[0], y])} step={0.01} min={-1} max={1} unit="y" aria-label={`${label} y`} />
        </Row>
      </Field>
    )
  }
  if (kind === 'depth') {
    const v = typeof value === 'string' ? value : typeof value === 'number' ? String(value) : 'midground'
    return (
      <Field label={label}>
        <SelectBox label={label} value={v} onChange={onChange as (v: string) => void} options={['background', 'midground', 'foreground'].map((d) => ({ value: d, label: titleCase(d) }))} />
      </Field>
    )
  }
  const num = typeof value === 'number' ? value : kind === 'scale' ? 1 : 0
  return (
    <Field label={label}>
      <SliderField label={label} value={num} min={kind === 'scale' ? 0.5 : 0} max={kind === 'scale' ? 3 : 1} step={0.01} onChange={onChange as (v: number) => void} />
    </Field>
  )
}

function CameraInspector({ spec, sel }: { spec: ReelSpec; sel: Extract<Selection, { kind: 'camera' }> }) {
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const sc = spec.scenes[sel.scene]
  const m = sc?.camera.moves[sel.move]
  if (!sc || !m) return null
  const def = entry(catalog?.camera_moves, m.type)
  const kind = CAMERA_FROM_TO[m.type] ?? 'unit'
  const set = (fn: (x: typeof m) => void) => edit((d) => void fn(d.scenes[sel.scene].camera.moves[sel.move]))
  return (
    <>
      <Section title="Camera move">
        <Field label="Type" hint={def?.summary}>
          <SelectBox label="Move type" value={m.type} onChange={(v) => set((x) => void ((x.type = v), (x.from = null), (x.to = null)))} options={(catalog?.camera_moves ?? []).map((c) => ({ value: c.name, label: titleCase(c.name), hint: c.summary }))} />
        </Field>
        <MoveValue kind={kind} label="From" value={m.from} onChange={(v) => set((x) => void (x.from = v as never))} />
        <MoveValue kind={kind} label="To" value={m.to} onChange={(v) => set((x) => void (x.to = v as never))} />
        <Field label="Easing" hint="How the move accelerates">
          <div className="flex items-center gap-2">
            <SelectBox label="Easing" value={m.ease} onChange={(v) => set((x) => void (x.ease = v))} options={(catalog?.easings ?? []).map((e) => ({ value: e.name, label: titleCase(e.name) }))} className="flex-1" />
            <svg viewBox="0 0 44 22" className="h-[var(--ctl-h)] w-[60px] shrink-0 rounded-ctl border border-line bg-raised" aria-hidden>
              <path d={curvePath(m.ease)} fill="none" stroke="var(--accent)" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </div>
        </Field>
      </Section>
      <Section title="Timing">
        <TimeFields t0={m.t0} t1={m.t1} max={sc.duration_sec} onChange={(t0, t1) => set((x) => void ((x.t0 = t0), (x.t1 = t1)))} />
      </Section>
    </>
  )
}

function SfxInspector({ spec, sel }: { spec: ReelSpec; sel: Extract<Selection, { kind: 'sfx' }> }) {
  const api = useApi()
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const { live, commit } = useLive()
  const sc = spec.scenes[sel.scene]
  const x = sc?.sfx[sel.sfx]
  if (!sc || !x) return null
  const set = (fn: (y: typeof x) => void) => edit((d) => void fn(d.scenes[sel.scene].sfx[sel.sfx]))
  return (
    <Section title="Sound effect">
      <Field label="Sound">
        <div className="flex gap-1.5">
          <SelectBox label="Sound effect" value={x.name} onChange={(v) => set((y) => void (y.name = v))} options={(catalog?.sfx ?? []).map((s) => ({ value: s.name, label: s.name }))} className="flex-1" />
          <IconButton label={`Listen to ${x.name}`} variant="secondary" onClick={() => void new Audio(api.sfxUrl(x.name)).play().catch(() => undefined)}>
            <Play className="size-4" />
          </IconButton>
        </div>
      </Field>
      <Field label="When">
        <NumberField value={x.t} onChange={(v) => set((y) => void (y.t = ms(Math.min(v, sc.duration_sec))))} min={0} max={sc.duration_sec} step={0.1} unit="s" aria-label="Time" digits={2} />
      </Field>
      <Field label="Volume">
        <SliderField label="Volume" value={x.volume} min={0} max={2} step={0.05} onChange={(v) => live((d) => void (d.scenes[sel.scene].sfx[sel.sfx].volume = v))} onCommit={commit} defaultValue={1} />
      </Field>
    </Section>
  )
}

function TransitionInspector({ spec, si }: { spec: ReelSpec; si: number }) {
  const catalog = useCatalog().data
  if (!spec.scenes[si]) return null
  return (
    <Section title="Transition into the next scene">
      <TransitionFields spec={spec} si={si} catalog={catalog} />
    </Section>
  )
}

// ------------------------------------------------------------------------------- the panel
export function Inspector() {
  const spec = useProject((s) => s.spec) as ReelSpec
  const sel = useProject((s) => s.selection)
  let body: ReactNode
  switch (sel.kind) {
    case 'reel':
      body = <ReelInspector spec={spec} />
      break
    case 'scene':
      body = <SceneInspector spec={spec} si={sel.scene} />
      break
    case 'character':
      body = <CharacterInspector spec={spec} id={sel.id} />
      break
    case 'layer':
      body = <LayerInspector spec={spec} sel={sel} />
      break
    case 'action':
      body = <ActionInspector spec={spec} sel={sel} />
      break
    case 'caption':
      body = <CaptionInspector spec={spec} sel={sel} />
      break
    case 'camera':
      body = <CameraInspector spec={spec} sel={sel} />
      break
    case 'sfx':
      body = <SfxInspector spec={spec} sel={sel} />
      break
    case 'transition':
      body = <TransitionInspector spec={spec} si={sel.scene} />
      break
  }
  return (
    <div id="inspector" className="flex min-h-0 flex-1 flex-col" aria-label="Inspector">
      <PanelHeader spec={spec} sel={sel} />
      <div className="min-h-0 flex-1 overflow-y-auto pb-6">{body}</div>
    </div>
  )
}

export type { CatalogEntry }
