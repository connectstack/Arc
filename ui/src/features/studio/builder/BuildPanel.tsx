// "Build": put a reel together by hand, one scene at a time, from the library. Seven steps (background, characters, objects, actions,
// sounds, words, camera) work on the scene being built; every pick goes through the same edit functions as the timeline and the
// inspector, so what is made here can be fine-tuned anywhere and undone with one step.
import { ChevronLeft, ChevronRight, Plus, Ruler, type LucideIcon } from 'lucide-react'
import { Box, Mountain, PersonStanding, Type, Users, Video, Volume2 } from 'lucide-react'
import { useEffect, useRef, type ComponentType } from 'react'
import { useCatalog } from '@/api/hooks'
import type { ReelSpec, Scene } from '@/api/types'
import { Button, IconButton, NumberField } from '@/components/ui'
import { cn } from '@/lib/cn'
import { budgetState, fitToDuration, ms, sceneSlots, sceneVisibleFrom, totalDuration } from '@/lib/timeline'
import { useProject } from '@/store/project'
import { useStudio, type BuildStep } from '@/store/studio'
import { outlineScene } from '../SceneObjects'
import { addScene, addSceneLike } from '../ops'
import { secs, type StepProps } from './parts'
import { ActionsStep, BackgroundStep, CameraStep, CastStep, ObjectsStep, SoundsStep, WordsStep } from './steps'

interface StepDef {
  value: BuildStep
  label: string
  icon: LucideIcon
  Body: ComponentType<StepProps>
  /** what the scene holds of it, for the step's heading */
  held: (sc: Scene) => string | number
}

export const STEPS: StepDef[] = [
  { value: 'background', label: 'Background', icon: Mountain, Body: BackgroundStep, held: (sc) => sc.background.template.replace(/_/g, ' ') },
  { value: 'cast', label: 'Characters', icon: Users, Body: CastStep, held: (sc) => sc.layers.length },
  { value: 'objects', label: 'Objects', icon: Box, Body: ObjectsStep, held: (sc) => sc.objects.length },
  { value: 'actions', label: 'Actions', icon: PersonStanding, Body: ActionsStep, held: (sc) => sc.layers.reduce((n, l) => n + l.actions.length, 0) },
  { value: 'sounds', label: 'Sounds', icon: Volume2, Body: SoundsStep, held: (sc) => sc.sfx.length },
  { value: 'words', label: 'Words', icon: Type, Body: WordsStep, held: (sc) => sc.captions.length },
  { value: 'camera', label: 'Camera', icon: Video, Body: CameraStep, held: (sc) => sc.camera.moves.length },
]

/** Is there nothing in the reel yet but sets (a reel just started from scratch)? */
const isBare = (spec: ReelSpec): boolean => spec.characters.length === 0 && spec.scenes.every((s) => s.objects.length === 0 && s.captions.length === 0 && s.sfx.length === 0)

/** Which scene, how long it is, how long the reel is against the 45-60 s it must be, and the way to the next scene. */
function BuildHeader({ spec, si }: { spec: ReelSpec; si: number }) {
  const catalog = useCatalog().data
  const edit = useProject((s) => s.edit)
  const replace = useProject((s) => s.replace)
  const select = useProject((s) => s.select)
  const setPlayhead = useProject((s) => s.setPlayhead)
  const sc = spec.scenes[si]
  const min = catalog?.limits.min_total_sec ?? 45
  const max = catalog?.limits.max_total_sec ?? 60
  const maxScene = catalog?.limits.max_scene_sec ?? 30
  const total = totalDuration(spec)
  const state = budgetState(total, min, max)
  const goTo = (i: number) => {
    const now = useProject.getState().spec
    if (!now?.scenes[i]) return
    select({ kind: 'scene', scene: i })
    setPlayhead(sceneVisibleFrom(sceneSlots(now), i))
  }
  const next = (like: boolean) => {
    edit((d) => select(like ? addSceneLike(d, si) : addScene(d, si)))
    goTo(si + 1)
  }
  // scaling every scene can only reach the budget when there are scenes enough for it
  const canFit = state === 'out' && spec.scenes.length * maxScene >= 50 && spec.scenes.length * 0.5 <= 50
  const note = total < min ? `${secs(min - total)} more to reach ${min} s` : total > max ? `${secs(total - max)} over ${max} s` : `within ${min}–${max} s`
  return (
    <div className="flex flex-col gap-2.5 border-b border-line px-3 py-2.5">
      <div className="flex items-center gap-1">
        <IconButton label="Previous scene" size="icon-sm" disabled={si === 0} onClick={() => goTo(si - 1)}>
          <ChevronLeft className="size-4" />
        </IconButton>
        <h2 className="min-w-0 flex-1 truncate text-center text-[13px] font-semibold tabular" aria-live="polite">
          Scene {si + 1} of {spec.scenes.length}
        </h2>
        <IconButton label="Next scene" size="icon-sm" disabled={si >= spec.scenes.length - 1} onClick={() => goTo(si + 1)}>
          <ChevronRight className="size-4" />
        </IconButton>
        <span title="How long this scene lasts" className="ml-1">
          <NumberField aria-label="Scene length" className="w-[72px]" value={sc.duration_sec} min={0.5} max={maxScene} step={0.5} unit="s" onChange={(v) => edit((d) => void (d.scenes[si].duration_sec = ms(v)))} />
        </span>
      </div>
      <div className="flex gap-1.5">
        <Button size="sm" className="flex-1" onClick={() => next(true)} title="Add a scene after this one on the same background, with the same characters standing where they stand now">
          <Plus className="size-3.5" aria-hidden /> Scene like this
        </Button>
        <Button size="sm" variant="ghost" onClick={() => next(false)} title="Add a scene after this one with a plain background and nobody in it">
          <Plus className="size-3.5" aria-hidden /> Blank scene
        </Button>
      </div>
      <div className="flex flex-col gap-1">
        <div className="flex items-baseline justify-between gap-2 text-[12px]">
          <span className="text-muted">Reel</span>
          <span className="tabular">
            <b className={cn('font-semibold', state === 'out' && 'text-danger')}>{secs(total)}</b> <span className="text-faint">of {min}–{max} s</span>
          </span>
        </div>
        <div className="relative" role="meter" aria-label="Reel length" aria-valuemin={0} aria-valuemax={max} aria-valuenow={Math.round(total * 10) / 10} aria-valuetext={`${secs(total)}, ${note}`}>
          <div className="h-1.5 overflow-hidden rounded-full bg-hover">
            <div className={cn('h-full rounded-full', state === 'out' ? 'bg-danger' : 'bg-success')} style={{ width: `${Math.min(100, (total / max) * 100)}%` }} />
          </div>
          <span className="absolute -top-0.5 h-2.5 w-px bg-line-strong" style={{ left: `${(min / max) * 100}%` }} aria-hidden />
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11.5px] text-faint">{note}</span>
          {canFit && (
            <Button size="sm" variant="ghost" onClick={() => replace(fitToDuration(spec, 50, maxScene))} title="Scale every scene (and what happens inside it) so the reel is 50 s long">
              <Ruler className="size-3.5" aria-hidden /> Fit to 50 s
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

export function BuildPanel() {
  const spec = useProject((s) => s.spec) as ReelSpec
  const open = useStudio((s) => s.buildStep)
  const set = useStudio((s) => s.set)
  // the scene being built, as a number: the panel does not redraw with every frame of a playing reel, only when the scene changes
  const si = useProject((s) => (s.spec ? outlineScene(s.spec, s.selection, s.playhead) : 0))
  const sc = spec.scenes[si]
  // a step that opens (from its heading, or from "Next") is brought to the top of the panel, and takes the keyboard focus when the button
  // that opened it went away with the step before it; the first render shows the panel as it is
  const items = useRef(new Map<BuildStep, HTMLLIElement>())
  const first = useRef(true)
  useEffect(() => {
    if (first.current) return void (first.current = false)
    if (!open) return
    items.current.get(open)?.scrollIntoView?.({ block: 'start' })
    if (!document.activeElement || document.activeElement === document.body) document.getElementById(`build-${open}-h`)?.focus({ preventScroll: true })
  }, [open])
  if (!sc) return null
  return (
    <div role="region" aria-label="Build a reel from the library" className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <BuildHeader spec={spec} si={si} />
      {isBare(spec) && <p className="border-b border-line px-3 py-2 text-[12px] leading-snug text-muted">Open a step and pick from the library. Fine-tune anything in the Inspector.</p>}
      <ol className="m-0 p-0">
        {STEPS.map((step) => {
          const isOpen = open === step.value
          const held = step.held(sc)
          const Icon = step.icon
          return (
            <li
              key={step.value}
              ref={(el) => {
                if (el) items.current.set(step.value, el)
              }}
              className="list-none border-b border-line"
            >
              <h3 className="m-0">
                <button type="button" id={`build-${step.value}-h`} aria-expanded={isOpen} aria-controls={`build-${step.value}`} onClick={() => set({ buildStep: isOpen ? null : step.value })} className={cn('flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-hover', isOpen && 'bg-hover/60')}>
                  <span className="grid size-6 shrink-0 place-items-center rounded-full bg-hover text-muted [&_svg]:size-3.5">
                    <Icon aria-hidden />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[13px] font-semibold">{step.label}</span>
                  {typeof held === 'string' ? <span className="max-w-[40%] truncate text-[11.5px] capitalize text-faint">{held}</span> : held > 0 ? <span className="rounded-full bg-hover px-1.5 text-[11px] tabular text-muted">{held}</span> : null}
                  <ChevronRight className={cn('size-3.5 shrink-0 text-faint transition-transform', isOpen && 'rotate-90')} aria-hidden />
                </button>
              </h3>
              {isOpen && (
                <div id={`build-${step.value}`} role="region" aria-labelledby={`build-${step.value}-h`} className="px-3 pb-3 pt-1">
                  <step.Body spec={spec} si={si} />
                </div>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
