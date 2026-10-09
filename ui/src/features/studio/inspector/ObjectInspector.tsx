// The inspector for an object of a scene (a car, a tree, a cake from the asset library) and for each of its motions.
import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronDown, ChevronRight, CircleAlert, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useCatalog } from '@/api/hooks'
import type { Catalog, ObjectMotion, ReelSpec, Scene, SceneObject, Vec2 } from '@/api/types'
import { Button, ColorField, Field, IconButton, Menu, NumberField, Segmented, SelectBox, SliderField, SwitchRow } from '@/components/ui'
import { AddAssetDialog } from '@/features/assets/AddAssetDialog'
import { AssetThumb } from '@/features/assets/AssetThumb'
import { cn } from '@/lib/cn'
import { followedShade } from '@/lib/colors'
import { curvePath } from '@/lib/easing'
import { titleCase } from '@/lib/format'
import { entry, nearestSlot, objectEntry, objectPersons, objectPoint, type Selection } from '@/lib/spec'
import { MIN_CLIP, ms } from '@/lib/timeline'
import { useProject } from '@/store/project'
import { MOTIONS, defaultDestination, motionDef, motionDetail, motionProblems, readNumber, retypeMotion, setMotionField, sortMotions, type Problem } from '../motions'
import { ObjectPickerPopover, sizeText, useAssetRef } from '../ObjectPicker'
import { addMotion, addObject, changeObjectAsset, deleteSelection } from '../ops'
import { objectName, objectProblems, staysToEnd } from '../objects'
import { Row, Section, useLive } from './common'

const round3 = (v: number): number => Math.round(v * 1000) / 1000

/** What is wrong, one line each: an icon and a word go with the colour. */
function Problems({ list }: { list: Problem[] }) {
  if (!list.length) return null
  return (
    <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
      {list.map((p, i) => (
        <li key={i} className="flex items-start gap-1.5 text-[12px] leading-snug text-muted">
          {p.tone === 'danger' ? <CircleAlert className="mt-px size-3.5 shrink-0 text-danger" aria-hidden /> : <AlertTriangle className="mt-px size-3.5 shrink-0 text-warning" aria-hidden />}
          <span>
            <span className="sr-only">{p.tone === 'danger' ? 'Problem: ' : 'Warning: '}</span>
            {p.text}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** Where something stands or goes: a named place of the set, or exact screen fractions (x across, y down). */
function PlaceField({ name, scene, catalog, value, onChange, hint }: { name: string; scene: Scene; catalog: Catalog | undefined; value: Vec2 | string; onChange: (v: Vec2 | string) => void; hint?: string }) {
  const slots = entry(catalog?.backgrounds, scene.background.template)?.slots ?? {}
  const names = Object.keys(slots)
  const isSlot = typeof value === 'string'
  const at: Vec2 = Array.isArray(value) ? value : (slots[value] ?? slots.center ?? [0.5, 0.8])
  const options = isSlot && !names.includes(value) ? [...names, value] : names
  return (
    <Field label={name} hint={hint}>
      <Segmented
        label={`${name} mode`}
        size="sm"
        value={isSlot ? 'slot' : 'point'}
        onChange={(m) => onChange(m === 'slot' ? (nearestSlot(slots, at[0], at[1]) ?? names[0] ?? 'center') : [round3(at[0]), round3(at[1])])}
        options={[
          { value: 'slot', label: 'Named place' },
          { value: 'point', label: 'Exact point' },
        ]}
      />
      {isSlot ? (
        <SelectBox label={`${name} place`} value={value} onChange={onChange} options={options.map((n) => ({ value: n, label: n }))} />
      ) : (
        <Row>
          <NumberField value={at[0]} onChange={(x) => onChange([x, at[1]])} step={0.01} min={-1} max={2} unit="x" aria-label={`${name} x`} />
          <NumberField value={at[1]} onChange={(y) => onChange([at[0], y])} step={0.01} min={-0.2} max={1.4} unit="y" aria-label={`${name} y`} />
        </Row>
      )}
    </Field>
  )
}

/** Two times of a scene (seconds from its start): from and until, kept at least a moment apart. */
function TimePair({ t0, t1, max, onChange, from, until }: { t0: number; t1: number; max: number; onChange: (t0: number, t1: number) => void; from: string; until: string }) {
  return (
    <Row>
      <Field label={from}>
        <NumberField value={t0} onChange={(v) => onChange(ms(Math.min(v, t1 - MIN_CLIP)), t1)} min={0} max={max} step={0.1} unit="s" aria-label={`${from} time`} digits={2} />
      </Field>
      <Field label={until}>
        <NumberField value={t1} onChange={(v) => onChange(t0, ms(Math.max(v, t0 + MIN_CLIP)))} min={0} max={max} step={0.1} unit="s" aria-label={`${until} time`} digits={2} />
      </Field>
      <p className="col-span-2 -mt-1 text-[11.5px] text-faint">Length {(t1 - t0).toFixed(2)} s · times are seconds from the start of the scene</p>
    </Row>
  )
}

// ------------------------------------------------------------------------------- the object
/** The asset of an object: its picture and name; a click opens the searchable list of the library. */
function AssetField({ spec, si, oi }: { spec: ReelSpec; si: number; oi: number }) {
  const catalog = useCatalog().data
  const qc = useQueryClient()
  const ref = useAssetRef()
  const edit = useProject((s) => s.edit)
  const [adding, setAdding] = useState(false)
  const o = spec.scenes[si]?.objects[oi]
  if (!o) return null
  const row = objectEntry(catalog, o.asset)
  // a drawing just added is in the catalog the query holds now, not in the one this render was made with
  const change = (name: string) => edit((d) => changeObjectAsset(d, qc.getQueryData<Catalog>(['catalog']) ?? catalog, si, oi, name))
  return (
    <Field label="Asset" hint={row?.summary}>
      <ObjectPickerPopover
        current={o.asset}
        onPick={change}
        onAddOwn={() => setAdding(true)}
        trigger={
          <button type="button" aria-label={`Asset: ${objectName(o.asset)}. Change it`} className="flex w-full items-center gap-2.5 rounded-ctl border border-line bg-raised px-2 py-1.5 text-left hover:border-line-strong">
            <span className="block w-8 shrink-0">
              <AssetThumb asset={ref(o.asset)} style={spec.meta.style} className="rounded-[5px]" alt="" />
            </span>
            <span className="min-w-0 flex-1 truncate font-medium">{objectName(o.asset)}</span>
            <ChevronDown className="size-3.5 shrink-0 text-faint" aria-hidden />
          </button>
        }
      />
      <AddAssetDialog
        open={adding}
        onOpenChange={setAdding}
        initial={{ kind: 'object', hint: 'The drawing you add takes the place of this one in the scene.' }}
        onAdded={(a) => {
          setAdding(false)
          change(a.name)
        }}
      />
    </Field>
  )
}

function ObjectFields({ spec, si, oi }: { spec: ReelSpec; si: number; oi: number }) {
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const { live, commit } = useLive()
  const sc = spec.scenes[si]
  const o = sc?.objects[oi]
  if (!sc || !o) return null
  const persons = objectPersons(catalog, o)
  const set = (fn: (x: SceneObject) => void) => edit((d) => void fn(d.scenes[si].objects[oi]))
  const lv = (fn: (x: SceneObject) => void) => live((d) => void fn(d.scenes[si].objects[oi]))
  const mid = o.depth === 'mid'
  return (
    <>
      <AssetField spec={spec} si={si} oi={oi} />
      <Problems list={objectProblems(sc, o, catalog)} />
      <PlaceField name="Position" scene={sc} catalog={catalog} value={o.position} onChange={(v) => set((x) => void (x.position = v))} hint="Where it stands: a named place on this set, or exact screen fractions. You can also drag it on the stage." />
      <Field label="Size" hint={persons === undefined ? undefined : `Height ≈ ${sizeText(persons)} standing at the same spot`}>
        <SliderField label="Scale" value={o.scale} min={0.05} max={6} step={0.01} onChange={(v) => lv((x) => void (x.scale = v))} onCommit={commit} defaultValue={1} />
      </Field>
      <Row>
        <Field label="Depth plane">
          <Segmented
            label="Depth"
            size="sm"
            value={o.depth}
            onChange={(v) => set((x) => void (x.depth = v))}
            options={[
              { value: 'background', label: 'Back' },
              { value: 'mid', label: 'Mid' },
              { value: 'foreground', label: 'Front' },
            ]}
          />
        </Field>
        <Field label="Facing" hint="The art looks right; Left mirrors it">
          <Segmented
            label="Facing"
            size="sm"
            value={o.facing}
            onChange={(v) => set((x) => void (x.facing = v))}
            options={[
              { value: 'auto', label: 'Auto' },
              { value: 'left', label: 'Left' },
              { value: 'right', label: 'Right' },
            ]}
          />
        </Field>
      </Row>
      <Field label="Layer" hint={mid ? 'On the Mid plane it can stand behind the characters or in front of them' : 'Only on the Mid plane: Back is always behind the characters and Front always in front of them'}>
        <Segmented
          label="Layer"
          size="sm"
          value={o.layer}
          onChange={(v) => set((x) => void (x.layer = v))}
          options={[
            { value: 'behind', label: 'Behind', disabled: !mid },
            { value: 'front', label: 'In front', disabled: !mid },
          ]}
        />
      </Field>
      <Field label="Rotation" hint="Degrees, clockwise">
        <SliderField label="Rotation" value={o.rotation} min={-360} max={360} step={1} unit="°" onChange={(v) => lv((x) => void (x.rotation = v))} onCommit={commit} defaultValue={0} />
      </Field>
      <Field label="Opacity">
        <SliderField label="Opacity" value={o.alpha} min={0} max={1} step={0.05} onChange={(v) => lv((x) => void (x.alpha = v))} onCommit={commit} defaultValue={1} />
      </Field>
    </>
  )
}

/** When it is on screen: from a time until a time, or until the end of the scene. */
function SpanFields({ spec, si, oi }: { spec: ReelSpec; si: number; oi: number }) {
  const edit = useProject((s) => s.edit)
  const sc = spec.scenes[si]
  const o = sc?.objects[oi]
  if (!sc || !o) return null
  const dur = sc.duration_sec
  const stays = staysToEnd(o)
  const set = (fn: (x: SceneObject) => void) => edit((d) => void fn(d.scenes[si].objects[oi]))
  return (
    <>
      {stays ? (
        <Row>
          <Field label="Visible from">
            <NumberField value={o.t0} onChange={(v) => set((x) => void (x.t0 = ms(Math.min(v, Math.max(0, dur - MIN_CLIP)))))} min={0} max={dur} step={0.1} unit="s" aria-label="Visible from time" digits={2} />
          </Field>
          <Field label="Visible until">
            <div className="flex h-[var(--ctl-h)] items-center rounded-ctl border border-line bg-raised px-2.5 text-[13px] text-muted">the end of the scene</div>
          </Field>
        </Row>
      ) : (
        <TimePair t0={o.t0} t1={o.t1 ?? dur} max={dur} from="Visible from" until="Visible until" onChange={(t0, t1) => set((x) => void ((x.t0 = t0), (x.t1 = t1)))} />
      )}
      <SwitchRow label="Until the end of the scene" hint="Switch off to make it disappear at a moment of your choice" checked={stays} onChange={(on) => set((x) => void (x.t1 = on ? null : ms(dur)))} />
    </>
  )
}

/** One colour field per part of the drawing that can be recoloured, with the drawing's own colour as the starting point. */
function Colours({ spec, si, oi }: { spec: ReelSpec; si: number; oi: number }) {
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const o = spec.scenes[si]?.objects[oi]
  if (!o) return null
  const row = objectEntry(catalog, o.asset)
  const drawing = (row?.palette ?? {}) as Record<string, string>
  const roles = row?.roles?.length ? row.roles : Object.keys(drawing)
  const changed = Object.keys(o.palette).length > 0
  return (
    <>
      {row && roles.length === 0 && <p className="text-[12px] text-faint">Nothing in this drawing can be recoloured.</p>}
      {!row && <p className="text-[12px] text-faint">The library has no drawing called “{o.asset}”, so its colours are not known.</p>}
      <div className="flex flex-col gap-2.5">
        {roles.map((r) => {
          // a shade of a part that was recoloured follows it: its swatch is the one the render will use
          const follows = followedShade(drawing, o.palette, r)
          return (
            <div key={r} className="grid grid-cols-[84px_1fr] items-center gap-2">
              <span className="min-w-0">
                <span className="block truncate text-[12px] text-muted">{titleCase(r)}</span>
                {follows && <span className="block truncate text-[10.5px] text-faint">follows {titleCase(r.replace(/_(dark|light)$/, ''))}</span>}
              </span>
              <ColorField
                label={titleCase(r)}
                value={o.palette[r]}
                fallback={follows ?? drawing[r] ?? '#888888'}
                resetTo="the drawing’s own colour"
                onChange={(v) =>
                  edit((d) => {
                    const palette = d.scenes[si].objects[oi].palette
                    if (v === undefined) delete palette[r]
                    else palette[r] = v
                  })
                }
              />
            </div>
          )
        })}
      </div>
      {roles.length > 0 && <p className="text-[11.5px] leading-snug text-faint">A shade such as body_dark follows its base colour, unless you set it as well.</p>}
      {changed && (
        <Button size="sm" className="self-start" onClick={() => edit((d) => void (d.scenes[si].objects[oi].palette = {}))}>
          Reset all colours
        </Button>
      )}
    </>
  )
}

// ------------------------------------------------------------------------------- motions
/** One motion: what kind, its own settings, when it happens, how it eases. */
export function MotionEditor({ spec, si, oi, mi, onReorder }: { spec: ReelSpec; si: number; oi: number; mi: number; onReorder?: (index: number) => void }) {
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const { live, commit } = useLive()
  const sc = spec.scenes[si]
  const o = sc?.objects[oi]
  const m = o?.motions[mi]
  if (!sc || !o || !m) return null
  const def = motionDef(m.type)
  const standing = objectPoint(sc, o, catalog)
  const at = (d: ReelSpec) => d.scenes[si].objects[oi].motions[mi]
  const set = (fn: (x: ObjectMotion) => void) => edit((d) => void fn(at(d)))
  const lv = (fn: (x: ObjectMotion) => void) => live((d) => void fn(at(d)))
  // the engine applies motions in list order, so a motion moved past another one swaps places with it
  const setTimes = (t0: number, t1: number) => {
    let now = mi
    edit((d) => {
      const x = at(d)
      x.t0 = ms(t0)
      x.t1 = ms(t1)
      now = sortMotions(d.scenes[si].objects[oi], mi)
    })
    if (now !== mi) onReorder?.(now)
  }
  const destination = Array.isArray(m.to) ? (m.to as Vec2) : typeof m.to === 'string' ? m.to : defaultDestination(standing)
  const easings = (catalog?.easings ?? []).map((e) => e.name)
  if (!easings.includes(m.ease)) easings.push(m.ease)
  return (
    <div className="flex flex-col gap-3">
      <Field label="Motion" hint={def?.summary}>
        <SelectBox
          label="Motion type"
          value={m.type}
          onChange={(v) => set((x) => retypeMotion(x, v, standing))}
          options={[...MOTIONS.map((d) => ({ value: d.type, label: d.label, hint: d.summary })), ...(def ? [] : [{ value: m.type, label: `${m.type} (unknown)`, hint: 'not a motion Reel knows' }])]}
        />
      </Field>
      {def?.fields.map((f) =>
        f.kind === 'destination' ? (
          <PlaceField key="destination" name="Destination" scene={sc} catalog={catalog} value={destination} onChange={(v) => set((x) => void setMotionField(x, 'to', v))} hint="Where it ends up. It stays there when the motion is over." />
        ) : f.kind === 'count' ? (
          <Field key={f.key} label={f.label}>
            <NumberField value={readNumber(m, f.key, f.fallback)} onChange={(v) => set((x) => void setMotionField(x, 'count', Math.round(v), f.fallback))} min={f.min} max={f.max} step={1} aria-label={f.label} />
          </Field>
        ) : (
          <Field key={`${f.key}-${f.label}`} label={f.label} hint={f.hint}>
            <SliderField label={f.label} value={readNumber(m, f.key, f.fallback)} min={f.min} max={f.max} step={f.step} unit={f.unit} onChange={(v) => lv((x) => void setMotionField(x, f.key, v, f.fallback))} onCommit={commit} defaultValue={f.fallback} />
          </Field>
        ),
      )}
      <TimePair t0={m.t0} t1={m.t1} max={sc.duration_sec} from="Motion starts" until="Motion ends" onChange={setTimes} />
      <Field label="Easing" hint="How the motion speeds up and slows down">
        <div className="flex items-center gap-2">
          <SelectBox label="Easing" value={m.ease} onChange={(v) => set((x) => void (x.ease = v))} options={easings.map((e) => ({ value: e, label: titleCase(e) }))} className="flex-1" />
          <svg viewBox="0 0 44 22" className="h-[var(--ctl-h)] w-[60px] shrink-0 rounded-ctl border border-line bg-raised" aria-hidden>
            <path d={curvePath(m.ease)} fill="none" stroke="var(--accent)" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </div>
      </Field>
      <Problems list={motionProblems(m, sc, catalog)} />
    </div>
  )
}

function MotionRow({ spec, si, oi, mi, open, onToggle, onReorder, onRemoved }: { spec: ReelSpec; si: number; oi: number; mi: number; open: boolean; onToggle: () => void; onReorder: (index: number) => void; onRemoved: () => void }) {
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const sc = spec.scenes[si]
  const m = sc?.objects[oi]?.motions[mi]
  if (!sc || !m) return null
  const label = motionDef(m.type)?.label ?? m.type
  const detail = motionDetail(m)
  const bad = motionProblems(m, sc, catalog).length > 0
  return (
    <div className="rounded-card border border-line bg-raised">
      <div className="flex items-center gap-1 pr-1">
        <button type="button" aria-expanded={open} onClick={onToggle} className="flex min-w-0 flex-1 items-center gap-1.5 rounded-card px-2.5 py-2 text-left hover:bg-hover">
          <ChevronRight className={cn('size-3.5 shrink-0 text-faint', open && 'rotate-90')} aria-hidden />
          <span className="font-medium">{label}</span> {detail && <span className="min-w-0 truncate text-muted">{detail}</span>}
          {bad && <AlertTriangle className="size-3.5 shrink-0 text-warning" aria-label="Has a problem" />}{' '}
          <span className="ml-auto shrink-0 pl-2 text-muted tabular">
            {m.t0.toFixed(1)}–{m.t1.toFixed(1)} s
          </span>
        </button>
        <IconButton
          label={`Remove ${label}`}
          size="icon-sm"
          onClick={() => {
            edit((d) => void deleteSelection(d, { kind: 'motion', scene: si, object: oi, motion: mi }))
            onRemoved()
          }}
          className="hover:text-danger"
        >
          <Trash2 className="size-3.5" />
        </IconButton>
      </div>
      {open && (
        <div className="border-t border-line p-2.5">
          <MotionEditor spec={spec} si={si} oi={oi} mi={mi} onReorder={onReorder} />
        </div>
      )}
    </div>
  )
}

function MotionAdder({ onAdd }: { onAdd: (type: string) => void }) {
  return (
    <Menu
      align="end"
      trigger={
        <Button variant="secondary" size="sm" aria-label="Add a motion">
          <Plus className="size-3.5" aria-hidden /> Add
        </Button>
      }
      entries={[
        { heading: 'At the playhead' },
        ...MOTIONS.map((d) => ({
          label: (
            <span className="flex max-w-[260px] flex-col">
              <span>{d.label}</span>
              <span className="whitespace-normal text-[11.5px] leading-snug text-faint">{d.summary}</span>
            </span>
          ),
          onSelect: () => onAdd(d.type),
        })),
      ]}
    />
  )
}

function Motions({ spec, si, oi }: { spec: ReelSpec; si: number; oi: number }) {
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const [open, setOpen] = useState<number | null>(null)
  const o = spec.scenes[si]?.objects[oi]
  if (!o) return null
  const add = (type: string) => {
    let made = -1
    edit((d) => {
      const s = addMotion(d, catalog, si, oi, type, useProject.getState().playhead)
      if (s?.kind === 'motion') made = s.motion
    })
    if (made >= 0) setOpen(made)
  }
  return (
    <>
      <div className="-mt-1 flex justify-end">
        <MotionAdder onAdd={add} />
      </div>
      {o.motions.length === 0 && <p className="text-[12px] text-faint">It stays as it is. Add a motion to make it move, hop, spin, fade in or grow; you can also add one from the timeline’s + beside its name.</p>}
      {o.motions.map((_, mi) => (
        <MotionRow key={mi} spec={spec} si={si} oi={oi} mi={mi} open={open === mi} onToggle={() => setOpen(open === mi ? null : mi)} onReorder={setOpen} onRemoved={() => setOpen(null)} />
      ))}
    </>
  )
}

// ------------------------------------------------------------------------------- the panels
export function ObjectInspector({ spec, sel }: { spec: ReelSpec; sel: Extract<Selection, { kind: 'object' }> }) {
  const sc = spec.scenes[sel.scene]
  if (!sc?.objects?.[sel.object]) return null
  return (
    <>
      <Section title="Object">
        <ObjectFields spec={spec} si={sel.scene} oi={sel.object} />
      </Section>
      <Section title="On screen">
        <SpanFields spec={spec} si={sel.scene} oi={sel.object} />
      </Section>
      <Section title="Colours">
        <Colours spec={spec} si={sel.scene} oi={sel.object} />
      </Section>
      <Section title="Motions">
        <Motions spec={spec} si={sel.scene} oi={sel.object} />
      </Section>
    </>
  )
}

export function MotionInspector({ spec, sel }: { spec: ReelSpec; sel: Extract<Selection, { kind: 'motion' }> }) {
  const select = useProject((s) => s.select)
  const o = spec.scenes[sel.scene]?.objects?.[sel.object]
  if (!o?.motions[sel.motion]) return null
  return (
    <Section title="Motion">
      <button type="button" className="flex items-center gap-1 self-start text-accent hover:underline" onClick={() => select({ kind: 'object', scene: sel.scene, object: sel.object })}>
        <ChevronRight className="size-3.5 rotate-180" aria-hidden /> Edit the {objectName(o.asset)}
      </button>
      <MotionEditor spec={spec} si={sel.scene} oi={sel.object} mi={sel.motion} onReorder={(k) => select({ ...sel, motion: k })} />
    </Section>
  )
}

/** The scene inspector's list of its objects, with the picker to add one. */
export function SceneObjectsSection({ spec, si }: { spec: ReelSpec; si: number }) {
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const select = useProject((s) => s.select)
  const sc = spec.scenes[si]
  if (!sc) return null
  const objects = sc.objects ?? []
  return (
    <Section
      title="Objects"
      defaultOpen={objects.length > 0}
      action={
        <ObjectPickerPopover
          align="end"
          onPick={(name) =>
            edit((d) => {
              const added = addObject(d, catalog, name, useProject.getState().playhead, si)
              if (added) select(added)
            })
          }
          trigger={
            <Button variant="secondary" size="sm" aria-label="Add an object to this scene">
              <Plus className="size-3.5" aria-hidden /> Add
            </Button>
          }
        />
      }
    >
      {objects.length === 0 && <p className="text-[12px] text-faint">Nothing but the characters. Add a car, a tree, a cake … from the library.</p>}
      {objects.map((o, oi) => (
        <button key={oi} type="button" className="flex items-center justify-between rounded-ctl border border-line bg-raised px-2.5 py-2 text-left hover:border-line-strong" onClick={() => select({ kind: 'object', scene: si, object: oi })}>
          <span className="font-medium">{objectName(o.asset)}</span> <span className="text-muted tabular">{o.motions.length ? `${o.motions.length} ${o.motions.length === 1 ? 'motion' : 'motions'}` : ''}</span>
        </button>
      ))}
    </Section>
  )
}
