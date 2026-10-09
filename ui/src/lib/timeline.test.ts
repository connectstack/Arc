import { describe, expect, it } from 'vitest'
import type { ReelSpec, Scene } from '@/api/types'
import { budgetState, fitToDuration, overlapSeconds, packRows, sceneAt, sceneSlots, snap, timecode, totalDuration } from './timeline'

const scene = (id: string, dur: number, tr?: { type: string; duration: number }): Scene => ({
  id,
  duration_sec: dur,
  background: { template: 'abstract', params: {} },
  camera: { moves: [{ type: 'pan', from: [0, 0], to: [0.1, 0], t0: 0, t1: dur, ease: 'linear', params: {} }] },
  layers: [{ character: 'a', position: 'center', scale: 1, depth: 'mid', facing: 'auto', actions: [{ name: 'idle', t0: 0, t1: dur, params: {} }] }],
  captions: [{ text: 'hi', t0: 1, t1: 2, style: 'subtitle', anchor: 'auto' }],
  sfx: [{ name: 'pop', t: 1, volume: 1 }],
  transition_out: { type: tr?.type ?? 'cut', duration: tr?.duration ?? 0, params: {} },
})

const spec = (scenes: Scene[]): ReelSpec => ({
  version: '1.0',
  meta: { title: 't', style: 'flat_vector', fps: 30, resolution: [1080, 1920], seed: 1, target_duration_sec: 50 },
  characters: [],
  scenes,
  audio: { music: null, voiceover: 'none', ducking: true, music_gain_db: -16, voice_gain_db: 0, sfx_gain_db: -8, auto_sfx: false },
})

describe('scene slots', () => {
  it('overlaps a transition with the next scene, like the engine', () => {
    const s = spec([scene('a', 10, { type: 'crossfade', duration: 1 }), scene('b', 10, { type: 'wipe', duration: 2 }), scene('c', 10)])
    const slots = sceneSlots(s)
    expect(slots.map((x) => x.start)).toEqual([0, 9, 17])
    expect(slots.map((x) => x.overlap)).toEqual([1, 2, 0])
    expect(totalDuration(s)).toBe(27) // 30 - 1 - 2
  })

  it('ignores cuts, zero durations and the last scene, and never overlaps more than a scene is long', () => {
    expect(overlapSeconds(scene('a', 10, { type: 'cut', duration: 2 }), scene('b', 10))).toBe(0)
    expect(overlapSeconds(scene('a', 10, { type: 'wipe', duration: 0 }), scene('b', 10))).toBe(0)
    expect(overlapSeconds(scene('a', 10, { type: 'wipe', duration: 2 }), undefined)).toBe(0)
    expect(overlapSeconds(scene('a', 10, { type: 'wipe', duration: 5 }), scene('b', 3))).toBe(3)
  })

  it('shows the incoming scene once its overlap begins', () => {
    const slots = sceneSlots(spec([scene('a', 10, { type: 'crossfade', duration: 2 }), scene('b', 10)]))
    expect(sceneAt(slots, 0)?.id).toBe('a')
    expect(sceneAt(slots, 7.9)?.id).toBe('a')
    expect(sceneAt(slots, 8.1)?.id).toBe('b')
    expect(sceneAt(slots, 99)?.id).toBe('b')
    expect(sceneAt([], 1)).toBeUndefined()
  })
})

describe('fit to duration', () => {
  it('scales scenes and everything timed inside them so the total hits the target', () => {
    const s = spec([scene('a', 20, { type: 'crossfade', duration: 1 }), scene('b', 20), scene('c', 20)])
    expect(totalDuration(s)).toBe(59)
    const fitted = fitToDuration(s, 50)
    expect(totalDuration(fitted)).toBeCloseTo(50, 1)
    const a = fitted.scenes[0]
    const f = a.duration_sec / 20
    expect(a.captions[0].t0).toBeCloseTo(1 * f, 2)
    expect(a.layers[0].actions[0].t1).toBeCloseTo(a.duration_sec, 2)
    expect(a.camera.moves[0].t1).toBeCloseTo(a.duration_sec, 2)
    expect(s.scenes[0].duration_sec).toBe(20) // the input is untouched
  })

  it('stretches short reels up to the target and respects the per-scene maximum', () => {
    const short = spec([scene('a', 5), scene('b', 5)])
    expect(totalDuration(fitToDuration(short, 50))).toBeCloseTo(50, 1)
    const capped = fitToDuration(spec([scene('a', 5), scene('b', 5)]), 100, 30)
    expect(capped.scenes.every((x) => x.duration_sec <= 30)).toBe(true)
  })
})

describe('helpers', () => {
  it('snaps within a threshold and reports what it snapped to', () => {
    expect(snap(2.04, [0, 2, 5], 0.1)).toEqual({ value: 2, to: 2 })
    expect(snap(3, [0, 2, 5], 0.1)).toEqual({ value: 3, to: null })
    expect(snap(4.95, [4.9, 5], 0.2).to).toBe(5)
  })

  it('packs overlapping clips into rows', () => {
    expect(packRows([{ t0: 0, t1: 2 }, { t0: 1, t1: 3 }, { t0: 2, t1: 4 }])).toEqual([0, 1, 0])
    expect(packRows([])).toEqual([])
  })

  it('formats timecodes and budget states', () => {
    expect(timecode(12.4)).toBe('00:12.40')
    expect(timecode(75.006)).toBe('01:15.01')
    expect(timecode(-3)).toBe('00:00.00')
    expect(budgetState(50)).toBe('ok')
    expect(budgetState(46)).toBe('near')
    expect(budgetState(58.5)).toBe('near')
    expect(budgetState(44.9)).toBe('out')
    expect(budgetState(61)).toBe('out')
  })
})
