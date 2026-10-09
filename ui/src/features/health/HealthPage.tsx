import { useQueryClient } from '@tanstack/react-query'
import { Check, Copy, Loader2, RefreshCw, Trash2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useApi } from '@/api/context'
import { useCacheStats, useCatalog, useDoctor, useEngines, useHealth } from '@/api/hooks'
import type { CacheStats, EngineStatus, TestResult } from '@/api/types'
import { ApiError } from '@/api/types'
import { Page, PageHeader } from '@/components/shell/PageHeader'
import { Banner, Button, Chip, Dialog, Kbd, SelectBox, Segmented, Skeleton, StatusChip, toast } from '@/components/ui'
import { bytes, plural, titleCase } from '@/lib/format'
import { MOD } from '@/lib/hotkeys'
import { useUi, type Density, type ThemePref } from '@/store/ui'

function Section({ title, description, children, action }: { title: string; description?: ReactNode; children: ReactNode; action?: ReactNode }) {
  return (
    <section aria-label={title} className="rounded-card border border-line bg-panel">
      <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-3.5">
        <div className="min-w-0">
          <h2 className="text-[14px] font-semibold tracking-tight">{title}</h2>
          {description && <p className="mt-0.5 max-w-[70ch] text-[12.5px] text-muted">{description}</p>}
        </div>
        {action}
      </div>
      <div className="divide-y divide-line">{children}</div>
    </section>
  )
}

function Row({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children?: ReactNode }) {
  return (
    <div className="grid items-center gap-x-4 gap-y-1.5 px-5 py-3 sm:grid-cols-[minmax(0,230px)_1fr]">
      <div className="min-w-0">
        <div className="font-medium">{label}</div>
        {hint && <div className="text-[11.5px] leading-snug text-faint">{hint}</div>}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-2">{children}</div>
    </div>
  )
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false)
  return (
    <Button
      size="sm"
      aria-label={label}
      onClick={() => {
        void navigator.clipboard?.writeText(text)
        setDone(true)
        setTimeout(() => setDone(false), 1600)
      }}
    >
      {done ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      {done ? 'Copied' : 'Copy'}
    </Button>
  )
}

/** One explicit check of a service. The cost is on the button, and the result never contains a key. */
function TestButton({ kind, target, cost, label = 'Test it', disabled }: { kind: 'llm' | 'tts'; target: string; cost: string; label?: string; disabled?: boolean }) {
  const api = useApi()
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<TestResult | null>(null)
  const run = async () => {
    setBusy(true)
    setRes(null)
    try {
      setRes(await api.doctorTest(kind, target))
      void qc.invalidateQueries({ queryKey: ['engines'] })
    } catch (e) {
      setRes({ ok: false, message: e instanceof ApiError ? e.detail : String(e) })
    } finally {
      setBusy(false)
    }
  }
  const acct = res?.account
  return (
    <>
      <span className="flex-1" />
      <Button size="sm" onClick={run} disabled={busy || disabled} title={cost}>
        {busy && <Loader2 className="size-3.5 animate-[spin_1s_linear_infinite]" />}
        {label}
        <span className="font-normal text-faint">· {cost}</span>
      </Button>
      {res && (
        <div role="status" className="flex basis-full flex-wrap items-center gap-x-2 gap-y-0.5 text-[12px]">
          <StatusChip tone={res.ok ? 'success' : 'danger'}>{res.ok ? 'Works' : 'Failed'}</StatusChip>
          <span className="min-w-0 break-words text-muted">{res.message}</span>
          {acct && (
            <span className="text-muted tabular">
              {titleCase(acct.tier)} plan · {acct.characters_left.toLocaleString()} of {acct.characters_limit.toLocaleString()} characters left
            </span>
          )}
        </div>
      )}
    </>
  )
}

function engineTone(e: EngineStatus) {
  return e.available ? 'success' : 'warning'
}

const KEY_NOTES: Record<string, string> = {
  OPENAI_API_KEY: 'A hosted planner (OpenAI)',
  ANTHROPIC_API_KEY: 'A hosted planner (Claude)',
  ELEVENLABS_API_KEY: 'Studio-quality voices (ElevenLabs)',
}

const ENV_SNIPPET = `# .env — next to pyproject.toml. It is already in .gitignore: never commit it.
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
ELEVENLABS_API_KEY=
`

const CACHE_ROWS: { kind: 'frames' | 'segments' | 'tts' | 'tmp'; title: string; blurb: string; warn?: string }[] = [
  { kind: 'frames', title: 'Drawn frames', blurb: 'Makes previews and re-renders fast. Safe to clear: they are drawn again when needed.' },
  { kind: 'segments', title: 'Encoded video chunks', blurb: 'Lets a second render reuse every unchanged part of the video.' },
  { kind: 'tts', title: 'Generated voice lines', blurb: 'Every spoken line is kept so it never has to be generated again.', warn: 'Online voices (ElevenLabs) are billed per character, so lines you clear here are billed again the next time they are spoken.' },
  { kind: 'tmp', title: 'Temporary files', blurb: 'Leftovers of renders that were stopped. Always safe to clear.' },
]

function StorageSection({ stats }: { stats: CacheStats | undefined }) {
  const api = useApi()
  const qc = useQueryClient()
  const [ask, setAsk] = useState<(typeof CACHE_ROWS)[number] | null>(null)
  const [busy, setBusy] = useState(false)
  const clear = async () => {
    if (!ask) return
    setBusy(true)
    try {
      const next = await api.clearCache(ask.kind)
      qc.setQueryData(['cache'], next)
      toast.success(`${ask.title} cleared`)
      setAsk(null)
    } catch (e) {
      toast.error('Could not clear the cache', e instanceof ApiError ? e.detail : String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Section title="Storage" description={stats ? `Reel keeps what it has already made in ${stats.root}. ${bytes(stats.total_bytes)} in total.` : 'What Reel has already made, so the next time is quick.'}>
      {CACHE_ROWS.map((r) => {
        const s = stats?.[r.kind]
        return (
          <Row key={r.kind} label={r.title} hint={r.blurb}>
            <span className="tabular text-muted">{s ? `${plural(s.files, 'file')} · ${bytes(s.bytes)}` : '…'}</span>
            <span className="flex-1" />
            <Button size="sm" disabled={!s || s.files === 0} onClick={() => setAsk(r)} aria-label={`Clear ${r.title.toLowerCase()}`}>
              <Trash2 className="size-3.5" /> Clear
            </Button>
          </Row>
        )
      })}
      <Dialog
        open={!!ask}
        onOpenChange={(o) => !o && setAsk(null)}
        size="sm"
        title={`Clear ${ask?.title.toLowerCase() ?? ''}?`}
        description={ask?.blurb}
        footer={
          <>
            <Button onClick={() => setAsk(null)}>Keep them</Button>
            <Button variant="danger" onClick={clear} disabled={busy}>
              {busy && <Loader2 className="size-3.5 animate-[spin_1s_linear_infinite]" />} Clear {ask && stats ? bytes(stats[ask.kind].bytes) : ''}
            </Button>
          </>
        }
      >
        {ask?.warn ? <Banner tone="warning" title="This can cost money">{ask.warn}</Banner> : <p className="text-muted">Your projects and finished videos are not touched.</p>}
      </Dialog>
    </Section>
  )
}

const SHORTCUTS: [string[], string][] = [
  [['Space'], 'Play or pause the draft'],
  [['←', '→'], 'One frame back or forward'],
  [['⇧', '←', '→'], 'One second back or forward'],
  [['↑', '↓'], 'Previous or next scene'],
  [['Home', 'End'], 'Jump to the start or the end'],
  [['[', ']'], 'Set the loop start or end'],
  [[MOD, 'Z'], 'Undo (add ⇧ to redo)'],
  [[MOD, 'S'], 'Save now (it also saves by itself)'],
  [[MOD, 'K'], 'Command palette: every action by name'],
  [[MOD, '↵'], 'Render a draft'],
]

export function HealthPage() {
  const qc = useQueryClient()
  const health = useHealth().data
  const doctorQ = useDoctor()
  const doctor = doctorQ.data
  const engines = useEngines().data
  const cache = useCacheStats().data
  const catalog = useCatalog().data
  const ui = useUi()
  const [refreshing, setRefreshing] = useState(false)

  const refresh = async () => {
    setRefreshing(true)
    await Promise.all(['doctor', 'health', 'engines', 'cache'].map((k) => qc.invalidateQueries({ queryKey: [k] })))
    setRefreshing(false)
  }

  const llm = doctor?.llm
  const keySource = (name: string) => llm?.keys.find((k) => k.name === name)?.source ?? null
  const localModels = (llm?.ollama.models ?? []).filter((m) => !m.remote)
  const cloudModels = (llm?.ollama.models ?? []).filter((m) => m.remote)
  const consented = Object.entries(ui.consent).filter(([, v]) => v)
  const styleOptions = (catalog?.styles ?? []).map((s) => ({ value: s.name, label: titleCase(s.name), hint: s.summary }))

  return (
    <>
      <PageHeader
        title="Health & settings"
        subtitle="Everything runs on this computer. Online services are used only when you pick one, and each use says what it costs."
        actions={
          <Button onClick={refresh} disabled={refreshing}>
            {refreshing ? <Loader2 className="size-4 animate-[spin_1s_linear_infinite]" /> : <RefreshCw className="size-4" />} Check again
          </Button>
        }
      />
      <Page>
        <div className="mx-auto flex w-full max-w-[980px] flex-col gap-5 px-4 py-6 sm:px-6">
          {doctorQ.isError && (
            <Banner tone="danger" title="The health check could not run">
              {doctorQ.error instanceof ApiError ? doctorQ.error.detail : 'The server did not answer.'}
            </Banner>
          )}

          <Section title="This computer" description="What Reel needs to draw and encode a video.">
            {!doctor || !health ? (
              <div className="flex flex-col gap-2 p-5" aria-busy>
                <Skeleton className="h-5 w-1/3" />
                <Skeleton className="h-5 w-1/2" />
                <Skeleton className="h-5 w-2/5" />
              </div>
            ) : (
              <>
                <Row label="Renderer" hint="Skia draws every frame">
                  <StatusChip tone={doctor.skia.ok ? 'success' : 'danger'}>{doctor.skia.ok ? 'Ready' : 'Missing'}</StatusChip>
                  <span className="text-muted">{doctor.skia.version}</span>
                </Row>
                <Row label="Video encoder" hint="ffmpeg turns frames and sound into an MP4">
                  <StatusChip tone={doctor.ffmpeg.ok ? 'success' : 'danger'}>{doctor.ffmpeg.ok ? 'Ready' : 'Missing'}</StatusChip>
                  <span className="min-w-0 truncate text-muted" title={doctor.ffmpeg.version}>
                    {doctor.ffmpeg.version}
                  </span>
                  {!doctor.ffmpeg.ok && <span className="text-warning">Install ffmpeg (on a Mac: brew install ffmpeg), then press “Check again”.</span>}
                </Row>
                <Row label="Fonts" hint="Chosen once, for the whole machine, so every render looks the same">
                  {doctor.fonts.map((f) => (
                    <Chip key={f.role}>
                      {f.role}: {f.family}
                    </Chip>
                  ))}
                </Row>
                <Row label="Version" hint="Reel · Python">
                  <span className="tabular text-muted">
                    {doctor.reel} · {doctor.python}
                  </span>
                </Row>
                <Row label="Workers" hint="Frames are drawn in parallel; one render uses this many cores">
                  <span className="tabular text-muted">{plural(health.workers, 'worker')}</span>
                </Row>
                <Row label="Projects folder" hint="Plain files you can copy, back up or put in git">
                  <code className="min-w-0 flex-1 truncate font-mono text-[12px] text-muted" title={health.workspace}>
                    {health.workspace}
                  </code>
                  <CopyButton text={health.workspace} label="Copy the projects folder path" />
                </Row>
                <Row label="Library" hint="Built in, plus any plugin files">
                  <span className="text-muted tabular">
                    {doctor.counts.actions} actions · {doctor.counts.styles} looks · {doctor.counts.backgrounds} backgrounds · {doctor.counts.transitions} transitions
                  </span>
                  {health.plugins.length > 0 && <Chip tone="accent">{plural(health.plugins.length, 'plugin')}</Chip>}
                </Row>
              </>
            )}
          </Section>

          <Section title="Voices" description="Who reads the script aloud. The ones that run here are free and private; an online voice sends the spoken lines to its service and bills per character.">
            {(doctor?.tts ?? engines?.engines ?? []).map((e) => (
              <Row
                key={e.name}
                label={
                  <span className="flex items-center gap-2">
                    {e.label}
                    {(doctor?.tts_default ?? engines?.default) === e.name && <Chip tone="accent">default</Chip>}
                  </span>
                }
                hint={e.detail}
              >
                <StatusChip tone={engineTone(e)}>{e.available ? 'Ready' : 'Not ready'}</StatusChip>
                <Chip tone={e.online ? 'warning' : 'neutral'}>{e.online ? `Online · ${e.destination ?? 'hosted'}` : 'Runs here'}</Chip>
                {e.online && e.key_source && <Chip>key from {e.key_source === 'shell' ? 'your shell' : '.env'}</Chip>}
                {e.online ? <TestButton kind="tts" target={e.name} cost="checks the key, no credits used" label="Check account" disabled={!e.available} /> : <span className="flex-1" />}
              </Row>
            ))}
            {!doctor && !engines && <Skeleton className="m-5 h-16" />}
          </Section>

          <Section title="Script planners" description="What turns a script into scenes. The built-in planner is instant and offline; a language model can plan with more imagination.">
            <Row label="Built-in planner" hint="Rule-based. Instant, private, free.">
              <StatusChip tone="success">Always ready</StatusChip>
            </Row>
            <Row label="Ollama" hint={llm?.ollama.running ? `Local models at ${llm.ollama.url}` : 'Run local models on this computer'}>
              <StatusChip tone={llm?.ollama.running && localModels.length > 0 ? 'success' : 'warning'}>{!llm ? '…' : !llm.ollama.running ? 'Not running' : localModels.length === 0 ? 'No model yet' : 'Ready'}</StatusChip>
              {localModels.slice(0, 4).map((m) => (
                <Chip key={m.name}>{m.name}</Chip>
              ))}
              {llm?.ollama.running && localModels.length === 0 && <span className="text-warning">Pull one: ollama pull llama3.1</span>}
              {localModels[0] ? <TestButton kind="llm" target={`ollama:${localModels[0].name}`} cost="free, runs here" /> : <span className="flex-1" />}
              {cloudModels.length > 0 && (
                <p className="basis-full text-[11.5px] text-faint">
                  {plural(cloudModels.length, 'Ollama cloud model')} ({cloudModels.map((m) => m.name).join(', ')}) send your script off this computer, so Reel never offers {cloudModels.length === 1 ? 'it' : 'them'}.
                </p>
              )}
            </Row>
            {(
              [
                ['OpenAI', 'openai', 'OPENAI_API_KEY', 'api.openai.com'],
                ['Claude (Anthropic)', 'anthropic', 'ANTHROPIC_API_KEY', 'api.anthropic.com'],
              ] as const
            ).map(([label, target, envName, host]) => (
              <Row key={target} label={label} hint={`Online · ${host}`}>
                <StatusChip tone={keySource(envName) ? 'success' : 'neutral'}>{keySource(envName) ? 'Key found' : 'No key'}</StatusChip>
                <TestButton kind="llm" target={target} cost="about 100 tokens, a fraction of a cent" disabled={!keySource(envName)} />
              </Row>
            ))}
            {llm?.default && (
              <Row label="Default planner" hint="From REEL_LLM in your environment">
                <code className="font-mono text-[12px] text-muted">{llm.default}</code>
              </Row>
            )}
          </Section>

          <Section title="API keys" description="Reel reads keys from your shell or from a .env file, and shows only whether one was found. This page never asks for, displays or stores a key.">
            {(llm?.keys ?? []).map((k) => (
              <Row key={k.name} label={<code className="font-mono text-[12.5px]">{k.name}</code>} hint={KEY_NOTES[k.name]}>
                <StatusChip tone={k.source ? 'success' : 'neutral'}>{k.source === 'shell' ? 'Found in your shell' : k.source === '.env' ? 'Found in .env' : 'Not set'}</StatusChip>
              </Row>
            ))}
            <div className="flex flex-col gap-2 px-5 py-4">
              <div className="flex items-center justify-between gap-3">
                <span className="text-[12.5px] font-medium">To add a key, paste it into your own .env file, then restart <code className="font-mono">reel serve</code></span>
                <CopyButton text={ENV_SNIPPET} label="Copy the .env template" />
              </div>
              <pre className="m-0 overflow-x-auto rounded-ctl bg-raised p-3 font-mono text-[12px] leading-relaxed text-muted">{ENV_SNIPPET}</pre>
            </div>
          </Section>

          <StorageSection stats={cache} />

          <Section title="Preferences" description="Saved in this browser only.">
            <Row label="Theme">
              <Segmented<ThemePref>
                label="Theme"
                size="sm"
                value={ui.theme}
                onChange={ui.setTheme}
                options={[
                  { value: 'system', label: 'Match system' },
                  { value: 'dark', label: 'Dark' },
                  { value: 'light', label: 'Light' },
                ]}
              />
            </Row>
            <Row label="Density" hint="Compact fits more of the timeline on a small screen">
              <Segmented<Density>
                label="Density"
                size="sm"
                value={ui.density}
                onChange={ui.setDensity}
                options={[
                  { value: 'comfortable', label: 'Comfortable' },
                  { value: 'compact', label: 'Compact' },
                ]}
              />
            </Row>
            <Row label="Default look" hint="For new reels">
              <SelectBox label="Default look" className="max-w-64" value={ui.defaultStyle} onChange={(v) => ui.set({ defaultStyle: v })} options={styleOptions} />
            </Row>
            <Row label="Default planner" hint="For new reels (a hosted one still asks before sending your script)">
              <Segmented
                label="Default planner"
                size="sm"
                value={ui.defaultPlanner}
                onChange={(v) => ui.set({ defaultPlanner: v })}
                options={[
                  { value: 'offline', label: 'Built-in' },
                  { value: 'ollama', label: 'Ollama' },
                  { value: 'openai', label: 'OpenAI' },
                  { value: 'anthropic', label: 'Claude' },
                ]}
              />
            </Row>
            <Row label="Default voice" hint="Auto picks the best voice that runs here; online voices are never chosen for you">
              <SelectBox
                label="Default voice"
                className="max-w-64"
                value={ui.ttsEngine}
                onChange={(v) => ui.set({ ttsEngine: v })}
                options={[{ value: 'auto', label: 'Auto' }, ...(engines?.engines ?? []).map((e) => ({ value: e.name, label: e.label, hint: e.online ? 'online, billed per character' : 'runs here', disabled: !e.available }))]}
              />
            </Row>
            <Row label="Online services you agreed to" hint="Asked once per service, the first time you use it">
              {consented.length === 0 ? (
                <span className="text-muted">None: nothing has been sent anywhere.</span>
              ) : (
                consented.map(([host]) => (
                  <span key={host} className="inline-flex items-center gap-1.5 rounded-chip bg-hover py-0.5 pl-2 pr-1 text-[12px]">
                    {host}
                    <Button size="sm" variant="ghost" className="h-5 px-1.5" onClick={() => ui.setConsent(host, false)} aria-label={`Stop allowing ${host}`}>
                      Revoke
                    </Button>
                  </span>
                ))
              )}
            </Row>
          </Section>

          <Section title="Keyboard" description="In the studio. Every one of these is also in the command palette.">
            <div className="grid gap-x-8 gap-y-2 px-5 py-4 sm:grid-cols-2">
              {SHORTCUTS.map(([keys, what]) => (
                <div key={what} className="flex items-center justify-between gap-3">
                  <span className="text-muted">{what}</span>
                  <span className="flex shrink-0 gap-1">
                    {keys.map((k) => (
                      <Kbd key={k}>{k}</Kbd>
                    ))}
                  </span>
                </div>
              ))}
            </div>
          </Section>
        </div>
      </Page>
    </>
  )
}
