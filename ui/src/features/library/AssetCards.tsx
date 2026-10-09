import { Box, Move, Mountain, Plus, User } from 'lucide-react'
import type { ReactNode } from 'react'
import { useApi } from '@/api/context'
import type { AssetInfo, AssetKind, CatalogEntry } from '@/api/types'
import { Chip, Skeleton } from '@/components/ui'
import { AssetThumb } from '@/features/assets/AssetThumb'
import { extraWords, sizeRatio } from '@/features/assets/assetFields'
import { cn } from '@/lib/cn'
import { Draggable } from './Draggable'
import type { LibraryItem, Origin } from './libraryItems'

export const KIND_ICON = { character: User, object: Box, place: Mountain } as const satisfies Record<AssetKind, unknown>

/** "Library" for what ships with Reel's asset library, "Yours" for the person's own files; the engine's own entries carry none. */
export function OriginBadge({ origin, className }: { origin: Origin; className?: string }) {
  if (origin === 'engine') return null
  return (
    <Chip tone={origin === 'user' ? 'accent' : 'neutral'} className={cn('h-[18px] shrink-0 px-1.5 text-[10.5px]', className)}>
      {origin === 'user' ? 'Yours' : 'Library'}
    </Chip>
  )
}

/** "0.52 × a person": how tall an object or character is, against a standing person. */
export function sizeLine(e: Pick<CatalogEntry, 'size' | 'height'>): string | null {
  const ratio = e.size ?? (e.height ? sizeRatio(e.height) : undefined)
  return ratio === undefined ? null : `${ratio} × a person`
}

const prettyName = (name: string): string => name.replace(/_/g, ' ')

/** The thumbnail of a library entry as the engine draws it; a static block stands in until the asset list has arrived (its version is in the address). */
function Thumb({ item, style, timeOfDay, className }: { item: LibraryItem; style: string; timeOfDay?: string; className?: string }) {
  if (!item.asset) return <Skeleton className={cn('aspect-[9/13] w-full rounded-none', className)} />
  return <AssetThumb asset={item.asset} style={style} timeOfDay={timeOfDay} alt="" className={className} />
}

/** What a card says under its name: how big it is, which colours a spec can change, the first words that find it (all of them in a tooltip). */
function CardFacts({ item, size = true }: { item: LibraryItem; size?: boolean }) {
  const { entry, roles } = item
  const words = extraWords({ name: entry.name, tags: item.tags })
  const line = size ? sizeLine(entry) : null
  if (!line && roles.length === 0 && words.length === 0) return null
  return (
    <span className="mt-0.5 flex flex-col gap-0.5 text-[11px] leading-snug text-faint">
      {line && <span className="tabular">{line}</span>}
      {roles.length > 0 && (
        <span className="truncate" title={`Colours a spec can change: ${roles.join(', ')}`}>
          Colours: {roles.slice(0, 3).join(', ')}
          {roles.length > 3 && ` +${roles.length - 3}`}
        </span>
      )}
      {words.length > 0 && (
        <span className="truncate" dir="auto" title={`Words that find it: ${words.join(', ')}`}>
          {words.slice(0, 3).join(' · ')}
          {words.length > 3 && ` +${words.length - 3}`}
        </span>
      )}
    </span>
  )
}

function CardText({ item, children }: { item: LibraryItem; children?: ReactNode }) {
  return (
    <span className="flex flex-1 flex-col gap-1 p-3">
      <span className="flex items-start justify-between gap-2">
        <span className="min-w-0 break-words text-[13.5px] font-semibold capitalize leading-tight">{prettyName(item.entry.name)}</span>
        <OriginBadge origin={item.origin} />
      </span>
      <span className="line-clamp-2 text-[12px] leading-snug text-muted">{item.entry.summary}</span>
      {children}
    </span>
  )
}

const CARD = 'overflow-hidden rounded-card border border-line bg-panel transition-colors hover:border-line-strong focus-within:border-line-strong'
// the card clips what is outside it, so the focus ring is drawn inside the button, not 2 px outside it
const CARD_BUTTON = 'flex h-full w-full flex-col text-left focus-visible:outline-offset-[-3px] disabled:cursor-progress'
// the card's rounded corners clip the picture; the picture itself stays square-cornered against the text under it
const FLAT = 'block overflow-hidden [&>div]:rounded-none [&_img]:rounded-none'

/** A character or an object of the asset library: its picture, name, summary, size, colours and first words. Click to see it closely. */
export function LibraryCard({ item, style, onOpen }: { item: LibraryItem; style: string; onOpen: (a: AssetInfo) => void }) {
  const { asset } = item
  return (
    <article data-asset={item.entry.name} className={CARD}>
      <button type="button" disabled={!asset} onClick={() => asset && onOpen(asset)} className={CARD_BUTTON}>
        <span aria-hidden className={FLAT}>
          <Thumb item={item} style={style} className="aspect-[9/13]" />
        </span>
        <CardText item={item}>
          <CardFacts item={item} />
        </CardText>
      </button>
    </article>
  )
}

/** A place of the asset library at three times of day. */
export function LibraryPlaceCard({ item, style, onOpen }: { item: LibraryItem; style: string; onOpen: (a: AssetInfo) => void }) {
  const { asset, entry } = item
  const spots = Object.keys(entry.slots ?? {}).filter((s) => !s.startsWith('off_')).length
  return (
    <article data-asset={entry.name} className={CARD}>
      <button type="button" disabled={!asset} onClick={() => asset && onOpen(asset)} className={CARD_BUTTON}>
        <span aria-hidden className="grid grid-cols-3 gap-px bg-line">
          {['day', 'dusk', 'night'].map((t) => (
            <span key={t} className={FLAT}>
              <Thumb item={item} style={style} timeOfDay={t} className="aspect-[9/14]" />
            </span>
          ))}
        </span>
        <CardText item={item}>
          <CardFacts item={item} size={false} />
          {spots > 0 && <span className="text-[11px] text-faint tabular">{spots} standing spots</span>}
        </CardText>
      </button>
    </article>
  )
}

/** The compact list of the Studio's left panel: a small picture, the name, the size and a button to add it to the scene at the playhead. */
export function ObjectRow({ item, style, onAdd }: { item: LibraryItem; style: string; onAdd: (() => void) | null }) {
  const { entry } = item
  const line = sizeLine(entry)
  return (
    <Draggable item={{ kind: 'object', name: entry.name }} enabled={!!onAdd} className="group flex items-center gap-2 rounded-ctl px-2 py-1.5 hover:bg-hover">
      {onAdd && <Move className="size-3 shrink-0 text-faint" aria-hidden />}
      <span aria-hidden className="block size-9 shrink-0 overflow-hidden rounded-[6px] bg-raised [&>div]:rounded-none [&_img]:rounded-none">
        <Thumb item={item} style={style} className="aspect-square" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline justify-between gap-2">
          <span className="truncate font-medium" title={entry.summary}>
            {prettyName(entry.name)}
          </span>
          {line && <span className="shrink-0 text-[10.5px] text-faint tabular">{line}</span>}
        </span>
        <span className="flex items-center gap-1.5">
          {item.origin === 'user' && <OriginBadge origin="user" className="h-4 text-[10px]" />}
          <span className="line-clamp-1 min-w-0 text-[11.5px] text-muted">{entry.summary}</span>
        </span>
      </span>
      {onAdd && (
        <button type="button" onClick={onAdd} aria-label={`Add ${entry.name} at the playhead`} title="Add at the playhead" className="rounded p-1 text-faint opacity-0 hover:bg-raised hover:text-fg focus-visible:opacity-100 group-hover:opacity-100">
          <Plus className="size-3.5" />
        </button>
      )}
    </Draggable>
  )
}

/** One of the engine's own characters (no asset behind it): the renderer's thumbnail and its default colours. */
export function EngineCharacterCard({ entry, style }: { entry: CatalogEntry; style: string }) {
  const api = useApi()
  return (
    <article data-asset={entry.name} className="overflow-hidden rounded-card border border-line bg-panel">
      <img src={api.libraryThumbUrl('archetype', entry.name, 'day', style)} alt={`${entry.name} archetype`} loading="lazy" className="aspect-[9/12] w-full object-cover" />
      <div className="p-3">
        <h3 className="font-semibold capitalize">{entry.name}</h3>
        <p className="mt-1 text-[12px] leading-snug text-muted">{entry.summary}</p>
        <div className="mt-2 flex gap-1">
          {Object.entries(entry.palette ?? {})
            .slice(0, 6)
            .map(([role, c]) => (
              <span key={role} title={`${role} ${c}`} className="size-4 rounded-full border border-black/10" style={{ background: c }} />
            ))}
        </div>
      </div>
    </article>
  )
}

/** One of the engine's own sets, at three times of day. */
export function EngineBackgroundCard({ entry, style }: { entry: CatalogEntry; style: string }) {
  const api = useApi()
  return (
    <article data-asset={entry.name} className="overflow-hidden rounded-card border border-line bg-panel">
      <div className="grid grid-cols-3 gap-px bg-line">
        {['day', 'dusk', 'night'].map((t) => (
          <img key={t} src={api.libraryThumbUrl('background', entry.name, t, style)} alt={`${entry.name} at ${t}`} loading="lazy" className="aspect-[9/14] w-full object-cover" />
        ))}
      </div>
      <div className="p-3">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold">{entry.name}</h3>
          <span className="text-[11px] text-faint">{Object.keys(entry.slots ?? {}).filter((s) => !s.startsWith('off_')).length} places</span>
        </div>
        <p className="mt-1 text-[12px] leading-snug text-muted">{entry.summary}</p>
      </div>
    </article>
  )
}
