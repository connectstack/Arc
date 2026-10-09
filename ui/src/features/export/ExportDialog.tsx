import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Clapperboard, Clock, Copy, Download, Film, Loader2, RotateCcw, ShieldAlert, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApi } from '@/api/context'
import { useEngines, useRenders } from '@/api/hooks'
import type { PresetName, ReelSpec, RenderBody, RenderResult } from '@/api/types'
import { ApiError } from '@/api/types'
import { Banner, Button, Chip, Dialog, Field, NumberField, ProgressBar, Segmented, SliderField, SwitchRow, toast } from '@/components/ui'
import { cn } from '@/lib/cn'
import { audioKey } from '@/store/audio'
import { bytes, mbps, plural, seconds } from '@/lib/format'
import { useJobs } from '@/store/jobs'
import { useLint } from '@/store/lint'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { useUi } from '@/store/ui'
import { announceRender } from './finished'

const PRESETS: { value: PresetName; title: string; size: string; blurb: string }[] = [
  { value: 'draft', title: 'Draft', size: '360 × 640', blurb: 'Fast: to check the story and the timing' },
  { value: 'standard', title: 'Standard', size: '540 × 960', blurb: 'A sharper preview to share for feedback' },
  { value: 'full', title: 'Full HD', size: '1080 × 1920', blurb: 'The real thing: for posting' },
  { value: 'custom', title: 'Custom', size: 'your settings', blurb: 'Pick the size, quality and range' },
]

const PHASE: Record<string, string> = { plan: 'Checking the spec', audio: 'Making the voice and sound', frames: 'Drawing the frames', mux: 'Putting the video together' }

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-ctl bg-raised px-3 py-2">
      <div className="text-[11px] text-faint">{label}</div>
      <div className="font-semibold tabular">{value}</div>
    </div>
  )
}

export function ExportDialog() {
  const api = useApi()
  const qc = useQueryClient()
  const nav = useNavigate()
  const spec = useProject((s) => s.spec) as ReelSpec
  const projectId = useProject((s) => s.id) as string
  const lint = useLint((s) => s.report)
  const loop = useStudio((s) => s.loop)
  const initial = useStudio((s) => s.exportPreset)
  const setStudio = useStudio((s) => s.set)
  const ttsPref = useUi((s) => s.ttsEngine)
  const ui = useUi()
  const engines = useEngines().data
  const history = useRenders()
  const jobs = useJobs((s) => s.jobs)
  const order = useJobs((s) => s.order)
  const track = useJobs((s) => s.track)
  const cancel = useJobs((s) => s.cancel)

  const mine = order.map((id) => jobs[id]).find((j) => j?.kind === 'render' && j.projectId === projectId)
  const [preset, setPreset] = useState<PresetName>(initial)
  const [view, setView] = useState<'config' | 'progress' | 'result'>(() => {
    if (mine && (mine.status === 'running' || mine.status === 'queued')) return 'progress'
    return mine?.status === 'done' && useStudio.getState().exportShowResult ? 'result' : 'config'
  })
  const [crf, setCrf] = useState(20)
  const [maxrate, setMaxrate] = useState('10M')
  const [scale, setScale] = useState(1)
  const [workers, setWorkers] = useState(0)
  const [lenient, setLenient] = useState(false)
  const [noAudio, setNoAudio] = useState(false)
  const [useLoop, setUseLoop] = useState(false)
  const [confirmSpend, setConfirmSpend] = useState(false)
  const [starting, setStarting] = useState(false)
  const [removing, setRemoving] = useState<RenderResult | null>(null)

  const tts = ttsPref === 'auto' ? undefined : ttsPref
  const chosen = ttsPref === 'auto' ? (engines?.default ?? 'babble') : ttsPref
  const engine = engines?.engines.find((e) => e.name === chosen)
  const speaking = spec.audio.voiceover === 'tts' && !noAudio

  const body = useMemo<RenderBody>(
    () => ({
      spec,
      project_id: projectId,
      preset,
      ...(preset === 'custom' ? { scale, crf, maxrate, workers: workers || undefined } : {}),
      ...(preset === 'full' ? { workers: workers || undefined } : {}),
      lenient: lenient || preset === 'draft' ? true : undefined,
      no_audio: noAudio || undefined,
      tts,
      range: useLoop && loop ? ([loop[0], loop[1]] as [number, number]) : undefined,
    }),
    [spec, projectId, preset, scale, crf, maxrate, workers, lenient, noAudio, tts, useLoop, loop],
  )

  const plan = useQuery({
    queryKey: ['plan', projectId, preset, scale, crf, maxrate, useLoop, loop, JSON.stringify(spec)],
    queryFn: () => api.renderPlan(body),
    enabled: view === 'config',
    staleTime: 3_000,
  })
  const estimate = useQuery({
    queryKey: ['estimate', chosen, audioKey(spec)],
    queryFn: () => api.estimateSpeech(spec, chosen),
    enabled: view === 'config' && speaking,
    staleTime: 3_000,
  })
  const billable = estimate.data?.online ? (estimate.data.billable_characters ?? 0) : 0

  useEffect(() => {
    if (useStudio.getState().exportShowResult) setStudio({ exportShowResult: false })
  }, [setStudio])

  useEffect(() => {
    if (mine?.status === 'done' && view === 'progress') setView('result')
  }, [mine?.status, view])

  const errors = lint?.counts.errors ?? 0
  const blocked = errors > 0 && preset !== 'draft' && !lenient

  const go = async () => {
    setStarting(true)
    try {
      const r = await api.render(body)
      track(api, r.job_id, { kind: 'render', title: `Render (${preset})`, projectId }, { onDone: (res) => announceRender(qc, nav, projectId, res) })
      setView('progress')
    } catch (e) {
      toast.error('Could not start the render', e instanceof ApiError ? `${e.detail}${e.hint ? ` — ${e.hint}` : ''}` : String(e))
    } finally {
      setStarting(false)
    }
  }
  const start = () => (billable > 0 && !ui.consent[engine?.destination ?? ''] ? setConfirmSpend(true) : void go())

  const close = (o: boolean) => !o && setStudio({ exportOpen: false })
  const result = mine?.status === 'done' ? (mine.result as unknown as RenderResult) : null
  const running = mine && (mine.status === 'running' || mine.status === 'queued')

  return (
    <Dialog
      open
      onOpenChange={close}
      size="lg"
      title={view === 'result' ? 'Your video is ready' : view === 'progress' ? 'Rendering' : 'Render a video'}
      description={view === 'config' ? 'The same spec gives the same video every time; unchanged parts are reused, so a second render is quick.' : spec.meta.title}
      footer={
        view === 'config' ? (
          <>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button variant="primary" size="lg" disabled={blocked || starting} onClick={start}>
              {starting ? <Loader2 className="size-4 animate-[spin_1s_linear_infinite]" /> : <Clapperboard className="size-4" />}
              Render {PRESETS.find((p) => p.value === preset)?.title}
              {plan.data && <span className="font-normal">· about {plan.data.est_seconds < 1 ? '1' : Math.round(plan.data.est_seconds)} s</span>}
            </Button>
          </>
        ) : view === 'progress' ? (
          <>
            {running && (
              <Button variant="danger" onClick={() => mine && void cancel(api, mine.id)}>
                Cancel render
              </Button>
            )}
            {!running && <Button onClick={() => setView('config')}>Back to settings</Button>}
          </>
        ) : (
          <>
            <Button onClick={() => setView('config')}>
              <RotateCcw className="size-4" /> Render again
            </Button>
            <Button variant="primary" onClick={() => close(false)}>
              Done
            </Button>
          </>
        )
      }
    >
      {view === 'config' && (
        <div className="flex flex-col gap-5">
          <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label="Render preset">
            {PRESETS.map((p) => (
              <button key={p.value} role="radio" aria-checked={preset === p.value} onClick={() => setPreset(p.value)} className={cn('rounded-card border-2 p-3.5 text-left transition-colors', preset === p.value ? 'border-accent bg-accent-soft/60' : 'border-line hover:border-line-strong')}>
                <div className="flex items-center justify-between">
                  <span className="font-semibold">{p.title}</span>
                  <span className="font-mono text-[11.5px] text-muted">{p.size}</span>
                </div>
                <div className="mt-0.5 text-[12px] text-muted">{p.blurb}</div>
              </button>
            ))}
          </div>

          <div className="rounded-card border border-line bg-raised p-3.5 text-[13px]" aria-live="polite">
            {plan.isLoading && <span className="text-muted">Working out what has to be drawn…</span>}
            {plan.isError && <span className="text-warning">{plan.error instanceof ApiError ? plan.error.detail : 'Could not plan the render'}</span>}
            {plan.data && (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
                <span>
                  <b className="font-semibold tabular">{plan.data.segments_cached}</b> of <b className="font-semibold tabular">{plan.data.segments_total}</b> chunks are already cached
                </span>
                <span className="flex items-center gap-1.5 text-muted">
                  <Clock className="size-3.5" aria-hidden /> about <b className="font-semibold text-fg tabular">{plan.data.est_seconds < 1 ? 'a second' : seconds(plan.data.est_seconds, 0)}</b> to draw the rest
                </span>
                <span className="text-muted tabular">
                  {plan.data.width}×{plan.data.height} · {seconds(plan.data.duration_sec)}
                </span>
                {plan.data.voice === 'pending' && (
                  <p className="m-0 basis-full text-[12px] text-muted">The voice is not generated yet. Captions are retimed to it, so most chunks are drawn fresh this time and reused afterwards.</p>
                )}
              </div>
            )}
          </div>

          {preset === 'custom' && (
            <div className="grid gap-5 sm:grid-cols-2">
              <Field label="Size" hint={plan.data ? `${plan.data.width} × ${plan.data.height}` : undefined}>
                <SliderField label="Size" value={scale} min={0.15} max={1} step={0.05} onChange={setScale} format={(v) => `${Math.round(v * 100)}%`} />
              </Field>
              <Field label="Quality" hint="Lower is better and bigger (20 is a good default)">
                <SliderField label="Quality (CRF)" value={crf} min={14} max={30} step={1} onChange={setCrf} />
              </Field>
              <Field label="Size cap" hint="A bitrate ceiling keeps the file shareable">
                <Segmented label="Size cap" value={maxrate} onChange={setMaxrate} options={[{ value: 'none', label: 'None' }, { value: '6M', label: '6M' }, { value: '10M', label: '10M' }, { value: '16M', label: '16M' }]} size="sm" />
              </Field>
              <Field label="Workers" hint="0 = automatic (all cores but two)">
                <NumberField value={workers} onChange={(v) => setWorkers(Math.max(0, Math.round(v)))} min={0} max={32} aria-label="Workers" />
              </Field>
            </div>
          )}

          <div className="flex flex-col gap-3">
            <SwitchRow label="Include sound" hint={speaking ? `Voice: ${engine?.label ?? chosen}${engine?.online ? ' (online)' : ''}` : 'Music and effects from the spec'} checked={!noAudio} onChange={(v) => setNoAudio(!v)} />
            {loop && <SwitchRow label="Only the looped range" hint={`${loop[0].toFixed(1)}–${loop[1].toFixed(1)} s (the In/Out markers)`} checked={useLoop} onChange={setUseLoop} />}
            {errors > 0 && preset !== 'draft' && <SwitchRow label="Render anyway (lenient)" hint="Unknown names fall back (action → idle, background → abstract)" checked={lenient} onChange={setLenient} />}
          </div>

          {billable > 0 && (
            <Banner tone="warning" title={`This render will bill ${billable.toLocaleString()} characters`}>
              {engine?.label} speaks {estimate.data?.new_lines} new {estimate.data?.new_lines === 1 ? 'line' : 'lines'} ({estimate.data?.cached_lines} cached lines are free). Generate the voice first in Voice & Audio if you want to hear it before rendering.
            </Banner>
          )}
          {blocked && (
            <Banner tone="danger" title={`The spec has ${plural(errors, 'error')}`}>
              Fix them in Problems, or switch on “Render anyway (lenient)”. Drafts always render leniently.
            </Banner>
          )}
        </div>
      )}

      {view === 'progress' && mine && (
        <div className="flex flex-col gap-4 py-2">
          {mine.status === 'error' || mine.status === 'cancelled' ? (
            <Banner tone={mine.status === 'error' ? 'danger' : 'info'} title={mine.status === 'cancelled' ? 'The render was cancelled' : (mine.error?.message ?? 'The render failed')}>
              {mine.status === 'cancelled' ? 'Nothing half-written is left behind.' : mine.error?.hint}
            </Banner>
          ) : (
            <>
              <div className="flex items-center gap-2.5">
                <Loader2 className="size-5 animate-[spin_1s_linear_infinite] text-accent" />
                <div className="min-w-0 flex-1">
                  <div className="font-semibold">{mine.status === 'queued' ? 'Waiting for the renderer…' : (PHASE[mine.phase ?? 'plan'] ?? 'Working')}</div>
                  <div className="text-[12px] text-muted tabular">
                    {mine.phase === 'frames' ? `${mine.done.toLocaleString()} of ${mine.total.toLocaleString()} frames` : mine.status === 'queued' ? `queue position ${mine.queuePosition ?? 1}` : ''}
                    {mine.etaSec ? ` · about ${Math.max(1, Math.round(mine.etaSec))} s left` : ''}
                  </div>
                </div>
                <span className="font-mono text-[18px] font-semibold tabular">{mine.total > 1 ? `${Math.round((mine.done / mine.total) * 100)}%` : ''}</span>
              </div>
              <ProgressBar value={mine.phase === 'frames' ? mine.done : 0} max={mine.phase === 'frames' ? mine.total : 1} label="Render progress" />
            </>
          )}
          {(mine.notes.length > 0 || mine.warnings.length > 0) && (
            <ul className="m-0 flex max-h-44 flex-col gap-1 overflow-y-auto rounded-card border border-line bg-raised p-3 text-[12.5px]">
              {mine.notes.map((n, i) => (
                <li key={`n${i}`} className="list-none text-muted">
                  {n}
                </li>
              ))}
              {mine.warnings.map((w, i) => (
                <li key={`w${i}`} className="list-none text-warning">
                  {w}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {view === 'result' && result && (
        <div className="grid gap-5 md:grid-cols-[260px_1fr]">
          <div className="mx-auto w-full max-w-[260px] overflow-hidden rounded-[18px] bg-black shadow-[0_0_0_4px_var(--raised)]">
            <video src={result.video_url} controls playsInline className="aspect-[9/16] w-full bg-black" aria-label={`Rendered video: ${result.title ?? ''}`} />
          </div>
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Chip tone="success">
                <CheckCircle2 className="size-3" aria-hidden /> Rendered
              </Chip>
              <Chip>{result.preset}</Chip>
              {result.has_audio && <Chip>with sound</Chip>}
            </div>
            <div className="grid grid-cols-2 gap-2 text-[13px]">
              <Stat label="Size" value={bytes(result.size_bytes)} />
              <Stat label="Resolution" value={`${result.width} × ${result.height}`} />
              <Stat label="Length" value={seconds(result.duration_sec)} />
              <Stat label="Bitrate" value={mbps(result.bitrate_bps)} />
              <Stat label="Chunks" value={`${result.segments.reused} reused · ${result.segments.encoded} drawn`} />
              <Stat label="Took" value={seconds(result.seconds)} />
            </div>
            {result.notes.map((n, i) => (
              <Banner key={i} tone="info">
                {n}
              </Banner>
            ))}
            {result.warnings.map((w, i) => (
              <Banner key={i} tone="warning">
                {w}
              </Banner>
            ))}
            <div className="flex flex-wrap items-center gap-2">
              <a href={result.video_url} download={`${result.id}.mp4`} className="inline-flex h-[var(--ctl-h)] items-center gap-1.5 rounded-ctl bg-solid px-3 font-medium text-accent-fg hover:bg-solid-hover">
                <Download className="size-4" /> Download MP4
              </a>
              <Button
                onClick={() => {
                  void navigator.clipboard?.writeText(result.path)
                  toast.success('Path copied', result.path)
                }}
              >
                <Copy className="size-4" /> Copy file path
              </Button>
            </div>
            <p className="break-all font-mono text-[11.5px] text-faint">{result.path}</p>
          </div>
        </div>
      )}

      {view === 'config' && (history.data?.length ?? 0) > 0 && (
        <details className="mt-5 rounded-card border border-line">
          <summary className="cursor-pointer px-4 py-2.5 text-[12.5px] font-medium text-muted hover:text-fg">
            <Film className="mr-1.5 inline size-3.5" aria-hidden /> Earlier renders ({history.data?.length})
          </summary>
          <ul className="m-0 divide-y divide-line p-0">
            {(history.data ?? []).slice(0, 8).map((r) => (
              <li key={r.id} className="flex list-none items-center gap-3 px-4 py-2 text-[12.5px]">
                <span className="min-w-0 flex-1 truncate font-medium">{r.title ?? r.id}</span>
                <span className="text-faint">
                  {r.preset} · {bytes(r.size_bytes)}
                </span>
                <a href={r.video_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                  Play
                </a>
                <button aria-label={`Delete the render ${r.title ?? r.id}`} title="Delete this video" onClick={() => setRemoving(r)} className="rounded p-1 text-faint hover:bg-hover hover:text-danger">
                  <Trash2 className="size-3.5" />
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}

      <Dialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        size="sm"
        title="Delete this video?"
        description={removing ? `${removing.title ?? removing.id} · ${removing.preset} · ${bytes(removing.size_bytes)}` : undefined}
        footer={
          <>
            <Button onClick={() => setRemoving(null)}>Keep it</Button>
            <Button
              variant="danger"
              onClick={async () => {
                const r = removing
                setRemoving(null)
                if (!r) return
                try {
                  await api.deleteRender(r.id)
                  await qc.invalidateQueries({ queryKey: ['renders'] })
                  toast.success('Video deleted')
                } catch (e) {
                  toast.error('Could not delete the video', e instanceof ApiError ? e.detail : String(e))
                }
              }}
            >
              <Trash2 className="size-4" /> Delete the video
            </Button>
          </>
        }
      >
        <p className="text-[13px]">The video file is removed from the workspace. The project itself is not touched, and you can render it again any time.</p>
      </Dialog>

      <Dialog
        open={confirmSpend}
        onOpenChange={setConfirmSpend}
        size="sm"
        title={`Send text to ${engine?.destination ?? 'an online service'}?`}
        description="The voice for this render is online and billed."
        footer={
          <>
            <Button onClick={() => setConfirmSpend(false)}>Cancel</Button>
            <Button
              variant="primary"
              onClick={() => {
                if (engine?.destination) ui.setConsent(engine.destination, true)
                setConfirmSpend(false)
                void go()
              }}
            >
              <ShieldAlert className="size-4" /> Yes, render and bill {billable.toLocaleString()} characters
            </Button>
          </>
        }
      >
        <p className="text-[13px]">
          The spoken lines are sent to <b>{engine?.destination}</b> with your key (which stays on the server). Lines already generated are reused for free.
        </p>
      </Dialog>
    </Dialog>
  )
}
