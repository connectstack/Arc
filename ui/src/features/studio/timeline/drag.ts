import type { PointerEvent as ReactPointerEvent } from 'react'
import { MIN_CLIP, clamp, ms, snap } from '@/lib/timeline'
import { useProject } from '@/store/project'

export type DragMode = 'move' | 'start' | 'end'

export interface DragContext {
  /** the clip's current scene-local times */
  t0: number
  t1: number
  /** length of the scene the clip lives in (clips never leave it) */
  sceneDuration: number
  /** global start of that scene, to compare with global snap points */
  sceneStart: number
  zoom: number
  /** global times to snap to (clip edges, scene boundaries, the playhead) */
  snapPoints: () => number[]
  apply: (t0: number, t1: number) => void
  /** a point event (sfx) has no length: only `move` applies */
  point?: boolean
  onCommit?: () => void
  /** the pointer went up without having moved (a click, not a drag) */
  onTap?: () => void
}

const GRID = 0.1

/** Drag or resize a clip with the mouse: snaps to 0.1 s, to clip edges and scene boundaries (hold Alt for none). One undo step per drag. */
export function beginClipDrag(e: ReactPointerEvent, mode: DragMode, ctx: DragContext): void {
  if (e.button !== 0) return
  e.preventDefault()
  e.stopPropagation()
  const el = e.currentTarget as HTMLElement
  el.setPointerCapture(e.pointerId)
  const startX = e.clientX
  const { t0, t1 } = ctx
  const len = t1 - t0
  const threshold = Math.max(0.04, 7 / ctx.zoom)
  const st = useProject.getState()
  let moved = false

  const snapLocal = (v: number, alt: boolean): number => {
    if (alt) return v
    const pts = ctx.snapPoints().map((p) => p - ctx.sceneStart)
    const s = snap(v, pts, threshold)
    return s.to !== null ? s.value : Math.round(v / GRID) * GRID
  }

  const move = (ev: PointerEvent) => {
    const dt = (ev.clientX - startX) / ctx.zoom
    if (!moved && Math.abs(ev.clientX - startX) < 3) return
    if (!moved) {
      moved = true
      st.beginGesture()
    }
    let n0 = t0
    let n1 = t1
    if (mode === 'move') {
      let a = clamp(t0 + dt, 0, Math.max(0, ctx.sceneDuration - len))
      if (!ev.altKey && !ctx.point) {
        // snap whichever edge is closer to a snap point
        const sa = snapLocal(a, false)
        const sb = snapLocal(a + len, false) - len
        a = Math.abs(sa - a) <= Math.abs(sb - a) ? sa : sb
      } else if (!ev.altKey) a = snapLocal(a, false)
      a = clamp(a, 0, Math.max(0, ctx.sceneDuration - len))
      n0 = a
      n1 = a + len
    } else if (mode === 'start') {
      n0 = clamp(snapLocal(t0 + dt, ev.altKey), 0, t1 - MIN_CLIP)
    } else {
      n1 = clamp(snapLocal(t1 + dt, ev.altKey), t0 + MIN_CLIP, ctx.sceneDuration)
    }
    ctx.apply(ms(n0), ms(n1))
  }
  const up = () => {
    el.releasePointerCapture?.(e.pointerId)
    el.removeEventListener('pointermove', move)
    el.removeEventListener('pointerup', up)
    el.removeEventListener('pointercancel', up)
    if (moved) {
      st.endGesture()
      ctx.onCommit?.()
    } else ctx.onTap?.()
  }
  el.addEventListener('pointermove', move)
  el.addEventListener('pointerup', up)
  el.addEventListener('pointercancel', up)
}
