import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { useApi } from '@/api/context'
import { useAssets } from '@/api/hooks'
import type { GapStatus, ReelSpec } from '@/api/types'
import { useDebounced } from '@/features/assets/useDebounced'
import { useProject } from '@/store/project'
import { gapKey, ignoredOf, useIgnored } from './ignored'

const NONE: GapStatus[] = []

/** Does this scene already show what the library now has for the gap (somebody placed it by hand)? */
function answered(spec: ReelSpec, g: GapStatus, sceneId: string): boolean {
  const sc = spec.scenes.find((s) => s.id === sceneId)
  if (!g.asset || !sc) return false
  if (g.kind === 'object') return sc.objects.some((o) => o.asset === g.asset)
  if (g.kind === 'place') return sc.background.template === g.asset
  return sc.layers.some((l) => spec.characters.find((c) => c.id === l.character)?.archetype === g.asset)
}

/** What of a gap is still open: the scenes that do not show the asset the library has for it. `null` when none is left. */
function open(spec: ReelSpec, g: GapStatus): GapStatus | null {
  if (!g.asset) return g
  if (g.kind === 'character' && g.character && spec.characters.find((c) => c.id === g.character)?.archetype === g.asset) return null
  const left = g.scenes.filter((id) => !answered(spec, g, id))
  return g.scenes.length && !left.length ? null : { ...g, scenes: left }
}

/**
 * What the reel's script needs that the library lacks, as the open reel stands: the gaps its planner recorded and what the script mentions
 * that nothing draws, each with the scenes that say it and the asset that answers it in the library now. Read again when a caption, the
 * script or the library changes. A gap the person set aside is left out (`all` has them).
 */
export function useLibraryGaps(): { gaps: GapStatus[]; all: GapStatus[]; loading: boolean; ignored: number } {
  const api = useApi()
  const project = useProject((s) => s.id)
  const spec = useProject((s) => s.spec)
  const script = useProject((s) => s.script)
  const assets = useAssets().data?.assets
  const lists = useIgnored((s) => s.lists)
  const library = assets ? assets.map((a) => `${a.name}:${a.version}`).join(',') : ''
  // only what a gap depends on: the gaps the spec recorded, and what each scene says
  const view = spec ? JSON.stringify([spec.meta.library_gaps ?? [], spec.scenes.map((s) => [s.id, s.captions.map((c) => c.text)])]) : ''
  const settledView = useDebounced(view, 300)
  const settledScript = useDebounced(script, 600)
  const query = useQuery({
    queryKey: ['library-gaps', project, settledView, settledScript, library],
    // the spec as it is when the question is asked: only the parts in `view` matter to the answer
    queryFn: () => api.libraryGaps(useProject.getState().spec as ReelSpec, settledScript),
    enabled: !!spec && !!project && !!assets,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  })
  const ignore = useMemo(() => ignoredOf(lists, project), [lists, project])
  const all = query.data ?? NONE
  const gaps = useMemo(
    () => all.flatMap((g) => (ignore.has(gapKey(g)) ? [] : [spec ? open(spec, g) : g]).filter((x): x is GapStatus => x !== null)),
    [all, ignore, spec],
  )
  return { gaps, all, loading: query.isLoading, ignored: all.length - gaps.length }
}

/** The gaps that say a word in this scene. */
export const gapsOfScene = (gaps: GapStatus[], sceneId: string | undefined): GapStatus[] => (sceneId === undefined ? [] : gaps.filter((g) => g.scenes.includes(sceneId)))
