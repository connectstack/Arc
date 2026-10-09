// Helpers for reading and building spec pieces: ids, defaults, colours, and mapping a lint path to something selectable.
import type { ActionClip, Caption, Catalog, Character, CatalogEntry, Layer, ReelSpec, Scene, SceneObject, Vec2 } from '@/api/types'
import { clamp, ms, sceneSlots } from './timeline'

export type Selection =
  | { kind: 'reel' }
  | { kind: 'scene'; scene: number }
  | { kind: 'character'; id: string }
  | { kind: 'layer'; scene: number; layer: number }
  | { kind: 'object'; scene: number; object: number }
  | { kind: 'motion'; scene: number; object: number; motion: number }
  | { kind: 'action'; scene: number; layer: number; action: number }
  | { kind: 'caption'; scene: number; caption: number }
  | { kind: 'camera'; scene: number; move: number }
  | { kind: 'sfx'; scene: number; sfx: number }
  | { kind: 'transition'; scene: number }

export function sameSelection(a: Selection, b: Selection): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

/** Does what `sel` points at still exist in `spec`? (After an undo, the clip that was selected may be gone.) */
export function selectionExists(spec: ReelSpec, sel: Selection): boolean {
  const sc = 'scene' in sel ? spec.scenes[sel.scene] : undefined
  switch (sel.kind) {
    case 'reel':
      return true
    case 'character':
      return spec.characters.some((c) => c.id === sel.id)
    case 'scene':
    case 'transition':
      return !!sc
    case 'layer':
      return !!sc?.layers[sel.layer]
    case 'object':
      return !!sc?.objects?.[sel.object]
    case 'motion':
      return !!sc?.objects?.[sel.object]?.motions?.[sel.motion]
    case 'action':
      return !!sc?.layers[sel.layer]?.actions[sel.action]
    case 'caption':
      return !!sc?.captions[sel.caption]
    case 'camera':
      return !!sc?.camera.moves[sel.move]
    case 'sfx':
      return !!sc?.sfx[sel.sfx]
  }
}

export function uniqueId(existing: Iterable<string>, base: string): string {
  const taken = new Set(existing)
  if (!taken.has(base)) return base
  let n = 2
  while (taken.has(`${base}_${n}`)) n++
  return `${base}_${n}`
}

export const entry = (list: CatalogEntry[] | undefined, name: string): CatalogEntry | undefined => list?.find((e) => e.name === name)

export function newAction(catalog: Catalog | undefined, name: string, t0: number, sceneDuration: number): ActionClip {
  const def = entry(catalog?.actions, name)
  const len = def?.default_duration ?? 2
  const min = def?.min_duration ?? 0.3
  const start = ms(Math.max(0, Math.min(t0, sceneDuration - min)))
  return { name, t0: start, t1: ms(Math.min(sceneDuration, start + len)), params: {} }
}

export function newCaption(t0: number, sceneDuration: number, text = 'New caption', speaker?: string): Caption {
  const start = ms(Math.max(0, Math.min(t0, sceneDuration - 0.5)))
  return { text, t0: start, t1: ms(Math.min(sceneDuration, start + 2)), style: 'subtitle', anchor: 'auto', ...(speaker ? { speaker } : {}) }
}

export function newLayer(characterId: string, position: Layer['position'] = 'center'): Layer {
  return { character: characterId, position, scale: 1, depth: 'mid', facing: 'auto', actions: [] }
}

export function newObject(asset: string, position: SceneObject['position'] = [0.5, 0.8], scale = 1): SceneObject {
  return { asset, position, scale, depth: 'mid', layer: 'behind', facing: 'auto', rotation: 0, alpha: 1, t0: 0, t1: null, motions: [], palette: {} }
}

export function newScene(spec: ReelSpec, template = 'abstract', duration = 5): Scene {
  return {
    id: uniqueId(
      spec.scenes.map((s) => s.id),
      'scene',
    ),
    duration_sec: duration,
    background: { template, params: {} },
    camera: { moves: [] },
    layers: spec.characters.slice(0, 1).map((c) => newLayer(c.id)),
    objects: [],
    captions: [],
    sfx: [],
    transition_out: { type: 'cut', duration: 0, params: {} },
  }
}

export function newCharacter(spec: ReelSpec, archetype = 'everyman'): Character {
  const id = uniqueId(
    spec.characters.map((c) => c.id),
    archetype === 'everyman' ? 'actor' : archetype,
  )
  return { id, archetype, name: id.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()), props: [], palette: {} }
}

/** The colour that identifies a character on the timeline and the stage: their shirt (own palette, else the archetype's). */
export function characterColor(spec: ReelSpec, catalog: Catalog | undefined, id: string): string {
  const ch = spec.characters.find((c) => c.id === id)
  return ch?.palette.shirt ?? entry(catalog?.archetypes, ch?.archetype ?? '')?.palette?.shirt ?? '#7c6cff'
}

export function characterName(spec: ReelSpec, id: string): string {
  const ch = spec.characters.find((c) => c.id === id)
  return ch?.name || id
}

/** Position of a layer in screen fractions (resolving a slot name through the background's slots). */
export function layerPoint(scene: Scene, layer: Layer, catalog: Catalog | undefined): [number, number] {
  if (Array.isArray(layer.position)) return [layer.position[0], layer.position[1]]
  const bg = entry(catalog?.backgrounds, scene.background.template)
  const slot = bg?.slots?.[layer.position]
  return slot ? [slot[0], slot[1]] : [0.5, bg?.ground_y ?? 0.74]
}

const PATH = /^scenes\[(\d+)\](?:\.(\w+)(?:\[(\d+)\])?(?:\.(\w+)(?:\[(\d+)\])?)?)?/

/** The thing a lint path names (`scenes[1].layers[0].actions[2].name` -> that action), or null. */
export function selectionFromPath(spec: ReelSpec, path: string): Selection | null {
  if (path.startsWith('meta') || path === 'audio' || path.startsWith('audio.')) return { kind: 'reel' }
  const ch = /^characters\[(\d+)\]/.exec(path)
  if (ch) {
    const c = spec.characters[Number(ch[1])]
    return c ? { kind: 'character', id: c.id } : null
  }
  const m = PATH.exec(path)
  if (!m) return null
  const scene = Number(m[1])
  if (!spec.scenes[scene]) return null
  const [, , group, idx, sub, subIdx] = m
  if (!group || group === 'duration_sec' || group === 'background' || group === 'id' || group === 'notes') return { kind: 'scene', scene }
  const i = idx === undefined ? undefined : Number(idx)
  if (group === 'layers' && i !== undefined) {
    if (sub === 'actions' && subIdx !== undefined) return { kind: 'action', scene, layer: i, action: Number(subIdx) }
    return { kind: 'layer', scene, layer: i }
  }
  if (group === 'objects' && i !== undefined) {
    if (sub === 'motions' && subIdx !== undefined) return { kind: 'motion', scene, object: i, motion: Number(subIdx) }
    return { kind: 'object', scene, object: i }
  }
  if (group === 'captions' && i !== undefined) return { kind: 'caption', scene, caption: i }
  if (group === 'camera') return sub === 'moves' && subIdx !== undefined ? { kind: 'camera', scene, move: Number(subIdx) } : { kind: 'scene', scene }
  if (group === 'sfx' && i !== undefined) return { kind: 'sfx', scene, sfx: i }
  if (group === 'transition_out') return { kind: 'transition', scene }
  return { kind: 'scene', scene }
}

/**
 * Where to put the playhead to show what a lint path points at, in global seconds: for an object, the moment it appears, for one of its
 * motions the moment it starts (a moment in, so the stage already shows it), else the start of the scene; null for the whole reel.
 */
export function timeForPath(spec: ReelSpec, path: string): number | null {
  const sel = selectionFromPath(spec, path)
  if (!sel || !('scene' in sel)) return null
  const slot = sceneSlots(spec)[sel.scene]
  if (!slot) return null
  const o = sel.kind === 'object' || sel.kind === 'motion' ? spec.scenes[sel.scene]?.objects?.[sel.object] : undefined
  const at = sel.kind === 'object' ? o?.t0 : sel.kind === 'motion' ? o?.motions?.[sel.motion]?.t0 : undefined
  return slot.start + clamp((at ?? 0) + 0.01, 0.01, Math.max(0.01, slot.duration - 0.01))
}

/** `scenes[1].layers[0].actions[2].name` -> `Scene 2 › Pip › talk` */
export function pathLabel(spec: ReelSpec, path: string): string {
  const sel = selectionFromPath(spec, path)
  if (!sel) return path || 'Reel'
  switch (sel.kind) {
    case 'reel':
      return path.startsWith('audio') ? 'Reel › Audio' : 'Reel'
    case 'character':
      return `Cast › ${characterName(spec, sel.id)}`
    case 'scene':
      return `Scene ${sel.scene + 1}`
    case 'layer': {
      const l = spec.scenes[sel.scene]?.layers[sel.layer]
      return `Scene ${sel.scene + 1} › ${l ? characterName(spec, l.character) : 'layer'}`
    }
    case 'action': {
      const l = spec.scenes[sel.scene]?.layers[sel.layer]
      const a = l?.actions[sel.action]
      return `Scene ${sel.scene + 1} › ${l ? characterName(spec, l.character) : 'layer'} › ${a?.name ?? 'action'}`
    }
    case 'object': {
      const o = spec.scenes[sel.scene]?.objects?.[sel.object]
      return `Scene ${sel.scene + 1} › ${o?.asset ?? 'object'}`
    }
    case 'motion': {
      const o = spec.scenes[sel.scene]?.objects?.[sel.object]
      return `Scene ${sel.scene + 1} › ${o?.asset ?? 'object'} › ${o?.motions?.[sel.motion]?.type ?? 'motion'}`
    }
    case 'caption': {
      const c = spec.scenes[sel.scene]?.captions[sel.caption]
      return `Scene ${sel.scene + 1} › Caption › ${(c?.text ?? '').slice(0, 24)}`
    }
    case 'camera':
      return `Scene ${sel.scene + 1} › Camera › ${spec.scenes[sel.scene]?.camera.moves[sel.move]?.type ?? 'move'}`
    case 'sfx':
      return `Scene ${sel.scene + 1} › Sound › ${spec.scenes[sel.scene]?.sfx[sel.sfx]?.name ?? ''}`
    case 'transition':
      return `Scene ${sel.scene + 1} › Transition`
  }
}

export function clampPositive(v: number, min = 0): number {
  return Number.isFinite(v) ? Math.max(min, v) : min
}

/** Design units of a character at layer scale 1 -> screen pixels (a 1080-wide frame): see CHAR_UNIT in reel/core/planner.py. */
export const CHAR_UNIT = 1.12

/** The frame the engine draws, in design pixels. */
export const FRAME_W = 1080
export const FRAME_H = 1920

/** A standing person, in design pixels: library things are sized against it (`size` 1 = a person). */
export const PERSON_HEIGHT = 575

/** The places every set has (a set adds its own). */
export const UNIVERSAL_SLOTS = ['far_left', 'left', 'center', 'right', 'far_right', 'off_left', 'off_right']

/** The engine's perspective: things lower on the screen are bigger (World.depth_scale in reel/core/planner.py). */
export function depthScale(catalog: Catalog | undefined, scene: Scene, y: number): number {
  const perspective = entry(catalog?.backgrounds, scene.background.template)?.perspective ?? 1
  return Math.max(0.35, 1 + perspective * (y - 0.8))
}

/** How tall a layer's character is on screen, as a fraction of the frame height (about; the camera is assumed idle). */
export function characterHeightFrac(spec: ReelSpec, catalog: Catalog | undefined, scene: Scene, layer: Layer, y: number): number {
  const ch = spec.characters.find((c) => c.id === layer.character)
  const height = entry(catalog?.archetypes, ch?.archetype ?? '')?.height ?? 575
  return (height * CHAR_UNIT * layer.scale * depthScale(catalog, scene, y)) / FRAME_H
}

// ------------------------------------------------------------------------------- objects
/** Where an object stands, in screen fractions: its position, or the set's place of that name (the middle of the set when there is none). */
export function objectPoint(scene: Scene, object: SceneObject, catalog: Catalog | undefined): [number, number] {
  if (Array.isArray(object.position)) return [object.position[0], object.position[1]]
  const bg = entry(catalog?.backgrounds, scene.background.template)
  const slot = bg?.slots?.[object.position] ?? bg?.slots?.center
  return slot ? [slot[0], slot[1]] : [0.5, 0.8]
}

/** The library row of an object's asset (its height, shape, size, colours), when the catalog has it. */
export const objectEntry = (catalog: Catalog | undefined, asset: string): CatalogEntry | undefined => entry(catalog?.objects, asset)

/** An object's height in persons at its scale (1 = as tall as a character at scale 1 standing at the same spot); undefined when unknown. */
export function objectPersons(catalog: Catalog | undefined, object: SceneObject): number | undefined {
  const row = objectEntry(catalog, object.asset)
  const size = row?.size ?? (row?.height === undefined ? undefined : row.height / PERSON_HEIGHT)
  return size === undefined ? undefined : size * object.scale
}

/** How big an object is on screen standing at height `y`: width and height as fractions of the frame's own width and height (about; the camera is assumed idle). */
export function objectSizeFrac(catalog: Catalog | undefined, scene: Scene, object: SceneObject, y: number): { w: number; h: number } {
  const row = objectEntry(catalog, object.asset)
  const px = (row?.height ?? 300) * CHAR_UNIT * object.scale * depthScale(catalog, scene, y)
  return { w: (px * (row?.aspect ?? 1)) / FRAME_W, h: px / FRAME_H }
}

/** A rectangle in screen fractions. */
export interface Box {
  x0: number
  y0: number
  x1: number
  y1: number
}

/**
 * The rectangle an object's art covers when it stands at `at` (default: where it is placed): its height and shape from the library,
 * its anchor (the point of the art that sits on the position), its scale and the set's perspective. Art that faces left is mirrored,
 * and so is the anchor. Rotation is not applied (the stage rotates the outline about the anchor).
 */
export function objectBox(catalog: Catalog | undefined, scene: Scene, object: SceneObject, at: [number, number] = objectPoint(scene, object, catalog)): Box {
  const [ax, ay] = objectEntry(catalog, object.asset)?.anchor ?? [0.5, 1]
  const { w, h } = objectSizeFrac(catalog, scene, object, at[1])
  const left = object.facing === 'left' ? 1 - ax : ax
  return { x0: at[0] - left * w, x1: at[0] + (1 - left) * w, y0: at[1] - ay * h, y1: at[1] + (1 - ay) * h }
}

const HEX_COLOR = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i

/** The colour that identifies an object on the timeline and the stage: its first recolourable part (own palette, else the drawing's own). */
export function objectColor(catalog: Catalog | undefined, object: SceneObject): string {
  const row = objectEntry(catalog, object.asset)
  const roles = row?.roles?.length ? row.roles : Object.keys(row?.palette ?? {})
  for (const role of roles) {
    const c = object.palette[role] ?? row?.palette?.[role]
    if (c && HEX_COLOR.test(c)) return c
  }
  return '#2dd4bf'
}

/** The nearest of a set's named places to a point (screen fractions), within `radius` px on a stage `W` x `H` px wide (as fractions, by default: no limit); null when none is that close. */
export function nearestSlot(slots: Iterable<readonly [string, Vec2]> | Record<string, Vec2>, x: number, y: number, W = 1, H = 1, radius = Infinity): string | null {
  const list = Symbol.iterator in slots ? [...(slots as Iterable<readonly [string, Vec2]>)] : Object.entries(slots as Record<string, Vec2>)
  let name: string | null = null
  let best = radius
  for (const [n, [sx, sy]] of list) {
    const d = Math.hypot((sx - x) * W, (sy - y) * H)
    if (d < best) ((best = d), (name = n))
  }
  return name
}

/** The scale a dragged corner handle gives: the scale the drag began with, times how much farther from (or nearer to) the anchor the pointer is now than when it was pressed. */
export function scaleByDrag(start: number, startDist: number, dist: number, min = 0.05, max = 6): number {
  const k = Math.max(14, dist) / Math.max(14, startDist)
  return Math.round(clamp(start * k, min, max) * 100) / 100
}
