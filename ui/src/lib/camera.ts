// What a camera move may ask for: the set is drawn only so far beyond the frame, so a move that asks for more shows an
// empty band. This mirrors reel/core/camera.py (clamp_move_values); the engine clamps in the renderer as well, so a spec
// can never break a frame, but the spec itself should say something sensible.

/** |x| and |y| of a pan, in frame fractions (a drift is 0.02 .. 0.15 in x and 0 .. 0.05 in y) */
export const PAN_LIMIT: readonly [number, number] = [0.25, 0.1]
export const ZOOM_RANGE: readonly [number, number] = [0.85, 2.5]
export const DOLLY_RANGE: readonly [number, number] = [-0.3, 1.5]
export const SHAKE_RANGE: readonly [number, number] = [0, 2]

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))
const r4 = (v: number) => Math.round(v * 1e4) / 1e4
const vec = (v: unknown): v is [number, number] => Array.isArray(v) && v.length === 2 && v.every(num)

/** A pan written as where the camera should look (every number well inside 0..1) rather than how far it drifts. */
function looksLikeScreenPositions(vs: [number, number][]): boolean {
  const flat = vs.flat()
  return flat.length > 0 && flat.every((x) => x >= 0.15 && x <= 0.95)
}

/**
 * Brings the `from` / `to` of a camera move inside what the set allows; returns what it did (empty: nothing was wrong).
 * A pan written as screen positions (`[0.8, 0.5]`) is read as a gentle drift from the centre (half the distance), then every
 * number is kept inside the limit. Values that are not numbers are dropped.
 */
export function clampMoveValues(move: Record<string, unknown>): string[] {
  const kind = move.type
  const done: string[] = []
  if (kind === 'pan') {
    const vs = [move.from, move.to].filter(vec)
    const beyond = vs.some((v) => Math.abs(v[0]) > PAN_LIMIT[0] || Math.abs(v[1]) > PAN_LIMIT[1])
    if (beyond && looksLikeScreenPositions(vs)) {
      for (const k of ['from', 'to']) {
        const v = move[k]
        if (vec(v)) move[k] = [r4((v[0] - 0.5) * 0.5), r4((v[1] - 0.5) * 0.5)]
      }
      done.push('read a pan written as screen positions as a gentle drift from the centre')
    }
  }
  for (const k of ['from', 'to'] as const) {
    const v = move[k]
    if (v === undefined || v === null) continue
    let next: unknown
    if (kind === 'pan') next = vec(v) ? [r4(clamp(v[0], -PAN_LIMIT[0], PAN_LIMIT[0])), r4(clamp(v[1], -PAN_LIMIT[1], PAN_LIMIT[1]))] : num(v) ? [r4(clamp(v, -PAN_LIMIT[0], PAN_LIMIT[0])), 0] : null
    else if (kind === 'zoom' || kind === 'dolly' || kind === 'shake') {
      const [lo, hi] = kind === 'zoom' ? ZOOM_RANGE : kind === 'dolly' ? DOLLY_RANGE : SHAKE_RANGE
      next = num(v) ? r4(clamp(v, lo, hi)) : null
    } else continue
    if (JSON.stringify(next) !== JSON.stringify(v)) {
      done.push(`kept the camera ${String(kind)} inside the range the set allows`)
      if (next === null) delete move[k]
      else move[k] = next
    }
  }
  return [...new Set(done)]
}
