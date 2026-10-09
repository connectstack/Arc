import { Check, Plus } from 'lucide-react'
import { useState } from 'react'
import type { AssetInfo, Coverage, LibraryGap } from '@/api/types'
import { Button } from '@/components/ui'
import { AddAssetDialog, type AddAssetInitial } from '@/features/assets/AddAssetDialog'
import { KIND_ICON, KIND_LABEL, initialFor } from './CoveragePanel'

/** After planning: what the script needed that the library lacked, as the planner recorded it (`meta.library_gaps`), with a way to add
 *  each one. `onAdded` is told which gap an asset answers, so the caller can swap it into the reel. `known` is what the script check found
 *  missing (its words in several languages and the sentence it came from), so the form starts with them. */
export function GapsCard({ gaps, sceneNumbers, onAdded, known }: { gaps: LibraryGap[]; sceneNumbers?: Record<string, number>; onAdded: (asset: AssetInfo, gap: LibraryGap) => void; known?: Coverage['missing'] }) {
  const [adding, setAdding] = useState<LibraryGap | null>(null)
  if (!gaps.length) return null
  const where = (g: LibraryGap) => {
    const nums = g.scenes.map((s) => sceneNumbers?.[s]).filter((n): n is number => n !== undefined)
    return nums.length ? ` · scene${nums.length > 1 ? 's' : ''} ${nums.join(', ')}` : ''
  }
  const initial = (g: LibraryGap): AddAssetInitial => {
    const found = known?.find((m) => m.kind === g.kind && m.name.replace(/_/g, ' ').toLowerCase() === g.name.toLowerCase())
    return {
      ...(found ? initialFor(found) : { name: g.name, kind: g.kind, tags: [g.name] }),
      hint: `Your script needs “${g.name}” and the library does not have it${g.stand_in ? `: ${g.stand_in} stands in for it` : ''}.`,
    }
  }
  return (
    <section aria-labelledby="gaps-h" className="rounded-card border border-line bg-raised/40 p-4 text-left">
      <h3 id="gaps-h" className="text-[13.5px] font-semibold">
        Not in the library
      </h3>
      <p className="mt-0.5 text-[12px] text-muted">The reel is planned with stand-ins for these. Add them and they are swapped in.</p>
      <ul className="m-0 mt-3 flex flex-col gap-2 p-0">
        {gaps.map((g) => {
          const Icon = KIND_ICON[g.kind]
          return (
            <li key={`${g.kind}:${g.name}`} className="flex list-none items-center gap-3 rounded-ctl border border-line bg-panel px-3 py-2">
              <Icon className="size-4 shrink-0 text-muted" aria-hidden />
              <div className="min-w-0 flex-1">
                <span className="font-medium">{g.name}</span>
                <span className="ml-2 text-[11.5px] text-faint">
                  {KIND_LABEL[g.kind]}
                  {g.stand_in ? ` · shown as ${g.stand_in}` : ' · left out'}
                  {where(g)}
                </span>
              </div>
              <Button size="sm" variant="secondary" onClick={() => setAdding(g)} aria-label={`Add ${g.name} to the library`}>
                <Plus className="size-3.5" /> Add asset
              </Button>
            </li>
          )
        })}
      </ul>
      {adding && (
        <AddAssetDialog
          open
          onOpenChange={(o) => !o && setAdding(null)}
          initial={initial(adding)}
          onAdded={(a) => {
            onAdded(a, adding)
            setAdding(null)
          }}
        />
      )}
    </section>
  )
}

export function AllFilled() {
  return (
    <p className="flex items-center gap-1.5 text-[12.5px] text-success">
      <Check className="size-4" aria-hidden /> Everything the script needs is in the library now.
    </p>
  )
}
