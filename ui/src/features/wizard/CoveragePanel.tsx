import { AlertTriangle, Box, CheckCircle2, Mountain, Plus, User } from 'lucide-react'
import { useState } from 'react'
import type { AssetKind, Coverage } from '@/api/types'
import { Button, Chip } from '@/components/ui'
import { cn } from '@/lib/cn'
import { plural, titleCase } from '@/lib/format'
import { AddAssetDialog, type AddAssetInitial } from '@/features/assets/AddAssetDialog'

export const KIND_ICON = { character: User, object: Box, place: Mountain } as const
export const KIND_LABEL: Record<AssetKind, string> = { character: 'character', object: 'object', place: 'place' }

type Missing = Coverage['missing'][number]

/** The form pre-filled from what the script says about a missing thing. */
export function initialFor(m: Missing): AddAssetInitial {
  return {
    name: m.name,
    kind: m.kind,
    tags: m.tags,
    hint: m.snippet ? `Your script says “${m.words[0] ?? m.name}”: ${m.snippet}` : `Your script mentions “${m.words[0] ?? m.name}”.`,
  }
}

/** Under the script: which characters, places and objects it mentions that the library can draw, and which it lacks, each with
 *  a way to add it. Advice, never a gate: a planner uses a stand-in (or leaves a thing out) and notes what it lacked. */
export function CoveragePanel({ coverage, loading }: { coverage: Coverage | undefined; loading: boolean }) {
  const [adding, setAdding] = useState<AddAssetInitial | null>(null)
  if (!coverage) {
    return loading ? <div className="h-16 rounded-card border border-line bg-raised/50" aria-hidden /> : null
  }
  const { covered, missing } = coverage
  if (!covered.length && !missing.length) return null
  return (
    <section aria-labelledby="coverage-h" className="rounded-card border border-line bg-raised/40 p-3.5">
      <h3 id="coverage-h" className="text-[13px] font-semibold">
        Library check
      </h3>
      {covered.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="inline-flex items-center gap-1 text-[12px] text-muted">
            <CheckCircle2 className="size-3.5 text-success" aria-hidden /> Can be drawn:
          </span>
          {covered.map((c) => {
            const Icon = KIND_ICON[c.kind]
            return (
              <Chip key={`${c.kind}:${c.asset}`} title={`${titleCase(c.asset)}: the script says ${c.words.join(', ')}${c.source === 'engine' ? ' (one of the engine’s own)' : ''}`}>
                <Icon className="size-3" aria-hidden />
                {c.asset.replace(/_/g, ' ')}
                {c.count > 1 && <span className="tabular text-faint">×{c.count}</span>}
              </Chip>
            )
          })}
        </div>
      )}
      {missing.length > 0 ? (
        <div className="mt-3">
          <div className="flex items-center gap-1.5 text-[12.5px] font-medium">
            <AlertTriangle className="size-3.5 text-warning" aria-hidden />
            {plural(missing.length, 'thing')} the script mentions {missing.length === 1 ? 'is' : 'are'} not in the library
          </div>
          <ul className="m-0 mt-2 flex flex-col gap-2 p-0">
            {missing.map((m) => {
              const Icon = KIND_ICON[m.kind]
              return (
                <li key={`${m.kind}:${m.name}`} className="flex list-none items-start gap-3 rounded-ctl border border-line bg-panel px-3 py-2">
                  <Icon className={cn('mt-0.5 size-4 shrink-0 text-muted')} aria-hidden />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span className="font-medium">{m.name.replace(/_/g, ' ')}</span>
                      <span className="text-[11.5px] text-faint">
                        {KIND_LABEL[m.kind]} · the script says {m.words.join(', ')}
                        {m.count > 1 ? ` (×${m.count})` : ''}
                      </span>
                    </div>
                    {m.snippet && <div className="truncate text-[12px] text-muted">“{m.snippet}”</div>}
                  </div>
                  <Button size="sm" variant="secondary" onClick={() => setAdding(initialFor(m))} aria-label={`Add ${m.name} to the library`}>
                    <Plus className="size-3.5" /> Add asset
                  </Button>
                </li>
              )
            })}
          </ul>
          <p className="mt-2 text-[11.5px] leading-snug text-faint">You can plan without them: the planner uses the closest thing it has (or leaves the object out) and the reel remembers what was missing, so adding it later can still swap it in.</p>
        </div>
      ) : (
        <p className="mt-2.5 text-[12px] text-muted">Everything the script mentions can be drawn.</p>
      )}
      {adding && <AddAssetDialog open onOpenChange={(o) => !o && setAdding(null)} initial={adding} onAdded={() => setAdding(null)} />}
    </section>
  )
}
