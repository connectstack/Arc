// Edits to a spec as plain functions: each takes the current spec (an immer draft) and returns the selection to show afterwards.
// The timeline, the inspector, the keyboard and the command palette all go through these, so every route to an edit behaves alike.
import type { ActionClip, Caption, CameraMove, Catalog, Layer, ReelSpec, Scene, SfxEvent } from '@/api/types'
import { plainCopy } from '@/lib/clone'
import { newAction, newCaption, newCharacter, newLayer, newScene, uniqueId, type Selection } from '@/lib/spec'
import { MIN_CLIP, clamp, ms, sceneAt, sceneSlots, type Slot } from '@/lib/timeline'

type Draft = ReelSpec

export interface ClipTimes {
  t0: number
  t1: number
}

/** The `{t0, t1}` object a selection points at (a live reference into the draft), or null for things without a time span. */
export function clipOf(spec: Draft, sel: Selection): ClipTimes | null {
  switch (sel.kind) {
    case 'action':
      return spec.scenes[sel.scene]?.layers[sel.layer]?.actions[sel.action] ?? null
    case 'caption':
      return spec.scenes[sel.scene]?.captions[sel.caption] ?? null
    case 'camera':
      return spec.scenes[sel.scene]?.camera.moves[sel.move] ?? null
    default:
      return null
  }
}

export function setClipTimes(spec: Draft, sel: Selection, t0: number, t1: number): void {
  const clip = clipOf(spec, sel)
  if (!clip) return
  clip.t0 = ms(t0)
  clip.t1 = ms(Math.max(t1, t0 + MIN_CLIP))
}

/** Which scene the playhead is in, and the local time there. */
export function sceneAtPlayhead(spec: Draft, playhead: number): { slot: Slot; local: number } | null {
  const slots = sceneSlots(spec)
  const slot = sceneAt(slots, playhead)
  return slot ? { slot, local: clamp(playhead - slot.start, 0, slot.duration) } : null
}

/** Like `sceneAtPlayhead`, but for a chosen scene: the playhead's time in it when it is inside, else the scene's start. */
function localIn(spec: Draft, scene: number, playhead: number): { slot: Slot; local: number } | null {
  const slot = sceneSlots(spec)[scene]
  if (!slot) return null
  return { slot, local: playhead >= slot.start && playhead < slot.end ? playhead - slot.start : 0 }
}

export function deleteSelection(spec: Draft, sel: Selection): Selection {
  switch (sel.kind) {
    case 'action': {
      spec.scenes[sel.scene]?.layers[sel.layer]?.actions.splice(sel.action, 1)
      return { kind: 'layer', scene: sel.scene, layer: sel.layer }
    }
    case 'caption':
      spec.scenes[sel.scene]?.captions.splice(sel.caption, 1)
      return { kind: 'scene', scene: sel.scene }
    case 'camera':
      spec.scenes[sel.scene]?.camera.moves.splice(sel.move, 1)
      return { kind: 'scene', scene: sel.scene }
    case 'sfx':
      spec.scenes[sel.scene]?.sfx.splice(sel.sfx, 1)
      return { kind: 'scene', scene: sel.scene }
    case 'layer':
      spec.scenes[sel.scene]?.layers.splice(sel.layer, 1)
      return { kind: 'scene', scene: sel.scene }
    case 'transition': {
      const sc = spec.scenes[sel.scene]
      if (sc) sc.transition_out = { type: 'cut', duration: 0, params: {} }
      return { kind: 'scene', scene: sel.scene }
    }
    case 'scene': {
      if (spec.scenes.length <= 1) return sel
      spec.scenes.splice(sel.scene, 1)
      return { kind: 'scene', scene: Math.max(0, sel.scene - 1) }
    }
    case 'character': {
      spec.characters = spec.characters.filter((c) => c.id !== sel.id)
      for (const sc of spec.scenes) {
        sc.layers = sc.layers.filter((l) => l.character !== sel.id)
        for (const c of sc.captions) if (c.speaker === sel.id) c.speaker = null
      }
      return { kind: 'reel' }
    }
    default:
      return sel
  }
}

const copy = plainCopy

export function duplicateSelection(spec: Draft, sel: Selection): Selection {
  switch (sel.kind) {
    case 'action': {
      const sc = spec.scenes[sel.scene]
      const list = sc?.layers[sel.layer]?.actions
      const a = list?.[sel.action]
      if (!sc || !list || !a) return sel
      const len = a.t1 - a.t0
      const t0 = Math.min(a.t1, Math.max(0, sc.duration_sec - len))
      list.splice(sel.action + 1, 0, { ...copy(a), t0: ms(t0), t1: ms(Math.min(sc.duration_sec, t0 + len)) })
      return { ...sel, action: sel.action + 1 }
    }
    case 'caption': {
      const sc = spec.scenes[sel.scene]
      const c = sc?.captions[sel.caption]
      if (!sc || !c) return sel
      const len = c.t1 - c.t0
      const t0 = Math.min(c.t1, Math.max(0, sc.duration_sec - len))
      sc.captions.splice(sel.caption + 1, 0, { ...copy(c), t0: ms(t0), t1: ms(Math.min(sc.duration_sec, t0 + len)) })
      return { ...sel, caption: sel.caption + 1 }
    }
    case 'camera': {
      const sc = spec.scenes[sel.scene]
      const m = sc?.camera.moves[sel.move]
      if (!sc || !m) return sel
      sc.camera.moves.splice(sel.move + 1, 0, copy(m))
      return { ...sel, move: sel.move + 1 }
    }
    case 'sfx': {
      const sc = spec.scenes[sel.scene]
      const x = sc?.sfx[sel.sfx]
      if (!sc || !x) return sel
      sc.sfx.splice(sel.sfx + 1, 0, { ...copy(x), t: ms(Math.min(sc.duration_sec, x.t + 0.4)) })
      return { ...sel, sfx: sel.sfx + 1 }
    }
    case 'layer': {
      const sc = spec.scenes[sel.scene]
      const l = sc?.layers[sel.layer]
      if (!sc || !l) return sel
      sc.layers.splice(sel.layer + 1, 0, copy(l))
      return { ...sel, layer: sel.layer + 1 }
    }
    case 'scene': {
      const sc = spec.scenes[sel.scene]
      if (!sc) return sel
      const dup = copy(sc)
      dup.id = uniqueId(
        spec.scenes.map((s) => s.id),
        `${sc.id}_copy`,
      )
      spec.scenes.splice(sel.scene + 1, 0, dup)
      return { kind: 'scene', scene: sel.scene + 1 }
    }
    default:
      return sel
  }
}

/** Cut the selected action or caption in two at the playhead (does nothing if the playhead is not inside it). */
export function splitAtPlayhead(spec: Draft, sel: Selection, playhead: number): Selection {
  const at = sceneAtPlayhead(spec, playhead)
  if (!at || (sel.kind !== 'action' && sel.kind !== 'caption')) return sel
  if (sel.scene !== at.slot.index) return sel
  const t = ms(at.local)
  if (sel.kind === 'action') {
    const list = spec.scenes[sel.scene]?.layers[sel.layer]?.actions
    const a = list?.[sel.action]
    if (!list || !a || t <= a.t0 + MIN_CLIP || t >= a.t1 - MIN_CLIP) return sel
    const tail: ActionClip = { ...copy(a), t0: t }
    a.t1 = t
    list.splice(sel.action + 1, 0, tail)
    return { ...sel, action: sel.action + 1 }
  }
  const caps = spec.scenes[sel.scene]?.captions
  const c = caps?.[sel.caption]
  if (!caps || !c || t <= c.t0 + MIN_CLIP || t >= c.t1 - MIN_CLIP) return sel
  const words = c.text.split(/\s+/)
  const cut = Math.max(1, Math.round((words.length * (t - c.t0)) / (c.t1 - c.t0)))
  const tail: Caption = { ...copy(c), t0: t, text: words.slice(cut).join(' ') || c.text }
  c.t1 = t
  c.text = words.slice(0, cut).join(' ') || c.text
  caps.splice(sel.caption + 1, 0, tail)
  return { ...sel, caption: sel.caption + 1 }
}

export function addAction(spec: Draft, catalog: Catalog | undefined, characterId: string, name: string, playhead: number): Selection | null {
  const at = sceneAtPlayhead(spec, playhead)
  if (!at) return null
  const sc = spec.scenes[at.slot.index]
  let li = sc.layers.findIndex((l) => l.character === characterId)
  if (li < 0) {
    sc.layers.push(newLayer(characterId))
    li = sc.layers.length - 1
  }
  const clip = newAction(catalog, name, at.local, sc.duration_sec)
  const layer: Layer = sc.layers[li]
  layer.actions.push(clip)
  layer.actions.sort((a, b) => a.t0 - b.t0)
  return { kind: 'action', scene: at.slot.index, layer: li, action: layer.actions.indexOf(clip) }
}

export function addCaption(spec: Draft, playhead: number, speaker?: string): Selection | null {
  const at = sceneAtPlayhead(spec, playhead)
  if (!at) return null
  const sc = spec.scenes[at.slot.index]
  const cap = newCaption(at.local, sc.duration_sec, 'New caption', speaker)
  sc.captions.push(cap)
  sc.captions.sort((a, b) => a.t0 - b.t0)
  return { kind: 'caption', scene: at.slot.index, caption: sc.captions.indexOf(cap) }
}

/** Add a camera move at the playhead, or (with `scene`) to that scene: where the playhead is inside it, else from its start. */
export function addCameraMove(spec: Draft, type: string, playhead: number, scene?: number): Selection | null {
  const at = scene === undefined ? sceneAtPlayhead(spec, playhead) : localIn(spec, scene, playhead)
  if (!at) return null
  const sc = spec.scenes[at.slot.index]
  const start = ms(Math.min(at.local, Math.max(0, sc.duration_sec - 1)))
  const defaults: Record<string, [CameraMove['from'], CameraMove['to']]> = {
    pan: [[0, 0], [0.08, 0]],
    zoom: [1, 1.2],
    dolly: [0, 0.4],
    shake: [0, 0.4],
    rack_focus: ['background', 'midground'],
  }
  const [from, to] = defaults[type] ?? [0, 1]
  const move: CameraMove = { type, from, to, t0: start, t1: ms(Math.min(sc.duration_sec, start + Math.min(2, sc.duration_sec))), ease: 'ease_in_out', params: {} }
  sc.camera.moves.push(move)
  sc.camera.moves.sort((a, b) => a.t0 - b.t0)
  return { kind: 'camera', scene: at.slot.index, move: sc.camera.moves.indexOf(move) }
}

export function addSfx(spec: Draft, name: string, playhead: number): Selection | null {
  const at = sceneAtPlayhead(spec, playhead)
  if (!at) return null
  const sc = spec.scenes[at.slot.index]
  const x: SfxEvent = { name, t: ms(at.local), volume: 1 }
  sc.sfx.push(x)
  sc.sfx.sort((a, b) => a.t - b.t)
  return { kind: 'sfx', scene: at.slot.index, sfx: sc.sfx.indexOf(x) }
}

export function addScene(spec: Draft, afterIndex?: number, template = 'abstract'): Selection {
  const sc: Scene = newScene(spec, template)
  const at = afterIndex === undefined ? spec.scenes.length : afterIndex + 1
  spec.scenes.splice(at, 0, sc)
  return { kind: 'scene', scene: at }
}

export function addCharacter(spec: Draft, archetype: string): Selection {
  const c = newCharacter(spec, archetype)
  spec.characters.push(c)
  return { kind: 'character', id: c.id }
}

export function moveScene(spec: Draft, from: number, to: number): void {
  if (from === to || from < 0 || to < 0 || from >= spec.scenes.length || to >= spec.scenes.length) return
  const [sc] = spec.scenes.splice(from, 1)
  spec.scenes.splice(to, 0, sc)
}

/** Rename a character id everywhere it is referenced. */
export function renameCharacterId(spec: Draft, oldId: string, nextId: string): void {
  if (!nextId || oldId === nextId || spec.characters.some((c) => c.id === nextId)) return
  const ch = spec.characters.find((c) => c.id === oldId)
  if (!ch) return
  ch.id = nextId
  for (const sc of spec.scenes) {
    for (const l of sc.layers) if (l.character === oldId) l.character = nextId
    for (const c of sc.captions) if (c.speaker === oldId) c.speaker = nextId
  }
}

/** Nudge a selected clip by `dt` seconds, staying inside its scene. */
export function nudge(spec: Draft, sel: Selection, dt: number): void {
  const sc = 'scene' in sel ? spec.scenes[sel.scene] : undefined
  if (!sc) return
  if (sel.kind === 'sfx') {
    const x = sc.sfx[sel.sfx]
    if (x) x.t = ms(clamp(x.t + dt, 0, sc.duration_sec))
    return
  }
  const clip = clipOf(spec, sel)
  if (!clip) return
  const len = clip.t1 - clip.t0
  const t0 = clamp(clip.t0 + dt, 0, Math.max(0, sc.duration_sec - len))
  clip.t0 = ms(t0)
  clip.t1 = ms(t0 + len)
}
