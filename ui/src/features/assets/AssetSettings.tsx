import { ChevronDown, ChevronRight, CircleAlert, X } from 'lucide-react'
import { useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { Field, Input, Segmented, SliderField } from '@/components/ui'
import { cn } from '@/lib/cn'
import {
  ANCHORS,
  FACINGS,
  KINDS,
  KIND_HEIGHT,
  KIND_INFO,
  MAX_HEIGHT,
  MAX_WORDS,
  MIN_HEIGHT,
  SIZE_PRESETS,
  WORD_SEPARATORS,
  addWords,
  anchorId,
  anchorText,
  canCutOut,
  nameProblem,
  sizeRatio,
  slugName,
  trimName,
  type AssetFormValue,
} from './assetFields'
import type { AssetInfo, AssetKind } from '@/api/types'

const box =
  'flex min-h-[var(--ctl-h)] w-full cursor-text flex-wrap items-center gap-1 rounded-ctl border border-line bg-raised px-1.5 py-1 text-[13px] text-fg transition-colors hover:border-line-strong focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/35'

/**
 * The words a script may use for an asset, as chips. Enter or a comma ends a word, a pasted "a, b, c" becomes three words,
 * Backspace on an empty box takes the last one back, and a word still being typed is kept when you leave the box.
 */
export function TagInput({ id, value, onChange, disabled, placeholder }: { id?: string; value: string[]; onChange: (words: string[]) => void; disabled?: boolean; placeholder?: string }) {
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const full = value.length >= MAX_WORDS

  const add = (parts: string[]) => {
    const next = addWords(value, parts)
    if (next !== value) onChange(next)
  }
  const flush = () => {
    if (text.trim()) add([text])
    setText('')
  }

  return (
    <div className={cn(box, disabled && 'opacity-50')} onClick={() => inputRef.current?.focus()}>
      {value.map((w) => (
        <span key={w} dir="auto" className="inline-flex h-[22px] max-w-full items-center gap-0.5 rounded-chip bg-hover pl-2 pr-0.5 text-[12px] font-medium text-fg">
          <span className="truncate">{w}</span>
          <button
            type="button"
            aria-label={`Remove ${w}`}
            disabled={disabled}
            onClick={(e) => {
              e.stopPropagation()
              onChange(value.filter((x) => x !== w))
              inputRef.current?.focus()
            }}
            className="grid size-[18px] place-items-center rounded text-muted hover:bg-line-strong hover:text-fg"
          >
            <X className="size-3" aria-hidden />
          </button>
        </span>
      ))}
      <input
        ref={inputRef}
        id={id}
        dir="auto"
        value={text}
        disabled={disabled || full}
        autoComplete="off"
        placeholder={value.length ? undefined : placeholder}
        onChange={(e) => {
          const v = e.target.value
          if (WORD_SEPARATORS.test(v)) {
            // a comma typed, or text with commas dropped in: every part but the last is finished
            const parts = v.split(WORD_SEPARATORS)
            const rest = parts.pop() ?? ''
            add(parts)
            setText(rest.replace(/^\s+/, ''))
          } else setText(v)
        }}
        onKeyDown={(e) => {
          if (e.nativeEvent.isComposing) return // an input method (Hindi typing, say) uses Enter to finish its own word
          if (e.key === 'Enter') {
            e.preventDefault()
            flush()
          } else if (e.key === 'Backspace' && !text && value.length) {
            onChange(value.slice(0, -1))
          }
        }}
        onPaste={(e) => {
          const pasted = e.clipboardData.getData('text')
          if (!WORD_SEPARATORS.test(pasted)) return // one word: let it land in the box as usual
          e.preventDefault()
          add([text, ...pasted.split(WORD_SEPARATORS)])
          setText('')
        }}
        onBlur={flush}
        className="min-w-[10ch] flex-1 bg-transparent px-1 py-0.5 text-[13px] outline-none placeholder:text-faint"
      />
    </div>
  )
}

function Help({ id, children }: { id?: string; children: ReactNode }) {
  return (
    <p id={id} className="text-[11.5px] leading-snug text-faint">
      {children}
    </p>
  )
}

export interface AssetSettingsProps {
  value: AssetFormValue
  onChange: (next: AssetFormValue) => void
  /** the art is a picture (PNG, JPG, WebP), which can have its plain background removed */
  picture?: boolean
  /** a problem with the name that only the server can know ("there is already an asset named ...") */
  nameError?: string | null
  /** shown right under the name (the "replace it?" question) */
  nameNotice?: ReactNode
  disabled?: boolean
  /** lets the caller focus the name field */
  nameRef?: RefObject<HTMLInputElement | null>
  /** Enter in the name or summary */
  onSubmit?: () => void
  /** "Auto" for the background cannot be chosen again once Yes or No was saved (the server keeps what it has) */
  lockAutoBackground?: boolean
}

/** Everything about an asset a person can set. Used by "Add asset" (for a file just read) and by the detail screen (to edit one). */
export function AssetSettings({ value, onChange, picture = false, nameError, nameNotice, disabled, nameRef, onSubmit, lockAutoBackground }: AssetSettingsProps) {
  const uid = useId()
  const own = useRef<HTMLInputElement>(null)
  const nameInput = nameRef ?? own
  const caret = useRef<number | null>(null)
  const [more, setMore] = useState(Boolean(value.credit))
  const place = value.kind === 'place'
  const problem = nameError ?? nameProblem(value.name)
  const set = (patch: Partial<AssetFormValue>) => onChange({ ...value, ...patch })
  const setPlace = (patch: Partial<AssetFormValue['place']>) => set({ place: { ...value.place, ...patch } })

  // typing in the middle of the name must not throw the cursor to the end when a letter is turned into another
  useLayoutEffect(() => {
    const el = nameInput.current
    if (caret.current !== null && el && document.activeElement === el) {
      const c = Math.min(caret.current, el.value.length)
      el.setSelectionRange(c, c)
    }
    caret.current = null
  })

  const setKind = (kind: AssetKind) => {
    if (kind === value.kind) return
    // a size nobody changed follows the kind (a character is taller than a cup)
    onChange({ ...value, kind, height: value.height === KIND_HEIGHT[value.kind] ? KIND_HEIGHT[kind] : value.height })
  }

  const anchorKey = anchorId(value.anchor) ?? 'custom'
  const cutoutKey = value.cutout === null ? 'auto' : value.cutout ? 'yes' : 'no'

  return (
    <fieldset disabled={disabled} className="m-0 flex min-w-0 flex-col gap-4 border-0 p-0">
      <legend className="sr-only">Asset settings</legend>

      <Field label="Kind" hint={undefined}>
        <Segmented<AssetKind>
          label="Kind"
          value={value.kind}
          onChange={setKind}
          options={KINDS.map((k) => ({ value: k, label: KIND_INFO[k].label, title: KIND_INFO[k].what }))}
          className="self-start"
        />
        <p className="text-[12px] leading-snug text-muted" aria-live="polite">
          {KIND_INFO[value.kind].what}
        </p>
      </Field>

      <Field label="Name" htmlFor={`${uid}-name`}>
        <Input
          id={`${uid}-name`}
          ref={nameInput}
          value={value.name}
          spellCheck={false}
          autoComplete="off"
          placeholder="for example fire_dragon"
          aria-invalid={problem ? true : undefined}
          aria-describedby={`${uid}-name-help`}
          className="font-mono"
          onChange={(e) => {
            const el = e.target
            const next = slugName(el.value)
            caret.current = slugName(el.value.slice(0, el.selectionStart ?? el.value.length)).length
            set({ name: next })
          }}
          onBlur={() => value.name !== trimName(value.name) && set({ name: trimName(value.name) })}
          onKeyDown={(e) => e.key === 'Enter' && !e.nativeEvent.isComposing && onSubmit?.()}
        />
        {problem ? (
          <p id={`${uid}-name-help`} role="alert" className="flex items-start gap-1.5 text-[11.5px] leading-snug text-danger">
            <CircleAlert className="mt-px size-3.5 shrink-0" aria-hidden />
            {problem}
          </p>
        ) : (
          <Help id={`${uid}-name-help`}>The id scripts use for it: lower-case letters, digits and underscores. Spaces and capitals are fixed as you type.</Help>
        )}
        {nameNotice}
      </Field>

      <Field label="Summary" htmlFor={`${uid}-summary`}>
        <Input
          id={`${uid}-summary`}
          dir="auto"
          value={value.summary}
          placeholder="A friendly orange cat, side view"
          aria-describedby={`${uid}-summary-help`}
          onChange={(e) => set({ summary: e.target.value })}
          onKeyDown={(e) => e.key === 'Enter' && !e.nativeEvent.isComposing && onSubmit?.()}
        />
        <Help id={`${uid}-summary-help`}>One line: what it is, not how it was drawn. The planners read it.</Help>
      </Field>

      <Field label="Words" htmlFor={`${uid}-words`}>
        <TagInput id={`${uid}-words`} value={value.tags} onChange={(tags) => set({ tags })} placeholder="dragon, wyvern, ड्रैगन" />
        <Help>
          Words scripts use for it: synonyms, plurals, Hindi in Devanagari, Hinglish. Press Enter or type a comma after each one; the planners match on these.
          {value.tags.length >= MAX_WORDS && ` That is the most it keeps (${MAX_WORDS}).`}
        </Help>
      </Field>

      {!place && (
        <Field label="Size" hint="How tall it is drawn, in design pixels at scale 1. A person is 575.">
          <SliderField label="Size in pixels" value={value.height} min={MIN_HEIGHT} max={MAX_HEIGHT} step={5} unit="px" onChange={(height) => set({ height })} />
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Size presets">
            {SIZE_PRESETS.map((p) => (
              <button
                key={p.label}
                type="button"
                aria-pressed={value.height === p.value}
                title={`Set the size to ${p.label}: ${p.value} px`}
                onClick={() => set({ height: p.value })}
                className={cn(
                  'inline-flex h-[22px] items-center gap-1 rounded-chip border px-2 text-[11.5px] font-medium transition-colors',
                  value.height === p.value ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:border-line-strong hover:text-fg',
                )}
              >
                {p.label}
                <span className="tabular text-faint">{p.value}</span>
              </button>
            ))}
          </div>
          <p className="tabular text-[12px] text-muted" aria-live="polite">
            = {sizeRatio(value.height)} × a person
          </p>
        </Field>
      )}

      {!place && (
        <Field label="Anchor" hint="The point of the picture that sits on the position a script gives it.">
          <Segmented<string>
            label="Anchor"
            value={anchorKey}
            onChange={(id) => {
              const a = ANCHORS.find((x) => x.id === id)
              if (a) set({ anchor: a.value })
            }}
            options={[...ANCHORS.map((a) => ({ value: a.id as string, label: a.label })), ...(anchorKey === 'custom' ? [{ value: 'custom', label: anchorText(value.anchor) }] : [])]}
            className="self-start"
          />
        </Field>
      )}

      {!place && (
        <Field label="Facing" hint="Which way the picture looks. The engine mirrors it when a scene needs it the other way; “Either” is for art that is the same both ways.">
          <Segmented<AssetInfo['facing']> label="Facing" value={value.facing} onChange={(facing) => set({ facing })} options={FACINGS} className="self-start" />
        </Field>
      )}

      {canCutOut(value.kind, picture) && (
        <Field label="Remove plain background" hint="Auto takes away a flat background (white, grey, any single colour) when the picture has no transparency of its own.">
          <Segmented<'auto' | 'yes' | 'no'>
            label="Remove plain background"
            value={cutoutKey}
            onChange={(k) => set({ cutout: k === 'auto' ? null : k === 'yes' })}
            options={[
              { value: 'auto', label: 'Auto', disabled: lockAutoBackground, title: lockAutoBackground ? 'Once a choice is saved it cannot go back to Auto: add the file again to reset it' : undefined },
              { value: 'yes', label: 'Yes' },
              { value: 'no', label: 'No' },
            ]}
            className="self-start"
          />
        </Field>
      )}

      {place && (
        <div role="group" aria-label="Where characters stand" className="flex flex-col gap-4 rounded-card border border-line p-3">
          <div className="eyebrow">Where characters stand</div>
          <Field label="Ground line" hint="Where their feet are, as a share of the frame height.">
            <SliderField label="Ground line" value={value.place.ground_y} min={0.5} max={0.95} step={0.01} defaultValue={0.8} onChange={(ground_y) => setPlace({ ground_y })} />
          </Field>
          <Field label="Horizon" hint="Where the sky meets the ground.">
            <SliderField label="Horizon" value={value.place.horizon} min={0.1} max={0.8} step={0.01} defaultValue={0.45} onChange={(horizon) => setPlace({ horizon })} />
          </Field>
          <Field label="Perspective" hint="How much characters grow towards the camera. 0 keeps them one size.">
            <SliderField label="Perspective" value={value.place.perspective} min={0} max={2} step={0.05} defaultValue={1} onChange={(perspective) => setPlace({ perspective })} />
          </Field>
        </div>
      )}

      <div>
        <button type="button" aria-expanded={more} aria-controls={`${uid}-more`} onClick={() => setMore(!more)} className="-ml-1 inline-flex items-center gap-1 rounded px-1 py-0.5 text-[12px] font-medium text-muted hover:text-fg">
          {more ? <ChevronDown className="size-3.5" aria-hidden /> : <ChevronRight className="size-3.5" aria-hidden />}
          More
        </button>
        {more && (
          <div id={`${uid}-more`} className="mt-2">
            <Field label="Credit" htmlFor={`${uid}-credit`} hint="Who made it, and the licence.">
              <Input id={`${uid}-credit`} dir="auto" value={value.credit} placeholder="drawn by me, CC0" onChange={(e) => set({ credit: e.target.value })} />
            </Field>
          </div>
        )}
      </div>
    </fieldset>
  )
}
