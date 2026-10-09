import { describe, expect, it } from 'vitest'
import type { Catalog, Scene } from '@/api/types'
import { movePaths, resolveTarget } from './paths'

const catalog = {
  actions: ['walk', 'run', 'jump', 'enter_from', 'exit_to'].map((name) => ({ name, summary: '', moves_root: true })).concat([{ name: 'talk', summary: '', moves_root: false }]),
  backgrounds: [{ name: 'street', summary: '', slots: { left: [0.25, 0.74], center: [0.5, 0.74], right: [0.75, 0.74] }, ground_y: 0.74 }],
  archetypes: [],
} as unknown as Catalog

const scene = (actions: Scene['layers'][0]['actions']): Scene => ({
  id: 's',
  duration_sec: 10,
  background: { template: 'street', params: {} },
  camera: { moves: [] },
  layers: [
    { character: 'mia', position: 'left', scale: 1, depth: 'mid', facing: 'auto', actions },
    { character: 'pip', position: [0.8, 0.7], scale: 1, depth: 'mid', facing: 'auto', actions: [] },
  ],
  captions: [],
  sfx: [],
  transition_out: { type: 'cut', duration: 0, params: {} },
})
const act = (name: string, t0: number, t1: number, params: Record<string, unknown> = {}) => ({ name, t0, t1, params })

describe('where a moving action takes a character', () => {
  it('resolves a destination written as a point, a slot or another character', () => {
    const sc = scene([])
    expect(resolveTarget([0.3, 0.6], sc, catalog)).toEqual([0.3, 0.6])
    expect(resolveTarget('right', sc, catalog)).toEqual([0.75, 0.74])
    expect(resolveTarget('pip', sc, catalog)).toEqual([0.8, 0.7])
    expect(resolveTarget('nowhere', sc, catalog)).toBeNull()
    expect(resolveTarget(undefined, sc, catalog)).toBeNull()
    expect(resolveTarget([1, 'x'], sc, catalog)).toBeNull()
  })

  it('draws a walk from where the character is to where they are going, and the next move starts there', () => {
    const sc = scene([act('walk', 1, 3, { to: 'center' }), act('talk', 3, 4), act('run', 5, 6, { to: [0.9, 0.7] })])
    const paths = movePaths(sc, 0, catalog, 2)
    expect(paths).toHaveLength(2) // talk has no path
    expect(paths[0]).toMatchObject({ action: 0, from: [0.25, 0.74], to: [0.5, 0.74], active: true })
    expect(paths[1]).toMatchObject({ action: 2, from: [0.5, 0.74], to: [0.9, 0.7], active: false })
  })

  it('reads an offset (dx, dy) and skips a walk on the spot', () => {
    const sc = scene([act('walk', 0, 1, { dx: 0.2 }), act('walk', 1, 2, { in_place: true, to: 'right' }), act('walk', 2, 3)])
    const paths = movePaths(sc, 0, catalog, 0.5)
    expect(paths).toHaveLength(1)
    expect(paths[0].from).toEqual([0.25, 0.74])
    expect(paths[0].to[0]).toBeCloseTo(0.45)
  })

  it('draws an entrance from the edge it comes in by, and an exit to the nearest edge', () => {
    const sc = scene([act('enter_from', 0, 1, { side: 'left' }), act('exit_to', 8, 9)])
    const [enter, exit] = movePaths(sc, 0, catalog, 0.5)
    expect(enter.from[0]).toBeLessThan(0)
    expect(enter.to).toEqual([0.25, 0.74]) // the layer's own position
    expect(exit.from).toEqual([0.25, 0.74])
    expect(exit.to[0]).toBeLessThan(0) // left is nearest
    const right = scene([act('enter_from', 0, 1, { to: 'right' })])
    expect(movePaths(right, 0, catalog, 0)[0].from[0]).toBeGreaterThan(1) // "auto" comes in from the nearest edge
  })

  it('draws a jump only when it drifts', () => {
    const sc = scene([act('jump', 0, 1), act('jump', 2, 3, { dx: 0.1 })])
    const paths = movePaths(sc, 0, catalog, 2.5)
    expect(paths).toHaveLength(1)
    expect(paths[0]).toMatchObject({ action: 1, active: true })
    expect(movePaths(sc, 5, catalog, 0)).toEqual([]) // no such layer
  })
})
