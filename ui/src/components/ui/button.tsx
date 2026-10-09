import { cva, type VariantProps } from 'class-variance-authority'
import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

const button = cva(
  'inline-flex shrink-0 select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-ctl font-medium transition-colors duration-150 disabled:pointer-events-none disabled:opacity-45 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        primary:
          'bg-solid text-accent-fg shadow-[inset_0_1px_0_rgb(255_255_255/0.16)] hover:bg-solid-hover active:brightness-95',
        secondary: 'border border-line bg-raised text-fg hover:border-line-strong hover:bg-hover',
        ghost: 'text-muted hover:bg-hover hover:text-fg data-[active=true]:bg-accent-soft data-[active=true]:text-accent',
        danger: 'border border-danger/40 bg-danger/10 text-danger hover:bg-danger/18',
        link: 'h-auto px-0 text-accent hover:underline',
      },
      size: {
        sm: 'h-7 px-2.5 text-[12px]',
        md: 'h-[var(--ctl-h)] px-3 text-[13px]',
        lg: 'h-9 px-4 text-[13px]',
        icon: 'h-[var(--ctl-h)] w-[var(--ctl-h)]',
        'icon-sm': 'h-7 w-7',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
)

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof button> {
  active?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button({ className, variant, size, active, type = 'button', ...props }, ref) {
  return <button ref={ref} type={type} data-active={active} className={cn(button({ variant, size }), className)} {...props} />
})

export const IconButton = forwardRef<HTMLButtonElement, ButtonProps & { label: string }>(function IconButton(
  { label, className, variant = 'ghost', size = 'icon', children, ...props },
  ref,
) {
  return (
    <Button ref={ref} variant={variant} size={size} aria-label={label} title={label} className={className} {...props}>
      {children}
    </Button>
  )
})
