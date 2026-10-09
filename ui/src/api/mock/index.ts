// The mock adapter: the whole API in memory (latency, job progress, lint, offline/online engines, errors), so every screen and state
// can be reviewed and tested without `reel serve`. Its lint is a small approximation; the engine's is authoritative.
import type { Api } from '../client'
import type {
  AudioResult,
  Catalog,
  EngineStatus,
  Examples,
  JobEvent,
  JobEventInput,
  JobSnapshot,
  LintIssue,
  LintReport,
  PreviewInfo,
  ProjectDoc,
  ProjectSummary,
  ReelSpec,
  RenderBody,
  RenderPlan,
  RenderResult,
  SpeechEstimate,
  VoiceInfo,
} from '../types'
import { ApiError } from '../types'
import { mockFrameBlob, posterSvg } from './frames'
import { sceneSlots, totalDuration } from '@/lib/timeline'

// under test the simulated latency is shrunk, so a whole render "takes" a few hundred milliseconds
const sleep = (ms: number) => new Promise((r) => setTimeout(r, import.meta.env.MODE === 'test' ? Math.ceil(ms / 25) : ms))
const uid = () => Math.random().toString(16).slice(2, 14)
const clone = <T>(v: T): T => structuredClone(v)

interface MockJob {
  snap: JobSnapshot
  listeners: Set<(e: JobEvent) => void>
  timers: number[]
}

function lint(spec: ReelSpec, catalog: Catalog): LintReport {
  const issues: LintIssue[] = []
  const add = (severity: LintIssue['severity'], code: string, path: string, message: string, hint?: string, extra: Partial<LintIssue> = {}) =>
    issues.push({ severity, code, path, message, ...(hint ? { hint } : {}), ...extra })
  const has = (list: { name: string }[], n: string) => list.some((e) => e.name === n)
  if (!has(catalog.styles, spec.meta.style)) add('error', 'REGISTRY_MISSING', 'meta.style', `style '${spec.meta.style}' is not registered`, 'closest registered: ' + catalog.styles.map((s) => s.name).join(', '), { kind: 'style', name: spec.meta.style })
  const ids = new Set(spec.characters.map((c) => c.id))
  spec.characters.forEach((c, i) => {
    if (!has(catalog.archetypes, c.archetype)) add('error', 'REGISTRY_MISSING', `characters[${i}].archetype`, `archetype '${c.archetype}' is not registered`, undefined, { kind: 'archetype', name: c.archetype })
  })
  spec.scenes.forEach((sc, si) => {
    const p = `scenes[${si}]`
    if (sc.duration_sec > catalog.limits.max_scene_sec) add('error', 'SCHEMA', `${p}.duration_sec`, `Input should be less than or equal to ${catalog.limits.max_scene_sec}`)
    if (!has(catalog.backgrounds, sc.background.template)) add('error', 'REGISTRY_MISSING', `${p}.background.template`, `background '${sc.background.template}' is not registered`, undefined, { kind: 'background', name: sc.background.template })
    sc.layers.forEach((ly, li) => {
      if (!ids.has(ly.character)) add('error', 'UNKNOWN_CHARACTER', `${p}.layers[${li}].character`, `layer uses unknown character '${ly.character}'`)
      ly.actions.forEach((a, ai) => {
        const ap = `${p}.layers[${li}].actions[${ai}]`
        const def = catalog.actions.find((x) => x.name === a.name)
        if (!def) return add('error', 'REGISTRY_MISSING', `${ap}.name`, `action '${a.name}' is not registered`, 'closest registered: walk, wave, talk', { kind: 'action', name: a.name })
        if (a.t1 > sc.duration_sec + 1e-6) add('error', 'ACTION_OUTSIDE_SCENE', ap, `action ends at ${a.t1}s, after the scene (${sc.duration_sec}s)`)
        if (def.min_duration && a.t1 - a.t0 < def.min_duration - 1e-6) add('warning', 'ACTION_TOO_SHORT', ap, `${a.name} needs at least ${def.min_duration}s, got ${(a.t1 - a.t0).toFixed(2)}s`, 'lengthen the action or pick a shorter one')
      })
    })
    sc.captions.forEach((c, ci) => {
      if (c.t1 > sc.duration_sec + 1e-6) add('warning', 'CAPTION_OUTSIDE_SCENE', `${p}.captions[${ci}]`, `caption ends at ${c.t1}s, after the scene (${sc.duration_sec}s)`)
      if (c.speaker && !ids.has(c.speaker)) add('warning', 'UNKNOWN_SPEAKER', `${p}.captions[${ci}].speaker`, `speaker '${c.speaker}' is not a character`)
    })
    if (si === spec.scenes.length - 1 && sc.transition_out.type !== 'cut') add('info', 'TRANSITION_LAST', `${p}.transition_out`, 'transition_out on the last scene is ignored')
  })
  const total = totalDuration(spec)
  if (total < catalog.limits.min_total_sec || total > catalog.limits.max_total_sec) {
    const sum = spec.scenes.reduce((a, s) => a + s.duration_sec, 0)
    const hint = total < catalog.limits.min_total_sec ? `add about ${(catalog.limits.min_total_sec - total).toFixed(1)}s of scene time` : `remove about ${(total - catalog.limits.max_total_sec).toFixed(1)}s of scene time`
    add('error', 'DURATION_BUDGET', 'scenes', `total duration is ${total.toFixed(2)}s (scenes ${sum.toFixed(2)}s - transition overlaps ${(sum - total).toFixed(2)}s); the budget is ${catalog.limits.min_total_sec}-${catalog.limits.max_total_sec}s`, hint)
  }
  const missing: LintReport['missing'] = {}
  for (const i of issues) if (i.code === 'REGISTRY_MISSING' && i.kind && i.name) ((missing[i.kind] ??= {})[i.name] ??= []).push(i.path)
  const order = { error: 0, warning: 1, info: 2 } as const
  issues.sort((a, b) => order[a.severity] - order[b.severity] || a.path.localeCompare(b.path))
  return {
    ok: !issues.some((i) => i.severity === 'error'),
    total_sec: Math.round(total * 1000) / 1000,
    n_scenes: spec.scenes.length,
    counts: { errors: issues.filter((i) => i.severity === 'error').length, warnings: issues.filter((i) => i.severity === 'warning').length, infos: issues.filter((i) => i.severity === 'info').length },
    missing,
    issues,
  }
}

export async function createMockApi(): Promise<Api> {
  const [cat, story, explainer, scripts] = await Promise.all([
    import('./data/catalog.json'),
    import('./data/story_50s.json'),
    import('./data/explainer_45s.json'),
    import('./data/scripts.json'),
  ])
  const catalog = clone(cat.default) as unknown as Catalog
  const hasKey = new URLSearchParams(location.search).get('mock') === 'eleven'
  const projects = new Map<string, { spec: ReelSpec; script: string; etag: string; updated_at: number }>()
  const seed = (id: string, spec: unknown, script: string, ago: number) =>
    projects.set(id, { spec: clone(spec) as unknown as ReelSpec, script, etag: uid(), updated_at: Date.now() / 1000 - ago })
  seed('explainer-45s', explainer.default, 'Why do we sleep? Short answer: to clean up and to remember.', 5400)
  seed('story-50s', story.default, 'The Lost Umbrella\n\nMia: Rain, rain, go away!\nPip: Oh no! Where is my umbrella?', 600)
  const renders: RenderResult[] = []
  const previews = new Map<string, { spec: ReelSpec; info: PreviewInfo }>()
  const jobs = new Map<string, MockJob>()
  const cached = new Set<string>()

  const summary = (id: string): ProjectSummary => {
    const p = projects.get(id)!
    const l = lint(p.spec, catalog)
    return { id, etag: p.etag, updated_at: p.updated_at, title: p.spec.meta.title, style: p.spec.meta.style, scenes: p.spec.scenes.length, duration_sec: totalDuration(p.spec), lint: { errors: l.counts.errors, warnings: l.counts.warnings } }
  }
  const doc = (id: string): ProjectDoc => {
    const p = projects.get(id)
    if (!p) throw new ApiError(404, `no project '${id}'`)
    return { id, spec: clone(p.spec), script: p.script, etag: p.etag, updated_at: p.updated_at }
  }

  function startJob(kind: string, title: string, run: (emit: (e: JobEventInput) => void, done: () => boolean) => Promise<void>): string {
    const id = uid()
    const job: MockJob = {
      snap: { id, kind, title, status: 'queued', created_at: Date.now() / 1000, started_at: null, finished_at: null, result: null, events: [] },
      listeners: new Set(),
      timers: [],
    }
    jobs.set(id, job)
    const emit = (e: JobEventInput) => {
      if (['done', 'error', 'cancelled'].includes(job.snap.status)) return
      const ev = { ...e, seq: job.snap.events.length } as JobEvent
      job.snap.events.push(ev)
      if (ev.type === 'status') job.snap.status = ev.status
      if (ev.type === 'done') ((job.snap.status = 'done'), (job.snap.result = ev.result))
      if (ev.type === 'error') job.snap.status = 'error'
      if (ev.type === 'cancelled') job.snap.status = 'cancelled'
      for (const l of [...job.listeners]) l(ev)
    }
    emit({ type: 'status', status: 'queued', position: 1 })
    void (async () => {
      await sleep(250)
      if (job.snap.status === 'cancelled') return
      emit({ type: 'status', status: 'running' })
      try {
        await run(emit, () => job.snap.status === 'cancelled')
      } catch (e) {
        emit({ type: 'error', message: String(e) })
      }
    })()
    return id
  }

  const engines = (): EngineStatus[] => [
    { name: 'piper', label: 'Piper', online: false, available: false, detail: 'installed but no voice model: put a .onnx voice in ~/.local/share/piper (reel never downloads one)' },
    { name: 'say', label: 'macOS Say', online: false, available: true, detail: 'ready' },
    { name: 'babble', label: 'Placeholder', online: false, available: true, detail: 'always available: a synthetic voice for lip-sync tests, not meant to be intelligible' },
    {
      name: 'elevenlabs',
      label: 'ElevenLabs',
      online: true,
      available: hasKey,
      detail: hasKey ? 'key found in shell; model eleven_multilingual_v2' : 'no key found: set ELEVENLABS_API_KEY in your shell or in a git-ignored .env file',
      destination: 'api.elevenlabs.io',
      model: 'eleven_multilingual_v2',
      key_source: hasKey ? 'shell' : null,
    },
  ]

  const speakable = (spec: ReelSpec) => spec.scenes.flatMap((s) => s.captions.filter((c) => (c.speak ?? c.style === 'subtitle') && c.text.trim() && c.t0 < s.duration_sec))

  const api: Api = {
    kind: 'mock',
    health: async () => ({ version: '0.1.0 (mock)', python: '3.12', ffmpeg: 'ffmpeg 8.0 (mock)', ffmpeg_ok: true, skia: '144 (mock)', workers: 10, cache_dir: '~/.cache/reel', plugins: [], workspace: '(in memory)' }),
    catalog: async () => clone(catalog),
    schema: async () => ({ title: 'ReelSpec (mock)', type: 'object', required: ['meta', 'scenes'] }),
    examples: async (): Promise<Examples> => ({
      specs: [
        { id: 'story_50s', title: 'The Lost Umbrella', style: 'paper_cutout', scenes: 10, duration_sec: 49.7 },
        { id: 'explainer_45s', title: 'Why Do We Sleep?', style: 'stickman', scenes: 9, duration_sec: 46.3 },
      ],
      scripts: clone(scripts.default) as Examples['scripts'],
    }),

    listProjects: async () => {
      await sleep(120)
      return [...projects.keys()].map(summary).sort((a, b) => b.updated_at - a.updated_at)
    },
    getProject: async (id) => (await sleep(80), doc(id)),
    createProject: async (body) => {
      await sleep(120)
      let spec: ReelSpec
      let script = body.script ?? ''
      if (body.from_example) spec = clone(body.from_example.startsWith('explainer') ? explainer.default : story.default) as unknown as ReelSpec
      else if (body.spec) spec = clone(body.spec)
      else {
        spec = clone(story.default) as unknown as ReelSpec
        spec.scenes = spec.scenes.slice(0, 2).map((s) => ({ ...s, duration_sec: 25, transition_out: { type: 'cut', duration: 0, params: {} } }))
        spec.meta.title = body.title ?? 'Untitled reel'
        script = ''
      }
      if (body.title) spec.meta.title = body.title
      let id = (body.title ?? spec.meta.title).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'reel'
      while (projects.has(id)) id += '-2'
      seed(id, spec, script, 0)
      return doc(id)
    },
    saveProject: async (id, spec, script, etag) => {
      await sleep(90)
      const p = projects.get(id)
      if (!p) throw new ApiError(404, `no project '${id}'`)
      if (p.etag !== etag) throw new ApiError(409, 'the project changed on disk since you opened it', undefined, { current_etag: p.etag, current: doc(id) })
      p.spec = clone(spec)
      if (script !== undefined) p.script = script
      p.etag = uid()
      p.updated_at = Date.now() / 1000
      return doc(id)
    },
    deleteProject: async (id) => void projects.delete(id),
    projectThumbUrl: (p) => posterSvg(p.id, p.style === 'stickman' ? 'dawn' : 'day'),

    lint: async (spec) => (await sleep(60), lint(spec as ReelSpec, catalog)),
    // a small stand-in for the engine's script check: whole lines, compared as words
    scriptCheck: async (script, spec) => {
      await sleep(60)
      const norm = (t: string) => t.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()
      const lines = script
        .split('\n')
        .map((l) => l.replace(/^[^:：]{1,24}[:：]\s*/, '').trim())
        .filter(Boolean)
      if (!lines.length) return { empty: true }
      const have = new Set(spec.scenes.flatMap((s) => s.captions.map((c) => norm(c.text))))
      const missing = lines.filter((l) => !have.has(norm(l)))
      const labels = spec.scenes.flatMap((s) => s.captions).filter((c) => c.text.trim().endsWith(':')).length
      const words = lines.join(' ').split(/\s+/).length
      return { empty: false, covered: 1 - missing.length / lines.length, units: lines.length, words, missing: missing.map((text) => ({ text, speaker: null, kind: 'narration' })), labels, foreign: 0, ok: missing.length === 0 && labels === 0, fits: words < 130, reading_sec: Math.round(words / 2.6) }
    },
    scriptLock: async (script, spec) => {
      const before = await api.scriptCheck(script, spec)
      if (!before.fits) throw new ApiError(409, 'this script is too long to show word for word', `it takes about ${before.reading_sec} s to read and a reel holds about 55 s: shorten it, or let a planner condense it`)
      const out = clone(spec)
      const last = out.scenes[out.scenes.length - 1]
      for (const sc of out.scenes) sc.captions = sc.captions.filter((c) => !c.text.trim().endsWith(':'))
      for (const m of before.missing ?? []) last.captions.push({ text: m.text, t0: 0.5, t1: Math.min(last.duration_sec, 3), style: 'subtitle', anchor: 'auto' })
      return { spec: out, changed: !before.ok, notes: before.ok ? [] : [`kept the script word for word: ${before.missing?.length ?? 0} line(s) of the script added`], lint: lint(out, catalog), coverage: { ...before, ok: true, covered: 1, missing: [], labels: 0 } }
    },
    generate: async (body) => {
      const id = startJob('generate', 'Script to reel', async (emit, cancelled) => {
        emit({ type: 'note', message: `Planning with ${body.planner === 'offline' ? 'the offline planner' : body.planner}` })
        await sleep(500)
        if (body.planner !== 'offline' && !hasKey && body.planner.startsWith('openai')) {
          emit({ type: 'error', message: 'OPENAI_API_KEY is not set', hint: 'set it in your shell or a git-ignored .env, or use the offline planner' })
          return
        }
        const spec = clone(story.default) as unknown as ReelSpec
        spec.meta.style = body.style
        spec.meta.seed = body.seed
        spec.meta.title = body.script.trim().split('\n')[0].slice(0, 60) || 'Untitled'
        emit({ type: 'note', message: `offline planner (no LLM): ${spec.scenes.length} scenes, ${spec.characters.length} character(s)` })
        await sleep(400)
        emit({ type: 'note', message: `rescaled scene durations: total 54.3s -> ${body.target_duration.toFixed(1)}s` })
        await sleep(300)
        if (cancelled()) return
        const l = lint(spec, catalog)
        emit({ type: 'done', result: { spec, lint: l, attempts: 1, notes: [], generator: 'offline planner' } })
      })
      return { job_id: id }
    },

    createPreview: async (spec, scale = 0.5) => {
      await sleep(40)
      const l = lint(spec, catalog)
      if (spec.scenes.length === 0) throw new ApiError(422, 'the spec is not valid yet, so there is nothing to draw', undefined, { issues: l.issues })
      const fps = spec.meta.fps || 30
      const total = Math.max(1, Math.round(totalDuration(spec) * fps))
      const info: PreviewInfo = { preview_id: uid(), fps, total_frames: total, width: Math.round(1080 * scale), height: Math.round(1920 * scale) }
      previews.set(info.preview_id, { spec: clone(spec), info })
      if (previews.size > 6) previews.delete(previews.keys().next().value as string)
      return info
    },
    frame: async (previewId, n) => {
      const p = previews.get(previewId)
      if (!p) throw new ApiError(404, 'this preview has expired', 'request a new preview for the current spec')
      return mockFrameBlob(p.spec, catalog, n / p.info.fps, p.info.width, p.info.height)
    },
    libraryThumbUrl: (kind, name, tod = 'day') => posterSvg(`${kind}:${name}`, tod),

    ttsEngines: async () => ({ engines: engines(), default: 'say' }),
    ttsVoices: async (engine): Promise<VoiceInfo[]> => {
      await sleep(150)
      if (engine === 'elevenlabs') {
        if (!hasKey) throw new ApiError(409, 'TTS engine \'elevenlabs\' needs an API key', 'see Voice & Audio for what this engine needs')
        return [
          { id: '21m00Tcm4TlvDq8ikWAM', name: 'Rachel', traits: { gender: 'female', age: 'young', accent: 'american', use_case: 'narration', category: 'premade' } },
          { id: 'pNInz6obpgDQGcFmaJgB', name: 'Adam', traits: { gender: 'male', age: 'middle_aged', use_case: 'narration', category: 'premade' } },
          { id: 'ErXwobaYiN019PkySvjV', name: 'Antoni', traits: { gender: 'male', age: 'young', use_case: 'characters', category: 'premade' } },
          { id: 'MF3mGyEYCl7XYWbV9V6O', name: 'Elli', traits: { gender: 'female', age: 'young', use_case: 'social media', category: 'premade' } },
        ]
      }
      if (engine === 'say') return ['Samantha', 'Daniel', 'Karen', 'Moira', 'Fred', 'Junior'].map((n) => ({ id: n, name: n, traits: { language: 'en_US' } }))
      return [{ id: 'babble', name: 'babble', traits: {} }]
    },
    ttsSample: async ({ engine, confirmBilling, text }) => {
      await sleep(300)
      if (engine === 'elevenlabs' && !confirmBilling) throw new ApiError(409, 'this audition would be billed by ElevenLabs', 'confirm to spend the credits', { confirm_required: true, billable_characters: text.length })
      return new Blob([], { type: 'audio/wav' })
    },
    estimateSpeech: async (spec, engine): Promise<SpeechEstimate> => {
      await sleep(80)
      const name = engine && engine !== 'auto' ? engine : 'say'
      if (name === 'elevenlabs' && !hasKey) return { available: false, reason: "TTS engine 'elevenlabs' needs an API key: set ELEVENLABS_API_KEY in your shell or in a git-ignored .env file", engine: name }
      const lines = new Map<string, number>()
      for (const c of speakable(spec)) lines.set(c.text.trim(), c.text.trim().length)
      const keys = [...lines.keys()]
      const cachedLines = keys.filter((k) => cached.has(`${name}:${k}`))
      const fresh = keys.filter((k) => !cached.has(`${name}:${k}`))
      return { available: true, engine: name, online: name === 'elevenlabs', lines: keys.length, cached_lines: cachedLines.length, new_lines: fresh.length, billable_characters: name === 'elevenlabs' ? fresh.reduce((a, k) => a + (lines.get(k) ?? 0), 0) : 0, destination: name === 'elevenlabs' ? 'api.elevenlabs.io' : null }
    },
    prepareAudio: async (spec, engine) => {
      const id = startJob('audio', 'Voice and sound', async (emit) => {
        emit({ type: 'progress', phase: 'audio', done: 0, total: 1 })
        await sleep(700)
        const name = engine && engine !== 'auto' ? engine : 'say'
        const caps = speakable(spec)
        for (const c of caps) cached.add(`${name}:${c.text.trim()}`)
        if (name === 'elevenlabs') emit({ type: 'note', message: `elevenlabs (eleven_multilingual_v2): ${caps.reduce((a, c) => a + c.text.length, 0)} characters in ${caps.length} line(s) were sent to api.elevenlabs.io and billed; lines already in the cache cost nothing` })
        const total = totalDuration(spec)
        const bins = 1000
        const slots = sceneSlots(spec)
        const voice = new Array<number>(bins).fill(0)
        const music = new Array<number>(bins).fill(0)
        const sfx = new Array<number>(bins).fill(0)
        spec.scenes.forEach((sc, si) => {
          for (const c of sc.captions) {
            for (let b = Math.floor(((slots[si].start + c.t0) / total) * bins); b < Math.min(bins, Math.ceil(((slots[si].start + c.t1) / total) * bins)); b++) voice[b] = 0.12 + 0.2 * Math.abs(Math.sin(b * 0.9))
          }
          for (const x of sc.sfx) sfx[Math.min(bins - 1, Math.floor(((slots[si].start + x.t) / total) * bins))] = 0.5
        })
        for (let b = 0; b < bins; b++) music[b] = 0.08 + 0.04 * Math.sin(b / 18)
        // the dip under the voice: 0.08 s ahead of each line, 0.45 s to recover (like the mixer's)
        const perSec = 40
        const gain = new Array<number>(Math.ceil(total * perSec)).fill(1)
        spec.scenes.forEach((sc, si) => {
          for (const c of sc.captions) {
            const a = slots[si].start + c.t0
            const z = slots[si].start + c.t1
            for (let i = Math.max(0, Math.floor((a - 0.08) * perSec)); i < Math.min(gain.length, Math.ceil((z + 0.45) * perSec)); i++) {
              const t = i / perSec
              const down = Math.min(1, Math.max(0, (t - (a - 0.08)) / 0.08))
              const up = Math.min(1, Math.max(0, (z + 0.45 - t) / 0.45))
              gain[i] = Math.min(gain[i], Math.round((1 - Math.min(down, up) * 0.75) * 1000) / 1000)
            }
          }
        })
        emit({ type: 'progress', phase: 'audio', done: 1, total: 1 })
        const result: AudioResult = { wav_url: `/api/audio/mock-${uid()}.wav`, spec: clone(spec), retimed: [], word_timings: {}, warnings: [], notes: [], report: { duration: total, peak: 0.57, approx_lufs: -16, ducked_seconds: 24.3, n_clips: caps.length + 12 }, peaks: { voice, music, sfx }, duck: { per_sec: perSec, depth_db: -12, gain }, total_sec: total }
        emit({ type: 'done', result: result as unknown as Record<string, unknown> })
      })
      return { job_id: id }
    },
    sfxUrl: (name) => `mock-sfx://${name}`,
    musicUrl: (mood) => `mock-music://${mood}`,
    audioUrl: (u) => u,

    renderPlan: async (body): Promise<RenderPlan> => {
      await sleep(90)
      const spec = (body.spec ?? clone(story.default)) as unknown as ReelSpec
      const scale = body.preset === 'draft' ? 1 / 3 : body.preset === 'standard' ? 0.5 : (body.scale ?? 1)
      const total = Math.max(1, Math.ceil(totalDuration(spec) / 2))
      const cachedN = renders.length ? Math.floor(total * 0.8) : 0
      return { frames: Math.round(totalDuration(spec) * 30), segments_total: total, segments_cached: cachedN, est_seconds: Math.round((total - cachedN) * 1.75 * scale ** 1.7 * 10) / 10, width: Math.round(1080 * scale), height: Math.round(1920 * scale), duration_sec: totalDuration(spec), preset: body.preset, voice: 'ready' }
    },
    render: async (body: RenderBody) => {
      const spec = (body.spec ?? clone(story.default)) as unknown as ReelSpec
      const rid = `${body.project_id ?? 'reel'}-${body.preset}-${Date.now().toString(36)}`
      const scale = body.preset === 'draft' ? 1 / 3 : body.preset === 'standard' ? 0.5 : (body.scale ?? 1)
      const id = startJob('render', `Render (${body.preset})`, async (emit, cancelled) => {
        const l = lint(spec, catalog)
        if (!l.ok && body.preset !== 'draft' && !body.lenient) {
          emit({ type: 'error', message: `the spec has ${l.counts.errors} error(s), so it was not rendered`, hint: 'fix them in Problems, or render anyway with the lenient option (unknown names fall back)', lint: l })
          return
        }
        emit({ type: 'progress', phase: 'plan', done: 0, total: 1 })
        await sleep(300)
        emit({ type: 'progress', phase: 'audio', done: 0, total: 1 })
        await sleep(500)
        emit({ type: 'progress', phase: 'audio', done: 1, total: 1 })
        const frames = Math.round(totalDuration(spec) * 30)
        emit({ type: 'note', message: `${renders.length ? 28 : 0} of 35 chunks are already cached` })
        const t0 = performance.now()
        for (let f = 0; f <= frames; f += 60) {
          if (cancelled()) return
          const done = Math.min(frames, f)
          const elapsed = (performance.now() - t0) / 1000
          emit({ type: 'progress', phase: 'frames', done, total: frames, eta_sec: done ? Math.round((elapsed * (frames - done)) / done * 10) / 10 : null })
          await sleep(60)
        }
        emit({ type: 'progress', phase: 'mux', done: 1, total: 1 })
        const res: RenderResult = { id: rid, video_url: `/api/renders/${rid}/video`, path: `/workspace/renders/${rid}.mp4`, size_bytes: Math.round(frames * 2400 * scale), width: Math.round(1080 * scale), height: Math.round(1920 * scale), duration_sec: totalDuration(spec), frames, seconds: 4.2, bitrate_bps: 2_400_000, segments: { total: 35, encoded: 35, reused: 0 }, warnings: [], notes: [], preset: body.preset, project_id: body.project_id ?? null, title: spec.meta.title, style: spec.meta.style, has_audio: true, created_at: Date.now() / 1000 }
        renders.unshift(res)
        emit({ type: 'done', result: res as unknown as Record<string, unknown> })
      })
      return { job_id: id, render_id: rid }
    },
    listRenders: async () => clone(renders),
    deleteRender: async (id) => void renders.splice(renders.findIndex((r) => r.id === id), 1),

    getJob: async (id, after = 0) => {
      const j = jobs.get(id)
      if (!j) throw new ApiError(404, 'no such job', 'jobs are forgotten when the server restarts')
      return { ...clone(j.snap), events: j.snap.events.slice(after) }
    },
    cancelJob: async (id) => {
      const j = jobs.get(id)
      if (!j || ['done', 'error', 'cancelled'].includes(j.snap.status)) return
      j.snap.status = 'cancelled'
      const ev: JobEvent = { type: 'cancelled', seq: j.snap.events.length }
      j.snap.events.push(ev)
      for (const l of [...j.listeners]) l(ev)
    },
    subscribeJob: (id, onEvent, after = 0) => {
      const j = jobs.get(id)
      if (!j) return () => undefined
      for (const e of j.snap.events.slice(after)) onEvent(e)
      j.listeners.add(onEvent)
      return () => j.listeners.delete(onEvent)
    },

    doctor: async () => ({
      reel: '0.1.0 (mock)',
      python: '3.12',
      ffmpeg: { ok: true, version: 'ffmpeg version 8.0 (mock)' },
      skia: { ok: true, version: '144 (mock)' },
      fonts: [{ role: 'headline', family: 'Impact' }, { role: 'sans', family: 'Avenir Next' }, { role: 'marker', family: 'Marker Felt' }],
      tts: engines(),
      tts_default: 'say',
      llm: { ollama: { running: true, url: 'http://localhost:11434', models: [{ name: 'gemma2:2b', remote: false }, { name: 'qwen3-coder:480b-cloud', remote: true }] }, keys: [{ name: 'OPENAI_API_KEY', source: null }, { name: 'ANTHROPIC_API_KEY', source: null }, { name: 'ELEVENLABS_API_KEY', source: hasKey ? 'shell' : null }], default: null },
      counts: { actions: 16, styles: 3, backgrounds: 7, transitions: 4 },
      cache_dir: '~/.cache/reel',
    }),
    doctorTest: async (kind, target) => {
      await sleep(600)
      if (kind === 'tts' && target === 'elevenlabs') {
        return hasKey
          ? { ok: true, lines: ['elevenlabs: key accepted by api.elevenlabs.io; model eleven_multilingual_v2', '            plan creator: 98800 of 100000 characters left this period', '            4 voice(s) available'], account: { tier: 'creator', characters_left: 98800, characters_limit: 100000 }, cost: 'no credits spent' }
          : { ok: false, message: "TTS engine 'elevenlabs' needs an API key: set ELEVENLABS_API_KEY in your shell or in a git-ignored .env file" }
      }
      return { ok: false, message: `${target.toUpperCase()}_API_KEY is not set` }
    },
    cache: async () => ({ root: '~/.cache/reel', frames: { files: 3120, bytes: 1_400_000_000 }, segments: { files: 140, bytes: 480_000_000 }, tts: { files: 24, bytes: 3_200_000 }, tmp: { files: 0, bytes: 0 }, total_bytes: 1_883_200_000 }),
    clearCache: async (kind) => {
      const c = await api.cache()
      c[kind] = { files: 0, bytes: 0 }
      c.total_bytes = c.frames.bytes + c.segments.bytes + c.tts.bytes + c.tmp.bytes
      return c
    },
  }
  return api
}
