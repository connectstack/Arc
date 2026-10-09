import type { DragEvent, ReactNode } from 'react'
import { cn } from '@/lib/cn'

/** What a dragged library item carries (`application/x-reel-item`): the timeline and the stage read it when it is dropped on them. */
export const ITEM_MIME = 'application/x-reel-item'

export function Draggable({ item, children, className, enabled = true }: { item: { kind: string; name: string }; children: ReactNode; className?: string; enabled?: boolean }) {
  if (!enabled) return <div className={className}>{children}</div>
  return (
    <div draggable onDragStart={(e: DragEvent) => (e.dataTransfer.setData(ITEM_MIME, JSON.stringify(item)), (e.dataTransfer.effectAllowed = 'copy'))} className={cn('cursor-grab active:cursor-grabbing', className)}>
      {children}
    </div>
  )
}
