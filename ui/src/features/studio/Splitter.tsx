import type { PointerEvent } from 'react'
import { cn } from '@/lib/cn'

/** A draggable divider. `dir` is the direction it resizes in: 'x' (a vertical bar) or 'y' (a horizontal bar). */
export function Splitter({ dir, onDrag, label, value, min, max }: { dir: 'x' | 'y'; onDrag: (delta: number) => void; label: string; value: number; min: number; max: number }) {
  const start = (e: PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    const el = e.currentTarget
    el.setPointerCapture(e.pointerId)
    let last = dir === 'x' ? e.clientX : e.clientY
    const move = (ev: globalThis.PointerEvent) => {
      const cur = dir === 'x' ? ev.clientX : ev.clientY
      onDrag(cur - last)
      last = cur
    }
    const up = () => {
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerup', up)
    }
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerup', up)
  }
  return (
    <div
      role="separator"
      aria-orientation={dir === 'x' ? 'vertical' : 'horizontal'}
      aria-label={label}
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      onPointerDown={start}
      onKeyDown={(e) => {
        const k = dir === 'x' ? ['ArrowLeft', 'ArrowRight'] : ['ArrowUp', 'ArrowDown']
        if (e.key === k[0]) (e.preventDefault(), onDrag(-16))
        if (e.key === k[1]) (e.preventDefault(), onDrag(16))
      }}
      className={cn('group relative z-10 shrink-0 bg-line outline-none transition-colors hover:bg-accent focus-visible:bg-accent', dir === 'x' ? 'w-px cursor-col-resize' : 'h-px cursor-row-resize')}
    >
      <span className={cn('absolute', dir === 'x' ? '-inset-x-1.5 inset-y-0' : '-inset-y-1.5 inset-x-0')} aria-hidden />
    </div>
  )
}
