import { ImageOff, Loader2 } from 'lucide-react'
import { useId, useState } from 'react'
import type { AssetKind } from '@/api/types'
import { Field, SelectBox, SwitchRow } from '@/components/ui'
import { cn } from '@/lib/cn'
import { titleCase } from '@/lib/format'
import { TIMES_OF_DAY, type TimeOfDay } from './assetFields'

/**
 * One picture drawn by the engine. When the address changes, the old picture stays until the new one has arrived (nothing flickers)
 * and a small spinner says it is being drawn again; if it cannot be drawn, a short note stands in for it.
 */
export function PreviewImage({ src, alt, className }: { src: string; alt: string; className?: string }) {
  const [loaded, setLoaded] = useState('')
  const [failed, setFailed] = useState('')
  const busy = loaded !== src && failed !== src
  return (
    <div className={cn('relative aspect-[9/16] w-full overflow-hidden rounded-card bg-raised', className)}>
      {failed === src ? (
        <div role="img" aria-label={`${alt}: it cannot be drawn with these settings`} className="absolute inset-0 grid place-items-center p-2 text-center text-[11px] leading-snug text-faint">
          <span>
            <ImageOff className="mx-auto mb-1 size-4" aria-hidden />
            Cannot be drawn
          </span>
        </div>
      ) : (
        <img src={src} alt={alt} draggable={false} onLoad={() => setLoaded(src)} onError={() => setFailed(src)} className="absolute inset-0 size-full object-cover" />
      )}
      {busy && failed !== src && <Loader2 aria-hidden className="absolute right-1.5 top-1.5 size-3.5 animate-[spin_0.9s_linear_infinite] text-faint" />}
    </div>
  )
}

export interface StylePreviewsProps {
  /** the styles to show, in order (the catalog's) */
  styles: string[]
  /** the picture for one style, as an address (the caller builds it for what is being shown) */
  srcFor: (style: string) => string
  /** what the pictures show, for their alternative text */
  subject: string
  kind: AssetKind
  /** show it next to a person at the size a scene gives it (things and characters) */
  trueScale: boolean
  onTrueScale: (on: boolean) => void
  /** places are drawn at a time of day */
  timeOfDay: TimeOfDay
  onTimeOfDay: (t: TimeOfDay) => void
}

/** An asset drawn in every style, side by side as small 9:16 frames, with the one switch that matters for its kind. */
export function StylePreviews({ styles, srcFor, subject, kind, trueScale, onTrueScale, timeOfDay, onTimeOfDay }: StylePreviewsProps) {
  const uid = useId()
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-3 gap-2">
        {styles.map((s) => (
          <figure key={s} className="m-0 flex min-w-0 flex-col gap-1">
            <PreviewImage src={srcFor(s)} alt={`${subject} drawn in the ${titleCase(s)} style`} />
            <figcaption aria-hidden className="truncate text-center text-[11px] text-muted">
              {titleCase(s)}
            </figcaption>
          </figure>
        ))}
      </div>
      {kind === 'place' ? (
        <Field label="Time of day" htmlFor={`${uid}-time`}>
          <SelectBox<TimeOfDay> id={`${uid}-time`} label="Time of day" value={timeOfDay} onChange={onTimeOfDay} options={TIMES_OF_DAY.map((t) => ({ value: t, label: titleCase(t) }))} />
        </Field>
      ) : (
        <SwitchRow label="Next to a person (true size)" hint="Shows it beside a person at the size a scene gives it, so a chair that is too small or a phone that is too big is easy to see." checked={trueScale} onChange={onTrueScale} />
      )}
    </div>
  )
}
