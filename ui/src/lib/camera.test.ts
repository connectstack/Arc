import { describe, expect, it } from 'vitest'
import { clampMoveValues } from './camera'

describe('keeping a camera move on the set', () => {
  it('reads a pan written as screen positions as a gentle drift', () => {
    const m: Record<string, unknown> = { type: 'pan', from: [0.2, 0.5], to: [0.8, 0.5] }
    expect(clampMoveValues(m)).toHaveLength(1)
    expect(m.from).toEqual([-0.15, 0])
    expect(m.to).toEqual([0.15, 0])
    expect(clampMoveValues(m)).toEqual([]) // idempotent
  })

  it('leaves a legitimate drift alone and reins in the rest', () => {
    const fine = { type: 'pan', from: [0, 0], to: [0.06, 0] }
    expect(clampMoveValues(fine)).toEqual([])
    const wide: Record<string, unknown> = { type: 'pan', from: [0.12, 0], to: [0.9, 0] } // a zero among the numbers: an offset
    expect(clampMoveValues(wide)).not.toEqual([])
    expect(wide.to).toEqual([0.25, 0])
    const zoom: Record<string, unknown> = { type: 'zoom', from: 0.2, to: 7 }
    clampMoveValues(zoom)
    expect(zoom).toEqual({ type: 'zoom', from: 0.85, to: 2.5 })
    const junk: Record<string, unknown> = { type: 'dolly', from: 'far', to: 0.5 }
    clampMoveValues(junk)
    expect('from' in junk).toBe(false)
    expect(clampMoveValues({ type: 'rack_focus', to: 'foreground' })).toEqual([])
  })
})
