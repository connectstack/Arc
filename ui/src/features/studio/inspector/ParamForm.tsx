import { useId } from 'react'
import type { Param } from '@/api/types'
import { Field, Input, NumberField, Segmented, SelectBox, SliderField, SwitchRow } from '@/components/ui'
import { titleCase } from '@/lib/format'

export interface ParamContext {
  /** names a target may be: background slots and character ids */
  names: string[]
}

/** Params the engine fills in itself (e.g. word timings from the voice): never shown. */
const HIDDEN = new Set(['words'])

type Kind = 'enum' | 'boolean' | 'integer' | 'number' | 'nullable-number' | 'target' | 'text' | 'json'

export function kindOf(p: Param): Kind {
  const t = p.type
  if (p.enum && p.enum.length) return 'enum'
  if (t === 'boolean') return 'boolean'
  if (t === 'integer') return 'integer'
  if (t === 'number') return 'number'
  if (t === 'number | null') return 'nullable-number'
  if (t.includes('array') && t.includes('string')) return 'target'
  if (t === 'string' || t === 'string | null') return 'text'
  return 'json'
}

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b)

/** Set one param; a value equal to the engine default is removed, so specs stay small and follow the defaults as they evolve. */
export function setParam(params: Record<string, unknown>, p: Param, v: unknown): Record<string, unknown> {
  const next = { ...params }
  if (v === undefined || v === null || same(v, p.default)) delete next[p.name]
  else next[p.name] = v
  return next
}

function TargetField({ p, value, onChange, names }: { p: Param; value: unknown; onChange: (v: unknown) => void; names: string[] }) {
  const listId = useId()
  const mode = Array.isArray(value) ? 'point' : typeof value === 'string' ? 'name' : 'default'
  const point = Array.isArray(value) ? (value as number[]) : [0.5, 0.74]
  return (
    <div className="flex flex-col gap-2">
      <Segmented
        label={`${p.name} target`}
        size="sm"
        value={mode}
        onChange={(m) => onChange(m === 'default' ? null : m === 'name' ? (names[0] ?? 'center') : [0.5, 0.74])}
        options={[
          { value: 'default', label: 'Default' },
          { value: 'name', label: 'Slot or name' },
          { value: 'point', label: 'Point' },
        ]}
      />
      {mode === 'name' && (
        <>
          <Input list={listId} value={String(value)} aria-label={`${p.name} name`} onChange={(e) => onChange(e.target.value)} placeholder="center, a slot, a character id…" />
          <datalist id={listId}>
            {names.map((n) => (
              <option key={n} value={n} />
            ))}
          </datalist>
        </>
      )}
      {mode === 'point' && (
        <div className="grid grid-cols-2 gap-2">
          <NumberField value={point[0]} onChange={(x) => onChange([x, point[1]])} step={0.01} min={-1} max={2} aria-label={`${p.name} x`} unit="x" />
          <NumberField value={point[1]} onChange={(y) => onChange([point[0], y])} step={0.01} min={-1} max={2} aria-label={`${p.name} y`} unit="y" />
        </div>
      )}
    </div>
  )
}

export function ParamField({ p, value, onChange, ctx }: { p: Param; value: unknown; onChange: (v: unknown) => void; ctx: ParamContext }) {
  const kind = kindOf(p)
  const id = useId()
  const label = titleCase(p.name)
  const cur = value === undefined ? p.default : value
  const hint = p.description

  if (kind === 'boolean') return <SwitchRow label={label} hint={hint} checked={Boolean(cur)} onChange={onChange} />

  let control: React.ReactNode
  switch (kind) {
    case 'enum': {
      const options = (p.enum ?? []).map((v) => ({ value: v, label: titleCase(v) }))
      control =
        options.length <= 4 ? (
          <Segmented label={label} value={String(cur ?? '')} onChange={onChange} options={options} size="sm" className="flex-wrap" />
        ) : (
          <SelectBox id={id} label={label} value={String(cur ?? '')} onChange={onChange} options={options} />
        )
      break
    }
    case 'number':
    case 'integer': {
      const step = kind === 'integer' ? 1 : p.maximum !== undefined && p.minimum !== undefined && p.maximum - p.minimum <= 3 ? 0.05 : 0.1
      control =
        p.minimum !== undefined && p.maximum !== undefined ? (
          <SliderField label={label} value={Number(cur ?? p.minimum)} min={p.minimum} max={p.maximum} step={step} onChange={onChange} defaultValue={typeof p.default === 'number' ? p.default : undefined} />
        ) : (
          <NumberField id={id} value={Number(cur ?? 0)} onChange={onChange} step={step} min={p.minimum} max={p.maximum} />
        )
      break
    }
    case 'nullable-number':
      control = (
        <div className="flex items-center gap-2">
          <NumberField id={id} value={typeof cur === 'number' ? cur : 0} onChange={onChange} step={0.1} min={0} className="flex-1" />
          {typeof cur === 'number' && (
            <button className="text-[12px] text-accent hover:underline" onClick={() => onChange(null)}>
              Use default
            </button>
          )}
        </div>
      )
      break
    case 'target':
      control = <TargetField p={p} value={cur} onChange={onChange} names={ctx.names} />
      break
    case 'text':
      control = <Input id={id} value={typeof cur === 'string' ? cur : ''} onChange={(e) => onChange(e.target.value || null)} placeholder={p.default === null || p.default === undefined ? 'default' : String(p.default)} />
      break
    default:
      control = (
        <Input
          id={id}
          value={cur === undefined || cur === null ? '' : JSON.stringify(cur)}
          className="font-mono"
          onChange={(e) => {
            try {
              onChange(e.target.value.trim() ? JSON.parse(e.target.value) : null)
            } catch {
              /* keep typing: only valid JSON is applied */
            }
          }}
        />
      )
  }
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      {control}
    </Field>
  )
}

export function ParamForm({ params, value, onChange, ctx }: { params: Param[]; value: Record<string, unknown>; onChange: (next: Record<string, unknown>) => void; ctx: ParamContext }) {
  const shown = params.filter((p) => !HIDDEN.has(p.name))
  if (shown.length === 0) return <p className="text-[12px] text-faint">No settings: this one is fully automatic.</p>
  return (
    <div className="flex flex-col gap-3.5">
      {shown.map((p) => (
        <ParamField key={p.name} p={p} value={value[p.name]} ctx={ctx} onChange={(v) => onChange(setParam(value, p, v))} />
      ))}
    </div>
  )
}
