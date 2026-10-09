// A short name for what changed between two versions of a spec: the labels of the undo history ("Moved walk", "Edited caption").
// It compares the two specs structurally, so every route to an edit (timeline, inspector, keyboard, JSON) is named the same way.
import type { ActionClip, Caption, ReelSpec, Scene } from '@/api/types'

const same = (a: unknown, b: unknown): boolean => a === b || JSON.stringify(a) === JSON.stringify(b)
const trim = (s: string, n = 28): string => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s)
const sec = (v: number): string => `${Math.round(v * 100) / 100}`

/** The keys of two objects whose values differ. */
function changedKeys(a: object, b: object): string[] {
  const x = a as Record<string, unknown>
  const y = b as Record<string, unknown>
  return [...new Set([...Object.keys(x), ...Object.keys(y)])].filter((k) => !same(x[k], y[k]))
}

/** The first index at which two lists differ, or -1 when they are equal. */
function firstDiff<T>(a: T[], b: T[]): number {
  const n = Math.min(a.length, b.length)
  for (let i = 0; i < n; i++) if (!same(a[i], b[i])) return i
  return a.length === b.length ? -1 : n
}

function describeClip(a: { t0: number; t1: number }, b: { t0: number; t1: number }, what: string, rest: boolean): string | null {
  if (a.t0 === b.t0 && a.t1 === b.t1) return null
  if (rest) return `Edited ${what}`
  const lenA = Math.round((a.t1 - a.t0) * 1000)
  const lenB = Math.round((b.t1 - b.t0) * 1000)
  return lenA === lenB ? `Moved ${what}` : `Resized ${what}`
}

function describeAction(a: ActionClip, b: ActionClip): string {
  if (a.name !== b.name) return `Changed ${a.name} to ${b.name}`
  const other = changedKeys(a, b).some((k) => k !== 't0' && k !== 't1')
  return describeClip(a, b, b.name, other) ?? `Edited ${b.name}`
}

function describeCaption(a: Caption, b: Caption): string {
  const other = changedKeys(a, b).filter((k) => k !== 't0' && k !== 't1')
  if (other.includes('text')) return `Edited caption “${trim(b.text, 22)}”`
  return describeClip(a, b, 'a caption', other.length > 0) ?? 'Edited a caption'
}

function listChange<T>(a: T[], b: T[], noun: string, label: (item: T) => string, edited: (x: T, y: T) => string): string | null {
  if (same(a, b)) return null
  if (b.length > a.length) return `Added ${label(b[firstDiff(a, b)] ?? b[b.length - 1])}`
  if (b.length < a.length) return `Removed ${label(a[firstDiff(a, b)] ?? a[a.length - 1])}`
  const i = firstDiff(a, b)
  return i < 0 ? null : (edited(a[i], b[i]) ?? `Edited a ${noun}`)
}

function describeScene(a: Scene, b: Scene, n: number, spec: ReelSpec): string {
  const at = `Scene ${n + 1}`
  const keys = changedKeys(a, b)
  const only = (k: string) => keys.length === 1 && keys[0] === k
  if (only('duration_sec')) return `${at}: length ${sec(a.duration_sec)} → ${sec(b.duration_sec)} s`
  if (keys.includes('background') && keys.length === 1) return `${at}: ${a.background.template === b.background.template ? 'edited the background' : `background → ${b.background.template}`}`
  if (only('transition_out')) return `${at}: changed the transition`
  if (only('id')) return `Renamed ${a.id} to ${b.id}`
  if (only('notes')) return `${at}: edited the notes`
  if (only('captions')) return listChange(a.captions, b.captions, 'caption', (c) => `a caption “${trim(c.text, 22)}”`, describeCaption) ?? `${at}: edited captions`
  if (only('sfx')) return listChange(a.sfx, b.sfx, 'sound', (x) => `the ${x.name} sound`, (x, y) => (x.name !== y.name ? `Changed ${x.name} to ${y.name}` : x.t !== y.t ? 'Moved a sound' : 'Edited a sound')) ?? `${at}: edited sounds`
  if (only('camera')) return listChange(a.camera.moves, b.camera.moves, 'camera move', (m) => `a ${m.type.replace('_', ' ')} move`, (x, y) => describeClip(x, y, `the ${y.type.replace('_', ' ')} move`, changedKeys(x, y).some((k) => k !== 't0' && k !== 't1')) ?? `Edited the ${y.type.replace('_', ' ')} move`) ?? `${at}: edited the camera`
  if (only('layers')) {
    const who = (c: string) => spec.characters.find((x) => x.id === c)?.name || c
    const i = firstDiff(a.layers, b.layers)
    if (b.layers.length > a.layers.length) return `Added ${who(b.layers[i < 0 ? b.layers.length - 1 : i].character)} to ${at}`
    if (b.layers.length < a.layers.length) return `Removed ${who(a.layers[i < 0 ? a.layers.length - 1 : i].character)} from ${at}`
    if (i >= 0) {
      const la = a.layers[i]
      const lb = b.layers[i]
      const lk = changedKeys(la, lb)
      if (lk.length === 1 && lk[0] === 'actions') {
        return listChange(la.actions, lb.actions, 'action', (x) => `${x.name} for ${who(lb.character)}`, describeAction) ?? `Edited ${who(lb.character)}`
      }
      if (lk.length === 1 && lk[0] === 'position') return `Moved ${who(lb.character)}`
      if (lk.length === 1 && lk[0] === 'scale') return `Resized ${who(lb.character)}`
      if (lk.length === 1) return `Changed ${who(lb.character)}’s ${lk[0]}`
      return `Edited ${who(lb.character)}`
    }
  }
  return `${at}: ${keys.length === 1 ? `edited ${keys[0].replace('_', ' ')}` : 'several changes'}`
}

/** A short label for the edit that turned `before` into `after` (never empty). */
export function describeChange(before: ReelSpec, after: ReelSpec): string {
  if (same(before, after)) return 'No change'
  const top = changedKeys(before, after)
  if (top.length > 1) {
    // several parts changed (the title and a scene, say): name each on its own, the way a single change would be named
    if (top.length > 3) return `Changed ${top.length} parts of the reel`
    const each = top.map((k) => describeChange(before, { ...before, [k]: (after as unknown as Record<string, unknown>)[k] } as ReelSpec))
    return each.join('; ')
  }
  if (top.length === 1 && top[0] === 'meta') {
    const m = changedKeys(before.meta, after.meta)
    if (m.length === 1 && m[0] === 'title') return `Renamed the reel to “${trim(after.meta.title)}”`
    if (m.length === 1 && m[0] === 'style') return `Changed the style to ${after.meta.style.replace('_', ' ')}`
    if (m.length === 1 && m[0] === 'fx') return 'Changed the look (post effects)'
    if (m.length === 1 && m[0] === 'safe_area') return 'Changed the safe area'
    if (m.length === 1 && m[0] === 'seed') return `Changed the seed to ${after.meta.seed}`
    return `Changed ${m.map((k) => k.replace('_', ' ')).join(', ')}`
  }
  if (top.length === 1 && top[0] === 'audio') {
    const k = changedKeys(before.audio, after.audio)
    return k.length === 1 ? `Changed the ${k[0].replace(/_/g, ' ')}` : 'Changed the audio settings'
  }
  if (top.length === 1 && top[0] === 'characters') {
    const a = before.characters
    const b = after.characters
    if (b.length > a.length) return `Added ${b[b.length - 1].name || b[b.length - 1].id} to the cast`
    if (b.length < a.length) return `Removed ${(a.find((c) => !b.some((x) => x.id === c.id)) ?? a[a.length - 1]).name || 'a character'} from the cast`
    const i = firstDiff(a, b)
    if (i >= 0) {
      const keys = changedKeys(a[i], b[i])
      const name = b[i].name || b[i].id
      if (keys.length === 1 && keys[0] === 'palette') return `Recoloured ${name}`
      if (keys.length === 1) return `Changed ${name}’s ${keys[0]}`
      return `Edited ${name}`
    }
  }
  if (top.length === 1 && top[0] === 'scenes') {
    const a = before.scenes
    const b = after.scenes
    if (b.length > a.length) return `Added scene ${b[Math.max(0, firstDiff(a, b))]?.id ?? ''}`.trim()
    if (b.length < a.length) return `Removed scene ${a[Math.max(0, firstDiff(a, b))]?.id ?? ''}`.trim()
    const ids = (l: Scene[]) => l.map((s) => s.id).join('|')
    if (ids(a) !== ids(b) && [...a.map((s) => s.id)].sort().join('|') === [...b.map((s) => s.id)].sort().join('|')) return 'Reordered the scenes'
    const differing = a.map((s, i) => (same(s, b[i]) ? -1 : i)).filter((i) => i >= 0)
    if (differing.length === 1) return describeScene(a[differing[0]], b[differing[0]], differing[0], after)
    if (differing.every((i) => changedKeys(a[i], b[i]).every((k) => k === 'duration_sec' || k === 'captions' || k === 'layers' || k === 'camera' || k === 'sfx'))) return `Rescaled ${differing.length} scenes`
    return `Edited ${differing.length} scenes`
  }
  return 'Edited the reel'
}
