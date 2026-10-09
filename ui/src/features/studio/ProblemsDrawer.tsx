import { AlertTriangle, CheckCircle2, ChevronDown, CircleAlert, Info, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useCatalog } from '@/api/hooks'
import type { LintIssue, ReelSpec } from '@/api/types'
import { Button, Chip, IconButton } from '@/components/ui'
import { clampMoveValues } from '@/lib/camera'
import { cn } from '@/lib/cn'
import { pathLabel, selectionFromPath, timeForPath } from '@/lib/spec'
import { fitToDuration } from '@/lib/timeline'
import { useLint } from '@/store/lint'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { fitObjectTimes } from './clips'

const ICON = { error: CircleAlert, warning: AlertTriangle, info: Info } as const
const TONE = { error: 'text-danger', warning: 'text-warning', info: 'text-info' } as const

interface Fix {
  label: string
  run: (d: ReelSpec) => void
}

/** A one-click repair for the issues that have an obvious one. */
function fixFor(issue: LintIssue, catalog: ReturnType<typeof useCatalog>['data']): Fix | null {
  if (issue.code === 'REGISTRY_MISSING' && issue.kind && issue.name) {
    const name = issue.name
    switch (issue.kind) {
      case 'action':
        return { label: 'Replace with idle', run: (d) => void d.scenes.forEach((s) => s.layers.forEach((l) => l.actions.forEach((a) => a.name === name && ((a.name = 'idle'), (a.params = {}))))) }
      case 'background':
        return { label: 'Use the abstract set', run: (d) => void d.scenes.forEach((s) => s.background.template === name && (s.background = { template: 'abstract', params: {} })) }
      case 'archetype':
        return { label: 'Use everyman', run: (d) => void d.characters.forEach((c) => c.archetype === name && (c.archetype = 'everyman')) }
      case 'style':
        return { label: 'Use flat vector', run: (d) => void (d.meta.style = 'flat_vector') }
      case 'transition':
        return { label: 'Use a cut', run: (d) => void d.scenes.forEach((s) => s.transition_out.type === name && (s.transition_out = { type: 'cut', duration: 0, params: {} })) }
      case 'caption_style':
        return { label: 'Use subtitle', run: (d) => void d.scenes.forEach((s) => s.captions.forEach((c) => c.style === name && (c.style = 'subtitle'))) }
      case 'sfx':
        return { label: 'Remove the sound', run: (d) => void d.scenes.forEach((s) => (s.sfx = s.sfx.filter((x) => x.name !== name))) }
      case 'object':
        return { label: 'Remove the object', run: (d) => void d.scenes.forEach((s) => (s.objects = s.objects.filter((o) => o.asset !== name))) }
    }
  }
  if (issue.code === 'CAMERA_MOVE_INVALID') {
    const at = /^scenes\[(\d+)\]\.camera\.moves\[(\d+)\]/.exec(issue.path)
    if (at) {
      return {
        label: 'Keep the camera on the set',
        run: (d) => {
          const mv = d.scenes[Number(at[1])]?.camera.moves[Number(at[2])]
          if (mv) clampMoveValues(mv as unknown as Record<string, unknown>)
        },
      }
    }
  }
  if (issue.code === 'DURATION_BUDGET') return { label: 'Fit to 50 s', run: () => undefined } // handled by the caller (needs a whole-spec replace)
  if (issue.code === 'TIME_OVERFLOW') return { label: 'Fit inside the scene', run: (d) => void clampToScenes(d, catalog?.limits.max_scene_sec ?? 30) }
  if (issue.code === 'POSITION_SLOT') {
    // an object's place, or the place one of its moves goes to: only that one is repaired
    const at = /^scenes\[(\d+)\]\.objects\[(\d+)\](?:\.motions\[(\d+)\]\.to|\.position)/.exec(issue.path)
    if (at) {
      return {
        label: 'Use “center”',
        run: (d) => {
          const o = d.scenes[Number(at[1])]?.objects[Number(at[2])]
          if (!o) return
          if (at[3] !== undefined) {
            const m = o.motions[Number(at[3])]
            if (m) m.to = 'center'
          } else o.position = 'center'
        },
      }
    }
  }
  if (issue.code === 'POSITION_SLOT') return { label: 'Use “center”', run: (d) => void d.scenes.forEach((s) => s.layers.forEach((l) => typeof l.position === 'string' && !(catalog?.universal_slots ?? []).includes(l.position) && (l.position = 'center'))) }
  return null
}

function clampToScenes(d: ReelSpec, _max: number): void {
  for (const s of d.scenes) {
    for (const l of s.layers) for (const a of l.actions) ((a.t1 = Math.min(a.t1, s.duration_sec)), (a.t0 = Math.min(a.t0, Math.max(0, a.t1 - 0.1))))
    for (const c of s.captions) ((c.t1 = Math.min(c.t1, s.duration_sec)), (c.t0 = Math.min(c.t0, Math.max(0, c.t1 - 0.1))))
    for (const m of s.camera.moves) ((m.t1 = Math.min(m.t1, s.duration_sec)), (m.t0 = Math.min(m.t0, Math.max(0, m.t1 - 0.1))))
    for (const o of s.objects ?? []) fitObjectTimes(o, s.duration_sec)
  }
}

function Row({ issue, spec }: { issue: LintIssue; spec: ReelSpec }) {
  const catalog = useCatalog().data
  const select = useProject((s) => s.select)
  const setPlayhead = useProject((s) => s.setPlayhead)
  const edit = useProject((s) => s.edit)
  const replace = useProject((s) => s.replace)
  const Icon = ICON[issue.severity]
  const sel = selectionFromPath(spec, issue.path)
  const fix = fixFor(issue, catalog)
  const goto = () => {
    if (!sel) return
    select(sel)
    if ('scene' in sel) {
      // an object or a motion is shown at the moment it appears; anything else at the start of its scene
      const at = timeForPath(spec, issue.path)
      if (at !== null) setPlayhead(at)
    }
  }
  return (
    <li className="flex items-start gap-3 border-b border-line px-4 py-2.5 last:border-0">
      <Icon className={cn('mt-0.5 size-4 shrink-0', TONE[issue.severity])} aria-label={issue.severity} />
      <div className="min-w-0 flex-1">
        <div className="text-[12.5px] leading-snug">{issue.message}</div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11.5px] text-faint">
          <span className="font-medium text-muted">{pathLabel(spec, issue.path)}</span>
          <code className="font-mono">{issue.path || 'spec'}</code>
          <Chip className="h-4 px-1 text-[10px]">{issue.code}</Chip>
        </div>
        {issue.hint && <div className="mt-1 text-[12px] text-muted">{issue.hint}</div>}
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        {fix && (
          <Button size="sm" onClick={() => (issue.code === 'DURATION_BUDGET' ? replace(fitToDuration(spec, 50, catalog?.limits.max_scene_sec ?? 30)) : edit(fix.run))}>
            {fix.label}
          </Button>
        )}
        {sel && (
          <Button size="sm" variant="ghost" onClick={goto}>
            Go to
          </Button>
        )}
      </div>
    </li>
  )
}

export function ProblemsDrawer() {
  const spec = useProject((s) => s.spec) as ReelSpec
  const report = useLint((s) => s.report)
  const busy = useLint((s) => s.busy)
  const set = useStudio((s) => s.set)
  const edit = useProject((s) => s.edit)
  const catalog = useCatalog().data
  const [filter, setFilter] = useState<'all' | 'error' | 'warning' | 'info'>('all')
  const issues = useMemo(() => (report?.issues ?? []).filter((i) => filter === 'all' || i.severity === filter), [report, filter])
  // every problem that has a one-click repair, repaired together as one undo step (fitting to 50 s needs a whole-spec replace, so it stays its own button)
  const fixable = useMemo(() => (report?.issues ?? []).flatMap((i) => (i.code === 'DURATION_BUDGET' ? [] : [fixFor(i, catalog)])).filter((f): f is Fix => f !== null), [report, catalog])
  const c = report?.counts
  return (
    <div className="pop absolute inset-x-0 bottom-0 z-30 flex max-h-[min(60%,420px)] min-h-[160px] flex-col border-t border-line-strong bg-panel shadow-[0_-12px_40px_rgb(0_0_0/0.25)]" role="region" aria-label="Problems">
      <div className="flex h-10 shrink-0 items-center gap-3 border-b border-line px-3">
        <h2 className="text-[13px] font-semibold">Problems</h2>
        {report && (
          <div className="flex items-center gap-1.5" role="group" aria-label="Filter problems">
            {(
              [
                ['all', `All ${issues.length === (report?.issues.length ?? 0) ? (report?.issues.length ?? 0) : ''}`.trim()],
                ['error', `${c?.errors ?? 0} errors`],
                ['warning', `${c?.warnings ?? 0} warnings`],
                ['info', `${c?.infos ?? 0} notes`],
              ] as const
            ).map(([k, label]) => (
              <button key={k} aria-pressed={filter === k} onClick={() => setFilter(k)} className={cn('rounded-full border px-2.5 py-0.5 text-[11.5px] font-medium', filter === k ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:text-fg')}>
                {label}
              </button>
            ))}
          </div>
        )}
        {busy && <span className="text-[11.5px] text-faint">checking…</span>}
        <div className="flex-1" />
        {fixable.length > 1 && (
          <Button size="sm" onClick={() => edit((d) => void fixable.forEach((f) => f.run(d)))} title="Apply every one-click repair below, as one undo step">
            Fix all {fixable.length}
          </Button>
        )}
        <IconButton label="Close problems" size="icon-sm" onClick={() => set({ problemsOpen: false })}>
          <X className="size-4" />
        </IconButton>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {report && report.issues.length === 0 && (
          <div className="flex items-center gap-2 px-4 py-6 text-success">
            <CheckCircle2 className="size-5" aria-hidden />
            <span className="font-medium">No problems: this reel passes every check the renderer makes.</span>
          </div>
        )}
        {report && report.issues.length > 0 && issues.length === 0 && <p className="px-4 py-6 text-muted">Nothing in this filter.</p>}
        <ul className="m-0 p-0">
          {issues.map((i, n) => (
            <Row key={`${i.code}-${i.path}-${n}`} issue={i} spec={spec} />
          ))}
        </ul>
        {!report && <p className="px-4 py-6 text-muted">Checking the spec…</p>}
      </div>
    </div>
  )
}

export { ChevronDown }
