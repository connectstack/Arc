// What the asset screens share: the settings of an asset as a form edits them, the words they are made of, and how the form
// becomes the fields the API takes. No React in here, so the rules (names, words, sizes) can be tested on their own.
import type { AssetDraft, AssetFields, AssetInfo, AssetKind, DraftLook, PlaceInfo, Vec2 } from '@/api/types'
import { ApiError } from '@/api/types'

/** A standing person is this tall in design px; a library thing is sized against it. */
export const PERSON_HEIGHT = 575
/** How tall a thing of each kind is drawn when nothing else is said (a place fills the 1920 px frame). */
export const KIND_HEIGHT: Record<AssetKind, number> = { character: 420, object: 300, place: 1920 }
export const MIN_HEIGHT = 40
export const MAX_HEIGHT = 1500
/** The most words an asset keeps (the server drops the rest). */
export const MAX_WORDS = 40
export const NAME_RE = /^[a-z][a-z0-9_]{0,39}$/

export const KINDS: AssetKind[] = ['object', 'character', 'place']

export const KIND_INFO: Record<AssetKind, { label: string; plural: string; what: string; folder: string; use: string }> = {
  object: { label: 'Object', plural: 'objects', what: 'A thing placed in a scene: a car, a tree, a cake.', folder: 'objects', use: 'scenes[].objects[]' },
  character: { label: 'Character', plural: 'characters', what: 'A creature or person that acts, drawn as one picture: a cat, a dragon.', folder: 'characters', use: 'characters[].archetype' },
  place: { label: 'Place', plural: 'places', what: 'A whole background: a beach, a village, a classroom.', folder: 'places', use: 'scenes[].background.template' },
}

/** Sizes to start from, in design px at scale 1 (think "metres x 340"). */
export const SIZE_PRESETS: { label: string; value: number }[] = [
  { label: 'hand-held', value: 150 },
  { label: 'chair', value: 330 },
  { label: 'person', value: PERSON_HEIGHT },
  { label: 'house', value: 720 },
  { label: 'tree', value: 820 },
]

/** The point of the art that sits on the position a spec gives it, as fractions of the art's box. */
export const ANCHORS: { id: 'ground' | 'centre' | 'feet'; label: string; value: Vec2 }[] = [
  { id: 'ground', label: 'Stands on the ground', value: [0.5, 1] },
  { id: 'centre', label: 'Centre (floats)', value: [0.5, 0.5] },
  { id: 'feet', label: 'Animal feet', value: [0.5, 0.94] },
]

export const FACINGS: { value: AssetInfo['facing']; label: string }[] = [
  { value: 'right', label: 'Right' },
  { value: 'left', label: 'Left' },
  { value: 'none', label: 'Either' },
]

export const TIMES_OF_DAY = ['dawn', 'day', 'dusk', 'night'] as const
export type TimeOfDay = (typeof TIMES_OF_DAY)[number]

/** Where a place's characters stand when nothing else is said (the server's defaults). */
export const DEFAULT_PLACE: PlaceInfo = { ground_y: 0.8, horizon: 0.45, perspective: 1, slots: {} }

/** The settings of one asset as the form holds them. Everything has a value, so every control is controlled. */
export interface AssetFormValue {
  name: string
  kind: AssetKind
  summary: string
  tags: string[]
  /** design px at scale 1; not used for places */
  height: number
  anchor: Vec2
  facing: AssetInfo['facing']
  /** pictures only: null leaves it to the engine (a plain background goes when there is no transparency) */
  cutout: boolean | null
  credit: string
  place: PlaceInfo
}

// ------------------------------------------------------------------------------- names
/**
 * What a name field turns what you type into: lower-case letters, digits and underscores, starting with a letter. Underscores
 * at the end stay while you type (a space between two words is on its way to becoming one); `trimName` removes them.
 */
export function slugName(text: string): string {
  let s = text
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '') // é -> e
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+/, '')
  if (s && !/^[a-z]/.test(s)) s = `a_${s}`
  return s.slice(0, 40)
}

export const trimName = (name: string): string => name.replace(/_+$/, '')

/** Why this name cannot be used, or null. */
export function nameProblem(name: string): string | null {
  if (!name) return 'Give it a name: lower-case letters, digits and underscores, starting with a letter.'
  if (!NAME_RE.test(name)) return 'Use lower-case letters, digits and underscores, starting with a letter (40 characters at most).'
  return null
}

// ------------------------------------------------------------------------------- words
/** Commas, semicolons and line breaks (also the Arabic, ideographic and full-width commas) end a word. */
export const WORD_SEPARATORS = /[,;\n\r،、，]+/

/** A word as the server keeps it: trimmed, one space between its parts, lower case (scripts without case are left as they are). */
export const normalizeWord = (w: string): string => w.trim().replace(/\s+/g, ' ').toLowerCase()

export const splitWords = (text: string): string[] => text.split(WORD_SEPARATORS).map(normalizeWord).filter(Boolean)

/** `existing` with the new words after it: no duplicates, at most `MAX_WORDS`. Returns the same array when nothing was added. */
export function addWords(existing: string[], incoming: string[]): string[] {
  const out = [...existing]
  for (const raw of incoming) {
    const w = normalizeWord(raw)
    if (w && !out.includes(w) && out.length < MAX_WORDS) out.push(w)
  }
  return out.length === existing.length ? existing : out
}

/** The words that are not just the asset's own name (the card shows the name already). */
export const extraWords = (a: Pick<AssetInfo, 'name' | 'tags'>): string[] => a.tags.filter((t) => t !== a.name.replace(/_/g, ' ') && t !== a.name)

// ------------------------------------------------------------------------------- sizes
export const sizeRatio = (height: number): number => Math.round((height / PERSON_HEIGHT) * 100) / 100

/** "0.52 × a person" (a place fills the whole frame instead). */
export const sizeText = (a: Pick<AssetInfo, 'kind' | 'height'>): string => (a.kind === 'place' ? 'the whole frame' : `${sizeRatio(a.height)} × a person`)

export function anchorId(anchor: Vec2): (typeof ANCHORS)[number]['id'] | null {
  return ANCHORS.find((a) => Math.abs(a.value[0] - anchor[0]) < 0.005 && Math.abs(a.value[1] - anchor[1]) < 0.005)?.id ?? null
}

export const anchorText = (anchor: Vec2): string => ANCHORS.find((a) => a.id === anchorId(anchor))?.label ?? `Custom (${anchor[0]}, ${anchor[1]})`

// ------------------------------------------------------------------------------- form <-> API
export interface AssetPrefill {
  name?: string
  kind?: AssetKind
  summary?: string
  tags?: string[]
}

/** The form for a file just read: what the server suggests, with what the caller already knows laid over it. */
export function formFromDraft(d: AssetDraft, prefill: AssetPrefill = {}): AssetFormValue {
  const s = d.suggested
  const kind = prefill.kind ?? s.kind
  return {
    name: (prefill.name ? slugName(prefill.name) : '') || s.name,
    kind,
    summary: prefill.summary ?? s.summary,
    tags: addWords([], [...(prefill.tags ?? []), ...s.tags]),
    height: kind === s.kind ? s.height : KIND_HEIGHT[kind],
    anchor: s.anchor,
    facing: s.facing,
    cutout: null,
    credit: '',
    place: { ...DEFAULT_PLACE, slots: {} },
  }
}

export function formFromAsset(a: AssetInfo): AssetFormValue {
  return {
    name: a.name,
    kind: a.kind,
    summary: a.summary,
    tags: [...a.tags],
    height: a.height,
    anchor: a.anchor,
    facing: a.facing,
    cutout: a.cutout ?? null,
    credit: a.credit,
    place: a.place ? { ...a.place, slots: { ...a.place.slots } } : { ...DEFAULT_PLACE, slots: {} },
  }
}

/** Does the picture take part in background removal? A place's picture is a backdrop: nothing is cut out of it. */
export const canCutOut = (kind: AssetKind, picture: boolean): boolean => picture && kind !== 'place'

/** The API's fields for a form. A place has no size or anchor of its own (it is the whole frame). */
export function fieldsFromForm(v: AssetFormValue, picture: boolean): AssetFields {
  const place = v.kind === 'place'
  return {
    name: v.name,
    kind: v.kind,
    summary: v.summary.trim(),
    tags: v.tags,
    height: place ? KIND_HEIGHT.place : v.height,
    anchor: place ? [0.5, 1] : v.anchor,
    facing: v.facing,
    ...(canCutOut(v.kind, picture) ? { cutout: v.cutout } : {}),
    credit: v.credit.trim(),
    ...(place ? { place: v.place } : {}),
  }
}

/** Only the fields that differ between two versions of a form (what `updateAsset` is given). */
export function changesFromForm(before: AssetFormValue, after: AssetFormValue, picture: boolean): Partial<AssetFields> {
  const a = fieldsFromForm(before, picture)
  const b = fieldsFromForm(after, picture)
  const out: Record<string, unknown> = {}
  for (const key of Object.keys(b) as (keyof AssetFields)[]) {
    if (JSON.stringify(a[key]) !== JSON.stringify(b[key])) out[key] = b[key]
  }
  return out as Partial<AssetFields>
}

/**
 * How an unsaved upload is drawn for the settings so far (everything but the style). A place is a backdrop: its size, anchor and
 * facing do not matter, nor does standing next to a person. A picture's plain background only takes part when the kind can have one cut out.
 */
export function draftLook(v: AssetFormValue, picture: boolean, trueScale: boolean, timeOfDay: TimeOfDay): Omit<DraftLook, 'style'> {
  if (v.kind === 'place') return { kind: 'place', trueScale: false, timeOfDay }
  return { kind: v.kind, trueScale, timeOfDay, height: v.height, anchor: v.anchor, facing: v.facing, ...(canCutOut(v.kind, picture) ? { cutout: v.cutout } : {}) }
}

// ------------------------------------------------------------------------------- errors, specs
/** What went wrong in words for a person: the server's own message and hint when it sent them. */
export function describeError(e: unknown): { detail: string; hint?: string } {
  if (e instanceof ApiError) return { detail: e.detail, hint: e.hint }
  if (e instanceof Error) return { detail: e.message }
  return { detail: String(e) }
}

/** A line of a spec that uses the asset (for the detail screen). */
export function specSnippet(a: Pick<AssetInfo, 'name' | 'kind' | 'roles'>): { where: string; json: string } {
  const role = a.roles?.[0]
  const palette = role ? `, "palette": {"${role}": "#2a6fdb"}` : ''
  switch (a.kind) {
    case 'object':
      return { where: KIND_INFO.object.use, json: `{"asset": "${a.name}", "position": [0.5, 0.8]${palette}}` }
    case 'character':
      return { where: KIND_INFO.character.use, json: `{"id": "pal", "archetype": "${a.name}"${palette}}` }
    case 'place':
      return { where: KIND_INFO.place.use, json: `{"template": "${a.name}"}` }
  }
}
