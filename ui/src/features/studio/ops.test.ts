import { produce } from 'immer'
import { describe, expect, it } from 'vitest'
import type { Catalog, ReelSpec, Scene } from '@/api/types'
import { newObject, objectBox } from '@/lib/spec'
import { OBJECT_FIXTURES } from './objects.fixture'
import { addAction, addCameraMove, addMotion, addObject, changeObjectAsset, clipOf, deleteSelection, duplicateSelection, nudge, setClipTimes, setObjectSpan, splitAtPlayhead } from './ops'

const scene = (id: string, dur: number): Scene => ({
  id,
  duration_sec: dur,
  background: { template: 'abstract', params: {} },
  camera: { moves: [] },
  layers: [{ character: 'mia', position: 'center', scale: 1, depth: 'mid', facing: 'auto', actions: [{ name: 'idle', t0: 0, t1: dur, params: {} }] }],
  objects: [],
  captions: [{ text: 'one two three four', t0: 1, t1: 3, style: 'subtitle', anchor: 'auto', speaker: 'mia' }],
  sfx: [],
  transition_out: { type: 'cut', duration: 0, params: {} },
})

const make = (): ReelSpec => ({
  version: '1.0',
  meta: { title: 't', style: 'flat_vector', fps: 30, resolution: [1080, 1920], seed: 1, target_duration_sec: 50, aspect: '9:16' },
  characters: [
    { id: 'mia', archetype: 'kid', props: [], palette: {} },
    { id: 'pip', archetype: 'elder', props: [], palette: {} },
  ],
  scenes: [scene('a', 10), scene('b', 8)],
  audio: { music: null, voiceover: 'none', ducking: true, music_gain_db: -16, voice_gain_db: 0, sfx_gain_db: -8, auto_sfx: false },
})

describe('adding a camera move', () => {
  it('goes to the scene under the playhead, at the local time', () => {
    const s = make()
    const sel = addCameraMove(s, 'zoom', 12.5) // scene b starts at 10
    expect(sel).toEqual({ kind: 'camera', scene: 1, move: 0 })
    expect(s.scenes[1].camera.moves[0].t0).toBeCloseTo(2.5)
    expect(s.scenes[0].camera.moves).toHaveLength(0)
  })

  it('can be aimed at a chosen scene: from the playhead if it is inside, else from the start', () => {
    const s = make()
    addCameraMove(s, 'pan', 4, 0) // the playhead is in scene a
    expect(s.scenes[0].camera.moves[0].t0).toBeCloseTo(4)
    addCameraMove(s, 'pan', 4, 1) // ... but this one is for scene b
    expect(s.scenes[1].camera.moves[0].t0).toBe(0)
    expect(addCameraMove(s, 'pan', 4, 9)).toBeNull() // no such scene
  })
})

describe('adding an action', () => {
  it('puts a character who is not in the scene yet into it', () => {
    const s = make()
    const sel = addAction(s, undefined, 'pip', 'wave', 2)
    expect(sel).toMatchObject({ kind: 'action', scene: 0 })
    expect(s.scenes[0].layers.map((l) => l.character)).toEqual(['mia', 'pip'])
    expect(s.scenes[0].layers[1].actions[0].name).toBe('wave')
  })
})

describe('nudging, splitting, duplicating, deleting', () => {
  it('keeps a nudged clip inside its scene and keeps its length', () => {
    const s = make()
    s.scenes[0].layers[0].actions = [{ name: 'wave', t0: 8, t1: 9.5, params: {} }]
    nudge(s, { kind: 'action', scene: 0, layer: 0, action: 0 }, 5)
    const a = s.scenes[0].layers[0].actions[0]
    expect(a.t1).toBeCloseTo(10)
    expect(a.t1 - a.t0).toBeCloseTo(1.5)
  })

  it('splits a caption at the playhead and shares its words out', () => {
    const s = make()
    const sel = splitAtPlayhead(s, { kind: 'caption', scene: 0, caption: 0 }, 2)
    expect(sel).toEqual({ kind: 'caption', scene: 0, caption: 1 })
    expect(s.scenes[0].captions.map((c) => c.text)).toEqual(['one two', 'three four'])
    expect(s.scenes[0].captions[0].t1).toBe(2)
    expect(s.scenes[0].captions[1].t0).toBe(2)
  })

  it('leaves a clip alone when the playhead is not inside it', () => {
    const s = make()
    const sel = splitAtPlayhead(s, { kind: 'caption', scene: 0, caption: 0 }, 5)
    expect(sel).toEqual({ kind: 'caption', scene: 0, caption: 0 })
    expect(s.scenes[0].captions).toHaveLength(1)
  })

  it('duplicates a scene under a fresh id', () => {
    const s = make()
    const sel = duplicateSelection(s, { kind: 'scene', scene: 0 })
    expect(sel).toEqual({ kind: 'scene', scene: 1 })
    const ids = s.scenes.map((x) => x.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('deleting a character removes them from every scene and clears them as a speaker', () => {
    const s = make()
    deleteSelection(s, { kind: 'character', id: 'mia' })
    expect(s.characters.map((c) => c.id)).toEqual(['pip'])
    expect(s.scenes.every((sc) => sc.layers.every((l) => l.character !== 'mia'))).toBe(true)
    expect(s.scenes[0].captions[0].speaker).toBeNull()
  })

  it('never deletes the last scene', () => {
    const s = make()
    s.scenes = [s.scenes[0]]
    deleteSelection(s, { kind: 'scene', scene: 0 })
    expect(s.scenes).toHaveLength(1)
  })
})

// The app edits through immer drafts (Proxies): copying one with structuredClone throws, which broke Duplicate and Split for real.
describe('on an immer draft, as the store edits', () => {
  it('duplicates a clip, a layer and a scene', () => {
    const next = produce(make(), (d) => {
      duplicateSelection(d, { kind: 'action', scene: 0, layer: 0, action: 0 })
      duplicateSelection(d, { kind: 'caption', scene: 0, caption: 0 })
      duplicateSelection(d, { kind: 'layer', scene: 0, layer: 0 })
      duplicateSelection(d, { kind: 'scene', scene: 1 })
    })
    expect(next.scenes[0].layers[0].actions).toHaveLength(2)
    expect(next.scenes[0].captions).toHaveLength(2)
    expect(next.scenes[0].layers).toHaveLength(2)
    expect(next.scenes).toHaveLength(3)
    expect(next.scenes[2].layers[0].actions[0]).toEqual(next.scenes[1].layers[0].actions[0])
  })

  it('splits an action and a caption at the playhead', () => {
    const next = produce(make(), (d) => {
      splitAtPlayhead(d, { kind: 'action', scene: 0, layer: 0, action: 0 }, 4)
      splitAtPlayhead(d, { kind: 'caption', scene: 0, caption: 0 }, 2)
    })
    expect(next.scenes[0].layers[0].actions.map((a) => [a.t0, a.t1])).toEqual([[0, 4], [4, 10]])
    expect(next.scenes[0].captions).toHaveLength(2)
  })
})

describe('adding an object', () => {
  const catalog = {
    backgrounds: [
      { name: 'abstract', summary: '', perspective: 0, ground_y: 0.74, slots: { center: [0.5, 0.74], left: [0.27, 0.74], right: [0.73, 0.74] } },
      { name: 'street', summary: '', perspective: 1, ground_y: 0.7, slots: { center: [0.5, 0.7] } },
    ],
    objects: OBJECT_FIXTURES,
  } as unknown as Catalog

  it('goes into the scene under the playhead, standing on the ground line of its set', () => {
    const s = make()
    s.scenes[1].background.template = 'street'
    const sel = addObject(s, catalog, 'car', 12.5) // scene b starts at 10
    expect(sel).toEqual({ kind: 'object', scene: 1, object: 0 })
    expect(s.scenes[0].objects).toHaveLength(0)
    expect(s.scenes[1].objects[0]).toMatchObject({ asset: 'car', depth: 'mid', layer: 'behind', t0: 0, t1: null, motions: [], palette: {} })
    expect(s.scenes[1].objects[0].position[1]).toBe(0.7)
    expect(addObject(s, catalog, 'tree', 3)).toEqual({ kind: 'object', scene: 0, object: 0 })
  })

  it('lands clear of the characters, and of the objects already there', () => {
    const s = make() // mia stands in the middle
    addObject(s, catalog, 'car', 1)
    addObject(s, catalog, 'tree', 1)
    const [car, tree] = s.scenes[0].objects.map((o) => o.position as [number, number])
    expect(Math.abs(car[0] - 0.5)).toBeGreaterThan(0.2)
    expect(Math.abs(tree[0] - 0.5)).toBeGreaterThan(0.2)
    expect(Math.abs(tree[0] - car[0])).toBeGreaterThan(0.4) // the other side, not on top of the car
  })

  it('is sized to fit the frame, however wide it is drawn, and never hangs out of it', () => {
    for (const name of ['car', 'tree', 'cake', 'billboard', 'balloon']) {
      const s = make()
      addObject(s, catalog, name, 1)
      const o = s.scenes[0].objects[0]
      const box = objectBox(catalog, s.scenes[0], o)
      expect(o.scale, name).toBeGreaterThan(0)
      expect(o.scale, name).toBeLessThanOrEqual(1)
      expect(box.x0, name).toBeGreaterThanOrEqual(0)
      expect(box.x1, name).toBeLessThanOrEqual(1)
      expect(box.x1 - box.x0, name).toBeLessThanOrEqual(0.43) // at most about two fifths of the frame across
      expect(box.y1 - box.y0, name).toBeLessThanOrEqual(0.5) // and half of it tall
    }
    // a small thing keeps its natural size
    const s = make()
    addObject(s, catalog, 'cake', 1)
    expect(s.scenes[0].objects[0].scale).toBe(1)
  })

  it('stands where it is dropped, kept near the frame, in the scene asked for', () => {
    const s = make()
    expect(addObject(s, catalog, 'car', 12.5, 0, [0.9, 0.7])).toEqual({ kind: 'object', scene: 0, object: 0 })
    expect(s.scenes[0].objects[0].position).toEqual([0.9, 0.7])
    addObject(s, catalog, 'car', 0, 0, [1.8, -2])
    expect(s.scenes[0].objects[1].position).toEqual([1.1, 0])
    expect(addObject(s, catalog, 'car', 0, 9)).toBeNull() // no such scene
  })

  it('rests a thing that floats on the ground line by its bottom edge, not its middle', () => {
    const s = make()
    addObject(s, catalog, 'balloon', 1) // anchored in its middle
    const o = s.scenes[0].objects[0]
    const box = objectBox(catalog, s.scenes[0], o)
    expect(box.y1).toBeCloseTo(0.74, 2) // the set's ground line
    expect(o.position[1]).toBeLessThan(0.74)
    addObject(s, catalog, 'tree', 1) // a tree stands: its anchor is on the ground line
    expect(s.scenes[0].objects[1].position[1]).toBe(0.74)
  })

  it('works before the catalog has arrived', () => {
    const s = make()
    addObject(s, undefined, 'car', 1)
    expect(s.scenes[0].objects[0]).toMatchObject({ asset: 'car', scale: 1, position: [expect.any(Number), 0.8] })
  })
})

describe('an object and its motions', () => {
  const catalog = { objects: OBJECT_FIXTURES, backgrounds: [{ name: 'abstract', summary: '', slots: { center: [0.5, 0.74] } }] } as unknown as Catalog
  const withCar = (): ReelSpec => {
    const s = make()
    s.scenes[0].objects.push({ ...newObject('car', [0.23, 0.74], 0.675), palette: { body: '#2a6fdb', leaves: '#00ff00' }, motions: [{ type: 'hop', t0: 4, t1: 5, ease: 'ease_in_out' }] })
    return s
  }

  it('gives another drawing the same place, size and times, and drops colours the new one has no part for', () => {
    const s = withCar()
    s.scenes[0].objects[0].t0 = 1
    changeObjectAsset(s, catalog, 0, 0, 'tree')
    expect(s.scenes[0].objects[0]).toMatchObject({ asset: 'tree', position: [0.23, 0.74], scale: 0.675, t0: 1, palette: { leaves: '#00ff00' } })
    expect(s.scenes[0].objects[0].motions).toHaveLength(1)
    changeObjectAsset(s, catalog, 0, 0, 'tree') // the same: nothing changes
    expect(s.scenes[0].objects[0].palette).toEqual({ leaves: '#00ff00' })
    changeObjectAsset(s, catalog, 0, 5, 'tree') // no such object
  })

  it('gets a motion at the playhead, in time order, as long as that kind usually lasts', () => {
    const s = withCar()
    expect(addMotion(s, catalog, 0, 0, 'spin', 2)).toEqual({ kind: 'motion', scene: 0, object: 0, motion: 0 })
    expect(addMotion(s, catalog, 0, 0, 'float', 7)).toEqual({ kind: 'motion', scene: 0, object: 0, motion: 2 })
    const m = s.scenes[0].objects[0].motions
    expect(m.map((x) => x.type)).toEqual(['spin', 'hop', 'float'])
    expect(m[0]).toMatchObject({ t0: 2, t1: 3.5 })
    expect(m[2]).toMatchObject({ t0: 7, t1: 10 })
  })

  it('is kept inside the scene when added late, and starts at the scene’s start when the playhead is elsewhere', () => {
    const s = withCar()
    addMotion(s, catalog, 0, 0, 'float', 9.9)
    const late = s.scenes[0].objects[0].motions.at(-1)
    expect(late?.t1).toBe(10)
    expect(late?.t0).toBeCloseTo(9.7, 6)
    addMotion(s, catalog, 0, 0, 'shake', 14) // the playhead is in scene b
    expect(s.scenes[0].objects[0].motions[0]).toMatchObject({ type: 'shake', t0: 0 })
    expect(addMotion(s, catalog, 0, 3, 'hop', 1)).toBeNull() // no such object
    expect(addMotion(s, catalog, 4, 0, 'hop', 1)).toBeNull() // no such scene
  })

  it('gets a move that goes somewhere other than where it stands', () => {
    const s = withCar()
    addMotion(s, catalog, 0, 0, 'move', 0)
    expect(s.scenes[0].objects[0].motions[0]).toMatchObject({ type: 'move', to: [0.53, 0.74] })
  })

  it('is deleted with its motions, or one motion at a time (and then the object is what is shown)', () => {
    const s = withCar()
    expect(deleteSelection(s, { kind: 'motion', scene: 0, object: 0, motion: 0 })).toEqual({ kind: 'object', scene: 0, object: 0 })
    expect(s.scenes[0].objects[0].motions).toHaveLength(0)
    expect(deleteSelection(s, { kind: 'object', scene: 0, object: 0 })).toEqual({ kind: 'scene', scene: 0 })
    expect(s.scenes[0].objects).toHaveLength(0)
    expect(deleteSelection(s, { kind: 'object', scene: 0, object: 4 })).toEqual({ kind: 'scene', scene: 0 }) // already gone: nothing breaks
  })

  it('is duplicated a little to the side, independent of the original', () => {
    const s = withCar()
    expect(duplicateSelection(s, { kind: 'object', scene: 0, object: 0 })).toEqual({ kind: 'object', scene: 0, object: 1 })
    const [a, b] = s.scenes[0].objects
    expect(b.position).toEqual([0.31, 0.74])
    expect(b).toMatchObject({ asset: 'car', scale: 0.675, palette: a.palette })
    b.motions[0].t0 = 8
    b.palette.body = '#000000'
    expect(a.motions[0].t0).toBe(4)
    expect(a.palette.body).toBe('#2a6fdb')
    // a place of the set stays a place
    s.scenes[0].objects[1].position = 'right'
    duplicateSelection(s, { kind: 'object', scene: 0, object: 1 })
    expect(s.scenes[0].objects[2].position).toBe('right')
  })

  it('has a motion duplicated right after the first, where it ends, inside the scene', () => {
    const s = withCar()
    expect(duplicateSelection(s, { kind: 'motion', scene: 0, object: 0, motion: 0 })).toEqual({ kind: 'motion', scene: 0, object: 0, motion: 1 })
    expect(s.scenes[0].objects[0].motions.map((m) => [m.t0, m.t1])).toEqual([[4, 5], [5, 6]])
    s.scenes[0].objects[0].motions[1] = { type: 'hop', t0: 9, t1: 10, ease: 'linear' }
    duplicateSelection(s, { kind: 'motion', scene: 0, object: 0, motion: 1 })
    const last = s.scenes[0].objects[0].motions[2]
    expect(last.t1).toBeLessThanOrEqual(10)
    expect(last.t1 - last.t0).toBeCloseTo(1, 6)
  })

  it('says when it is on screen: from a time until a time, or until the end of the scene', () => {
    const s = withCar()
    setObjectSpan(s, 0, 0, 2, 6)
    expect(s.scenes[0].objects[0]).toMatchObject({ t0: 2, t1: 6 })
    setObjectSpan(s, 0, 0, 3, null)
    expect(s.scenes[0].objects[0]).toMatchObject({ t0: 3, t1: null })
    setObjectSpan(s, 0, 0, 3, 3) // it has to be there for a moment at least
    expect(s.scenes[0].objects[0].t1).toBeCloseTo(3.1, 6)
    setObjectSpan(s, 0, 0, -2, null)
    expect(s.scenes[0].objects[0].t0).toBe(0)
    setObjectSpan(s, 0, 7, 1, 2) // no such object: nothing happens
  })

  it('has its motion timed like any clip: a minimum length, rounded to milliseconds', () => {
    const s = withCar()
    const sel = { kind: 'motion', scene: 0, object: 0, motion: 0 } as const
    expect(clipOf(s, sel)).toBe(s.scenes[0].objects[0].motions[0])
    setClipTimes(s, sel, 2.12345, 2.12345)
    expect(s.scenes[0].objects[0].motions[0]).toMatchObject({ t0: 2.123, t1: 2.223 })
    expect(clipOf(s, { kind: 'object', scene: 0, object: 0 })).toBeNull() // the bar of an object is not a clip with one span
  })

  it('is nudged in time: a motion as a clip, an object as a whole or (staying to the end) by its start alone', () => {
    const s = withCar()
    nudge(s, { kind: 'motion', scene: 0, object: 0, motion: 0 }, 7)
    expect(s.scenes[0].objects[0].motions[0]).toMatchObject({ t0: 9, t1: 10 }) // stays inside the scene, keeps its length

    nudge(s, { kind: 'object', scene: 0, object: 0 }, 2) // stays to the end: only its start moves
    expect(s.scenes[0].objects[0]).toMatchObject({ t0: 2, t1: null })
    nudge(s, { kind: 'object', scene: 0, object: 0 }, 50)
    expect(s.scenes[0].objects[0].t0).toBeCloseTo(9.9, 6)
    nudge(s, { kind: 'object', scene: 0, object: 0 }, -50)
    expect(s.scenes[0].objects[0].t0).toBe(0)

    setObjectSpan(s, 0, 0, 2, 5)
    nudge(s, { kind: 'object', scene: 0, object: 0 }, 1)
    expect(s.scenes[0].objects[0]).toMatchObject({ t0: 3, t1: 6 })
    nudge(s, { kind: 'object', scene: 0, object: 0 }, 20)
    expect(s.scenes[0].objects[0]).toMatchObject({ t0: 7, t1: 10 })
  })

  it('behaves the same on an immer draft, as the store edits', () => {
    const next = produce(withCar(), (d) => {
      addObject(d, catalog, 'tree', 1)
      addMotion(d, catalog, 0, 1, 'pulse', 1)
      duplicateSelection(d, { kind: 'object', scene: 0, object: 0 })
      deleteSelection(d, { kind: 'motion', scene: 0, object: 2, motion: 0 })
    })
    expect(next.scenes[0].objects.map((o) => o.asset)).toEqual(['car', 'car', 'tree'])
    expect(next.scenes[0].objects.map((o) => o.motions.length)).toEqual([1, 1, 0]) // the copy of the car has its hop; the tree's pulse was deleted
  })
})

