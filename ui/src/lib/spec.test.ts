import { describe, expect, it } from 'vitest'
import type { Catalog, ReelSpec, Scene, SceneObject } from '@/api/types'
import { depthScale, nearestSlot, newObject, objectBox, objectColor, objectPersons, objectPoint, objectSizeFrac, pathLabel, scaleByDrag, selectionExists, selectionFromPath, timeForPath } from './spec'

const catalog = {
  backgrounds: [
    { name: 'street', summary: '', perspective: 1, ground_y: 0.74, slots: { left: [0.27, 0.74], center: [0.5, 0.74], right: [0.73, 0.74] } },
    { name: 'abstract', summary: '', perspective: 0, ground_y: 0.74, slots: { center: [0.5, 0.74] } },
  ],
  objects: [
    { name: 'car', summary: '', height: 300, aspect: 2, anchor: [0.5, 1], size: 0.52, roles: ['body', 'window'], palette: { body: '#d9342b', window: '#bfe3f2' } },
    { name: 'balloon', summary: '', height: 400, aspect: 0.6, anchor: [0.5, 0.5], roles: [], palette: {} },
    { name: 'flag', summary: '', height: 500, aspect: 1, anchor: [0.25, 1] },
  ],
} as unknown as Catalog

const scene = (extra: Partial<Scene> = {}): Scene => ({
  id: 's',
  duration_sec: 6,
  background: { template: 'street', params: {} },
  camera: { moves: [] },
  layers: [],
  objects: [],
  captions: [],
  sfx: [],
  transition_out: { type: 'cut', duration: 0, params: {} },
  ...extra,
})

const obj = (extra: Partial<SceneObject> = {}): SceneObject => ({ ...newObject('car', [0.5, 0.8]), ...extra })

const spec = (scenes: Scene[]): ReelSpec => ({
  version: '1.0',
  meta: { title: 't', style: 'flat_vector', fps: 30, resolution: [1080, 1920], seed: 1, target_duration_sec: 50 },
  characters: [],
  scenes,
  audio: { music: null, voiceover: 'none', ducking: true, music_gain_db: -16, voice_gain_db: 0, sfx_gain_db: -8, auto_sfx: false },
})

describe('where an object stands', () => {
  it('is its position, or the set’s place of that name, or the middle of the set', () => {
    const sc = scene()
    expect(objectPoint(sc, obj({ position: [0.2, 0.6] }), catalog)).toEqual([0.2, 0.6])
    expect(objectPoint(sc, obj({ position: 'right' }), catalog)).toEqual([0.73, 0.74])
    expect(objectPoint(sc, obj({ position: 'nowhere' }), catalog)).toEqual([0.5, 0.74])
    expect(objectPoint(scene({ background: { template: 'unknown', params: {} } }), obj({ position: 'right' }), catalog)).toEqual([0.5, 0.8])
  })
})

describe('how big an object is on the stage', () => {
  it('follows the engine: height × 1.12 × scale × perspective, over the frame', () => {
    const sc = scene()
    const { w, h } = objectSizeFrac(catalog, sc, obj(), 0.8)
    expect(h).toBeCloseTo((300 * 1.12) / 1920, 6)
    expect(w).toBeCloseTo((300 * 1.12 * 2) / 1080, 6)
    // twice the scale, twice the size; lower on the screen is bigger when the set has perspective, and not when it has none
    expect(objectSizeFrac(catalog, sc, obj({ scale: 2 }), 0.8).h).toBeCloseTo(h * 2, 6)
    expect(objectSizeFrac(catalog, sc, obj(), 0.9).h).toBeGreaterThan(h)
    expect(objectSizeFrac(catalog, scene({ background: { template: 'abstract', params: {} } }), obj(), 0.9).h).toBeCloseTo(h, 6)
    expect(depthScale(catalog, sc, 0.0)).toBe(0.35) // never smaller than a third
  })

  it('puts the art around its anchor: the bottom middle by default, the middle of a thing that floats, mirrored when it faces left', () => {
    const sc = scene()
    const { w, h } = objectSizeFrac(catalog, sc, obj(), 0.8)
    const stands = objectBox(catalog, sc, obj({ position: [0.5, 0.8] }), [0.5, 0.8])
    expect(stands.x0).toBeCloseTo(0.5 - w / 2, 6)
    expect(stands.x1).toBeCloseTo(0.5 + w / 2, 6)
    expect(stands.y0).toBeCloseTo(0.8 - h, 6)
    expect(stands.y1).toBeCloseTo(0.8, 6)

    const floats = objectBox(catalog, sc, obj({ asset: 'balloon' }), [0.5, 0.5])
    expect((floats.y0 + floats.y1) / 2).toBeCloseTo(0.5, 6) // centred on its anchor

    const flag = obj({ asset: 'flag' })
    const right = objectBox(catalog, sc, flag, [0.5, 0.8])
    const left = objectBox(catalog, sc, { ...flag, facing: 'left' }, [0.5, 0.8])
    expect(0.5 - right.x0).toBeCloseTo(0.25 * (right.x1 - right.x0), 6) // the anchor is a quarter of the way across
    expect(0.5 - left.x0).toBeCloseTo(0.75 * (left.x1 - left.x0), 6) // mirrored: three quarters
  })

  it('knows its height in persons from the library’s size, else from its height', () => {
    expect(objectPersons(catalog, obj({ scale: 2 }))).toBeCloseTo(1.04, 6)
    expect(objectPersons(catalog, obj({ asset: 'flag', scale: 1 }))).toBeCloseTo(500 / 575, 6)
    expect(objectPersons(catalog, obj({ asset: 'nothing' }))).toBeUndefined()
  })

  it('is told apart by its first recolourable part', () => {
    expect(objectColor(catalog, obj())).toBe('#d9342b')
    expect(objectColor(catalog, obj({ palette: { body: '#112233' } }))).toBe('#112233')
    expect(objectColor(catalog, obj({ asset: 'balloon' }))).toBe('#2dd4bf')
  })
})

describe('dragging on the stage', () => {
  it('finds the set’s place nearest a point, when it is close enough', () => {
    const slots = Object.entries(catalog.backgrounds[0].slots ?? {})
    expect(nearestSlot(slots, 0.28, 0.74, 400, 700, 16)).toBe('left')
    expect(nearestSlot(slots, 0.4, 0.74, 400, 700, 16)).toBeNull() // 40 px from the nearest
    expect(nearestSlot(catalog.backgrounds[0].slots ?? {}, 0.6, 0.74)).toBe('center') // no limit: always one
    expect(nearestSlot([], 0.5, 0.5)).toBeNull()
  })

  it('scales by how much farther from the anchor the pointer is, within limits', () => {
    expect(scaleByDrag(1, 100, 150)).toBe(1.5)
    expect(scaleByDrag(2, 100, 50)).toBe(1)
    expect(scaleByDrag(1, 100, 10000)).toBe(6) // never over what the engine takes
    expect(scaleByDrag(1, 100, 0)).toBe(0.14) // a pointer on the anchor itself does not collapse it to nothing
    expect(scaleByDrag(0.05, 100, 1)).toBe(0.05)
  })
})

describe('objects and motions in paths and selections', () => {
  const s = spec([scene({ objects: [obj({ motions: [{ type: 'hop', t0: 1, t1: 2, ease: 'ease_in_out' }] }), obj({ asset: 'flag', t0: 2.5 })] })])

  it('reads a lint path into the object or the motion it names', () => {
    expect(selectionFromPath(s, 'scenes[0].objects[1]')).toEqual({ kind: 'object', scene: 0, object: 1 })
    expect(selectionFromPath(s, 'scenes[0].objects[1].position')).toEqual({ kind: 'object', scene: 0, object: 1 })
    expect(selectionFromPath(s, 'scenes[0].objects[0].motions[0]')).toEqual({ kind: 'motion', scene: 0, object: 0, motion: 0 })
    expect(selectionFromPath(s, 'scenes[0].objects[0].motions[0].to')).toEqual({ kind: 'motion', scene: 0, object: 0, motion: 0 })
    expect(selectionFromPath(s, 'scenes[0].objects')).toEqual({ kind: 'scene', scene: 0 }) // "too many objects" is about the scene
    expect(selectionFromPath(s, 'scenes[3].objects[0]')).toBeNull()
  })

  it('names them for people', () => {
    expect(pathLabel(s, 'scenes[0].objects[1]')).toBe('Scene 1 › flag')
    expect(pathLabel(s, 'scenes[0].objects[0].motions[0].t1')).toBe('Scene 1 › car › hop')
  })

  it('knows whether they are still there (after an undo, say)', () => {
    expect(selectionExists(s, { kind: 'object', scene: 0, object: 1 })).toBe(true)
    expect(selectionExists(s, { kind: 'object', scene: 0, object: 2 })).toBe(false)
    expect(selectionExists(s, { kind: 'motion', scene: 0, object: 0, motion: 0 })).toBe(true)
    expect(selectionExists(s, { kind: 'motion', scene: 0, object: 1, motion: 0 })).toBe(false)
  })

  it('shows what a problem points at when it appears: the object’s first moment, the motion’s start, else the scene’s', () => {
    const two = spec([scene({ duration_sec: 5 }), ...s.scenes])
    expect(timeForPath(two, 'scenes[1].objects[1]')).toBeCloseTo(5 + 2.5 + 0.01, 6)
    expect(timeForPath(two, 'scenes[1].objects[0].motions[0].to')).toBeCloseTo(5 + 1 + 0.01, 6)
    expect(timeForPath(two, 'scenes[1].objects[0]')).toBeCloseTo(5 + 0.01, 6)
    expect(timeForPath(two, 'scenes[1].duration_sec')).toBeCloseTo(5 + 0.01, 6)
    expect(timeForPath(two, 'meta.title')).toBeNull()
  })
})
