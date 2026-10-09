import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Api } from '@/api/client'
import type { JobEvent } from '@/api/types'
import { useJobs } from './jobs'

/** Just the two calls the job store makes; `subscribeJob` replays `events` at once, like the server does for a late joiner. */
function fakeApi(known: Record<string, object[]>): Api {
  return {
    getJob: vi.fn(async (id: string) => {
      if (!(id in known)) throw new Error('no such job')
      return { id, status: 'running', events: [] }
    }),
    subscribeJob: vi.fn((id: string, onEvent: (e: JobEvent) => void) => {
      ;(known[id] ?? []).forEach((e, seq) => onEvent({ ...e, seq } as JobEvent))
      return () => undefined
    }),
  } as unknown as Api
}

beforeEach(() => {
  sessionStorage.clear()
  useJobs.setState({ jobs: {}, order: [] })
})

describe('jobs across a page reload', () => {
  it('remembers a render while it runs and forgets it when it ends', () => {
    const api = fakeApi({ r1: [{ type: 'progress', phase: 'frames', done: 5, total: 10 }] })
    useJobs.getState().track(api, 'r1', { kind: 'render', title: 'Render (draft)', projectId: 'p' })
    expect(JSON.parse(sessionStorage.getItem('reel.jobs') ?? '{}')).toEqual({ r1: { kind: 'render', title: 'Render (draft)', projectId: 'p' } })

    const done = fakeApi({ r2: [{ type: 'done', result: { id: 'v' } }] })
    useJobs.getState().track(done, 'r2', { kind: 'render', title: 'Render (full)', projectId: 'p' })
    expect(JSON.parse(sessionStorage.getItem('reel.jobs') ?? '{}')).toEqual({ r1: expect.anything() }) // r2 ended
  })

  it('does not keep audio or planner jobs: their results live in the page', () => {
    const api = fakeApi({ a1: [] })
    useJobs.getState().track(api, 'a1', { kind: 'audio', title: 'Voice' })
    expect(sessionStorage.getItem('reel.jobs')).toBeNull()
  })

  it('picks a running render up again after a reload, with its progress replayed', async () => {
    sessionStorage.setItem('reel.jobs', JSON.stringify({ t2r1: { kind: 'render', title: 'Render (draft)', projectId: 'p' } }))
    const api = fakeApi({ t2r1: [{ type: 'progress', phase: 'frames', done: 40, total: 100 }] })
    await useJobs.getState().resume(api)
    const job = useJobs.getState().jobs.t2r1
    expect(job).toMatchObject({ kind: 'render', projectId: 'p', status: 'running', done: 40, total: 100 })
    expect(useJobs.getState().active().map((j) => j.id)).toEqual(['t2r1'])
  })

  it('tells you about a render that finished while the page was t3gone', async () => {
    sessionStorage.setItem('reel.jobs', JSON.stringify({ t3r1: { kind: 'render', title: 'Render (full)', projectId: 'p' } }))
    const api = fakeApi({ t3r1: [{ type: 'done', result: { id: 'video-1', title: 'My reel' } }] })
    const onDone = vi.fn()
    await useJobs.getState().resume(api, onDone)
    expect(onDone).toHaveBeenCalledWith({ kind: 'render', title: 'Render (full)', projectId: 'p' }, { id: 'video-1', title: 'My reel' })
    expect(useJobs.getState().jobs.t3r1.status).toBe('done')
    expect(sessionStorage.getItem('reel.jobs')).toBe('{}')
  })

  it('forgets a job the server no longer knows (it restarted)', async () => {
    sessionStorage.setItem('reel.jobs', JSON.stringify({ t4gone: { kind: 'render', title: 'Render', projectId: 'p' } }))
    await useJobs.getState().resume(fakeApi({}))
    expect(useJobs.getState().jobs).toEqual({})
    expect(sessionStorage.getItem('reel.jobs')).toBe('{}')
  })

  it('does not follow the same job twice', async () => {
    sessionStorage.setItem('reel.jobs', JSON.stringify({ t5r1: { kind: 'render', title: 'Render', projectId: 'p' } }))
    const api = fakeApi({ t5r1: [{ type: 'progress', phase: 'frames', done: 1, total: 2 }] })
    await useJobs.getState().resume(api)
    await useJobs.getState().resume(api)
    expect(api.subscribeJob).toHaveBeenCalledTimes(1)
  })
})
