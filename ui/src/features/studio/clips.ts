// Several clips at once, and the clipboard: the edits behind Shift+click, Copy / Cut / Paste and dragging a selection.
// Like ops.ts these are plain functions on a spec draft, so the timeline, the keyboard, the menus and the palette all behave alike.
import type { ActionClip, CameraMove, Caption, ReelSpec, SfxEvent } from '@/api/types'
import { plainCopy } from '@/lib/clone'
import { newLayer, type Selection } from '@/lib/spec'
import { MIN_CLIP, clamp, ms } from '@/lib/timeline'
import { clipOf, deleteSelection, duplicateSelection, nudge, sceneAtPlayhead, setClipTimes } from './ops'

type Draft = ReelSpec

const plain = plainCopy

export type ClipSelection = Extract<Selection, { kind: 'action' | 'caption' | 'camera' | 'sfx' }>

export const isClipSelection = (s: Selection): s is ClipSelection => s.kind === 'action' || s.kind === 'caption' || s.kind === 'camera' || s.kind === 'sfx'

/** Every selected clip, the main one first (anything that is not a clip is left out). */
export function selectedClips(main: Selection, extra: Selection[]): ClipSelection[] {
  return [main, ...extra].filter(isClipSelection)
}

/** `[scene, layer, index]`: where a clip sits in its list (clips of the same kind and scene and layer share one list). */
function place(s: ClipSelection): [number, number, number] {
  switch (s.kind) {
    case 'action':
      return [s.scene, s.layer, s.action]
    case 'caption':
      return [s.scene, 0, s.caption]
    case 'camera':
      return [s.scene, 0, s.move]
    case 'sfx':
      return [s.scene, 0, s.sfx]
  }
}

const withIndex = (s: ClipSelection, i: number): ClipSelection => {
  switch (s.kind) {
    case 'action':
      return { ...s, action: i }
    case 'caption':
      return { ...s, caption: i }
    case 'camera':
      return { ...s, move: i }
    case 'sfx':
      return { ...s, sfx: i }
  }
}

const listKey = (s: ClipSelection): string => `${s.kind}:${place(s)[0]}:${place(s)[1]}`

const ascending = (a: ClipSelection, b: ClipSelection): number => {
  const [pa, pb] = [place(a), place(b)]
  return pa[0] - pb[0] || pa[1] - pb[1] || pa[2] - pb[2] || a.kind.localeCompare(b.kind)
}

/** Delete the clips together (the last ones first, so removing one never moves another still to go). */
export function deleteMany(spec: Draft, sels: ClipSelection[]): Selection {
  for (const s of [...sels].sort((a, b) => ascending(b, a))) deleteSelection(spec, s)
  return { kind: 'scene', scene: place(sels[0])[0] }
}

/** Duplicate every clip next to itself; returns the copies in the order of `sels` (the first is the copy of the first). */
export function duplicateMany(spec: Draft, sels: ClipSelection[]): ClipSelection[] {
  const made = new Map<string, number>()
  const out: ClipSelection[] = new Array<ClipSelection>(sels.length)
  for (const { s, i } of sels.map((s, i) => ({ s, i })).sort((a, b) => ascending(a.s, b.s))) {
    const k = made.get(listKey(s)) ?? 0 // copies already inserted before this one in its list
    out[i] = duplicateSelection(spec, withIndex(s, place(s)[2] + k)) as ClipSelection
    made.set(listKey(s), k + 1)
  }
  return out
}

export function nudgeMany(spec: Draft, sels: ClipSelection[], dt: number): void {
  for (const s of sels) nudge(spec, s, dt)
}

// ------------------------------------------------------------------------------- moving a selection as one
export interface ClipBase {
  sel: ClipSelection
  t0: number
  t1: number
}

/** Where each selected clip is now (read when a drag starts, so every step of the drag is measured from there). */
export function baseOf(spec: Draft, sels: ClipSelection[]): ClipBase[] {
  const out: ClipBase[] = []
  for (const sel of sels) {
    if (sel.kind === 'sfx') {
      const x = spec.scenes[sel.scene]?.sfx[sel.sfx]
      if (x) out.push({ sel, t0: x.t, t1: x.t })
      continue
    }
    const c = clipOf(spec, sel)
    if (c) out.push({ sel, t0: c.t0, t1: c.t1 })
  }
  return out
}

/** Move the clips by `delta` seconds from where `baseOf` found them; each stays inside its own scene. */
export function shiftClips(spec: Draft, base: ClipBase[], delta: number): void {
  for (const b of base) {
    const sc = spec.scenes[b.sel.scene]
    if (!sc) continue
    const len = b.t1 - b.t0
    const t0 = clamp(b.t0 + delta, 0, Math.max(0, sc.duration_sec - len))
    if (b.sel.kind === 'sfx') {
      const x = sc.sfx[b.sel.sfx]
      if (x) x.t = ms(t0)
    } else setClipTimes(spec, b.sel, t0, t0 + len)
  }
}

// ------------------------------------------------------------------------------- the clipboard
export type ClipData =
  | { kind: 'action'; t0: number; t1: number; character: string; clip: ActionClip }
  | { kind: 'caption'; t0: number; t1: number; clip: Caption }
  | { kind: 'camera'; t0: number; t1: number; clip: CameraMove }
  | { kind: 'sfx'; t0: number; t1: number; clip: SfxEvent }

export interface Clipboard {
  items: ClipData[]
}

/** Copies of the selected clips (deep, so later edits do not reach them), or null when none can be read. */
export function copyClips(spec: Draft, sels: ClipSelection[]): Clipboard | null {
  const items: ClipData[] = []
  for (const sel of sels) {
    const sc = spec.scenes[sel.scene]
    if (!sc) continue
    if (sel.kind === 'action') {
      const layer = sc.layers[sel.layer]
      const clip = layer?.actions[sel.action]
      if (layer && clip) items.push({ kind: 'action', t0: clip.t0, t1: clip.t1, character: layer.character, clip: plain(clip) })
    } else if (sel.kind === 'caption') {
      const clip = sc.captions[sel.caption]
      if (clip) items.push({ kind: 'caption', t0: clip.t0, t1: clip.t1, clip: plain(clip) })
    } else if (sel.kind === 'camera') {
      const clip = sc.camera.moves[sel.move]
      if (clip) items.push({ kind: 'camera', t0: clip.t0, t1: clip.t1, clip: plain(clip) })
    } else {
      const clip = sc.sfx[sel.sfx]
      if (clip) items.push({ kind: 'sfx', t0: clip.t, t1: clip.t, clip: plain(clip) })
    }
  }
  return items.length ? { items } : null
}

/** What kind of thing a paste would put where: a sentence for the menu ("2 clips"). */
export function describeClipboard(board: Clipboard | null): string {
  if (!board) return 'nothing copied yet'
  const n = board.items.length
  const kinds = new Set(board.items.map((i) => i.kind))
  const noun = kinds.size > 1 ? 'clip' : ({ action: 'action', caption: 'caption', camera: 'camera move', sfx: 'sound' } as const)[board.items[0].kind]
  return `${n} ${noun}${n === 1 ? '' : 's'}`
}

/** Whose lane a paste goes to: the selected character, or the character of the selected layer or action. */
export function pasteTarget(spec: Draft, sel: Selection): string | undefined {
  if (sel.kind === 'character') return sel.id
  if (sel.kind === 'layer' || sel.kind === 'action') return spec.scenes[sel.scene]?.layers[sel.layer]?.character
  return undefined
}

/**
 * Put the copies in the scene the playhead is in, starting at the playhead (the group keeps its spacing and is pulled back
 * to fit when it would run past the end of the scene). Actions go to `character`'s lane when given, else back to whose they were.
 * Returns what was pasted, or null when nothing could be (no scene at the playhead, or the character is gone).
 */
export function pasteClips(spec: Draft, board: Clipboard, playhead: number, character?: string): ClipSelection[] | null {
  const at = sceneAtPlayhead(spec, playhead)
  if (!at) return null
  const si = at.slot.index
  const sc = spec.scenes[si]
  const base = Math.min(...board.items.map((i) => i.t0))
  const span = Math.max(...board.items.map((i) => i.t1)) - base
  const start = clamp(at.local, 0, Math.max(0, sc.duration_sec - span))
  const shift = start - base
  const at2 = (t: number) => ms(clamp(t + shift, 0, sc.duration_sec))
  const put: { kind: ClipSelection['kind']; ref: object; layer?: number }[] = []
  for (const item of board.items) {
    if (item.kind === 'action') {
      const who = character ?? item.character
      if (!spec.characters.some((c) => c.id === who)) continue
      let li = sc.layers.findIndex((l) => l.character === who)
      if (li < 0) {
        sc.layers.push(newLayer(who))
        li = sc.layers.length - 1
      }
      const t0 = at2(item.t0)
      const clip: ActionClip = { ...plain(item.clip), t0, t1: ms(Math.max(at2(item.t1), t0 + MIN_CLIP)) }
      sc.layers[li].actions.push(clip)
      sc.layers[li].actions.sort((a, b) => a.t0 - b.t0)
      put.push({ kind: 'action', ref: clip, layer: li })
    } else if (item.kind === 'caption') {
      const t0 = at2(item.t0)
      const clip: Caption = { ...plain(item.clip), t0, t1: ms(Math.max(at2(item.t1), t0 + MIN_CLIP)) }
      if (character && item.clip.speaker) clip.speaker = character
      sc.captions.push(clip)
      sc.captions.sort((a, b) => a.t0 - b.t0)
      put.push({ kind: 'caption', ref: clip })
    } else if (item.kind === 'camera') {
      const t0 = at2(item.t0)
      const clip: CameraMove = { ...plain(item.clip), t0, t1: ms(Math.max(at2(item.t1), t0 + MIN_CLIP)) }
      sc.camera.moves.push(clip)
      sc.camera.moves.sort((a, b) => a.t0 - b.t0)
      put.push({ kind: 'camera', ref: clip })
    } else {
      const clip: SfxEvent = { ...plain(item.clip), t: at2(item.t0) }
      sc.sfx.push(clip)
      sc.sfx.sort((a, b) => a.t - b.t)
      put.push({ kind: 'sfx', ref: clip })
    }
  }
  // indices are read at the end, after every insert has settled into its sorted place
  const out: ClipSelection[] = []
  for (const p of put) {
    if (p.kind === 'action') out.push({ kind: 'action', scene: si, layer: p.layer as number, action: sc.layers[p.layer as number].actions.indexOf(p.ref as ActionClip) })
    else if (p.kind === 'caption') out.push({ kind: 'caption', scene: si, caption: sc.captions.indexOf(p.ref as Caption) })
    else if (p.kind === 'camera') out.push({ kind: 'camera', scene: si, move: sc.camera.moves.indexOf(p.ref as CameraMove) })
    else out.push({ kind: 'sfx', scene: si, sfx: sc.sfx.indexOf(p.ref as SfxEvent) })
  }
  return out.length ? out : null
}
