import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, AudioLines, Cloud, Loader2, Mic, Music, Play, ShieldCheck, Square, Volume2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useApi } from '@/api/context'
import { useCatalog, useEngines } from '@/api/hooks'
import type { AudioResult, EngineStatus, ReelSpec, TestResult, VoiceInfo } from '@/api/types'
import { ApiError } from '@/api/types'
import { Page, PageHeader } from '@/components/shell/PageHeader'
import { Banner, Button, Chip, Field, Input, ProgressBar, Segmented, SelectBox, SliderField, StatusChip, SwitchRow, toast } from '@/components/ui'
import { cn } from '@/lib/cn'
import { plural, titleCase } from '@/lib/format'
import { characterName } from '@/lib/spec'
import { audioKey, isFresh, useAudio } from '@/store/audio'
import { useProject } from '@/store/project'
import { useUi } from '@/store/ui'
import { useVoice } from '@/store/voice'
import { applyRetimed } from './retime'
import { useGenerateVoice } from './useGenerateVoice'

function Card({ title, icon, children, aside, id }: { title: string; icon?: ReactNode; children: ReactNode; aside?: ReactNode; id?: string }) {
  return (
    <section aria-labelledby={id} className="rounded-card border border-line bg-panel">
      <header className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
        <h2 id={id} className="flex items-center gap-2 text-[14px] font-semibold">
          {icon && <span className="text-muted [&_svg]:size-4">{icon}</span>}
          {title}
        </h2>
        {aside}
      </header>
      <div className="p-5">{children}</div>
    </section>
  )
}

// ------------------------------------------------------------------------------- engines
function EngineCard({ e, selected, onSelect }: { e: EngineStatus; selected: boolean; onSelect: () => void }) {
  const api = useApi()
  const [test, setTest] = useState<TestResult | null>(null)
  const [busy, setBusy] = useState(false)
  const check = async () => {
    setBusy(true)
    try {
      setTest(await api.doctorTest('tts', e.name))
    } catch (err) {
      setTest({ ok: false, message: err instanceof ApiError ? err.detail : String(err) })
    } finally {
      setBusy(false)
    }
  }
  const tone = e.available ? 'success' : 'neutral'
  return (
    <div className={cn('flex flex-col gap-2.5 rounded-card border-2 p-4 transition-colors', selected ? 'border-accent bg-accent-soft/50' : 'border-line hover:border-line-strong')}>
      <button role="radio" aria-checked={selected} onClick={onSelect} disabled={!e.available} className="flex items-start gap-3 text-left disabled:cursor-not-allowed">
        <span className={cn('mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border-2', selected ? 'border-accent' : 'border-line-strong')}>{selected && <span className="size-2 rounded-full bg-accent" />}</span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-[14px] font-semibold">{e.label}</span>
            {e.online ? (
              <Chip tone="warning">
                <Cloud className="size-3" aria-hidden /> Online
              </Chip>
            ) : (
              <Chip tone="neutral">
                <ShieldCheck className="size-3" aria-hidden /> Runs here
              </Chip>
            )}
            <StatusChip tone={tone}>{e.available ? 'ready' : e.name === 'elevenlabs' ? 'no key found' : 'not available'}</StatusChip>
          </span>
          <span className="mt-1 block text-[12px] leading-snug text-muted">{e.detail}</span>
        </span>
      </button>
      {e.online && (
        <div className="ml-7 flex flex-col gap-2 border-t border-line pt-2.5 text-[12px]">
          <p className="text-muted">
            Sends the spoken lines (the caption text, nothing else) to <b className="font-medium text-fg">{e.destination ?? 'the service'}</b> and uses your credits. Every line is paid for once: finished lines are cached on this machine. Model:{' '}
            <code className="font-mono">{e.model}</code>.
          </p>
          {e.available && (
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={() => void check()} disabled={busy}>
                {busy ? <Loader2 className="size-3.5 animate-[spin_1s_linear_infinite]" /> : <ShieldCheck className="size-3.5" />} Check account
              </Button>
              <span className="text-faint">spends no credits</span>
              {test?.ok && test.account && (
                <Chip tone="success">
                  plan {test.account.tier} · <span className="tabular">{test.account.characters_left.toLocaleString()}</span> of <span className="tabular">{test.account.characters_limit.toLocaleString()}</span> characters left
                </Chip>
              )}
              {test?.ok && !test.account && <Chip tone="success">key accepted</Chip>}
              {test && !test.ok && <span className="text-danger">{test.message}</span>}
            </div>
          )}
          {!e.available && (
            <p className="text-faint">
              Put <code className="font-mono">ELEVENLABS_API_KEY=…</code> in your shell or a git-ignored <code className="font-mono">.env</code> file, then restart <code className="font-mono">reel serve</code>. Reel Studio never asks for the key itself.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------------------- the page
export function AudioPage() {
  const api = useApi()
  const catalog = useCatalog().data
  const engines = useEngines()
  const spec = useProject((s) => s.spec) as ReelSpec
  const projectId = useProject((s) => s.id) as string
  const edit = useProject((s) => s.edit)
  const ui = useUi()
  const result = useAudio((s) => (s.projectId === projectId ? s.result : null))
  const specKey = useAudio((s) => s.specKey)
  const madeWith = useAudio((s) => s.engine)
  const { run: generateVoice, running, job } = useGenerateVoice()
  const ask = useVoice((s) => s.ask)
  const [audition, setAudition] = useState<string | null>(null)
  const player = useRef<HTMLAudioElement | null>(null)

  const engineList = engines.data?.engines ?? []
  const chosen = ui.ttsEngine === 'auto' ? (engines.data?.default ?? 'babble') : ui.ttsEngine
  const engine = engineList.find((e) => e.name === chosen)
  const online = !!engine?.online
  const host = engine?.destination ?? ''
  const consented = !online || !!ui.consent[host]
  const speaking = spec.audio.voiceover === 'tts'
  const stale = !!result && !isFresh(spec, { result, specKey, engine: madeWith }, chosen)

  const estimate = useQuery({
    queryKey: ['estimate', chosen, audioKey(spec)],
    queryFn: () => api.estimateSpeech(spec, chosen),
    enabled: speaking && !!engines.data,
    staleTime: 2_000,
  })
  const est = estimate.data

  const voices = useQuery({
    queryKey: ['voices', chosen],
    queryFn: () => api.ttsVoices(chosen),
    enabled: !!engine?.available && consented && speaking,
    retry: false,
    staleTime: 60_000,
  })

  const onGenerate = () =>
    void generateVoice().then((r) => r && requestAnimationFrame(() => document.getElementById('result')?.scrollIntoView({ block: 'nearest' })))

  const stopAudio = () => {
    player.current?.pause()
    setAudition(null)
  }

  const playUrl = (url: string, key: string) => {
    if (audition === key) return stopAudio()
    player.current?.pause()
    const a = new Audio(url)
    player.current = a
    setAudition(key)
    a.onended = () => setAudition(null)
    void a.play().catch(() => setAudition(null))
  }

  const sample = async (voice: string | null | undefined, name: string) => {
    const text = `Hello, I'm ${name}. This is how I sound.`
    const confirmed = online
    if (online && !(await ask({ host, chars: text.length, what: `audition ${name}` }))) return
    try {
      const blob = await api.ttsSample({ engine: chosen, voice, text, confirmBilling: confirmed })
      playUrl(URL.createObjectURL(blob), `voice:${name}`)
    } catch (e) {
      toast.error('Could not play a sample', e instanceof ApiError ? e.detail : String(e))
    }
  }

  useEffect(() => () => player.current?.pause(), [])

  const set = (fn: (a: ReelSpec['audio']) => void) => edit((d) => void fn(d.audio))
  const musicMode = spec.audio.music === null ? 'none' : spec.audio.music.startsWith('procedural') ? 'generated' : 'file'
  const mood = spec.audio.music?.startsWith('procedural:') ? spec.audio.music.slice(11) : ''

  const voiceOptions = useMemo(() => {
    const base: { value: string; label: string; hint?: string }[] = [{ value: '__auto', label: 'Automatic', hint: 'a voice that fits the character' }]
    for (const v of (voices.data ?? []) as VoiceInfo[]) base.push({ value: v.id, label: v.name, hint: Object.values(v.traits).join(' · ') })
    return base
  }, [voices.data])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Voice & Audio"
        subtitle={spec.meta.title}
        actions={
          <Button variant="primary" size="lg" disabled={running || (!speaking && spec.audio.music === null) || (speaking && !!engine && !engine.available)} onClick={onGenerate}>
            {running ? <Loader2 className="size-4 animate-[spin_1s_linear_infinite]" /> : <Mic className="size-4" />}
            {running ? 'Working…' : online && est?.billable_characters ? `Generate voice (${est.billable_characters.toLocaleString()} characters billed)` : 'Generate voice & sound'}
          </Button>
        }
      />
      <Page>
        <div className="mx-auto flex max-w-[1000px] flex-col gap-5 px-4 py-6 sm:px-6">
          {running && job && (
            <Banner tone="info" title="Making the soundtrack…">
              <ProgressBar value={job.done} max={job.total} className="mt-2" label="Voice and sound progress" />
            </Banner>
          )}

          <Card title="Voice-over" icon={<AudioLines />} id="vo">
            <div className="flex flex-wrap items-center gap-4">
              <Segmented label="Voice-over" value={spec.audio.voiceover} onChange={(v) => set((a) => void (a.voiceover = v))} options={[{ value: 'tts', label: 'Spoken by a voice' }, { value: 'none', label: 'No voice-over' }, { value: 'file', label: 'My recording' }]} />
              {spec.audio.voiceover === 'file' && <Input aria-label="Recording file" value={spec.audio.voiceover_file ?? ''} placeholder="path/to/voice.wav (relative to the project)" onChange={(e) => set((a) => void (a.voiceover_file = e.target.value || null))} className="max-w-sm flex-1" />}
              {speaking && <span className="text-[12px] text-muted">Subtitle captions are read aloud; the speaker’s mouth follows the words and the caption stays up while it is spoken.</span>}
            </div>
          </Card>

          {speaking && (
            <>
              <Card title="Speech engine" icon={<Volume2 />} id="engine" aside={<span className="text-[12px] text-faint">{ui.ttsEngine === 'auto' ? `automatic: ${chosen}` : 'chosen by you'}</span>}>
                <div role="radiogroup" aria-label="Speech engine" className="grid gap-3 md:grid-cols-2">
                  {engineList.map((e) => (
                    <EngineCard key={e.name} e={e} selected={chosen === e.name} onSelect={() => ui.set({ ttsEngine: e.name })} />
                  ))}
                </div>
                <p className="mt-3 text-[12px] text-faint">“Automatic” never picks an online engine. Offline voices run on this machine; nothing is sent anywhere.</p>
              </Card>

              <Card title="Cast voices" icon={<Mic />} id="cast" aside={online && !consented ? <Button size="sm" onClick={() => ui.setConsent(host, true)}>Allow contacting {host}</Button> : undefined}>
                {online && !consented && <p className="mb-3 text-[12.5px] text-muted">Listing {engine?.label}’s voices contacts {host} with your key (no credits are spent). Allow it to choose voices here.</p>}
                {voices.isError && <Banner tone="warning" title="Could not list this engine’s voices">{voices.error instanceof ApiError ? `${voices.error.detail}${voices.error.hint ? ` — ${voices.error.hint}` : ''}` : String(voices.error)}</Banner>}
                <div className="divide-y divide-line">
                  {spec.characters.map((c) => {
                    const current = c.voice ?? '__auto'
                    const known = voiceOptions.some((o) => o.value === current)
                    const options = known ? voiceOptions : [...voiceOptions, { value: current, label: `Custom: ${current}`, hint: 'not in this engine’s list' }]
                    return (
                      <div key={c.id} className="grid grid-cols-[1fr_minmax(0,1.2fr)_auto] items-center gap-3 py-3 first:pt-0 last:pb-0">
                        <div className="min-w-0">
                          <div className="truncate font-medium">{characterName(spec, c.id)}</div>
                          <div className="text-[11.5px] text-faint">{c.archetype}</div>
                        </div>
                        <SelectBox label={`Voice for ${characterName(spec, c.id)}`} value={current} onChange={(v) => edit((d) => void (d.characters.find((x) => x.id === c.id)!.voice = v === '__auto' ? null : v))} options={options} />
                        <Button size="sm" variant="secondary" onClick={() => void sample(c.voice, characterName(spec, c.id))} aria-label={`Listen to ${characterName(spec, c.id)}`} disabled={!engine?.available}>
                          {audition === `voice:${characterName(spec, c.id)}` ? <Square className="size-3.5 fill-current" /> : <Play className="size-3.5" />} {audition === `voice:${characterName(spec, c.id)}` ? 'Stop' : 'Listen'}
                          {online && <span className="text-faint">· ~{(`Hello, I'm ${characterName(spec, c.id)}. This is how I sound.`).length} chars</span>}
                        </Button>
                      </div>
                    )
                  })}
                  {spec.characters.length === 0 && <p className="text-muted">No characters yet.</p>}
                </div>
              </Card>

              <Card title="What this will cost" icon={<ShieldCheck />} id="cost">
                {!est && <p className="text-muted">Counting the spoken lines…</p>}
                {est && !est.available && <Banner tone="warning" title={`${engine?.label ?? 'This engine'} cannot be used yet`}>{est.reason}</Banner>}
                {est?.available && (
                  <div className="flex flex-col gap-3">
                    <div className="flex flex-wrap items-center gap-2 text-[13px]" aria-live="polite">
                      <b className="font-semibold tabular">{plural(est.lines ?? 0, 'line')}</b>
                      <span className="text-muted">to speak ·</span>
                      <Chip tone="success">
                        <span className="tabular">{est.cached_lines}</span> already generated (free)
                      </Chip>
                      <Chip tone={est.new_lines ? (online ? 'warning' : 'neutral') : 'neutral'}>
                        <span className="tabular">{est.new_lines}</span> new
                      </Chip>
                      {est.online && (
                        <b className={cn('font-semibold tabular', est.billable_characters ? 'text-warning' : 'text-success')}>
                          {est.billable_characters ? `${est.billable_characters?.toLocaleString()} characters will be billed by ${est.destination ?? host}` : 'nothing will be billed'}
                        </b>
                      )}
                    </div>
                    <p className="text-[12px] text-faint">
                      {est.online ? 'Lines you have already generated are reused from the cache for free, and editing other parts of the reel never re-bills them. Changing a line’s text, its voice or the model generates (and bills) it again.' : 'This engine runs on your machine: no network, no cost. Generated lines are cached, so re-rendering is instant.'}
                    </p>
                  </div>
                )}
              </Card>
            </>
          )}

          <Card title="Music & mix" icon={<Music />} id="mix">
            <div className="grid gap-6 md:grid-cols-2">
              <div className="flex flex-col gap-3">
                <Field label="Music">
                  <Segmented label="Music" value={musicMode} onChange={(m) => set((a) => void (a.music = m === 'none' ? null : m === 'generated' ? (mood ? `procedural:${mood}` : 'procedural') : ''))} options={[{ value: 'none', label: 'None' }, { value: 'generated', label: 'Generated bed' }, { value: 'file', label: 'A file' }]} />
                </Field>
                {musicMode === 'generated' && (
                  <div className="grid grid-cols-2 gap-1.5" role="radiogroup" aria-label="Music mood">
                    {(catalog?.music_moods ?? []).map((m) => (
                      <div key={m} className={cn('flex items-center overflow-hidden rounded-ctl border', mood === m ? 'border-accent bg-accent-soft' : 'border-line')}>
                        <button role="radio" aria-checked={mood === m} onClick={() => set((a) => void (a.music = `procedural:${m}`))} className="flex-1 px-2.5 py-1.5 text-left text-[12.5px] font-medium capitalize">
                          {m}
                        </button>
                        <button aria-label={audition === `music:${m}` ? `Stop ${m}` : `Listen to ${m}`} onClick={() => playUrl(api.musicUrl(m, 10), `music:${m}`)} className="px-2 text-faint hover:text-fg">
                          {audition === `music:${m}` ? <Square className="size-3.5 fill-current" /> : <Play className="size-3.5" />}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                {musicMode === 'generated' && !mood && <p className="text-[12px] text-faint">With no mood chosen the style picks one that suits it.</p>}
                {musicMode === 'file' && <Input aria-label="Music file" value={spec.audio.music ?? ''} placeholder="path/to/music.mp3 (relative to the project)" onChange={(e) => set((a) => void (a.music = e.target.value))} />}
              </div>
              <div className="flex flex-col gap-4">
                <Field label="Music level"><SliderField label="Music level" value={spec.audio.music_gain_db} min={-60} max={12} step={0.5} unit="dB" onChange={(v) => set((a) => void (a.music_gain_db = v))} defaultValue={-16} /></Field>
                <Field label="Voice level"><SliderField label="Voice level" value={spec.audio.voice_gain_db} min={-60} max={12} step={0.5} unit="dB" onChange={(v) => set((a) => void (a.voice_gain_db = v))} defaultValue={0} /></Field>
                <Field label="Sound-effect level"><SliderField label="Sound-effect level" value={spec.audio.sfx_gain_db} min={-60} max={12} step={0.5} unit="dB" onChange={(v) => set((a) => void (a.sfx_gain_db = v))} defaultValue={-8} /></Field>
                <SwitchRow label="Duck the music under the voice" hint="The bed dips while someone speaks" checked={spec.audio.ducking} onChange={(v) => set((a) => void (a.ducking = v))} />
                <SwitchRow label="Automatic footsteps and landings" hint="Sound effects from the actions themselves" checked={spec.audio.auto_sfx} onChange={(v) => set((a) => void (a.auto_sfx = v))} />
              </div>
            </div>
          </Card>

          {result && (
            <ResultCard result={result} stale={stale} onApply={() => result && applyRetimed(result)} spec={spec} onPlay={(u) => playUrl(u, 'mix')} playing={audition === 'mix'} />
          )}
        </div>
      </Page>

    </div>
  )
}

function Waveforms({ result }: { result: AudioResult }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current
    if (!c) return
    const w = c.clientWidth
    const dpr = window.devicePixelRatio || 1
    c.width = w * dpr
    c.height = 126 * dpr
    const ctx = c.getContext('2d')
    if (!ctx) return
    ctx.scale(dpr, dpr)
    const css = getComputedStyle(document.documentElement)
    const rows: [keyof AudioResult['peaks'], string, string][] = [
      ['voice', css.getPropertyValue('--accent').trim(), 'Voice'],
      ['music', css.getPropertyValue('--muted').trim(), 'Music'],
      ['sfx', css.getPropertyValue('--warning').trim(), 'Effects'],
    ]
    rows.forEach(([key, color], r) => {
      const peaks = result.peaks[key]
      const mid = 21 + r * 42
      ctx.fillStyle = color
      for (let x = 0; x < w; x++) {
        const v = peaks[Math.min(peaks.length - 1, Math.floor((x / w) * peaks.length))] ?? 0
        const amp = Math.min(19, Math.max(v > 0.004 ? 0.8 : 0, v * 40))
        if (amp > 0) ctx.fillRect(x, mid - amp, 1, amp * 2)
      }
    })
  }, [result])
  return (
    <div className="relative">
      <canvas ref={ref} className="h-[126px] w-full rounded-ctl bg-raised" role="img" aria-label="Waveforms of the voice, the music and the sound effects" />
      <div className="pointer-events-none absolute inset-y-0 left-2 flex flex-col justify-around py-1 text-[10.5px] font-medium text-faint" aria-hidden>
        <span>Voice</span>
        <span>Music</span>
        <span>Effects</span>
      </div>
    </div>
  )
}

function ResultCard({ result, stale, onApply, spec, onPlay, playing }: { result: AudioResult; stale: boolean; onApply: () => void; spec: ReelSpec; onPlay: (url: string) => void; playing: boolean }) {
  const api = useApi()
  const rep = result.report
  return (
    <Card
      title="The soundtrack"
      icon={<Volume2 />}
      id="result"
      aside={
        stale ? (
          <StatusChip tone="warning">out of date: captions, voices or sounds changed</StatusChip>
        ) : (
          <StatusChip tone="success">up to date</StatusChip>
        )
      }
    >
      <div className="flex flex-col gap-4">
        <Waveforms result={result} />
        <div className="flex flex-wrap items-center gap-4 text-[12.5px]">
          {result.wav_url && (
            <Button size="sm" variant="secondary" onClick={() => onPlay(api.audioUrl(result.wav_url as string))}>
              {playing ? <Square className="size-3.5 fill-current" /> : <Play className="size-3.5" />} {playing ? 'Stop' : 'Play the mix'}
            </Button>
          )}
          {rep && (
            <>
              <span className="text-muted">Loudness <b className="font-semibold text-fg tabular">{rep.approx_lufs.toFixed(1)} LUFS</b></span>
              <span className="text-muted">Peak <b className="font-semibold text-fg tabular">{(20 * Math.log10(Math.max(rep.peak, 1e-6))).toFixed(1)} dBFS</b></span>
              <span className="text-muted">Music ducked <b className="font-semibold text-fg tabular">{rep.ducked_seconds.toFixed(1)} s</b></span>
              <span className="text-muted">{plural(rep.n_clips, 'clip')}</span>
            </>
          )}
        </div>
        {result.notes.map((n, i) => (
          <Banner key={i} tone="info">
            {n}
          </Banner>
        ))}
        {result.warnings.length > 0 && (
          <div className="flex flex-col gap-2">
            <h3 className="flex items-center gap-1.5 text-[12.5px] font-semibold text-warning">
              <AlertTriangle className="size-3.5" /> {plural(result.warnings.length, 'warning')}
            </h3>
            <ul className="m-0 flex flex-col gap-1.5 p-0">
              {result.warnings.map((w, i) => (
                <li key={i} className="list-none rounded-ctl bg-warning/10 px-3 py-2 text-[12.5px]">
                  {w}
                </li>
              ))}
            </ul>
          </div>
        )}
        {result.retimed.length > 0 && (
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-[12.5px] font-semibold">Captions the speech retimed</h3>
              <Button size="sm" variant="primary" onClick={onApply}>
                Apply to the reel
              </Button>
            </div>
            <ul className="m-0 flex flex-col gap-1 p-0">
              {result.retimed.slice(0, 12).map((r, i) => (
                <li key={i} className="flex list-none flex-wrap items-baseline gap-x-2 text-[12.5px]">
                  <span className="text-muted">Scene {r.scene + 1}</span>
                  <span className="truncate font-medium">“{(spec.scenes[r.scene]?.captions[r.caption]?.text ?? '').slice(0, 44)}”</span>
                  <span className="tabular text-faint">
                    ends {r.was_t1.toFixed(1)} → <b className="font-semibold text-fg">{r.t1.toFixed(1)} s</b>
                  </span>
                </li>
              ))}
            </ul>
            <p className="text-[11.5px] text-faint">A render does this by itself; applying it here makes the editor show the same timing.</p>
          </div>
        )}
        {result.retimed.length === 0 && <p className="text-[12.5px] text-muted">Every caption already fits its speech.</p>}
      </div>
      <span className="sr-only">{titleCase('soundtrack')}</span>
    </Card>
  )
}
