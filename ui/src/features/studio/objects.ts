// Facts about the objects of a scene that the outline, the inspector, the timeline and the stage all tell: when one is on screen, where it
// stands in words, and what is wrong with it. (The motions' own table is in motions.ts.)
import type { Catalog, Scene, SceneObject } from '@/api/types'
import { UNIVERSAL_SLOTS, entry, objectEntry } from '@/lib/spec'
import type { Problem } from './motions'

const round2 = (v: number): number => Math.round(v * 100) / 100

/** Does the object stay until the end of its scene (no `t1`)? */
export const staysToEnd = (o: SceneObject): boolean => o.t1 === null || o.t1 === undefined

/** When an object is on screen, in scene seconds: from `t0` until `t1` (the end of the scene when it stays). */
export function objectSpan(scene: Scene, o: SceneObject): [number, number] {
  return [o.t0, o.t1 ?? scene.duration_sec]
}

/** Is the object drawn at scene time `local`? (The engine's own test.) */
export function visibleAt(o: SceneObject, local: number): boolean {
  return local >= o.t0 && (o.t1 === null || o.t1 === undefined || local < o.t1)
}

/** `traffic_light` -> `traffic light` (an object with no asset yet is "unnamed object") */
export const objectName = (asset: string): string => asset.replace(/_/g, ' ') || 'unnamed object'

/** Where an object stands, in words: the name of its place, or its point as screen fractions. */
export function positionText(o: SceneObject): string {
  return typeof o.position === 'string' ? o.position.replace(/_/g, ' ') : `${round2(o.position[0])}, ${round2(o.position[1])}`
}

/** When it is there: "the whole scene", "from 1.5 s", "1.5 to 4 s". */
export function whenText(scene: Scene, o: SceneObject): string {
  const [t0, t1] = objectSpan(scene, o)
  if (t0 <= 0 && staysToEnd(o)) return 'the whole scene'
  if (staysToEnd(o)) return `from ${round2(t0)} s`
  return `${round2(t0)} to ${round2(t1)} s`
}

/** Is `name` a place the scene's set has? Unknown (so fine) when the catalog does not describe the set. */
export function hasPlace(scene: Scene, catalog: Catalog | undefined, name: string): boolean {
  const slots = entry(catalog?.backgrounds, scene.background.template)?.slots
  return !slots || name in slots || UNIVERSAL_SLOTS.includes(name)
}

/** What is wrong with an object itself (its motions are checked by `motionProblems`): the checks of the engine's linter. */
export function objectProblems(scene: Scene, o: SceneObject, catalog: Catalog | undefined): Problem[] {
  const out: Problem[] = []
  // a catalog with no objects at all is not loaded (or is a demo's): nothing can be said about the name
  if ((catalog?.objects?.length ?? 0) > 0 && !objectEntry(catalog, o.asset)) out.push({ tone: 'warning', text: `“${o.asset}” is not in the asset library: a lenient render leaves it out and a strict one stops.` })
  if (typeof o.position === 'string' && !hasPlace(scene, catalog, o.position)) out.push({ tone: 'danger', text: `“${o.position}” is not a place on the ${scene.background.template} set.` })
  if (o.t1 !== null && o.t1 !== undefined && o.t1 <= o.t0) out.push({ tone: 'danger', text: 'It has to stay until after the moment it appears.' })
  else if (o.t0 >= scene.duration_sec - 1e-6) out.push({ tone: 'warning', text: `It appears at ${round2(o.t0)} s, at or after the end of the scene (${round2(scene.duration_sec)} s), so it is never seen.` })
  return out
}
