import * as RSelect from '@radix-ui/react-select'
import * as RSlider from '@radix-ui/react-slider'
import * as RSwitch from '@radix-ui/react-switch'
import * as RToggle from '@radix-ui/react-toggle-group'
import { Check, ChevronDown, RotateCcw } from 'lucide-react'
import { forwardRef, useEffect, useId, useState, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

export function Field({ label, hint, children, action, className, htmlFor }: { label: ReactNode; hint?: ReactNode; children: ReactNode; action?: ReactNode; className?: string; htmlFor?: string }) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={htmlFor} className="text-[12px] font-medium text-fg">
          {label}
        </label>
        {action}
      </div>
      {children}
      {hint && <p className="text-[11.5px] leading-snug text-faint">{hint}</p>}
    </div>
  )
}

const control =
  'h-[var(--ctl-h)] w-full rounded-ctl border border-line bg-raised px-2.5 text-[13px] text-fg outline-none transition-colors placeholder:text-faint hover:border-line-strong focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/35 disabled:opacity-50'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...props }, ref) {
  return <input ref={ref} className={cn(control, className)} {...props} />
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...props }, ref) {
  return <textarea ref={ref} dir="auto" className={cn(control, 'h-auto min-h-20 resize-y py-2 leading-relaxed', className)} {...props} />
})

/** A number box that commits on Enter / blur, steps with the arrow keys, and never shows float noise. */
export function NumberField({ value, onChange, min, max, step = 1, unit, className, id, 'aria-label': ariaLabel, digits }: { value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number; unit?: string; className?: string; id?: string; 'aria-label'?: string; digits?: number }) {
  const places = digits ?? (step >= 1 ? 0 : Math.min(3, Math.max(1, Math.ceil(-Math.log10(step)))))
  const show = (v: number) => (Number.isFinite(v) ? String(Number(v.toFixed(places))) : '')
  const [text, setText] = useState(show(value))
  useEffect(() => setText(show(value)), [value]) // eslint-disable-line react-hooks/exhaustive-deps
  const clamp = (v: number) => Math.min(max ?? Infinity, Math.max(min ?? -Infinity, v))
  const commit = (raw: string) => {
    const v = Number(raw.replace(',', '.'))
    if (raw.trim() === '' || !Number.isFinite(v)) return setText(show(value))
    const c = clamp(v)
    setText(show(c))
    if (c !== value) onChange(Number(c.toFixed(places)))
  }
  return (
    <div className={cn('relative', className)}>
      <input
        id={id}
        aria-label={ariaLabel}
        inputMode="decimal"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
          if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            e.preventDefault()
            const mult = e.shiftKey ? 10 : e.altKey ? 0.1 : 1
            const cur = Number(text.replace(',', '.'))
            const next = clamp((Number.isFinite(cur) ? cur : value) + (e.key === 'ArrowUp' ? 1 : -1) * step * mult)
            setText(show(next))
            onChange(Number(next.toFixed(places + 1)))
          }
        }}
        className={cn(control, 'tabular text-right', unit && 'pr-7')}
      />
      {unit && <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[11px] text-faint">{unit}</span>}
    </div>
  )
}

export function SliderField({ value, onChange, min, max, step = 0.01, unit, label, format, defaultValue, onCommit }: { value: number; onChange: (v: number) => void; min: number; max: number; step?: number; unit?: string; label: string; format?: (v: number) => string; defaultValue?: number; onCommit?: () => void }) {
  const id = useId()
  return (
    <div className="flex items-center gap-2.5">
      <RSlider.Root
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={([v]) => onChange(v)}
        onValueCommit={() => onCommit?.()}
        className="relative flex h-5 min-w-0 flex-1 touch-none select-none items-center"
      >
        <RSlider.Track className="relative h-1 grow rounded-full bg-line-strong">
          <RSlider.Range className="absolute h-full rounded-full bg-accent" />
        </RSlider.Track>
        <RSlider.Thumb id={id} aria-label={label} className="block size-3.5 rounded-full border-2 border-accent bg-panel shadow transition-transform hover:scale-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent" />
      </RSlider.Root>
      <NumberField value={value} onChange={onChange} min={min} max={max} step={step} unit={unit} className="w-[72px]" aria-label={`${label} value`} />
      {defaultValue !== undefined && value !== defaultValue && (
        <button type="button" aria-label={`Reset ${label}`} title="Reset to default" onClick={() => onChange(defaultValue)} className="text-faint hover:text-fg">
          <RotateCcw className="size-3.5" />
        </button>
      )}
      <span className="sr-only">{format ? format(value) : value}</span>
    </div>
  )
}

export function Toggle({ checked, onChange, label, disabled, id }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean; id?: string }) {
  return (
    <RSwitch.Root
      id={id}
      checked={checked}
      onCheckedChange={onChange}
      disabled={disabled}
      aria-label={label}
      className="relative h-[18px] w-8 shrink-0 rounded-full bg-line-strong outline-none transition-colors data-[state=checked]:bg-accent disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <RSwitch.Thumb className="block size-3.5 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[16px]" />
    </RSwitch.Root>
  )
}

export function SwitchRow({ label, hint, checked, onChange, disabled }: { label: ReactNode; hint?: ReactNode; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  const id = useId()
  return (
    <div className="flex items-start justify-between gap-3">
      <label htmlFor={id} className="min-w-0">
        <div className="text-[12.5px] font-medium text-fg">{label}</div>
        {hint && <div className="text-[11.5px] leading-snug text-faint">{hint}</div>}
      </label>
      <Toggle id={id} checked={checked} onChange={onChange} label={typeof label === 'string' ? label : 'toggle'} disabled={disabled} />
    </div>
  )
}

export interface SegOption<T extends string> {
  value: T
  label: ReactNode
  title?: string
  disabled?: boolean
}

export function Segmented<T extends string>({ value, onChange, options, size = 'md', label, className }: { value: T; onChange: (v: T) => void; options: SegOption<T>[]; size?: 'sm' | 'md'; label: string; className?: string }) {
  return (
    <RToggle.Root
      type="single"
      value={value}
      onValueChange={(v) => v && onChange(v as T)}
      aria-label={label}
      className={cn('inline-flex rounded-ctl border border-line bg-raised p-0.5', className)}
    >
      {options.map((o) => (
        <RToggle.Item
          key={o.value}
          value={o.value}
          disabled={o.disabled}
          title={o.title}
          className={cn(
            'inline-flex items-center justify-center gap-1.5 rounded-[6px] px-2.5 text-[12px] font-medium text-muted transition-colors hover:text-fg disabled:opacity-40',
            'data-[state=on]:bg-accent-soft data-[state=on]:text-accent',
            size === 'sm' ? 'h-6' : 'h-[calc(var(--ctl-h)-6px)]',
          )}
        >
          {o.label}
        </RToggle.Item>
      ))}
    </RToggle.Root>
  )
}

export interface SelectOption<T extends string> {
  value: T
  label: ReactNode
  hint?: ReactNode
  group?: string
  disabled?: boolean
}

export function SelectBox<T extends string>({ value, onChange, options, placeholder, label, className, id, disabled }: { value: T | ''; onChange: (v: T) => void; options: SelectOption<T>[]; placeholder?: string; label: string; className?: string; id?: string; disabled?: boolean }) {
  const groups = [...new Set(options.map((o) => o.group ?? ''))]
  return (
    <RSelect.Root value={value || undefined} onValueChange={(v) => onChange(v as T)} disabled={disabled}>
      <RSelect.Trigger id={id} aria-label={label} className={cn(control, 'flex items-center justify-between gap-2 text-left data-[placeholder]:text-faint', className)}>
        <span className="min-w-0 truncate">
          <RSelect.Value placeholder={placeholder ?? 'Choose…'} />
        </span>
        <RSelect.Icon>
          <ChevronDown className="size-3.5 text-faint" />
        </RSelect.Icon>
      </RSelect.Trigger>
      <RSelect.Portal>
        <RSelect.Content position="popper" sideOffset={4} className="pop z-50 max-h-[320px] min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-card bg-panel text-fg shadow-[var(--shadow-pop)]">
          <RSelect.Viewport className="p-1">
            {groups.map((g) => (
              <RSelect.Group key={g}>
                {g && <RSelect.Label className="eyebrow px-2 pb-1 pt-2">{g}</RSelect.Label>}
                {options
                  .filter((o) => (o.group ?? '') === g)
                  .map((o) => (
                    <RSelect.Item key={o.value} value={o.value} disabled={o.disabled} className="relative flex cursor-default select-none items-center gap-2 rounded-[6px] py-1.5 pl-2 pr-7 text-[13px] outline-none data-[disabled]:opacity-40 data-[highlighted]:bg-hover">
                      <div className="min-w-0">
                        <RSelect.ItemText>{o.label}</RSelect.ItemText>
                        {o.hint && <div className="truncate text-[11.5px] text-faint">{o.hint}</div>}
                      </div>
                      <RSelect.ItemIndicator className="absolute right-2">
                        <Check className="size-3.5 text-accent" />
                      </RSelect.ItemIndicator>
                    </RSelect.Item>
                  ))}
              </RSelect.Group>
            ))}
          </RSelect.Viewport>
        </RSelect.Content>
      </RSelect.Portal>
    </RSelect.Root>
  )
}

const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i

export function ColorField({ value, onChange, fallback, label }: { value: string | undefined; onChange: (v: string | undefined) => void; fallback: string; label: string }) {
  const shown = value ?? fallback
  const [text, setText] = useState(shown)
  useEffect(() => setText(shown), [shown])
  return (
    <div className="flex items-center gap-2">
      <label className="relative size-[var(--ctl-h)] shrink-0 cursor-pointer overflow-hidden rounded-ctl border border-line" style={{ background: shown }} title={`Pick ${label}`}>
        <input type="color" aria-label={`${label} colour`} value={HEX.test(shown) && shown.length === 7 ? shown : '#000000'} onChange={(e) => onChange(e.target.value)} className="absolute inset-0 size-full cursor-pointer opacity-0" />
      </label>
      <Input
        aria-label={`${label} hex`}
        value={text}
        spellCheck={false}
        className="font-mono"
        onChange={(e) => setText(e.target.value)}
        onBlur={() => (HEX.test(text) ? onChange(text.toLowerCase()) : setText(shown))}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      />
      {value !== undefined && (
        <button type="button" aria-label={`Reset ${label} to the archetype default`} title="Use the archetype's colour" onClick={() => onChange(undefined)} className="text-faint hover:text-fg">
          <RotateCcw className="size-3.5" />
        </button>
      )}
    </div>
  )
}
