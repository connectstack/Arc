import { CheckCircle2, Info, TriangleAlert, X, XCircle } from 'lucide-react'
import type { ReactNode } from 'react'
import { create } from 'zustand'
import { cn } from '@/lib/cn'

export interface ToastItem {
  id: number
  tone: 'info' | 'success' | 'warning' | 'danger'
  title: string
  message?: string
  action?: { label: string; onClick: () => void }
}

interface ToastState {
  items: ToastItem[]
  push: (t: Omit<ToastItem, 'id'>, ms?: number) => number
  dismiss: (id: number) => void
}

let next = 1

export const useToasts = create<ToastState>((set, get) => ({
  items: [],
  push: (t, ms = 5000) => {
    const id = next++
    set({ items: [...get().items.slice(-3), { ...t, id }] })
    if (ms > 0) setTimeout(() => get().dismiss(id), ms)
    return id
  },
  dismiss: (id) => set({ items: get().items.filter((i) => i.id !== id) }),
}))

export const toast = {
  info: (title: string, message?: string) => useToasts.getState().push({ tone: 'info', title, message }),
  success: (title: string, message?: string) => useToasts.getState().push({ tone: 'success', title, message }),
  warning: (title: string, message?: string) => useToasts.getState().push({ tone: 'warning', title, message }, 8000),
  error: (title: string, message?: string) => useToasts.getState().push({ tone: 'danger', title, message }, 9000),
  action: (tone: ToastItem['tone'], title: string, action: ToastItem['action'], message?: string) => useToasts.getState().push({ tone, title, message, action }, 12000),
}

const ICON: Record<ToastItem['tone'], ReactNode> = {
  info: <Info className="size-4 text-info" aria-hidden />,
  success: <CheckCircle2 className="size-4 text-success" aria-hidden />,
  warning: <TriangleAlert className="size-4 text-warning" aria-hidden />,
  danger: <XCircle className="size-4 text-danger" aria-hidden />,
}

export function Toaster() {
  const { items, dismiss } = useToasts()
  return (
    <div aria-live="polite" aria-atomic="false" className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[360px] max-w-[calc(100vw-32px)] flex-col gap-2">
      {items.map((t) => (
        <div key={t.id} role={t.tone === 'danger' ? 'alert' : 'status'} className={cn('pop pointer-events-auto flex items-start gap-3 rounded-card bg-panel p-3 shadow-[var(--shadow-pop)]')}>
          <span className="mt-0.5">{ICON[t.tone]}</span>
          <div className="min-w-0 flex-1">
            <div className="font-medium text-fg">{t.title}</div>
            {t.message && <div className="mt-0.5 break-words text-muted">{t.message}</div>}
            {t.action && (
              <button className="mt-1.5 font-medium text-accent hover:underline" onClick={() => (t.action?.onClick(), dismiss(t.id))}>
                {t.action.label}
              </button>
            )}
          </div>
          <button aria-label="Dismiss" onClick={() => dismiss(t.id)} className="rounded p-0.5 text-faint hover:text-fg">
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  )
}
