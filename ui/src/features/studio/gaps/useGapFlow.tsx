import { useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { useApi } from '@/api/context'
import { ApiError, type GapStatus } from '@/api/types'
import { toast } from '@/components/ui'
import { AddAssetDialog, type AddAssetInitial } from '@/features/assets/AddAssetDialog'
import { initialFor } from '@/features/wizard/CoveragePanel'
import { useScriptCoverage } from '@/features/wizard/useScriptCoverage'
import { normalizeSpec } from '@/lib/normalize'
import { characterName } from '@/lib/spec'
import { useProject } from '@/store/project'

/** The scenes of a gap, as numbers a person reads: "scene 3" or "scenes 3, 5". */
export function sceneNumbers(ids: string[], sceneIds: string[]): string {
  const nums = ids.map((id) => sceneIds.indexOf(id) + 1).filter((n) => n > 0)
  return nums.length ? `scene${nums.length > 1 ? 's' : ''} ${nums.join(', ')}` : ''
}

export interface GapFlow {
  /** Ask for a file for the missing asset (the Add asset dialog, started from what the script says); once it is in the library it is put to use in `scope`. */
  add: (gap: GapStatus, scope: string[]) => void
  /** Put the asset the library has for the gap to use in `scope` (scene ids). Resolves to whether the reel changed. */
  use: (gap: GapStatus, scope: string[], asset?: string) => Promise<boolean>
  /** The dialog, to be rendered once by whoever uses the flow. */
  dialog: ReactNode
}

/**
 * Answering a gap from the Studio: add the missing asset to the library (the form opens already filled in from the script) and put it to
 * use in the scenes asked for, as one undo step. An object stands on a free spot of the scene, a place becomes the scene's set, a character
 * is drawn as it everywhere. What the other scenes of the gap still lack stays a gap, and the library has the asset for them now.
 */
export function useGapFlow(): GapFlow {
  const api = useApi()
  const qc = useQueryClient()
  const replace = useProject((s) => s.replace)
  const select = useProject((s) => s.select)
  const script = useProject((s) => s.script)
  const known = useScriptCoverage(script).data?.missing
  const [adding, setAdding] = useState<{ gap: GapStatus; scope: string[] } | null>(null)

  const use = async (gap: GapStatus, scope: string[], asset?: string): Promise<boolean> => {
    const spec = useProject.getState().spec
    if (!spec) return false
    try {
      const r = await api.fillGaps(spec, [
        {
          kind: gap.kind,
          name: gap.name,
          scope,
          ...(asset ? { asset } : {}),
          gap: { kind: gap.kind, name: gap.name, scenes: gap.scenes, ...(gap.character ? { character: gap.character } : {}), ...(gap.stand_in ? { stand_in: gap.stand_in } : {}) },
        },
      ])
      const done = r.filled[0]
      if (!done) {
        toast.info('Added to the library', `“${gap.name}” is there now, but this reel could not be changed to use it: put it where you want it from the Build tab.`)
        return false
      }
      const next = normalizeSpec(r.spec)
      replace(next)
      void qc.invalidateQueries({ queryKey: ['library-gaps'] })
      const ids = next.scenes.map((s) => s.id)
      const where = sceneNumbers(scope, ids)
      const rest = gap.scenes.filter((id) => !scope.includes(id) && ids.includes(id))
      const others = rest.length && (gap.kind !== 'character' || !gap.character) ? ` ${sceneNumbers(rest, ids)} ${rest.length === 1 ? 'says' : 'say'} “${gap.name}” too: each has a button to use it there.` : ''
      if (gap.kind === 'object') {
        const si = ids.indexOf(scope[0])
        const oi = next.scenes[si]?.objects.findIndex((o) => o.asset === done.asset) ?? -1
        if (si >= 0 && oi >= 0) select({ kind: 'object', scene: si, object: oi })
        toast.success(`${gap.name} is in ${where || 'the reel'}`, `It stands where there was room: drag it where you want it.${others}`)
      } else if (gap.kind === 'place') {
        toast.success(`${where ? where[0].toUpperCase() + where.slice(1) : 'The scene'} is on the “${done.asset.replace(/_/g, ' ')}” now`, others.trim() || undefined)
      } else if (gap.character) {
        toast.success(`${characterName(next, gap.character)} is drawn as the “${done.asset.replace(/_/g, ' ')}” now`, 'In every scene: a character looks the same throughout.')
      } else {
        // nobody stood in for it: it joined the scenes asked for as a character of its own
        const who = next.characters.find((c) => c.archetype === done.asset)
        const si = ids.indexOf(scope[0])
        const li = who ? (next.scenes[si]?.layers.findIndex((l) => l.character === who.id) ?? -1) : -1
        if (si >= 0 && li >= 0) select({ kind: 'layer', scene: si, layer: li })
        toast.success(`${who ? characterName(next, who.id) : gap.name} is in ${where || 'the reel'}`, `It stands still for now: give it actions on the timeline.${others}`)
      }
      return true
    } catch (e) {
      toast.error('Could not update the reel', e instanceof ApiError ? `${e.detail}${e.hint ? ` — ${e.hint}` : ''}` : String(e))
      return false
    }
  }

  const initial = (gap: GapStatus, scope: string[]): AddAssetInitial => {
    const spec = useProject.getState().spec
    const where = spec ? sceneNumbers(scope.length ? scope : gap.scenes, spec.scenes.map((s) => s.id)) : ''
    const found = known?.find((m) => m.kind === gap.kind && m.name.replace(/_/g, ' ').toLowerCase() === gap.name.replace(/_/g, ' ').toLowerCase())
    return {
      ...(found ? initialFor(found) : { name: gap.name, kind: gap.kind, tags: [gap.name] }),
      hint: `Your script mentions “${gap.name}”${where ? ` in ${where}` : ''} and the library does not have it${gap.stand_in ? `: ${gap.stand_in} stands in for it` : ''}. Add it and it is put to use there.`,
    }
  }

  return {
    add: (gap, scope) => setAdding({ gap, scope }),
    use,
    dialog: adding ? (
      <AddAssetDialog
        open
        onOpenChange={(o) => !o && setAdding(null)}
        initial={initial(adding.gap, adding.scope)}
        onAdded={(a) => {
          const { gap, scope } = adding
          setAdding(null)
          void use(gap, scope, a.name)
        }}
      />
    ) : null,
  }
}
