// Helpers for reading and building spec pieces: ids, defaults, colours, and mapping a lint path to something selectable.
import type { ActionClip, Caption, Catalog, Character, CatalogEntry, Layer, ReelSpec, Scene } from '@/api/types'
import { ms } from './timeline'

export type Selection =
  | { kind: 'reel' }
  | { kind: 'scene'; scene: number }
  | { kind: 'character'; id: string }
  | { kind: 'layer'; scene: number; layer: number }
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
  if (group === 'captions' && i !== undefined) return { kind: 'caption', scene, caption: i }
  if (group === 'camera') return sub === 'moves' && subIdx !== undefined ? { kind: 'camera', scene, move: Number(subIdx) } : { kind: 'scene', scene }
  if (group === 'sfx' && i !== undefined) return { kind: 'sfx', scene, sfx: i }
  if (group === 'transition_out') return { kind: 'transition', scene }
  return { kind: 'scene', scene }
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

/** How tall a layer's character is on screen, as a fraction of the frame height (about; the camera is assumed idle). */
export function characterHeightFrac(spec: ReelSpec, catalog: Catalog | undefined, scene: Scene, layer: Layer, y: number): number {
  const ch = spec.characters.find((c) => c.id === layer.character)
  const height = entry(catalog?.archetypes, ch?.archetype ?? '')?.height ?? 575
  const perspective = entry(catalog?.backgrounds, scene.background.template)?.perspective ?? 1
  const depthScale = Math.max(0.35, 1 + perspective * (y - 0.8))
  return (height * CHAR_UNIT * layer.scale * depthScale) / 1920
}
