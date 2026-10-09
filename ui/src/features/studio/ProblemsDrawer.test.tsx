import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { createMockApi } from '@/api/mock'
import type { LintReport } from '@/api/types'
import { useLint } from '@/store/lint'
import { useProject } from '@/store/project'
import { renderScreen } from '@/test/harness'
import { ProblemsDrawer } from './ProblemsDrawer'

const issue = (path: string) => ({ severity: 'error' as const, code: 'CAMERA_MOVE_INVALID', path, message: `pan moves the picture off the set (${path})`, hint: undefined })

async function open() {
  window.history.replaceState({}, '', '/')
  const api = await createMockApi()
  useProject.getState().load(await api.getProject('story-50s'))
  // the OpenAI spec's habit: a pan written as where the camera looks, not how far it drifts
  useProject.getState().edit((d) => {
    d.scenes[0].camera.moves = [{ type: 'pan', from: [0.2, 0.5], to: [0.8, 0.5], t0: 0, t1: 4, ease: 'linear', params: {} }]
    d.scenes[1].camera.moves = [{ type: 'pan', from: [0.5, 0.5], to: [0.5, 0.6], t0: 0, t1: 4, ease: 'linear', params: {} }]
  })
  const report: LintReport = {
    ok: false,
    total_sec: 50,
    n_scenes: 8,
    counts: { errors: 4, warnings: 0, infos: 0 },
    missing: {},
    issues: ['scenes[0].camera.moves[0]', 'scenes[0].camera.moves[0]', 'scenes[1].camera.moves[0]', 'scenes[1].camera.moves[0]'].map(issue),
  }
  act(() => useLint.getState().set(report, false))
  return api
}

beforeEach(() => {
  useProject.getState().unload()
  useLint.setState({ report: null, busy: false })
})

describe('the problems list', () => {
  it('repairs every problem that has a one-click fix at once, and one undo takes it back', async () => {
    const user = userEvent.setup()
    const api = await open()
    await renderScreen(<ProblemsDrawer />, { api })
    const st = useProject.getState
    const before = st().spec!.scenes[0].camera.moves[0]

    await user.click(await screen.findByRole('button', { name: /fix all 4/i }))
    const [a, b] = [st().spec!.scenes[0].camera.moves[0], st().spec!.scenes[1].camera.moves[0]]
    expect(a.from).toEqual([-0.15, 0]) // [0.2, 0.5] read as a drift from the centre
    expect(a.to).toEqual([0.15, 0])
    expect(b.from).toEqual([0, 0])
    expect((b.to as number[])[1]).toBeCloseTo(0.05)

    act(() => st().undo())
    expect(st().spec!.scenes[0].camera.moves[0]).toEqual(before)
  })

  it('offers no "fix all" for a single problem', async () => {
    const api = await open()
    act(() => useLint.getState().set({ ok: false, total_sec: 50, n_scenes: 8, counts: { errors: 1, warnings: 0, infos: 0 }, missing: {}, issues: [issue('scenes[0].camera.moves[0]')] }, false))
    await renderScreen(<ProblemsDrawer />, { api })
    expect(await screen.findByRole('button', { name: /keep the camera on the set/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /fix all/i })).not.toBeInTheDocument()
  })
})
