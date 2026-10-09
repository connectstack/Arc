// Pure timeline math, mirroring reel/core/timeline.py: scene i+1 starts `overlap_i` before scene i ends,
// so the reel's length is sum(durations) - sum(overlaps) and that is what the 45-60 s budget is checked against.
import type { ReelSpec, Scene } from '@/api/types'

export interface Slot {
  index: number
  id: string
  /** global start, seconds */
  start: number
  duration: number
  end: number
  /** seconds this scene's transition overlaps the next scene */
  overlap: number
}

export const MIN_CLIP = 0.1

export function overlapSeconds(scene: Scene, next: Scene | undefined): number {
  const t = scene.transition_out
  if (!next || !t || t.type === 'cut' || !(t.duration > 0)) return 0
  return Math.min(t.duration, scene.duration_sec, next.duration_sec)
}

export function sceneSlots(spec: Pick<ReelSpec, 'scenes'>): Slot[] {
  const out: Slot[] = []
  let start = 0
  spec.scenes.forEach((s, i) => {
    const overlap = overlapSeconds(s, spec.scenes[i + 1])
    out.push({ index: i, id: s.id, start, duration: s.duration_sec, end: start + s.duration_sec, overlap })
    start += s.duration_sec - overlap
  })
  return out
}

export function totalDuration(spec: Pick<ReelSpec, 'scenes'>): number {
  const slots = sceneSlots(spec)
  return slots.length ? slots[slots.length - 1].end : 0
}

/** The scene on top at global time `t`: the incoming scene as soon as its overlap begins. */
export function sceneAt(slots: Slot[], t: number): Slot | undefined {
  if (!slots.length) return undefined
  for (let i = 0; i < slots.length; i++) {
    const s = slots[i]
    const handoverAt = s.end - s.overlap
    if (t < handoverAt || i === slots.length - 1) return s
  }
  return slots[slots.length - 1]
}

export const toGlobal = (slot: Slot, local: number): number => slot.start + local
export const toLocal = (slot: Slot, global: number): number => global - slot.start

export const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v))

/** Round to a millisecond: spec times never carry float noise. */
export const ms = (v: number): number => Math.round(v * 1000) / 1000

export interface Snap {
  value: number
  /** the point it snapped to, or null */
  to: number | null
}

/** Snap `v` to the nearest of `points` within `threshold` seconds. */
export function snap(v: number, points: number[], threshold: number): Snap {
  let best: number | null = null
  let bestD = threshold
  for (const p of points) {
    const d = Math.abs(p - v)
    if (d <= bestD) {
      best = p
      bestD = d
    }
  }
  return best === null ? { value: v, to: null } : { value: best, to: best }
}

/** Row index for each interval so that overlapping ones never share a row (greedy, in start order). */
export function packRows(items: { t0: number; t1: number }[]): number[] {
  const order = items.map((_, i) => i).sort((a, b) => items[a].t0 - items[b].t0 || items[a].t1 - items[b].t1)
  const rowEnds: number[] = []
  const rows = new Array<number>(items.length).fill(0)
  for (const i of order) {
    let r = rowEnds.findIndex((end) => end <= items[i].t0 + 1e-6)
    if (r < 0) {
      r = rowEnds.length
      rowEnds.push(0)
    }
    rowEnds[r] = items[i].t1
    rows[i] = r
  }
  return rows
}

/** The overall time range a clip may occupy inside its scene. */
export function clipBounds(scene: Scene): [number, number] {
  return [0, scene.duration_sec]
}

/** `00:12.40` (minutes:seconds.centiseconds). */
export function timecode(sec: number): string {
  const s = Math.max(0, sec)
  const m = Math.floor(s / 60)
  const rest = s - m * 60
  return `${String(m).padStart(2, '0')}:${rest.toFixed(2).padStart(5, '0')}`
}

export function frameOf(sec: number, fps: number): number {
  return Math.round(sec * fps)
}

/** The scale factor that makes a spec's total `target` seconds long when every scene is scaled by it. */
function solveScale(durations: number[], transitions: Scene['transition_out'][], target: number, maxScene: number): number {
  const total = (k: number): number => {
    const d = durations.map((x) => Math.min(maxScene, Math.max(0.5, x * k)))
    let sum = d.reduce((a, b) => a + b, 0)
    for (let i = 0; i < d.length - 1; i++) {
      const t = transitions[i]
      if (t && t.type !== 'cut' && t.duration > 0) sum -= Math.min(t.duration, d[i], d[i + 1])
    }
    return sum
  }
  let lo = 0.05
  let hi = 20
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2
    if (total(mid) < target) lo = mid
    else hi = mid
  }
  return (lo + hi) / 2
}

/**
 * "Fit to N s": scales every scene's duration (and everything timed inside it) so the reel is `target` long.
 * Relative timing inside a scene is kept; a scene never goes over `maxScene` or under half a second.
 */
export function fitToDuration(spec: ReelSpec, target: number, maxScene = 30): ReelSpec {
  const k = solveScale(
    spec.scenes.map((s) => s.duration_sec),
    spec.scenes.map((s) => s.transition_out),
    target,
    maxScene,
  )
  const next: ReelSpec = structuredClone(spec)
  for (const sc of next.scenes) {
    const old = sc.duration_sec
    const dur = ms(Math.min(maxScene, Math.max(0.5, old * k)))
    const f = old > 0 ? dur / old : 1
    sc.duration_sec = dur
    for (const layer of sc.layers) for (const a of layer.actions) ((a.t0 = ms(a.t0 * f)), (a.t1 = ms(a.t1 * f)))
    for (const c of sc.captions) ((c.t0 = ms(c.t0 * f)), (c.t1 = ms(c.t1 * f)))
    for (const m of sc.camera.moves) ((m.t0 = ms(m.t0 * f)), (m.t1 = ms(m.t1 * f)))
    for (const x of sc.sfx) x.t = ms(x.t * f)
  }
  return next
}

/** Budget state for the duration bar: where the total sits relative to the allowed range. */
export type Budget = 'ok' | 'near' | 'out'

export function budgetState(total: number, min = 45, max = 60, margin = 3): Budget {
  if (total < min || total > max) return 'out'
  if (total < min + margin || total > max - margin) return 'near'
  return 'ok'
}
