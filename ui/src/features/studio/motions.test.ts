import { describe, expect, it } from 'vitest'
import type { Catalog, ObjectMotion, Scene, SceneObject } from '@/api/types'
import { newObject } from '@/lib/spec'
import { MOTIONS, defaultDestination, motionDef, motionDetail, motionProblems, newMotion, objectMovePaths, readNumber, resolveDestination, retypeMotion, setMotionField, sortMotions } from './motions'

const catalog = { backgrounds: [{ name: 'street', summary: '', slots: { left: [0.27, 0.74], center: [0.5, 0.74], right: [0.73, 0.74] } }] } as unknown as Catalog

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

const motion = (type: string, t0: number, t1: number, extra: Partial<ObjectMotion> = {}): ObjectMotion => ({ type, t0, t1, ease: 'ease_in_out', ...extra })

describe('the motions of an object', () => {
  it('are the eight the engine knows, each with a sentence and the settings it takes', () => {
    expect(MOTIONS.map((m) => m.type)).toEqual(['move', 'hop', 'float', 'spin', 'pulse', 'fade', 'grow', 'shake'])
    for (const m of MOTIONS) {
      expect(m.summary.length).toBeGreaterThan(10)
      expect(m.length).toBeGreaterThan(0)
    }
    expect(motionDef('hop')?.fields.map((f) => (f.kind === 'destination' ? 'to' : f.key))).toEqual(['count', 'amount'])
    expect(motionDef('move')?.fields).toEqual([{ kind: 'destination' }])
    expect(motionDef('levitate')).toBeUndefined()
  })

  it('start where they are asked for, as long as that kind usually lasts, and never outside the scene', () => {
    expect(newMotion('hop', 2, 6)).toEqual({ type: 'hop', t0: 2, t1: 3, ease: 'ease_in_out' })
    // near the end of a scene there is less room: it starts a little earlier and ends with the scene
    const late = newMotion('float', 5.9, 6)
    expect(late.t1).toBe(6)
    expect(late.t0).toBeCloseTo(5.7, 6)
    expect(late.t1 - late.t0).toBeGreaterThan(0.1)
    // a negative time or one past the scene is brought inside
    expect(newMotion('spin', -4, 6).t0).toBe(0)
    expect(newMotion('spin', 99, 6).t1).toBeLessThanOrEqual(6)
    // a scene shorter than the motion shortens it
    expect(newMotion('float', 0, 1.2)).toMatchObject({ t0: 0, t1: 1.2 })
  })

  it('give a new move somewhere to go: a third of the frame towards the emptier side', () => {
    expect(newMotion('move', 0, 6, [0.2, 0.8]).to).toEqual([0.5, 0.8])
    expect(newMotion('move', 0, 6, [0.8, 0.75]).to).toEqual([0.5, 0.75])
    expect(defaultDestination([0.5, 0.8])).toEqual([0.2, 0.8])
    expect(newMotion('hop', 0, 6)).not.toHaveProperty('to')
  })

  it('write a setting only when it differs from the engine’s own default', () => {
    const m = motion('hop', 0, 1)
    setMotionField(m, 'count', 3, 1)
    setMotionField(m, 'amount', 0.5, 0.35)
    expect(m).toMatchObject({ count: 3, amount: 0.5 })
    setMotionField(m, 'amount', 0.35, 0.35) // back to the default: the field goes
    setMotionField(m, 'count', undefined)
    expect(m).toEqual({ type: 'hop', t0: 0, t1: 1, ease: 'ease_in_out' })
    setMotionField(m, 'to', 'right')
    expect(m.to).toBe('right')
  })

  it('read a setting the way the engine does', () => {
    expect(readNumber(motion('hop', 0, 1), 'amount', 0.35)).toBe(0.35)
    expect(readNumber(motion('hop', 0, 1, { amount: 0.6 }), 'amount', 0.35)).toBe(0.6)
    expect(readNumber(motion('fade', 0, 1, { to: 'right' }), 'to', 1)).toBe(1) // not a number: the default
    expect(readNumber(motion('hop', 0, 1, { count: 0 }), 'count', 1)).toBe(0)
  })

  it('forget the settings of the old kind when the kind changes, and a move gets a destination', () => {
    const m = motion('hop', 1, 2, { count: 4, amount: 0.5, from: 0.2 })
    retypeMotion(m, 'move', [0.8, 0.8])
    expect(m).toEqual({ type: 'move', t0: 1, t1: 2, ease: 'ease_in_out', to: [0.5, 0.8] })
    retypeMotion(m, 'spin', [0.8, 0.8])
    expect(m).toEqual({ type: 'spin', t0: 1, t1: 2, ease: 'ease_in_out' })
  })

  it('tell what they do in a few words', () => {
    expect(motionDetail(motion('move', 0, 1, { to: 'right' }))).toBe('to right')
    expect(motionDetail(motion('move', 0, 1, { to: 'far_left' }))).toBe('to far left')
    expect(motionDetail(motion('move', 0, 1, { to: [0.2, 0.756] }))).toBe('to 0.2, 0.76')
    expect(motionDetail(motion('hop', 0, 1))).toBe('')
    expect(motionDetail(motion('hop', 0, 1, { count: 3 }))).toBe('×3')
    expect(motionDetail(motion('spin', 0, 1, { amount: 2 }))).toBe('2 turns')
    expect(motionDetail(motion('spin', 0, 1, { amount: -1 }))).toBe('-1 turn')
    expect(motionDetail(motion('pulse', 0, 1))).toBe('×3')
    expect(motionDetail(motion('fade', 0, 1, { from: 1, to: 0 }))).toBe('1 → 0')
    expect(motionDetail(motion('grow', 0, 1))).toBe('')
  })
})

describe('keeping motions in time order', () => {
  const object = (...motions: ObjectMotion[]): SceneObject => ({ ...newObject('car'), motions })

  it('sorts by start, keeping the order of motions that start together, and says where one went', () => {
    const o = object(motion('hop', 3, 4), motion('spin', 1, 2), motion('float', 1, 3))
    expect(sortMotions(o, 0)).toBe(2)
    expect(o.motions.map((m) => m.type)).toEqual(['spin', 'float', 'hop'])
    expect(sortMotions(o, 1)).toBe(1) // already in order: nothing moves
    expect(sortMotions(o)).toBe(-1)
  })

  it('leaves a list that is in order as the very same list', () => {
    const o = object(motion('hop', 0, 1), motion('spin', 1, 2))
    const before = o.motions
    sortMotions(o, 0)
    expect(o.motions).toBe(before)
  })
})

describe('what is wrong with a motion', () => {
  const sc = scene()
  const texts = (m: ObjectMotion) => motionProblems(m, sc, catalog).map((p) => p.text)

  it('is nothing for a motion that is fine', () => {
    expect(motionProblems(motion('hop', 1, 2), sc, catalog)).toEqual([])
    expect(motionProblems(motion('move', 1, 3, { to: 'right' }), sc, catalog)).toEqual([])
    expect(motionProblems(motion('move', 1, 3, { to: [1.2, 0.8] }), sc, catalog)).toEqual([])
    expect(motionProblems(motion('move', 1, 3, { to: 'far_left' }), sc, catalog)).toEqual([]) // a place every set has
  })

  it('names a motion that ends before it starts, or after the scene', () => {
    expect(motionProblems(motion('hop', 2, 2), sc, catalog)).toEqual([{ tone: 'danger', text: 'It must end after it starts.' }])
    expect(motionProblems(motion('hop', 5, 7), sc, catalog)).toEqual([{ tone: 'warning', text: 'It ends at 7 s, after the scene (6 s): the rest is cut off.' }])
  })

  it('names a move that goes nowhere real', () => {
    expect(texts(motion('move', 1, 2))).toEqual(['A move needs a destination.'])
    expect(texts(motion('move', 1, 2, { to: 3 as unknown as string }))).toEqual(['A move goes to a place or a point, not a number.'])
    expect(texts(motion('move', 1, 2, { to: 'sofa' }))).toEqual(['“sofa” is not a place on the street set.'])
  })

  it('names a motion Reel does not know', () => {
    expect(texts(motion('levitate', 1, 2))[0]).toMatch(/“levitate” is not a motion Reel knows.*move, hop/)
  })
})

describe('where a move takes an object', () => {
  const o = (...motions: ObjectMotion[]): SceneObject => ({ ...newObject('car', [0.2, 0.8]), motions })

  it('reads a destination as a point or as a place of the set', () => {
    expect(resolveDestination([0.3, 0.6], scene(), catalog)).toEqual([0.3, 0.6])
    expect(resolveDestination('right', scene(), catalog)).toEqual([0.73, 0.74])
    expect(resolveDestination('sofa', scene(), catalog)).toBeNull()
    expect(resolveDestination(undefined, scene(), catalog)).toBeNull()
    expect(resolveDestination([1, 'x'], scene(), catalog)).toBeNull()
  })

  it('draws each move from where the last one left the object, and says which is under way', () => {
    const paths = objectMovePaths(scene(), o(motion('move', 1, 2, { to: 'center' }), motion('hop', 2, 3), motion('move', 4, 5, { to: [0.9, 0.8] })), catalog, 1.5)
    expect(paths).toEqual([
      { motion: 0, from: [0.2, 0.8], to: [0.5, 0.74], active: true },
      { motion: 2, from: [0.5, 0.74], to: [0.9, 0.8], active: false },
    ])
  })

  it('skips a move with no usable destination', () => {
    expect(objectMovePaths(scene(), o(motion('move', 1, 2), motion('move', 2, 3, { to: 'sofa' })), catalog, 1)).toEqual([])
  })
})
