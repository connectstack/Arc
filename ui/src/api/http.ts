import type { Api } from './client'
import { ApiError } from './types'
import type { JobEvent, JobSnapshot, ProjectSummary } from './types'

async function parseError(res: Response): Promise<ApiError> {
  let body: Record<string, unknown> = {}
  try {
    body = (await res.json()) as Record<string, unknown>
  } catch {
    /* not JSON */
  }
  const { detail, hint, ...extra } = body
  const text = typeof detail === 'string' ? detail : res.statusText || `HTTP ${res.status}`
  return new ApiError(res.status, text, typeof hint === 'string' ? hint : undefined, extra)
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  let res: Response
  try {
    res = await fetch(path, { credentials: 'same-origin', ...init })
  } catch (e) {
    throw new ApiError(0, 'cannot reach the Reel Studio server', 'is `reel serve` still running?', { cause: String(e) })
  }
  if (!res.ok) throw await parseError(res)
  return res
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  return (await request(path, init)).json() as Promise<T>
}

function post<T>(path: string, body: unknown, init: RequestInit = {}): Promise<T> {
  return json<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...init,
  })
}

const qs = (params: Record<string, string | number | undefined>): string => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined) p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}

export function createHttpApi(): Api {
  return {
    kind: 'http',
    health: () => json('/api/health'),
    catalog: () => json('/api/catalog'),
    schema: () => json('/api/schema'),
    examples: () => json('/api/examples'),

    listProjects: () => json('/api/projects'),
    getProject: (id) => json(`/api/projects/${encodeURIComponent(id)}`),
    createProject: (body) => post('/api/projects', body),
    saveProject: (id, spec, script, etag) =>
      json(`/api/projects/${encodeURIComponent(id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'If-Match': etag },
        body: JSON.stringify({ spec, script }),
      }),
    deleteProject: async (id) => {
      await request(`/api/projects/${encodeURIComponent(id)}`, { method: 'DELETE' })
    },
    projectThumbUrl: (p: ProjectSummary) => p.thumb_url ?? `/api/projects/${encodeURIComponent(p.id)}/thumb`,

    lint: (spec) => post('/api/lint', { spec }),
    generate: (body) => post('/api/generate', body),

    scriptCheck: (script, spec) => post('/api/script/check', { script, spec }),
    scriptLock: (script, spec) => post('/api/script/lock', { script, spec }),
    createPreview: (spec, scale = 0.5, wordTimings) => post('/api/preview', { spec, scale, ...(wordTimings ? { word_timings: wordTimings } : {}) }),
    frame: async (previewId, n, signal) => (await request(`/api/preview/${previewId}/frame/${n}`, { signal })).blob(),
    libraryThumbUrl: (kind, name, timeOfDay, style) =>
      `/api/library/thumb/${kind}/${encodeURIComponent(name)}${qs({ time_of_day: timeOfDay, style })}`,

    listAssets: () => json('/api/assets'),
    createAssetDraft: (file, opts = {}) =>
      json(`/api/assets/draft${qs({ filename: opts.filename ?? (file instanceof File ? file.name : 'asset'), kind: opts.kind })}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: file,
      }),
    assetDraftThumbUrl: (id, look) =>
      `/api/assets/draft/${encodeURIComponent(id)}/thumb${qs({
        style: look.style,
        time_of_day: look.timeOfDay,
        true_scale: look.trueScale ? 'true' : undefined,
        kind: look.kind,
        height: look.height,
        anchor_x: look.anchor?.[0],
        anchor_y: look.anchor?.[1],
        facing: look.facing,
        cutout: look.cutout === null || look.cutout === undefined ? undefined : String(look.cutout),
      })}`,
    commitAssetDraft: (id, fields, replace = false) => post(`/api/assets/draft/${encodeURIComponent(id)}/commit`, { ...fields, replace }),
    discardAssetDraft: async (id) => {
      await request(`/api/assets/draft/${encodeURIComponent(id)}`, { method: 'DELETE' })
    },
    updateAsset: (name, changes) =>
      json(`/api/assets/${encodeURIComponent(name)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changes),
      }),
    deleteAsset: async (name) => {
      await request(`/api/assets/${encodeURIComponent(name)}`, { method: 'DELETE' })
    },
    assetThumbUrl: (a, style, timeOfDay, trueScale) =>
      `/api/assets/${encodeURIComponent(a.name)}/thumb${qs({ style, time_of_day: timeOfDay, true_scale: trueScale ? 'true' : undefined, v: a.version })}`,
    assetArtUrl: (name) => `/api/assets/${encodeURIComponent(name)}/art`,
    scriptAssets: (script) => post('/api/script/assets', { script }),
    fillGaps: (spec, only) => post('/api/spec/fill-gaps', { spec, ...(only ? { only } : {}) }),

    ttsEngines: () => json('/api/tts/engines'),
    ttsVoices: (engine) => json(`/api/tts/voices${qs({ engine })}`),
    ttsSample: async ({ engine, voice, text, confirmBilling }) =>
      (
        await request('/api/tts/sample', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ engine, voice, text, confirm_billing: !!confirmBilling }),
        })
      ).blob(),
    estimateSpeech: (spec, engine) => post('/api/audio/estimate', { spec, engine }),
    prepareAudio: (spec, engine) => post('/api/audio/prepare', { spec, engine }),
    sfxUrl: (name) => `/api/sfx/${encodeURIComponent(name)}.wav`,
    musicUrl: (mood, seconds = 10) => `/api/music/${encodeURIComponent(mood)}.wav${qs({ seconds })}`,
    audioUrl: (wavUrl) => wavUrl,

    renderPlan: (body) => post('/api/render/plan', body),
    render: (body) => post('/api/render', body),
    listRenders: () => json('/api/renders'),
    deleteRender: async (id) => {
      await request(`/api/renders/${encodeURIComponent(id)}`, { method: 'DELETE' })
    },

    getJob: (id, after = 0) => json<JobSnapshot>(`/api/jobs/${id}${qs({ after })}`),
    cancelJob: async (id) => {
      await request(`/api/jobs/${id}`, { method: 'DELETE' })
    },
    subscribeJob(id, onEvent, after = 0) {
      // Server-sent events: the browser reconnects by itself and resumes from Last-Event-ID.
      const es = new EventSource(`/api/jobs/${id}/events${qs({ after })}`)
      let closed = false
      es.onmessage = (m) => {
        const ev = JSON.parse(m.data) as JobEvent
        onEvent(ev)
        if (ev.type === 'done' || ev.type === 'error' || ev.type === 'cancelled') {
          closed = true
          es.close()
        }
      }
      es.onerror = () => {
        // The stream ended (or the connection dropped): if the job finished meanwhile, a snapshot tells us how.
        if (closed) return
        void json<JobSnapshot>(`/api/jobs/${id}${qs({ after })}`)
          .then((snap) => {
            if (snap.status === 'done' || snap.status === 'error' || snap.status === 'cancelled') {
              closed = true
              es.close()
              for (const ev of snap.events) onEvent(ev)
            }
          })
          .catch(() => undefined)
      }
      return () => {
        closed = true
        es.close()
      }
    },

    doctor: () => json('/api/doctor'),
    doctorTest: (kind, target) => post('/api/doctor/test', { kind, target }),
    cache: () => json('/api/cache'),
    clearCache: (kind) => json(`/api/cache/${kind}`, { method: 'DELETE' }).then((r) => (r as { stats: never }).stats),
  }
}
