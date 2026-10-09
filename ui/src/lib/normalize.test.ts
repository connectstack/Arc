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
