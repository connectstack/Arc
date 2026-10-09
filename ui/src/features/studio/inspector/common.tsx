import { ChevronRight } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { cn } from '@/lib/cn'

export function Section({ title, children, action, defaultOpen = true, className }: { title: string; children: ReactNode; action?: ReactNode; defaultOpen?: boolean; className?: string }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className={cn('border-b border-line px-4 py-3.5', className)}>
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <button className="eyebrow flex items-center gap-1 hover:text-fg" onClick={() => setOpen(!open)} aria-expanded={open}>
          <ChevronRight className={cn('size-3 transition-transform', open && 'rotate-90')} aria-hidden />
          {title}
        </button>
        {action}
      </div>
      {open && <div className="flex flex-col gap-3">{children}</div>}
    </section>
  )
}

export function Row({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('grid grid-cols-2 gap-2.5', className)}>{children}</div>
}
