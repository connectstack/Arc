import { describe, expect, it } from 'vitest'
import story from '@/api/mock/data/story_50s.json'
import explainer from '@/api/mock/data/explainer_45s.json'
import { normalizeSpec, stripDefaults } from './normalize'

describe('normalizeSpec / stripDefaults', () => {
  it('fills in everything a minimal spec leaves out', () => {
    const s = normalizeSpec({
      meta: { title: 'T', style: 'stickman' },
      characters: [{ id: 'a', archetype: 'kid' }],
      scenes: [{ id: 's', duration_sec: 5, background: { template: 'room' }, layers: [{ character: 'a', actions: [{ name: 'wave', t0: 0, t1: 2 }] }], captions: [{ text: 'hi', t0: 0, t1: 1 }] }],
    })
    expect(s.characters[0]).toMatchObject({ props: [], palette: {} })
    const sc = s.scenes[0]
    expect(sc.camera).toEqual({ moves: [] })
    expect(sc.sfx).toEqual([])
    expect(sc.transition_out).toEqual({ type: 'cut', duration: 0, params: {} })
    expect(sc.layers[0]).toMatchObject({ position: [0.5, 0.8], scale: 1, depth: 'mid', facing: 'auto' })
    expect(sc.layers[0].actions[0].params).toEqual({})
    expect(sc.captions[0]).toMatchObject({ style: 'subtitle', anchor: 'auto' })
    expect(s.meta).toMatchObject({ fps: 30, resolution: [1080, 1920], seed: 0, target_duration_sec: 50 })
    expect(s.audio).toMatchObject({ voiceover: 'none', music: null, ducking: true })
  })

  it('never throws on rubbish and still returns a usable shape', () => {
    for (const bad of [null, undefined, 'x', 3, [], { scenes: 'no', characters: 7 }]) {
      const s = normalizeSpec(bad)
      expect(Array.isArray(s.scenes) && Array.isArray(s.characters)).toBe(true)
    }
  })

  it('is lossless: strip then normalize gives back the same model', () => {
    for (const raw of [story, explainer]) {
      const once = normalizeSpec(raw)
      const twice = normalizeSpec(stripDefaults(once))
      expect(twice).toEqual(once)
    }
  })

  it('keeps files small: defaults are not written back', () => {
    const slim = JSON.stringify(stripDefaults(normalizeSpec(story)))
    const fat = JSON.stringify(normalizeSpec(story))
    expect(slim.length).toBeLessThan(fat.length * 0.85)
    const o = stripDefaults(normalizeSpec({ meta: { title: 't', style: 's' }, characters: [{ id: 'a', archetype: 'kid' }], scenes: [{ id: 's', duration_sec: 5, background: { template: 'room' } }] }))
    expect(JSON.stringify(o)).not.toMatch(/"props"|"palette"|"camera"|"transition_out"|"fps"|"layers"/)
  })

  it('keeps what differs from the default', () => {
    const o = stripDefaults(normalizeSpec({ meta: { title: 't', style: 's', fps: 24, seed: 7 }, characters: [], scenes: [{ id: 'a', duration_sec: 5, background: { template: 'room', params: { mood: 'warm' } }, transition_out: { type: 'wipe', duration: 0.6 } }] })) as { meta: Record<string, unknown>; scenes: Record<string, unknown>[] }
    expect(o.meta).toMatchObject({ fps: 24, seed: 7 })
    expect(o.scenes[0]).toMatchObject({ background: { params: { mood: 'warm' } }, transition_out: { type: 'wipe', duration: 0.6 } })
  })
})

describe('objects from the asset library', () => {
  const withObjects = {
    meta: { title: 'T', style: 'flat_vector', library_gaps: [] },
    characters: [{ id: 'a', archetype: 'kid' }],
    scenes: [
      {
        id: 's1',
        duration_sec: 6,
        background: { template: 'street' },
        objects: [
          { asset: 'tree' },
          {
            asset: 'car',
            position: 'right',
            scale: 0.8,
            depth: 'foreground',
            layer: 'front',
            facing: 'left',
            rotation: -12,
            alpha: 0.9,
            t0: 1,
            t1: 5,
            palette: { body: '#2a6fdb' },
            motions: [{ type: 'move', t0: 1, t1: 3, to: [1.2, 0.8] }, { type: 'hop', t0: 3, t1: 4, count: 3, amount: 0.5, ease: 'bounce' }, { type: 'fade', t0: 0, t1: 1, from: 0.2, to: 1 }],
          },
        ],
      },
    ],
  }

  it('fills in everything an object and its motions leave out', () => {
    const [tree, car] = normalizeSpec(withObjects).scenes[0].objects
    expect(tree).toEqual({ asset: 'tree', position: [0.5, 0.8], scale: 1, depth: 'mid', layer: 'behind', facing: 'auto', rotation: 0, alpha: 1, t0: 0, t1: null, motions: [], palette: {} })
    expect(car).toMatchObject({ position: 'right', scale: 0.8, depth: 'foreground', layer: 'front', facing: 'left', rotation: -12, alpha: 0.9, t0: 1, t1: 5, palette: { body: '#2a6fdb' } })
    expect(car.motions[0]).toEqual({ type: 'move', t0: 1, t1: 3, to: [1.2, 0.8], ease: 'ease_in_out' })
    expect(car.motions[1]).toMatchObject({ count: 3, amount: 0.5, ease: 'bounce' })
  })

  it('gives a scene with no objects an empty list, and survives objects that are rubbish', () => {
    expect(normalizeSpec({ scenes: [{ id: 'x', duration_sec: 3 }] }).scenes[0].objects).toEqual([])
    const odd = normalizeSpec({ scenes: [{ id: 'x', duration_sec: 3, objects: [null, 7, 'car', { asset: 5, position: [1], depth: 'sideways', t1: 'soon', motions: 'many', palette: [] }] }] }).scenes[0].objects
    expect(odd).toHaveLength(4)
    expect(odd[3]).toMatchObject({ asset: '', position: [0.5, 0.8], depth: 'mid', t1: null, motions: [], palette: {} })
  })

  it('writes back only what differs from the defaults', () => {
    const out = stripDefaults(normalizeSpec(withObjects)) as { meta: Record<string, unknown>; scenes: { objects: Record<string, unknown>[] }[] }
    const [tree, car] = out.scenes[0].objects
    expect(tree).toEqual({ asset: 'tree' })
    expect(car).toEqual({
      asset: 'car',
      position: 'right',
      scale: 0.8,
      depth: 'foreground',
      layer: 'front',
      facing: 'left',
      rotation: -12,
      alpha: 0.9,
      t0: 1,
      t1: 5,
      palette: { body: '#2a6fdb' },
      motions: [{ type: 'move', t0: 1, t1: 3, to: [1.2, 0.8] }, { type: 'hop', t0: 3, t1: 4, count: 3, amount: 0.5, ease: 'bounce' }, { type: 'fade', t0: 0, t1: 1, from: 0.2, to: 1 }],
    })
    expect('library_gaps' in out.meta).toBe(false) // nothing missing from the library: nothing to remember
    expect('objects' in (stripDefaults(normalizeSpec({ meta: { title: 't', style: 's' }, scenes: [{ id: 'a', duration_sec: 3 }] })) as { scenes: object[] }).scenes[0]).toBe(false)
  })

  it('is lossless for objects too: strip then normalize gives back the same model', () => {
    const once = normalizeSpec(withObjects)
    expect(normalizeSpec(stripDefaults(once))).toEqual(once)
    // a spec that remembers what the library lacked keeps it
    const gaps = normalizeSpec({ ...withObjects, meta: { title: 'T', style: 'flat_vector', library_gaps: [{ kind: 'object', name: 'rickshaw', scenes: ['s1'] }] } })
    expect(normalizeSpec(stripDefaults(gaps)).meta.library_gaps).toEqual([{ kind: 'object', name: 'rickshaw', scenes: ['s1'] }])
  })

  it('keeps an object that stays until the end (no t1) apart from one that is cut at a time', () => {
    const stays = normalizeSpec({ scenes: [{ id: 'x', duration_sec: 3, objects: [{ asset: 'car' }, { asset: 'car', t1: 2 }] }] }).scenes[0].objects
    expect(stays.map((o) => o.t1)).toEqual([null, 2])
    const back = (stripDefaults(normalizeSpec({ scenes: [{ id: 'x', duration_sec: 3, objects: [{ asset: 'car' }, { asset: 'car', t1: 2 }] }] })) as { scenes: { objects: Record<string, unknown>[] }[] }).scenes[0].objects
    expect(back).toEqual([{ asset: 'car' }, { asset: 'car', t1: 2 }])
  })
})

