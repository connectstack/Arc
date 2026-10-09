import { cva, type VariantProps } from 'class-variance-authority'
import { AlertTriangle, CheckCircle2, Info, Loader2, XCircle } from 'lucide-react'
import type { HTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/cn'

const chip = cva('inline-flex h-[22px] items-center gap-1 whitespace-nowrap rounded-chip px-2 text-[11.5px] font-medium', {
  variants: {
    tone: {
      neutral: 'bg-hover text-muted',
      accent: 'bg-accent-soft text-accent',
      success: 'bg-success/14 text-success',
      warning: 'bg-warning/16 text-warning',
      danger: 'bg-danger/14 text-danger',
      info: 'bg-info/14 text-info',
    },
  },
  defaultVariants: { tone: 'neutral' },
})

export function Chip({ tone, className, children, ...props }: HTMLAttributes<HTMLSpanElement> & VariantProps<typeof chip>) {
  return (
    <span className={cn(chip({ tone }), className)} {...props}>
      {children}
    </span>
  )
}

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'info'

const ICON = { success: CheckCircle2, warning: AlertTriangle, danger: XCircle, info: Info, accent: Info, neutral: Info } as const

/** A status that is never conveyed by colour alone: icon + text. */
export function StatusChip({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  const Icon = ICON[tone]
  return (
    <Chip tone={tone}>
      <Icon className="size-3" aria-hidden />
      {children}
    </Chip>
  )
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn('size-4 animate-[spin_0.9s_linear_infinite]', className)} aria-label="Loading" />
}

export function ProgressBar({ value, max = 1, tone = 'accent', className, label }: { value: number; max?: number; tone?: 'accent' | 'success' | 'danger'; className?: string; label?: string }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  return (
    <div role="progressbar" aria-valuemin={0} aria-valuemax={max} aria-valuenow={Math.round(value)} aria-label={label} className={cn('h-1.5 w-full overflow-hidden rounded-full bg-hover', className)}>
      <div className={cn('h-full rounded-full transition-[width] duration-150', tone === 'accent' ? 'bg-accent' : tone === 'success' ? 'bg-success' : 'bg-danger')} style={{ width: `${pct}%` }} />
    </div>
  )
}

/** A static placeholder (no shimmer: nothing on screen moves unless it is doing something). */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('rounded-ctl bg-hover/70', className)} aria-hidden />
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-line bg-raised px-1.5 py-px font-mono text-[10.5px] text-muted">{children}</kbd>
}

export function Banner({ tone = 'info', title, children, action }: { tone?: 'info' | 'warning' | 'danger' | 'success'; title?: ReactNode; children?: ReactNode; action?: ReactNode }) {
  const Icon = ICON[tone]
  const color = { info: 'text-info', warning: 'text-warning', danger: 'text-danger', success: 'text-success' }[tone]
  return (
    <div role={tone === 'danger' ? 'alert' : 'status'} className="flex items-start gap-3 rounded-card border border-line bg-raised p-3">
      <Icon className={cn('mt-0.5 size-4', color)} aria-hidden />
      <div className="min-w-0 flex-1">
        {title && <div className="font-medium text-fg">{title}</div>}
        {children && <div className="text-muted">{children}</div>}
      </div>
      {action}
    </div>
  )
}

export function EmptyState({ art, title, children, action, className }: { art?: ReactNode; title: string; children?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-3 px-6 py-12 text-center', className)}>
      {art}
      <div className="text-[15px] font-semibold text-fg">{title}</div>
      {children && <p className="max-w-sm text-muted">{children}</p>}
      {action}
    </div>
  )
}
