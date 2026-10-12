import { AlertTriangle, Plus } from 'lucide-react'
import type { GapStatus, ReelSpec } from '@/api/types'
import { Button, Tip } from '@/components/ui'
import { KIND_ICON, KIND_LABEL } from '@/features/wizard/CoveragePanel'
import { cn } from '@/lib/cn'
import { plural } from '@/lib/format'
import { characterName } from '@/lib/spec'
import { useUi } from '@/store/ui'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { useWide } from '@/lib/useMedia'
import { Section } from '../inspector/common'
import { gapKey, useIgnored } from './ignored'
import { sceneNumbers, useGapFlow, type GapFlow } from './useGapFlow'
import { gapsOfScene, useLibraryGaps } from './useLibraryGaps'

const nice = (name: string): string => name.replace(/_/g, ' ')

/** What a gap says in plain words, and what the button will do. `here` is the scene it is shown in (none: the whole reel). A character
 *  something stands in for is one character in every scene, so it is changed everywhere; any other gap is answered scene by scene. */
function describe(gap: GapStatus, spec: ReelSpec, here: string | undefined): { status: string; action: string; all?: string } {
  const ids = spec.scenes.map((s) => s.id)
  const where = sceneNumbers(gap.scenes, ids)
  const everywhere = gap.kind === 'character' && !!gap.character
  const who = gap.character ? characterName(spec, gap.character) : gap.name
  const there = here !== undefined ? 'this scene' : where || 'the scenes that say it'
  if (gap.asset) {
    const other = gap.scenes.length - (here !== undefined && gap.scenes.includes(here) ? 1 : 0)
    const many = gap.scenes.length || 1
    const action = everywhere ? `Use it for ${who}` : gap.kind === 'place' ? 'Use it as the set' : here !== undefined ? 'Use it in this scene' : `Use it in ${plural(many, 'scene')}`
    return {
      status: `The library has “${nice(gap.asset)}” now.`,
      action,
      ...(here !== undefined && other > 0 && !everywhere ? { all: `Use it in all ${gap.scenes.length} scenes` } : {}),
    }
  }
  if (gap.kind === 'object') return { status: `${here !== undefined ? 'This scene says' : 'The script says'} “${gap.name}” and the library has no such object. Add it and it goes into ${there}.`, action: 'Add asset…' }
  if (gap.kind === 'place') return { status: `${gap.stand_in ? `${nice(gap.stand_in)} stands in for “${gap.name}”` : `The library has no “${gap.name}”`}. Add it and the set of ${there} becomes it.`, action: 'Add asset…' }
  if (!everywhere) return { status: `${here !== undefined ? 'This scene says' : 'The script says'} “${gap.name}” and the library has no such character, so nobody stands in for it. Add it and it joins ${there}.`, action: 'Add asset…' }
  return { status: `${who} is drawn as ${gap.stand_in ? nice(gap.stand_in) : 'a stand-in'} because the library has no “${gap.name}”. Add it and ${who} is drawn as it in every scene.`, action: 'Add asset…' }
}

function Row({ gap, spec, sceneId, flow, project }: { gap: GapStatus; spec: ReelSpec; sceneId?: string; flow: GapFlow; project: string | null }) {
  const Icon = KIND_ICON[gap.kind]
  const ids = spec.scenes.map((s) => s.id)
  const scope = sceneId !== undefined ? [sceneId] : gap.scenes
  const d = describe(gap, spec, sceneId)
  const where = sceneNumbers(gap.scenes, ids)
  const run = (all = false) => (gap.asset ? void flow.use(gap, all ? gap.scenes : scope, gap.asset) : flow.add(gap, scope))
  return (
    <li className="flex list-none flex-col gap-2 rounded-ctl border border-line bg-raised p-2.5">
      <div className="flex items-start gap-2.5">
        <Icon className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-medium">{nice(gap.name)}</span>
            <span className="text-[11.5px] text-faint">
              {KIND_LABEL[gap.kind]}
              {where ? ` · ${where}` : ''}
            </span>
          </div>
          <p className="mt-0.5 text-[12px] leading-snug text-muted">{d.status}</p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 pl-[26px]">
        <Button size="sm" variant={gap.asset ? 'primary' : 'secondary'} onClick={() => run()} aria-label={gap.asset ? `${d.action}: ${nice(gap.name)}` : `Add ${nice(gap.name)} to the library${sceneId !== undefined ? ' and this scene' : ''}`}>
          {!gap.asset && <Plus className="size-3.5" aria-hidden />} {d.action}
        </Button>
        {d.all && (
          <Button size="sm" variant="ghost" onClick={() => run(true)}>
            {d.all}
          </Button>
        )}
        <button type="button" className="ml-auto text-[11.5px] text-faint hover:text-fg" onClick={() => project && useIgnored.getState().ignore(project, gapKey(gap))} title="Stop warning about this one in this reel">
          Not needed
        </button>
      </div>
    </li>
  )
}

/**
 * The things the script says that the library cannot draw, with the way to add each: in a scene's inspector (`scene`: only that scene's, and
 * what is added goes into that scene), or for the whole reel (the Script tab: what is added goes into every scene that says it). What the
 * library can draw now (it was added since, or came with a newer version) is listed apart, with a button to use it.
 */
export function MissingAssets({ scene }: { scene?: number }) {
  const spec = useProject((s) => s.spec)
  const project = useProject((s) => s.id)
  const { gaps, ignored } = useLibraryGaps()
  const flow = useGapFlow()
  const restore = useIgnored((s) => s.restore)
  if (!spec) return null
  const sceneId = scene !== undefined ? spec.scenes[scene]?.id : undefined
  const mine = scene !== undefined ? gapsOfScene(gaps, sceneId) : gaps
  const missing = mine.filter((g) => !g.asset)
  const ready = mine.filter((g) => g.asset)
  if (!mine.length && !(scene === undefined && ignored > 0)) return flow.dialog
  const list = (items: GapStatus[], label: string) => (
    <ul className="m-0 flex flex-col gap-2 p-0" aria-label={label}>
      {items.map((g) => (
        <Row key={`${g.kind}:${g.name}`} gap={g} spec={spec} sceneId={sceneId} flow={flow} project={project} />
      ))}
    </ul>
  )
  if (scene !== undefined) {
    return (
      <>
        {missing.length > 0 && (
          <Section title="Missing from the library" action={<span className="rounded-chip bg-warning/15 px-1.5 text-[11px] font-medium text-warning">{missing.length}</span>}>
            <p className="-mt-1 text-[11.5px] leading-snug text-faint">Your script says {missing.length === 1 ? 'this' : 'these'} in this scene and the library cannot draw {missing.length === 1 ? 'it' : 'them'}.</p>
            {list(missing, 'Missing from the library')}
          </Section>
        )}
        {ready.length > 0 && (
          <Section title="In the library now" action={<span className="rounded-chip bg-accent-soft px-1.5 text-[11px] font-medium text-accent">{ready.length}</span>}>
            <p className="-mt-1 text-[11.5px] leading-snug text-faint">Your script says {ready.length === 1 ? 'this' : 'these'} in this scene and {ready.length === 1 ? 'it is' : 'they are'} not in it yet.</p>
            {list(ready, 'In the library now')}
          </Section>
        )}
        {flow.dialog}
      </>
    )
  }
  return (
    <section aria-labelledby="missing-h" className="mb-3 rounded-card border border-line bg-raised/40 p-3">
      <h3 id="missing-h" className="flex items-center gap-1.5 text-[13px] font-semibold">
        <AlertTriangle className={cn('size-4', missing.length ? 'text-warning' : 'text-faint')} aria-hidden />
        {missing.length ? `${plural(missing.length, 'thing')} missing from the library` : 'Nothing is missing from the library'}
      </h3>
      {missing.length > 0 && (
        <>
          <p className="mb-2 mt-0.5 text-[12px] text-muted">Your script says {missing.length === 1 ? 'this' : 'these'} and the library cannot draw {missing.length === 1 ? 'it' : 'them'}. Add each one and it is put to use in the scenes that say it.</p>
          {list(missing, 'Missing from the library')}
        </>
      )}
      {ready.length > 0 && (
        <>
          <h4 className="mb-2 mt-3 text-[12.5px] font-semibold">{ready.length} in the library now</h4>
          {list(ready, 'In the library now')}
        </>
      )}
      {ignored > 0 && project && (
        <button type="button" className="mt-2 text-[11.5px] text-accent hover:underline" onClick={() => restore(project)}>
          Show the {ignored} you set aside
        </button>
      )}
      {flow.dialog}
    </section>
  )
}

/** In the top bar: how many things the script says that the library cannot draw (or, when it can draw them all now, how many are ready to
 *  use); a click opens the list. */
export function MissingChip() {
  const { gaps } = useLibraryGaps()
  const set = useStudio((s) => s.set)
  const setUi = useUi((s) => s.set)
  const wide = useWide()
  const missing = gaps.filter((g) => !g.asset).length
  const ready = gaps.length - missing
  if (!gaps.length) return null
  const open = () => {
    setUi({ leftOpen: true })
    set({ leftTab: 'script', drawer: wide ? null : 'left' })
  }
  if (missing === 0) {
    return (
      <button type="button" onClick={open} title="The library can draw what your script says now. Open the list to use it." className="flex h-7 shrink-0 items-center gap-1.5 rounded-chip border border-accent/40 bg-accent-soft px-2 text-[12px] font-medium text-accent hover:border-accent" aria-label={`${plural(ready, 'thing')} from your script ready to use from the library`}>
        <Plus className="size-3.5" aria-hidden />
        <span className="tabular">{ready}</span>
        <span className="hidden md:inline">ready to use</span>
      </button>
    )
  }
  return (
    <Tip label={`Your script says things the library cannot draw. Open the list to add them.${ready ? ` ${plural(ready, 'other')} can be drawn now.` : ''}`}>
      <button type="button" onClick={open} className="flex h-7 shrink-0 items-center gap-1.5 rounded-chip border border-warning/40 bg-warning/10 px-2 text-[12px] font-medium text-warning hover:bg-warning/15" aria-label={`${plural(missing, 'thing')} missing from the library`}>
        <AlertTriangle className="size-3.5" aria-hidden />
        <span className="tabular">{missing}</span>
        <span className="hidden md:inline">missing from the library</span>
      </button>
    </Tip>
  )
}
