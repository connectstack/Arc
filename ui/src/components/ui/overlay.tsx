import * as RContext from '@radix-ui/react-context-menu'
import * as RDialog from '@radix-ui/react-dialog'
import * as RMenu from '@radix-ui/react-dropdown-menu'
import * as RPopover from '@radix-ui/react-popover'
import * as RTabs from '@radix-ui/react-tabs'
import * as RTooltip from '@radix-ui/react-tooltip'
import { X } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { Kbd } from './feedback'

export function Dialog({ open, onOpenChange, title, description, children, footer, size = 'md', className }: { open: boolean; onOpenChange: (o: boolean) => void; title: ReactNode; description?: ReactNode; children: ReactNode; footer?: ReactNode; size?: 'sm' | 'md' | 'lg' | 'xl'; className?: string }) {
  const width = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-2xl', xl: 'max-w-4xl' }[size]
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fade fixed inset-0 z-40 bg-black/55 backdrop-blur-[2px]" />
        <RDialog.Content className={cn('pop fixed left-1/2 top-1/2 z-50 flex max-h-[88vh] w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-[14px] bg-panel text-fg shadow-[var(--shadow-pop)] outline-none', width, className)}>
          <div className="flex items-start justify-between gap-4 px-5 pb-3 pt-4">
            <div className="min-w-0">
              <RDialog.Title className="text-[16px] font-semibold tracking-tight">{title}</RDialog.Title>
              {description ? <RDialog.Description className="mt-0.5 text-muted">{description}</RDialog.Description> : <RDialog.Description className="sr-only">{typeof title === 'string' ? title : 'Dialog'}</RDialog.Description>}
            </div>
            <RDialog.Close aria-label="Close" className="rounded-ctl p-1.5 text-muted hover:bg-hover hover:text-fg">
              <X className="size-4" />
            </RDialog.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-4">{children}</div>
          {footer && <div className="flex items-center justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  )
}

/** A panel that slides in from a side as a modal (focus stays inside it; Esc or a click outside closes it). */
export function Sheet({ open, onOpenChange, side, title, children }: { open: boolean; onOpenChange: (o: boolean) => void; side: 'left' | 'right'; title: string; children: ReactNode }) {
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fade fixed inset-0 z-40 bg-black/50" />
        <RDialog.Content aria-describedby={undefined} className={cn('fade fixed inset-y-0 z-50 flex w-[min(380px,92vw)] flex-col bg-panel text-fg shadow-[var(--shadow-pop)] outline-none', side === 'left' ? 'left-0' : 'right-0')}>
          <div className="flex shrink-0 items-center justify-between border-b border-line px-3 py-2">
            <RDialog.Title className="text-[13px] font-semibold">{title}</RDialog.Title>
            <RDialog.Close aria-label={`Close ${title.toLowerCase()}`} className="rounded-ctl p-1.5 text-muted hover:bg-hover hover:text-fg">
              <X className="size-4" />
            </RDialog.Close>
          </div>
          <div className="flex min-h-0 flex-1 flex-col">{children}</div>
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  )
}

export function Popover({ trigger, children, align = 'start', className, open, onOpenChange }: { trigger: ReactNode; children: ReactNode; align?: 'start' | 'center' | 'end'; className?: string; open?: boolean; onOpenChange?: (o: boolean) => void }) {
  return (
    <RPopover.Root open={open} onOpenChange={onOpenChange}>
      <RPopover.Trigger asChild>{trigger}</RPopover.Trigger>
      <RPopover.Portal>
        <RPopover.Content align={align} sideOffset={6} collisionPadding={12} className={cn('pop z-50 rounded-card bg-panel p-3 text-fg shadow-[var(--shadow-pop)] outline-none', className)}>
          {children}
        </RPopover.Content>
      </RPopover.Portal>
    </RPopover.Root>
  )
}

export const TooltipProvider = RTooltip.Provider

export function Tip({ label, shortcut, children, side = 'bottom' }: { label: ReactNode; shortcut?: string; children: ReactNode; side?: 'top' | 'bottom' | 'left' | 'right' }) {
  return (
    <RTooltip.Root delayDuration={350}>
      <RTooltip.Trigger asChild>{children}</RTooltip.Trigger>
      <RTooltip.Portal>
        <RTooltip.Content side={side} sideOffset={6} className="pop z-50 flex items-center gap-2 rounded-ctl bg-fg px-2 py-1 text-[11.5px] font-medium text-bg shadow-lg">
          {label}
          {shortcut && <span className="rounded bg-bg/15 px-1 font-mono text-[10.5px]">{shortcut}</span>}
        </RTooltip.Content>
      </RTooltip.Portal>
    </RTooltip.Root>
  )
}

export interface MenuEntry {
  label?: ReactNode
  icon?: ReactNode
  onSelect?: () => void
  shortcut?: string
  danger?: boolean
  disabled?: boolean
  separator?: boolean
  heading?: string
}

const itemCls = 'relative flex cursor-default select-none items-center gap-2 rounded-[6px] px-2 py-1.5 text-[13px] outline-none data-[disabled]:opacity-40 data-[highlighted]:bg-hover'

function items(entries: MenuEntry[], Item: typeof RMenu.Item, Sep: typeof RMenu.Separator, Label: typeof RMenu.Label) {
  return entries.map((e, i) =>
    e.separator ? (
      <Sep key={i} className="my-1 h-px bg-line" />
    ) : e.heading ? (
      <Label key={i} className="eyebrow px-2 pb-1 pt-2">
        {e.heading}
      </Label>
    ) : (
      <Item key={i} disabled={e.disabled} onSelect={e.onSelect} className={cn(itemCls, e.danger && 'text-danger')}>
        {e.icon && <span className="text-muted [&_svg]:size-4">{e.icon}</span>}
        <span className="flex-1">{e.label}</span>
        {e.shortcut && <Kbd>{e.shortcut}</Kbd>}
      </Item>
    ),
  )
}

export function Menu({ trigger, entries, align = 'end' }: { trigger: ReactNode; entries: MenuEntry[]; align?: 'start' | 'center' | 'end' }) {
  return (
    <RMenu.Root>
      <RMenu.Trigger asChild>{trigger}</RMenu.Trigger>
      <RMenu.Portal>
        <RMenu.Content align={align} sideOffset={6} collisionPadding={12} className="pop z-50 min-w-[190px] rounded-card bg-panel p-1 text-fg shadow-[var(--shadow-pop)]">
          {items(entries, RMenu.Item, RMenu.Separator, RMenu.Label)}
        </RMenu.Content>
      </RMenu.Portal>
    </RMenu.Root>
  )
}

export function ContextMenu({ children, entries }: { children: ReactNode; entries: MenuEntry[] }) {
  return (
    <RContext.Root>
      <RContext.Trigger asChild>{children}</RContext.Trigger>
      <RContext.Portal>
        <RContext.Content className="pop z-50 min-w-[190px] rounded-card bg-panel p-1 text-fg shadow-[var(--shadow-pop)]">
          {items(entries, RContext.Item as unknown as typeof RMenu.Item, RContext.Separator as unknown as typeof RMenu.Separator, RContext.Label as unknown as typeof RMenu.Label)}
        </RContext.Content>
      </RContext.Portal>
    </RContext.Root>
  )
}

export interface TabDef {
  value: string
  label: ReactNode
  badge?: ReactNode
}

/** Underlined tabs. `children` is the panel of the selected tab (the caller decides what to show for `value`). */
export function Tabs({ value, onChange, tabs, label, className, children }: { value: string; onChange: (v: string) => void; tabs: TabDef[]; label: string; className?: string; children?: ReactNode }) {
  return (
    <RTabs.Root value={value} onValueChange={onChange} className="flex min-h-0 flex-1 flex-col">
      <RTabs.List aria-label={label} className={cn('flex gap-1 border-b border-line px-2', className)}>
        {tabs.map((t) => (
          <RTabs.Trigger
            key={t.value}
            value={t.value}
            className="relative flex h-9 items-center gap-1.5 px-2 text-[12.5px] font-medium text-muted outline-none transition-colors hover:text-fg data-[state=active]:text-fg data-[state=active]:after:absolute data-[state=active]:after:inset-x-1 data-[state=active]:after:-bottom-px data-[state=active]:after:h-0.5 data-[state=active]:after:rounded-full data-[state=active]:after:bg-accent focus-visible:outline-2 focus-visible:outline-accent"
          >
            {t.label}
            {t.badge}
          </RTabs.Trigger>
        ))}
      </RTabs.List>
      <RTabs.Content value={value} tabIndex={-1} className="flex min-h-0 flex-1 flex-col outline-none">
        {children}
      </RTabs.Content>
    </RTabs.Root>
  )
}
