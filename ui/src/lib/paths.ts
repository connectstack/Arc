// Where an action that moves a character takes them, for the dashed arrow on the stage. Positions are screen fractions, like a layer's.
import type { Catalog, Scene } from '@/api/types'
import { entry, layerPoint } from './spec'

export type Point = [number, number]

export interface MovePath {
  /** index of the action in its layer */
  action: number
  from: Point
  to: Point
  /** the playhead is inside the action */
  active: boolean
}

/** A destination written as `[x, y]`, a slot of the set, or another character in the scene; null when it names nothing. */
export function resolveTarget(to: unknown, scene: Scene, catalog: Catalog | undefined): Point | null {
  if (Array.isArray(to) && to.length === 2 && to.every((v) => typeof v === 'number')) return [to[0], to[1]]
  if (typeof to === 'string') {
    const slot = entry(catalog?.backgrounds, scene.background.template)?.slots?.[to]
    if (slot) return [slot[0], slot[1]]
    const other = scene.layers.find((l) => l.character === to)
    if (other) return layerPoint(scene, other, catalog)
  }
  return null
}

const EDGE = 0.1 // how far outside the frame an entrance starts and an exit ends
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)

/**
 * The path of each moving action of a layer, one after the other (each starts where the one before ended). `local` is the
 * playhead in scene time. Actions that stay put (`in_place`, a jump with no drift) have no path.
 */
export function movePaths(scene: Scene, layerIndex: number, catalog: Catalog | undefined, local: number): MovePath[] {
  const layer = scene.layers[layerIndex]
  if (!layer) return []
  const out: MovePath[] = []
  let at = layerPoint(scene, layer, catalog)
  const ordered = layer.actions.map((a, i) => [i, a] as const).sort((x, y) => x[1].t0 - y[1].t0)
  for (const [i, a] of ordered) {
    if (!entry(catalog?.actions, a.name)?.moves_root) continue
    const p = a.params ?? {}
    let from = at
    let to: Point | null = null
    if (a.name === 'enter_from') {
      const dest = resolveTarget(p.to, scene, catalog) ?? at
      const side = p.side === 'left' || p.side === 'right' ? p.side : dest[0] < 0.5 ? 'left' : 'right'
      from = [side === 'left' ? -EDGE : 1 + EDGE, dest[1]]
      to = dest
    } else if (a.name === 'exit_to') {
      const side = p.side === 'left' || p.side === 'right' ? p.side : at[0] < 0.5 ? 'left' : 'right'
      to = [side === 'left' ? -EDGE : 1 + EDGE, at[1]]
    } else if (a.name === 'walk' || a.name === 'run') {
      if (p.in_place) continue
      const dx = num(p.dx)
      const dy = num(p.dy)
      to = resolveTarget(p.to, scene, catalog) ?? (dx || dy ? [at[0] + dx, at[1] + dy] : null)
    } else if (a.name === 'jump') {
      const dx = num(p.dx)
      to = dx ? [at[0] + dx, at[1]] : null
    }
    if (!to) continue
    out.push({ action: i, from, to, active: local >= a.t0 && local <= a.t1 })
    at = to
  }
  return out
}
