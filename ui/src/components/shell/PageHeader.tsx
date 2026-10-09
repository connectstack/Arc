import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export function PageHeader({ title, subtitle, actions, className }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <header className={cn('flex shrink-0 flex-wrap items-end justify-between gap-x-4 gap-y-3 border-b border-line px-4 pb-4 pt-5 sm:px-6', className)}>
      <div className="min-w-0">
        <h1 className="text-[22px] font-semibold leading-tight tracking-tight sm:truncate">{title}</h1>
        {subtitle && <p className="mt-1 text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  )
}

export function Page({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('min-h-0 flex-1 overflow-y-auto', className)}>{children}</div>
}
