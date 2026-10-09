import { produce } from 'immer'
import { describe, expect, it } from 'vitest'
import type { ReelSpec, Scene } from '@/api/types'
import { addAction, addCameraMove, deleteSelection, duplicateSelection, nudge, splitAtPlayhead } from './ops'

const scene = (id: string, dur: number): Scene => ({
  id,
  duration_sec: dur,
  background: { template: 'abstract', params: {} },
  camera: { moves: [] },
  layers: [{ character: 'mia', position: 'center', scale: 1, depth: 'mid', facing: 'auto', actions: [{ name: 'idle', t0: 0, t1: dur, params: {} }] }],
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
