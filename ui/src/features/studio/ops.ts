// Edits to a spec as plain functions: each takes the current spec (an immer draft) and returns the selection to show afterwards.
// The timeline, the inspector, the keyboard and the command palette all go through these, so every route to an edit behaves alike.
import type { ActionClip, Caption, CameraMove, Catalog, Layer, ReelSpec, Scene, SfxEvent } from '@/api/types'
import { plainCopy } from '@/lib/clone'
import { CHAR_UNIT, UNIVERSAL_SLOTS, entry, layerPoint, newAction, newCaption, newCharacter, newLayer, newObject, newScene, objectEntry, objectPoint, objectSizeFrac, uniqueId, type Selection } from '@/lib/spec'
import { MIN_CLIP, clamp, ms, sceneAt, sceneSlots, type Slot } from '@/lib/timeline'
import { newMotion } from './motions'

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
    case 'motion':
      return spec.scenes[sel.scene]?.objects?.[sel.object]?.motions?.[sel.motion] ?? null
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
    case 'object':
      spec.scenes[sel.scene]?.objects.splice(sel.object, 1)
      return { kind: 'scene', scene: sel.scene }
    case 'motion':
      spec.scenes[sel.scene]?.objects[sel.object]?.motions.splice(sel.motion, 1)
      return { kind: 'object', scene: sel.scene, object: sel.object }
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
    case 'object': {
      const sc = spec.scenes[sel.scene]
      const o = sc?.objects[sel.object]
      if (!sc || !o) return sel
      const copyOf = copy(o)
      if (Array.isArray(copyOf.position)) copyOf.position = [ms(Math.min(1.1, copyOf.position[0] + 0.08)), copyOf.position[1]]
      sc.objects.splice(sel.object + 1, 0, copyOf)
      return { ...sel, object: sel.object + 1 }
    }
    case 'motion': {
      const sc = spec.scenes[sel.scene]
      const list = sc?.objects[sel.object]?.motions
      const m = list?.[sel.motion]
      if (!sc || !list || !m) return sel
      const len = m.t1 - m.t0
      const t0 = Math.min(m.t1, Math.max(0, sc.duration_sec - len))
      list.splice(sel.motion + 1, 0, { ...copy(m), t0: ms(t0), t1: ms(Math.min(sc.duration_sec, t0 + len)) })
      return { ...sel, motion: sel.motion + 1 }
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

/** How big an object of `asset` is made when it is added: its width at most 42% of the frame and its height at most half of it. */
function fitScale(catalog: Catalog | undefined, asset: string): number {
  const row = objectEntry(catalog, asset)
  const h = (row?.height ?? 300) * CHAR_UNIT
  const w = h * (row?.aspect ?? 1)
  return Math.round(Math.max(0.1, Math.min(1, (0.42 * 1080) / w, (0.5 * 1920) / h)) * 1000) / 1000
}

/**
 * Where an object of `asset` goes in a scene by default: standing on the set's ground line (a thing that floats rests its bottom edge
 * there), at a size that fits the frame, in the spot (left, right, middle ...) that is farthest from everyone already standing there,
 * and never hanging out of the frame.
 */
function objectPlacement(sc: Scene, catalog: Catalog | undefined, asset: string): { position: [number, number]; scale: number } {
  const scale = fitScale(catalog, asset)
  const ground = entry(catalog?.backgrounds, sc.background.template)?.ground_y ?? 0.8
  const { w, h } = objectSizeFrac(catalog, sc, newObject(asset, [0.5, ground], scale), ground)
  const [ax, ay] = objectEntry(catalog, asset)?.anchor ?? [0.5, 1]
  // a thing that floats (its anchor is high in the picture) is lifted so that its bottom edge, not its middle, is on the ground line
  const y = ay < 0.75 ? Math.max(0.05, ms(ground - (1 - ay) * h)) : ground
  const lo = ax * w + 0.02
  const hi = 1 - (1 - ax) * w - 0.02
  const fit = (x: number) => (lo > hi ? 0.5 : ms(clamp(x, lo, hi)))
  const taken = [...sc.layers.map((l) => layerPoint(sc, l, catalog)[0]), ...sc.objects.map((o) => objectPoint(sc, o, catalog)[0])]
  const gap = (x: number) => Math.min(1, ...taken.map((t) => Math.abs(x - t)))
  const spots = [0.14, 0.86, 0.5, 0.3, 0.7].map(fit)
  const x = spots.reduce((best, s) => (gap(s) > gap(best) ? s : best), spots[0])
  return { position: [x, y], scale }
}

/**
 * Put a library object in a scene (the playhead's, or `scene`). It lands in a clear spot, unless `at` says where (screen
 * fractions of the point it stands on: a drop on the stage), and is sized to fit the frame.
 */
export function addObject(spec: Draft, catalog: Catalog | undefined, asset: string, playhead: number, scene?: number, at?: [number, number]): Selection | null {
  const where = scene === undefined ? sceneAtPlayhead(spec, playhead) : localIn(spec, scene, playhead)
  if (!where) return null
  const sc = spec.scenes[where.slot.index]
  const placed = objectPlacement(sc, catalog, asset)
  const position: [number, number] = at ? [ms(clamp(at[0], -0.1, 1.1)), ms(clamp(at[1], 0, 1))] : placed.position
  sc.objects.push(newObject(asset, position, placed.scale))
  return { kind: 'object', scene: where.slot.index, object: sc.objects.length - 1 }
}

/** Give an object another library asset: it stays where it is, as big and as long as it was; colours the new drawing has no part for are dropped. */
export function changeObjectAsset(spec: Draft, catalog: Catalog | undefined, scene: number, object: number, asset: string): void {
  const o = spec.scenes[scene]?.objects[object]
  if (!o || o.asset === asset) return
  o.asset = asset
  const roles = objectEntry(catalog, asset)?.roles
  if (roles) for (const role of Object.keys(o.palette)) if (!roles.includes(role)) delete o.palette[role]
}

/** Add a motion of `type` to an object at the playhead (where it is inside the object's scene, else from the scene's start). */
export function addMotion(spec: Draft, catalog: Catalog | undefined, scene: number, object: number, type: string, playhead: number): Selection | null {
  const sc = spec.scenes[scene]
  const o = sc?.objects[object]
  const where = localIn(spec, scene, playhead)
  if (!sc || !o || !where) return null
  const m = newMotion(type, where.local, sc.duration_sec, objectPoint(sc, o, catalog))
  o.motions.push(m)
  o.motions.sort((a, b) => a.t0 - b.t0)
  return { kind: 'motion', scene, object, motion: o.motions.indexOf(m) }
}

/** Set when an object is on screen: from `t0` until `t1` (null: the end of the scene). */
export function setObjectSpan(spec: Draft, scene: number, object: number, t0: number, t1: number | null): void {
  const o = spec.scenes[scene]?.objects[object]
  if (!o) return
  o.t0 = ms(Math.max(0, t0))
  o.t1 = t1 === null ? null : ms(Math.max(t1, o.t0 + MIN_CLIP))
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

/** Add a sound at the playhead, or (with `scene`) to that scene: where the playhead is inside it, else at its start. */
export function addSfx(spec: Draft, name: string, playhead: number, scene?: number): Selection | null {
  const at = scene === undefined ? sceneAtPlayhead(spec, playhead) : localIn(spec, scene, playhead)
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

// ------------------------------------------------------------------------------- building a reel by hand
// The Build panel works on one scene at a time (named by index, never by the playhead: a scene that is fading out still holds the
// playhead's end of it), and a scene grows to hold what is put into it, up to the longest scene the engine allows.

/** The playhead's time inside `scene` in scene seconds; the scene's start when the playhead is somewhere else. */
export function localTime(spec: Draft, scene: number, playhead: number): number {
  return localIn(spec, scene, playhead)?.local ?? 0
}

const maxSceneSec = (catalog: Catalog | undefined): number => catalog?.limits.max_scene_sec ?? 30

/** Make room in a scene for something that ends at `t` (scene seconds). */
function growTo(sc: Scene, t: number, catalog: Catalog | undefined): void {
  if (t > sc.duration_sec) sc.duration_sec = ms(Math.min(maxSceneSec(catalog), t))
}

/**
 * Give a scene another set. The time of day stays; what only made sense on the old set does not: a person standing in a place the new set
 * does not have goes to the middle, and a thing that stood on the old ground line is put on the new one.
 */
export function setBackground(spec: Draft, catalog: Catalog | undefined, scene: number, template: string): void {
  const sc = spec.scenes[scene]
  if (!sc || sc.background.template === template) return
  const was = entry(catalog?.backgrounds, sc.background.template)
  const now = entry(catalog?.backgrounds, template)
  const tod = sc.background.params.time_of_day
  sc.background = { template, params: typeof tod === 'string' && tod !== 'day' ? { time_of_day: tod } : {} }
  if (now?.slots) {
    const has = (place: string): boolean => place in (now.slots ?? {}) || UNIVERSAL_SLOTS.includes(place)
    for (const l of sc.layers) if (typeof l.position === 'string' && !has(l.position)) l.position = 'center'
    for (const o of sc.objects) if (typeof o.position === 'string' && !has(o.position)) o.position = 'center'
  }
  if (was?.ground_y !== undefined && now?.ground_y !== undefined) {
    for (const o of sc.objects) if (Array.isArray(o.position) && Math.abs(o.position[1] - (was.ground_y ?? 0)) < 0.002) o.position = [o.position[0], ms(now.ground_y)]
  }
}

/** Set the time of day of a scene's set (`day` is the default, so it is written as no setting at all). */
export function setTimeOfDay(spec: Draft, scene: number, time: string): void {
  const bg = spec.scenes[scene]?.background
  if (!bg) return
  if (time === 'day') delete bg.params.time_of_day
  else bg.params = { ...bg.params, time_of_day: time }
}

/** The reel's music: `none`, `auto` (the bed its style picks the mood of) or the name of a mood for a generated bed. */
export function setMusic(spec: Draft, music: string): void {
  spec.audio.music = music === 'none' ? null : music === 'auto' ? 'procedural' : `procedural:${music}`
}

/** Where the next person of a scene stands: the first of the usual places that nobody holds, the middle first. */
function freePlace(sc: Scene): string {
  const held = new Set(sc.layers.map((l) => l.position))
  return ['center', 'left', 'right', 'far_left', 'far_right'].find((p) => !held.has(p)) ?? 'center'
}

/** Put a character of the reel in a scene (a new layer where nobody stands); they stay where they are if they are there already. */
export function castInScene(spec: Draft, scene: number, characterId: string): Selection | null {
  const sc = spec.scenes[scene]
  if (!sc || !spec.characters.some((c) => c.id === characterId)) return null
  let li = sc.layers.findIndex((l) => l.character === characterId)
  if (li < 0) {
    sc.layers.push(newLayer(characterId, freePlace(sc)))
    li = sc.layers.length - 1
  }
  return { kind: 'layer', scene, layer: li }
}

/** Add a new character to the reel and put them in `scene`. */
export function addCharacterToScene(spec: Draft, scene: number, archetype: string): Selection {
  const c = newCharacter(spec, archetype)
  spec.characters.push(c)
  return castInScene(spec, scene, c.id) ?? { kind: 'character', id: c.id }
}

/** Give a character an action in a scene that starts when their last one there ends (the first at the start); the scene grows if it must. */
export function addActionAfter(spec: Draft, catalog: Catalog | undefined, scene: number, characterId: string, name: string): Selection | null {
  const placed = castInScene(spec, scene, characterId)
  if (placed?.kind !== 'layer') return null
  const sc = spec.scenes[scene]
  const layer = sc.layers[placed.layer]
  const after = layer.actions.reduce((end, a) => Math.max(end, a.t1), 0)
  const clip = newAction(catalog, name, after, Math.max(sc.duration_sec, maxSceneSec(catalog)))
  growTo(sc, clip.t1, catalog)
  layer.actions.push(clip)
  layer.actions.sort((a, b) => a.t0 - b.t0)
  return { kind: 'action', scene, layer: placed.layer, action: layer.actions.indexOf(clip) }
}

/** Seconds a line of text needs on screen to be read (a little under three words a second, which is also about how fast it is spoken). */
export const readingSeconds = (text: string): number => ms(clamp(text.trim().split(/\s+/).filter(Boolean).length / 2.5, 1.5, 8))

/** Add a caption that follows the last one of the scene (the first is at the start), shown long enough to read; the scene grows if it must. */
export function addCaptionAfter(spec: Draft, catalog: Catalog | undefined, scene: number, text: string, speaker?: string | null): Selection | null {
  const sc = spec.scenes[scene]
  const line = text.trim()
  if (!sc || !line) return null
  const after = sc.captions.reduce((end, c) => Math.max(end, c.t1), 0)
  const cap = newCaption(after, Math.max(sc.duration_sec, maxSceneSec(catalog)), line, speaker && spec.characters.some((c) => c.id === speaker) ? speaker : undefined)
  cap.t1 = ms(Math.min(maxSceneSec(catalog), cap.t0 + readingSeconds(line)))
  growTo(sc, cap.t1, catalog)
  sc.captions.push(cap)
  sc.captions.sort((a, b) => a.t0 - b.t0)
  return { kind: 'caption', scene, caption: sc.captions.indexOf(cap) }
}

/** A new scene after `scene` on the same set with the same people, standing where they stood and doing nothing yet. */
export function addSceneLike(spec: Draft, scene: number): Selection {
  const from = spec.scenes[scene]
  if (!from) return addScene(spec)
  const sc: Scene = newScene(spec, from.background.template, from.duration_sec)
  sc.background = copy(from.background)
  sc.layers = from.layers.map((l) => ({ ...newLayer(l.character, copy(l.position)), scale: l.scale, depth: l.depth, facing: l.facing }))
  spec.scenes.splice(scene + 1, 0, sc)
  return { kind: 'scene', scene: scene + 1 }
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
  if (sel.kind === 'object') {
    // the time an object is on screen: moves as a whole, or (when it stays until the end of the scene) its start alone
    const o = sc.objects[sel.object]
    if (!o) return
    if (o.t1 === null || o.t1 === undefined) o.t0 = ms(clamp(o.t0 + dt, 0, Math.max(0, sc.duration_sec - MIN_CLIP)))
    else {
      const len = o.t1 - o.t0
      const t0 = clamp(o.t0 + dt, 0, Math.max(0, sc.duration_sec - len))
      o.t0 = ms(t0)
      o.t1 = ms(t0 + len)
    }
    return
  }
  const clip = clipOf(spec, sel)
  if (!clip) return
  const len = clip.t1 - clip.t0
  const t0 = clamp(clip.t0 + dt, 0, Math.max(0, sc.duration_sec - len))
  clip.t0 = ms(t0)
  clip.t1 = ms(t0 + len)
}
