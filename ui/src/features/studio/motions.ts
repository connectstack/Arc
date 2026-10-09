// What an object can do over time: the eight motions of the engine (reel/assets/objects.py), what each needs, how a new one starts,
// and where a move goes on the stage. The timeline, the inspector and the stage all read this one table.
import type { Catalog, ObjectMotion, Scene, SceneObject, Vec2 } from '@/api/types'
import { entry, objectPoint } from '@/lib/spec'
import { MIN_CLIP, ms } from '@/lib/timeline'
import { hasPlace } from './objects'

/** One setting of a motion. `fallback` is what the engine uses when the field is left out (so a value equal to it is not written). */
export type MotionField =
  | { kind: 'destination' }
  | { kind: 'number'; key: 'amount' | 'from' | 'to'; label: string; hint?: string; fallback: number; min: number; max: number; step: number; unit?: string }
  | { kind: 'count'; key: 'count'; label: string; hint?: string; fallback: number; min: number; max: number }

export interface MotionDef {
  type: string
  label: string
  /** one line: what it does */
  summary: string
  /** how long a new one lasts, seconds */
  length: number
  fields: MotionField[]
}

export const MOTIONS: MotionDef[] = [
  { type: 'move', label: 'Move', summary: 'Travels from where the object stands to a destination, then stays there.', length: 2, fields: [{ kind: 'destination' }] },
  {
    type: 'hop',
    label: 'Hop',
    summary: 'Jumps up and down, as many times as you say.',
    length: 1,
    fields: [
      { kind: 'count', key: 'count', label: 'Hops', fallback: 1, min: 1, max: 20 },
      { kind: 'number', key: 'amount', label: 'Height', hint: 'of each hop, as a share of the object’s own height', fallback: 0.35, min: 0.05, max: 2, step: 0.05 },
    ],
  },
  { type: 'float', label: 'Float', summary: 'Drifts gently up and down, like a balloon or a boat.', length: 3, fields: [{ kind: 'number', key: 'amount', label: 'Drift', hint: 'how far it rises and sinks, as a share of its height', fallback: 0.05, min: 0, max: 0.5, step: 0.01 }] },
  { type: 'spin', label: 'Spin', summary: 'Turns around and keeps the angle it ends at.', length: 1.5, fields: [{ kind: 'number', key: 'amount', label: 'Full turns', hint: 'a negative number turns it the other way', fallback: 1, min: -8, max: 8, step: 0.25 }] },
  {
    type: 'pulse',
    label: 'Pulse',
    summary: 'Swells and shrinks a few times, like a heartbeat.',
    length: 1.5,
    fields: [
      { kind: 'number', key: 'amount', label: 'Swell', hint: 'how much bigger it gets: 0.1 is 10%', fallback: 0.1, min: 0, max: 1, step: 0.01 },
      { kind: 'count', key: 'count', label: 'Pulses', fallback: 3, min: 1, max: 20 },
    ],
  },
  {
    type: 'fade',
    label: 'Fade',
    summary: 'Changes how see-through it is. By default it fades in.',
    length: 1,
    fields: [
      { kind: 'number', key: 'from', label: 'From opacity', fallback: 0, min: 0, max: 1, step: 0.05 },
      { kind: 'number', key: 'to', label: 'To opacity', fallback: 1, min: 0, max: 1, step: 0.05 },
    ],
  },
  {
    type: 'grow',
    label: 'Grow',
    summary: 'Changes its size. By default it grows from nothing to its own size.',
    length: 1,
    fields: [
      { kind: 'number', key: 'from', label: 'From size', hint: 'times its own size', fallback: 0, min: 0, max: 6, step: 0.05 },
      { kind: 'number', key: 'to', label: 'To size', hint: 'times its own size', fallback: 1, min: 0, max: 6, step: 0.05 },
    ],
  },
  { type: 'shake', label: 'Shake', summary: 'Trembles in place, like an engine or a fright.', length: 0.8, fields: [{ kind: 'number', key: 'amount', label: 'Strength', hint: 'as a share of its height', fallback: 0.02, min: 0, max: 0.3, step: 0.005 }] },
]

export const motionDef = (type: string): MotionDef | undefined => MOTIONS.find((m) => m.type === type)

/** The shortest a new motion is made when little of the scene is left. */
const SHORTEST = 0.3

/** Where a new move goes: a third of the frame towards the emptier side of the object. */
export function defaultDestination(standing: Vec2): Vec2 {
  const dx = standing[0] < 0.5 ? 0.3 : -0.3
  return [ms(standing[0] + dx), ms(standing[1])]
}

/** A motion of `type` starting at `t0` (scene seconds), as long as that kind usually is and inside the scene; `standing` is where the object is, for a move's destination. */
export function newMotion(type: string, t0: number, sceneDuration: number, standing: Vec2 = [0.5, 0.8]): ObjectMotion {
  const len = motionDef(type)?.length ?? 1.5
  const start = ms(Math.max(0, Math.min(t0, sceneDuration - SHORTEST)))
  const m: ObjectMotion = { type, t0: start, t1: ms(Math.max(start + MIN_CLIP, Math.min(sceneDuration, start + len))), ease: 'ease_in_out' }
  if (type === 'move') m.to = defaultDestination(standing)
  return m
}

/** Set one field of a motion; a value equal to the engine's own default (or none) is removed, so specs stay small. */
export function setMotionField(m: ObjectMotion, key: 'from' | 'to' | 'amount' | 'count', value: number | string | Vec2 | null | undefined, fallback?: number): void {
  const bag = m as unknown as Record<string, unknown>
  if (value === undefined || value === null || (fallback !== undefined && value === fallback)) delete bag[key]
  else bag[key] = value
}

/** Change a motion's kind: what only the old kind used is dropped, and a move gets a destination. */
export function retypeMotion(m: ObjectMotion, type: string, standing: Vec2): void {
  const bag = m as unknown as Record<string, unknown>
  m.type = type
  for (const k of ['from', 'to', 'amount', 'count']) delete bag[k]
  if (type === 'move') m.to = defaultDestination(standing)
}

const fmt = (v: number): string => String(Math.round(v * 100) / 100)

/** A number setting of a motion as the engine reads it: the value, or the default when it is left out (or is not a number). */
export function readNumber(m: ObjectMotion, key: 'amount' | 'from' | 'to' | 'count', fallback: number): number {
  const v = (m as unknown as Record<string, unknown>)[key]
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback
}

/**
 * Put an object's motions in time order (a stable sort by start). The engine applies them in list order, one on top of the other, so a
 * move that starts later has to come later in the list or an earlier move would pull the object back. Returns where the motion that was at
 * `index` is now (-1 without an `index`).
 */
export function sortMotions(o: SceneObject, index?: number): number {
  const order = o.motions.map((m, i) => ({ m, i })).sort((a, b) => a.m.t0 - b.m.t0 || a.i - b.i)
  if (order.some((x, k) => x.i !== k)) o.motions = order.map((x) => x.m)
  return index === undefined ? -1 : order.findIndex((x) => x.i === index)
}

/** Something wrong with an object or one of its motions, in the words of the linter (`reel lint` finds the same). */
export interface Problem {
  tone: 'danger' | 'warning'
  text: string
}

/** What is wrong with one motion: it must end after it starts, stay inside the scene, and a move needs somewhere real to go. */
export function motionProblems(m: ObjectMotion, scene: Scene, catalog: Catalog | undefined): Problem[] {
  const out: Problem[] = []
  if (!motionDef(m.type)) out.push({ tone: 'danger', text: `“${m.type}” is not a motion Reel knows. Choose one of: ${MOTIONS.map((x) => x.type).join(', ')}.` })
  if (m.t1 <= m.t0) out.push({ tone: 'danger', text: 'It must end after it starts.' })
  else if (m.t1 > scene.duration_sec + 1e-6) out.push({ tone: 'warning', text: `It ends at ${fmt(m.t1)} s, after the scene (${fmt(scene.duration_sec)} s): the rest is cut off.` })
  if (m.type === 'move') {
    if (m.to === undefined || m.to === null) out.push({ tone: 'danger', text: 'A move needs a destination.' })
    else if (typeof m.to === 'number') out.push({ tone: 'danger', text: 'A move goes to a place or a point, not a number.' })
    else if (typeof m.to === 'string' && !hasPlace(scene, catalog, m.to)) out.push({ tone: 'danger', text: `“${m.to}” is not a place on the ${scene.background.template} set.` })
  }
  return out
}

/** A few words about a motion's settings, for the timeline and the inspector's list ("to right", "×3", "0 → 1"). */
export function motionDetail(m: ObjectMotion): string {
  switch (m.type) {
    case 'move':
      return typeof m.to === 'string' ? `to ${m.to.replace(/_/g, ' ')}` : Array.isArray(m.to) ? `to ${fmt(m.to[0])}, ${fmt(m.to[1])}` : ''
    case 'hop':
      return m.count && m.count > 1 ? `×${m.count}` : ''
    case 'spin':
      return m.amount !== undefined && m.amount !== null && m.amount !== 1 ? `${fmt(m.amount)} turn${Math.abs(m.amount) === 1 ? '' : 's'}` : ''
    case 'pulse':
      return `×${m.count ?? 3}`
    case 'fade':
    case 'grow':
      return typeof m.from === 'number' || typeof m.to === 'number' ? `${fmt(typeof m.from === 'number' ? m.from : 0)} → ${fmt(typeof m.to === 'number' ? m.to : 1)}` : ''
    default:
      return ''
  }
}

// ------------------------------------------------------------------------------- where a move goes
export interface ObjectMovePath {
  /** index of the motion in the object's list */
  motion: number
  from: Vec2
  to: Vec2
  /** the playhead is inside the motion */
  active: boolean
}

/** A destination written as `[x, y]` or the name of one of the set's places; null when it names nothing. */
export function resolveDestination(to: unknown, scene: Scene, catalog: Catalog | undefined): Vec2 | null {
  if (Array.isArray(to) && to.length === 2 && to.every((v) => typeof v === 'number' && Number.isFinite(v))) return [to[0], to[1]]
  if (typeof to === 'string') {
    const slot = entry(catalog?.backgrounds, scene.background.template)?.slots?.[to]
    if (slot) return [slot[0], slot[1]]
  }
  return null
}

/** The path of each move of an object, one after the other as the engine applies them (each starts where the one before ended). `local` is the playhead in scene time. */
export function objectMovePaths(scene: Scene, object: SceneObject, catalog: Catalog | undefined, local: number): ObjectMovePath[] {
  const out: ObjectMovePath[] = []
  let at = objectPoint(scene, object, catalog)
  object.motions.forEach((m, i) => {
    if (m.type !== 'move') return
    const to = resolveDestination(m.to, scene, catalog)
    if (!to) return
    out.push({ motion: i, from: at, to, active: local >= m.t0 && local <= m.t1 })
    at = to
  })
  return out
}
