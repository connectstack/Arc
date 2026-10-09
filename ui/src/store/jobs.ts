import { create } from 'zustand'
import type { Api } from '@/api/client'
import type { JobEvent, JobStatus } from '@/api/types'

export type JobKind = 'render' | 'audio' | 'generate'

export interface TrackedJob {
  id: string
  kind: JobKind
  title: string
  projectId?: string
  status: JobStatus
  queuePosition?: number
  phase?: 'plan' | 'audio' | 'frames' | 'mux'
  done: number
  total: number
  etaSec?: number | null
  notes: string[]
  warnings: string[]
  result?: Record<string, unknown>
  error?: Extract<JobEvent, { type: 'error' }>
  startedAt: number
  finishedAt?: number
}

interface JobsState {
  jobs: Record<string, TrackedJob>
  order: string[]
  /**
   * After a page reload: follow the renders that were still running when the page went away (the server keeps them going and
   * replays their events). `onDone` runs for each one that finishes, including one that finished while the page was gone.
   */
  resume: (api: Api, onDone?: (info: { title: string; projectId?: string }, result: Record<string, unknown>) => void) => Promise<void>
  /** Start following a job the server already created. `onDone` runs once with its result. */
  track: (api: Api, id: string, info: { kind: JobKind; title: string; projectId?: string }, hooks?: { onDone?: (result: Record<string, unknown>) => void; onError?: (e: Extract<JobEvent, { type: 'error' }>) => void; onCancel?: () => void }) => void
  cancel: (api: Api, id: string) => Promise<void>
  dismiss: (id: string) => void
  active: () => TrackedJob[]
}

const subscriptions = new Map<string, () => void>()
const TERMINAL: JobStatus[] = ['done', 'error', 'cancelled']

// The renders in progress are remembered for this tab (not saved anywhere else), so a reload can pick them up again.
const KEY = 'reel.jobs'
type Remembered = Record<string, { kind: JobKind; title: string; projectId?: string }>
const recall = (): Remembered => {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? '{}') as Remembered
  } catch {
    return {}
  }
}
const remember = (v: Remembered): void => {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(v))
  } catch {
    /* storage can be blocked: a reload then simply forgets the job */
  }
}

export const useJobs = create<JobsState>((set, get) => ({
  jobs: {},
  order: [],
  track: (api, id, info, hooks) => {
    if (subscriptions.has(id)) return
    if (info.kind === 'render') remember({ ...recall(), [id]: info })
    const base: TrackedJob = { id, ...info, status: 'queued', done: 0, total: 1, notes: [], warnings: [], startedAt: Date.now() }
    set((s) => ({ jobs: { ...s.jobs, [id]: base }, order: [id, ...s.order.filter((x) => x !== id)] }))
    const patch = (fn: (j: TrackedJob) => TrackedJob) => set((s) => (s.jobs[id] ? { jobs: { ...s.jobs, [id]: fn(s.jobs[id]) } } : s))
    const off = api.subscribeJob(id, (e) => {
      patch((j) => {
        switch (e.type) {
          case 'status':
            return { ...j, status: e.status, queuePosition: e.position }
          case 'progress':
            return { ...j, status: 'running', phase: e.phase, done: e.done, total: e.total, etaSec: e.eta_sec ?? null }
          case 'note':
            return { ...j, notes: [...j.notes, e.message] }
          case 'warning':
            return { ...j, warnings: [...j.warnings, e.message] }
          case 'done':
            return { ...j, status: 'done', result: e.result, finishedAt: Date.now() }
          case 'error':
            return { ...j, status: 'error', error: e, finishedAt: Date.now() }
          case 'cancelled':
            return { ...j, status: 'cancelled', finishedAt: Date.now() }
          default:
            return j
        }
      })
      if (e.type === 'done') hooks?.onDone?.(e.result)
      if (e.type === 'error') hooks?.onError?.(e)
      if (e.type === 'cancelled') hooks?.onCancel?.()
      if (TERMINAL.includes(e.type as JobStatus)) {
        subscriptions.get(id)?.()
        subscriptions.delete(id)
        const left = recall()
        if (id in left) {
          delete left[id]
          remember(left)
        }
      }
    })
    subscriptions.set(id, off)
  },
  resume: async (api, onDone) => {
    for (const [id, info] of Object.entries(recall())) {
      if (get().jobs[id] || subscriptions.has(id)) continue
      try {
        await api.getJob(id, 0) // gone (the server restarted): this throws
        get().track(api, id, info, { onDone: (result) => onDone?.(info, result) })
      } catch {
        const left = recall()
        delete left[id]
        remember(left)
      }
    }
  },
  cancel: async (api, id) => {
    await api.cancelJob(id)
  },
  dismiss: (id) => set((s) => ({ jobs: Object.fromEntries(Object.entries(s.jobs).filter(([k]) => k !== id)), order: s.order.filter((x) => x !== id) })),
  active: () => Object.values(get().jobs).filter((j) => !TERMINAL.includes(j.status)),
}))
