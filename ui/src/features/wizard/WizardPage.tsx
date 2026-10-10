import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, ArrowRight, Check, CheckCircle2, ChevronDown, Dice5, FileText, Hammer, Loader2, Sparkles, XCircle } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApi } from '@/api/context'
import { useCatalog, useDoctor, useExamples } from '@/api/hooks'
import type { AssetInfo, Coverage, GenerateResult, LibraryGap, LintReport, ReelSpec } from '@/api/types'
import { ApiError } from '@/api/types'
import { Page, PageHeader } from '@/components/shell/PageHeader'
import { Banner, Button, Chip, Field, IconButton, Input, NumberField, Segmented, SliderField, SwitchRow, Textarea, toast } from '@/components/ui'
import { cn } from '@/lib/cn'
import { plural, titleCase, words } from '@/lib/format'
import { ScratchDialog } from '@/features/projects/ScratchDialog'
import { useJobs } from '@/store/jobs'
import { useUi } from '@/store/ui'
import { CoveragePanel } from './CoveragePanel'
import { AllFilled, GapsCard } from './GapsCard'
import { useScriptCoverage } from './useScriptCoverage'

type Planner = 'offline' | 'ollama' | 'openai' | 'anthropic'
const STEPS = ['Script', 'Look & planner', 'Generating'] as const

const HOSTS: Record<string, string> = { openai: 'api.openai.com', anthropic: 'api.anthropic.com' }

function StepDots({ step }: { step: number }) {
  return (
    <ol className="flex items-center gap-2 sm:gap-3" aria-label="Steps">
      {STEPS.map((s, i) => (
        <li key={s} className="flex items-center gap-2" aria-current={i === step ? 'step' : undefined}>
          <span className={cn('grid size-6 place-items-center rounded-full text-[11px] font-semibold', i < step ? 'bg-success text-bg' : i === step ? 'bg-solid text-accent-fg' : 'bg-hover text-faint')}>{i < step ? <Check className="size-3.5" /> : i + 1}</span>
          <span className={cn('text-[12.5px] font-medium', i === step ? 'text-fg' : 'hidden text-muted sm:inline')}>{s}</span>
          {i < STEPS.length - 1 && <span className="mx-1 hidden h-px w-8 bg-line-strong sm:block" aria-hidden />}
        </li>
      ))}
    </ol>
  )
}

export function WizardPage() {
  const api = useApi()
  const nav = useNavigate()
  const qc = useQueryClient()
  const catalog = useCatalog().data
  const examples = useExamples().data
  const doctor = useDoctor().data
  const ui = useUi()
  const track = useJobs((s) => s.track)

  const [step, setStep] = useState(0)
  const [script, setScript] = useState('')
  const [title, setTitle] = useState('')
  const [style, setStyle] = useState(ui.defaultStyle)
  const [length, setLength] = useState(50)
  const [planner, setPlanner] = useState<Planner>((ui.defaultPlanner as Planner) || 'offline')
  const [ollamaModel, setOllamaModel] = useState('')
  const [seed, setSeed] = useState(() => Math.floor(Math.random() * 9999))
  const [repairs, setRepairs] = useState(3)
  const [enrich, setEnrich] = useState(true)
  const [verbatim, setVerbatim] = useState(true)
  const [advanced, setAdvanced] = useState(false)
  const [scratchOpen, setScratchOpen] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined))
  const [created, setCreated] = useState<{ id: string } | null>(null)
  /** a planned reel that still lacks library things: kept open (not redirected) so they can be added before the studio opens */
  const [ready, setReady] = useState<{ id: string; etag: string; spec: ReelSpec; gaps: LibraryGap[] } | null>(null)
  const started = useRef(false)

  const wordCount = useMemo(() => words(script), [script])
  const coverage = useScriptCoverage(script)
  const estSeconds = wordCount / 2.5
  const llm = doctor?.llm
  const keyOf = (name: string) => llm?.keys.find((k) => k.name === name)?.source ?? null
  const localModels = (llm?.ollama.models ?? []).filter((m) => !m.remote)
  const options: { value: Planner; label: string; ok: boolean; reason?: string }[] = [
    { value: 'offline', label: 'Offline planner', ok: true },
    { value: 'ollama', label: 'Ollama (local)', ok: !!llm?.ollama.running && localModels.length > 0, reason: llm?.ollama.running ? 'no local model is pulled yet (`ollama pull llama3.1`)' : 'Ollama is not running (`ollama serve`)' },
    { value: 'openai', label: 'OpenAI', ok: !!keyOf('OPENAI_API_KEY'), reason: 'no key found: set OPENAI_API_KEY in your shell or a git-ignored .env' },
    { value: 'anthropic', label: 'Claude', ok: !!keyOf('ANTHROPIC_API_KEY'), reason: 'no key found: set ANTHROPIC_API_KEY in your shell or a git-ignored .env' },
  ]
  const current = options.find((o) => o.value === planner) ?? options[0]
  const host = HOSTS[planner]
  const consented = !host || ui.consent[host]
  const plannerSpec = planner === 'ollama' ? `ollama:${ollamaModel || localModels[0]?.name || 'llama3.1'}` : planner

  const generate = useMutation({
    mutationFn: async () => {
      const r = await api.generate({ script, style, target_duration: length, seed, planner: plannerSpec, enrich, repairs, verbatim })
      return r.job_id
    },
    onSuccess: (id) => {
      setJobId(id)
      track(api, id, { kind: 'generate', title: 'Script to reel' }, { onDone: (res) => void onDone(res as unknown as GenerateResult) })
    },
    onError: (e) => toast.error('Could not start planning', e instanceof ApiError ? `${e.detail}${e.hint ? ` — ${e.hint}` : ''}` : String(e)),
  })

  const onDone = async (res: GenerateResult) => {
    const name = title.trim() || script.trim().split('\n')[0].slice(0, 60) || 'Untitled reel'
    const spec: ReelSpec = { ...res.spec, meta: { ...res.spec.meta, title: name, style } }
    const doc = await api.createProject({ spec, script, title: name })
    void qc.invalidateQueries({ queryKey: ['projects'] })
    setCreated({ id: doc.id })
    const gaps = spec.meta.library_gaps ?? []
    if (gaps.length) {
      setReady({ id: doc.id, etag: doc.etag, spec: doc.spec, gaps })
      return
    }
    setTimeout(() => nav(`/p/${doc.id}`), 900)
  }

  /** An asset was added for one gap: swap it into the saved reel (the stand-in character becomes it, the place is used, the object is placed). */
  const onFilled = async (_asset: AssetInfo, gap: LibraryGap) => {
    if (!ready) return
    try {
      const r = await api.fillGaps(ready.spec, [{ kind: gap.kind, name: gap.name }])
      if (!r.filled.length) {
        toast.info('Added to the library', `“${gap.name}” is there now, but this reel could not be changed to use it: open the studio and place it yourself.`)
        return
      }
      const saved = await api.saveProject(ready.id, r.spec, script, ready.etag)
      setReady({ id: ready.id, etag: saved.etag, spec: saved.spec, gaps: r.pending })
      toast.success(`${gap.name} is in your reel`)
    } catch (e) {
      toast.error('Could not update the reel', e instanceof ApiError ? e.detail : String(e))
    }
  }

  const start = () => {
    setStep(2)
    started.current = true
    generate.mutate()
  }

  const manual = async (spec: ReelSpec) => {
    const doc = await api.createProject({ spec, script, title: title.trim() || 'Draft to fix' })
    void qc.invalidateQueries({ queryKey: ['projects'] })
    nav(`/p/${doc.id}`)
  }

  const canNext0 = script.trim().length > 10
  const canStart = canNext0 && current.ok && consented && (planner !== 'ollama' || !!(ollamaModel || localModels[0]))

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title="New reel" subtitle="From a plain-text script to a first cut: the planner lays out scenes, characters, camera and sound." actions={<StepDots step={step} />} />
      <Page>
        <div className="mx-auto max-w-[980px] px-4 py-6 sm:px-6">
          {step === 0 && (
            <div className="grid gap-5 lg:grid-cols-[1fr_260px]">
              <div className="flex flex-col gap-3">
                <Field label="Your script" hint={<>Write plain narration, or dialogue as <code className="rounded bg-raised px-1 font-mono text-[11px]">Name: line</code>. Any language works.</>}>
                  <Textarea autoFocus value={script} onChange={(e) => setScript(e.target.value)} rows={16} aria-label="Your script" placeholder={'The Lost Umbrella\n\nMia: Rain, rain, go away!\nPip: Oh no! Where is my umbrella?\n…'} className="min-h-[360px] text-[14px]" />
                </Field>
                <div className="flex flex-wrap items-center gap-3 text-[12px] text-muted">
                  <span className="tabular">
                    <b className="font-semibold text-fg">{plural(wordCount, 'word')}</b> · about {Math.round(estSeconds)} s spoken
                  </span>
                  {wordCount > 0 && estSeconds < 40 && <Chip tone="warning">a little short for 45–60 s: the planner will pace scenes out</Chip>}
                  {estSeconds > 75 && <Chip tone="warning">long: the planner will trim and compress</Chip>}
                </div>
                <CoveragePanel coverage={coverage.data} loading={coverage.isFetching} />
              </div>
              <div className="flex flex-col gap-3">
                <Field label="Title" hint="Optional: it defaults to the first line">
                  <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={script.trim().split('\n')[0].slice(0, 40) || 'Untitled reel'} aria-label="Title" />
                </Field>
                <div className="flex flex-col items-start gap-1.5 rounded-card border border-line bg-raised p-3">
                  <div className="font-medium">No script?</div>
                  <p className="text-[12px] leading-snug text-muted">Build the reel yourself: pick backgrounds, characters, objects, actions and sounds from the library.</p>
                  <Button size="sm" onClick={() => setScratchOpen(true)}>
                    <Hammer className="size-3.5" aria-hidden /> Build from scratch
                  </Button>
                </div>
                <div>
                  <div className="eyebrow mb-1.5">Try an example</div>
                  <div className="flex flex-col gap-1.5">
                    {(examples?.scripts ?? []).map((ex) => (
                      <button key={ex.id} onClick={() => (setScript(ex.text), setTitle(''))} className="flex items-center gap-2.5 rounded-card border border-line bg-raised px-3 py-2 text-left hover:border-accent">
                        <FileText className="size-4 shrink-0 text-muted" aria-hidden />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-medium">{ex.title}</span>
                          <span className="text-[11.5px] text-faint">
                            {plural(ex.words, 'word')} · {ex.language === 'en' ? 'English' : ex.language === 'hi' ? 'Hindi' : ex.language}
                          </span>
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="flex flex-col gap-8">
              <section aria-labelledby="style-h">
                <h2 id="style-h" className="mb-3 text-[15px] font-semibold">
                  Choose a look
                </h2>
                <div className="grid gap-4 sm:grid-cols-3" role="radiogroup" aria-label="Visual style">
                  {(catalog?.styles ?? []).map((s) => (
                    <button key={s.name} role="radio" aria-checked={style === s.name} onClick={() => setStyle(s.name)} className={cn('group relative overflow-hidden rounded-card border-2 text-left transition-colors', style === s.name ? 'border-accent' : 'border-line hover:border-line-strong')}>
                      <img src={api.libraryThumbUrl('style', s.name, 'day')} alt={`${titleCase(s.name)} sample frame`} loading="lazy" className="aspect-[9/12] w-full object-cover" />
                      {style === s.name && (
                        <span className="absolute right-2.5 top-2.5 grid size-6 place-items-center rounded-full bg-solid text-accent-fg">
                          <Check className="size-3.5" />
                        </span>
                      )}
                      <span className="block bg-panel p-3">
                        <span className="block font-semibold">{titleCase(s.name)}</span>
                        <span className="mt-0.5 block text-[12px] leading-snug text-muted">{s.summary}</span>
                      </span>
                    </button>
                  ))}
                </div>
              </section>

              <section aria-labelledby="len-h" className="grid gap-6 md:grid-cols-2">
                <div>
                  <h2 id="len-h" className="mb-3 text-[15px] font-semibold">
                    Length
                  </h2>
                  <SliderField label="Target length" value={length} min={45} max={60} step={0.5} unit="s" onChange={setLength} />
                  <p className="mt-1.5 text-[12px] text-faint">Reels are 45–60 s. The planner stretches or trims scenes to land on this.</p>
                </div>
                <div>
                  <h2 className="mb-3 text-[15px] font-semibold">Planner</h2>
                  <Segmented label="Planner" value={planner} onChange={setPlanner} options={options.map((o) => ({ value: o.value, label: o.label, disabled: !o.ok, title: o.ok ? undefined : o.reason }))} size="sm" className="flex-wrap" />
                  {!current.ok && <p className="mt-2 text-[12px] text-warning">{current.reason}</p>}
                  {planner === 'offline' && <p className="mt-2 text-[12px] text-faint">Instant, private, free: a rule-based planner that stages a script into scenes. The renderer never needs a model.</p>}
                  {planner === 'ollama' && localModels.length > 0 && (
                    <div className="mt-2.5 flex flex-wrap gap-1.5">
                      {localModels.map((m) => (
                        <button key={m.name} onClick={() => setOllamaModel(m.name)} aria-pressed={(ollamaModel || localModels[0].name) === m.name} className={cn('rounded-chip border px-2 py-1 text-[12px]', (ollamaModel || localModels[0].name) === m.name ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:text-fg')}>
                          {m.name}
                        </button>
                      ))}
                      <p className="basis-full text-[12px] text-faint">Runs on this machine; your script never leaves it. (Models ending in “-cloud” run remotely and are not offered.)</p>
                    </div>
                  )}
                  {host && current.ok && (
                    <label className="mt-2.5 flex items-start gap-2 rounded-card border border-line bg-raised p-3 text-[12.5px]">
                      <input type="checkbox" checked={!!ui.consent[host]} onChange={(e) => ui.setConsent(host, e.target.checked)} className="mt-0.5 accent-[var(--accent)]" />
                      <span>
                        <b className="font-medium">Send my script to {host}.</b> <span className="text-muted">Only the script text goes there (about 6K tokens in, 4K out per attempt: roughly half a cent on a small model). Your key is read by the server and never shown or sent anywhere else.</span>
                      </span>
                    </label>
                  )}
                </div>
              </section>

              <section>
                <button onClick={() => setAdvanced(!advanced)} className="eyebrow flex items-center gap-1 hover:text-fg" aria-expanded={advanced}>
                  <ChevronDown className={cn('size-3.5 transition-transform', !advanced && '-rotate-90')} /> Advanced
                </button>
                {advanced && (
                  <div className="mt-3 grid gap-5 md:grid-cols-3">
                    <Field label="Seed" hint="The same script + seed gives the same reel">
                      <div className="flex gap-1.5">
                        <NumberField value={seed} onChange={setSeed} step={1} className="flex-1" aria-label="Seed" />
                        <IconButton label="Roll a new seed" variant="secondary" onClick={() => setSeed(Math.floor(Math.random() * 9999))}>
                          <Dice5 className="size-4" />
                        </IconButton>
                      </div>
                    </Field>
                    <Field label="Repair attempts" hint="How often a model may correct its own mistakes (a model planner only)">
                      <SliderField label="Repair attempts" value={repairs} min={0} max={5} step={1} onChange={setRepairs} />
                    </Field>
                    <div className="flex flex-col gap-3 pt-1">
                      <SwitchRow label="Keep my script word for word" hint="The captions (and the voice) are exactly your lines, in order. A model's rewording, dropped or invented lines are put right. A script too long to read in 60 s is condensed anyway." checked={verbatim} onChange={setVerbatim} />
                      <SwitchRow label="Enrich" hint="Add gestures, camera moves and sound effects the model left out" checked={enrich} onChange={setEnrich} />
                    </div>
                  </div>
                )}
              </section>
            </div>
          )}

          {step === 2 && (
            <Generating
              job={job}
              created={created}
              starting={generate.isPending}
              onRetry={() => (setStep(1), setJobId(null))}
              onManual={manual}
              planner={planner}
              plannerLabel={current.label}
              ready={ready}
              known={coverage.data?.missing}
              onFilled={onFilled}
              onOpen={() => ready && nav(`/p/${ready.id}`)}
            />
          )}
        </div>
      </Page>

      <ScratchDialog open={scratchOpen} onOpenChange={setScratchOpen} />

      {step < 2 && (
        <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-line bg-panel px-4 py-3 sm:px-6">
          <Button variant="ghost" onClick={() => (step === 0 ? nav('/') : setStep(0))}>
            <ArrowLeft className="size-4" /> {step === 0 ? 'Cancel' : 'Back'}
          </Button>
          {step === 0 ? (
            <Button variant="primary" disabled={!canNext0} onClick={() => setStep(1)}>
              Choose a look <ArrowRight className="size-4" />
            </Button>
          ) : (
            <div className="flex items-center gap-3">
              {!consented && <span className="text-[12px] text-warning">Tick the box above to send your script to {host}</span>}
              <Button variant="primary" size="lg" disabled={!canStart} onClick={start}>
                <Sparkles className="size-4" /> Plan my reel
              </Button>
            </div>
          )}
        </footer>
      )}
    </div>
  )
}

function Generating({ job, created, starting, onRetry, onManual, planner, plannerLabel, ready, known, onFilled, onOpen }: { job: ReturnType<typeof useJobs.getState>['jobs'][string] | undefined; created: { id: string } | null; starting: boolean; onRetry: () => void; onManual: (spec: ReelSpec) => void; planner: string; plannerLabel: string; ready: { id: string; spec: ReelSpec; gaps: LibraryGap[] } | null; known?: Coverage['missing']; onFilled: (asset: AssetInfo, gap: LibraryGap) => void; onOpen: () => void }) {
  const failed = job?.status === 'error'
  const notes = job?.notes ?? []
  const lint = failed ? (job?.error?.lint as LintReport | null | undefined) : null
  const draft = failed ? (job?.error?.spec as ReelSpec | null | undefined) : null
  const rows: { text: string; state: 'done' | 'active' | 'todo' }[] = notes.map((n, i) => ({ text: n, state: !job || job.status === 'done' || i < notes.length - 1 || failed ? 'done' : 'active' }))
  if (created) rows.push({ text: ready ? 'Saved to your workspace.' : 'Saved to your workspace: opening the studio…', state: 'done' })
  const sceneNumbers = Object.fromEntries((ready?.spec.scenes ?? []).map((sc, i) => [sc.id, i + 1]))
  return (
    <div className="mx-auto max-w-xl py-10">
      <div className="mb-6 text-center">
        <div className={cn('mx-auto mb-3 grid size-12 place-items-center rounded-full', failed ? 'bg-danger/15 text-danger' : created ? 'bg-success/15 text-success' : 'bg-accent-soft text-accent')}>
          {failed ? <XCircle className="size-6" /> : created ? <CheckCircle2 className="size-6" /> : <Loader2 className="size-6 animate-[spin_1.1s_linear_infinite]" />}
        </div>
        <h2 className="text-[18px] font-semibold tracking-tight">{failed ? 'The planner could not finish' : created ? 'Your reel is ready' : `Planning with ${plannerLabel.toLowerCase()}…`}</h2>
        {!failed && !created && <p className="mt-1 text-muted">{planner === 'offline' ? 'This takes a second or two.' : 'A language model is writing the spec; this can take a minute.'}</p>}
      </div>
      <ul className="m-0 flex flex-col gap-2 p-0" aria-live="polite">
        {starting && !job && (
          <li className="flex list-none items-center gap-2.5 text-muted">
            <Loader2 className="size-4 animate-[spin_1.1s_linear_infinite]" /> Starting…
          </li>
        )}
        {rows.map((r, i) => (
          <li key={i} className="flex list-none items-start gap-2.5">
            {r.state === 'done' ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" aria-hidden /> : <Loader2 className="mt-0.5 size-4 shrink-0 animate-[spin_1.1s_linear_infinite] text-accent" aria-hidden />}
            <span className={r.state === 'done' ? 'text-muted' : ''}>{r.text}</span>
          </li>
        ))}
      </ul>
      {ready && (
        <div className="mt-5 flex flex-col gap-3">
          {ready.gaps.length > 0 ? <GapsCard gaps={ready.gaps} sceneNumbers={sceneNumbers} known={known} onAdded={onFilled} /> : <AllFilled />}
          <div className="flex justify-center">
            <Button variant="primary" size="lg" onClick={onOpen}>
              Open in the studio <ArrowRight className="size-4" />
            </Button>
          </div>
        </div>
      )}
      {failed && job?.error && (
        <div className="mt-5 flex flex-col gap-3">
          <Banner tone="danger" title={job.error.message}>
            {job.error.hint}
          </Banner>
          {lint && lint.issues.length > 0 && (
            <ul className="m-0 max-h-48 overflow-y-auto rounded-card border border-line p-0">
              {lint.issues.slice(0, 8).map((i, n) => (
                <li key={n} className="flex list-none items-start gap-2 border-b border-line px-3 py-2 text-[12.5px] last:border-0">
                  <AlertTriangle className={cn('mt-0.5 size-3.5 shrink-0', i.severity === 'error' ? 'text-danger' : 'text-warning')} aria-hidden />
                  <span>
                    {i.message} <code className="font-mono text-[11px] text-faint">{i.path}</code>
                  </span>
                </li>
              ))}
            </ul>
          )}
          <div className="flex justify-center gap-2">
            <Button onClick={onRetry}>
              <ArrowLeft className="size-4" /> Change the settings
            </Button>
            {draft && (
              <Button variant="primary" onClick={() => onManual(draft)}>
                Edit the draft manually
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
