import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { createMockApi } from '@/api/mock'
import type { LintIssue, LintReport, Severity } from '@/api/types'
import { newObject } from '@/lib/spec'
import { sceneSlots } from '@/lib/timeline'
import { useLint } from '@/store/lint'
import { useProject } from '@/store/project'
import { renderScreen } from '@/test/harness'
import { withObjects } from './objects.fixture'
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

// ------------------------------------------------------------------------------- objects
const problem = (code: string, path: string, extra: Partial<LintIssue> = {}, severity: Severity = 'error'): LintIssue => ({ severity, code, path, message: `${code} at ${path}`, ...extra })
const report = (...issues: LintIssue[]): LintReport => ({ ok: false, total_sec: 50, n_scenes: 10, counts: { errors: issues.length, warnings: 0, infos: 0 }, missing: {}, issues })

async function openWithObjects() {
  window.history.replaceState({}, '', '/')
  const api = withObjects(await createMockApi())
  useProject.getState().load(await api.getProject('story-50s'))
  useProject.getState().edit((d) => {
    // scene 1 is a street, 5.9 s; it starts 4.9 s into the reel
    d.scenes[1].objects.push({ ...newObject('car', 'sofa'), t0: 2, motions: [{ type: 'move', t0: 1, t1: 3, to: 'sofa', ease: 'ease_in_out' }, { type: 'hop', t0: 4, t1: 9, ease: 'ease_in_out' }] })
    d.scenes[1].objects.push({ ...newObject('rickshaw', [0.5, 0.74]), t0: 7 })
  })
  return api
}
const showing = (issues: LintIssue[]) => act(() => useLint.getState().set(report(...issues), false))
const sceneOne = () => useProject.getState().spec!.scenes[1]

describe('problems with objects', () => {
  it('leads to the object, with the playhead at the moment it appears', async () => {
    const user = userEvent.setup()
    const api = await openWithObjects()
    showing([problem('POSITION_SLOT', 'scenes[1].objects[0].position', { message: 'position slot sofa does not exist' })])
    await renderScreen(<ProblemsDrawer />, { api })
    expect(await screen.findByText('Scene 2 › car')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Go to' }))
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 1, object: 0 })
    expect(useProject.getState().playhead).toBeCloseTo(sceneSlots(useProject.getState().spec!)[1].start + 2.01, 6) // the car appears 2 s into the scene
  })

  it('leads to the motion, with the playhead where it starts', async () => {
    const user = userEvent.setup()
    const api = await openWithObjects()
    showing([problem('OBJECT_MOTION', 'scenes[1].objects[0].motions[0]', { message: 'a move needs `to`' })])
    await renderScreen(<ProblemsDrawer />, { api })
    expect(await screen.findByText('Scene 2 › car › move')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Go to' }))
    expect(useProject.getState().selection).toEqual({ kind: 'motion', scene: 1, object: 0, motion: 0 })
    expect(useProject.getState().playhead).toBeCloseTo(sceneSlots(useProject.getState().spec!)[1].start + 1.01, 6)
  })

  it('leads to the scene for a problem with all of its objects, and to the start of a scene for anything else', async () => {
    const user = userEvent.setup()
    const api = await openWithObjects()
    showing([problem('OBJECT_CLUTTER', 'scenes[1].objects', {}, 'warning'), problem('TIME_RANGE', 'scenes[3].duration_sec')])
    await renderScreen(<ProblemsDrawer />, { api })
    const [clutter, other] = await screen.findAllByRole('button', { name: 'Go to' })
    await user.click(clutter)
    expect(useProject.getState().selection).toEqual({ kind: 'scene', scene: 1 })
    await user.click(other)
    expect(useProject.getState().selection).toEqual({ kind: 'scene', scene: 3 })
    expect(useProject.getState().playhead).toBeCloseTo(sceneSlots(useProject.getState().spec!)[3].start + 0.01, 6)
  })

  it('removes an object the library does not have', async () => {
    const user = userEvent.setup()
    const api = await openWithObjects()
    showing([problem('REGISTRY_MISSING', 'scenes[1].objects[1].asset', { kind: 'object', name: 'rickshaw' })])
    await renderScreen(<ProblemsDrawer />, { api })
    await user.click(await screen.findByRole('button', { name: 'Remove the object' }))
    expect(sceneOne().objects.map((o) => o.asset)).toEqual(['car'])
    act(() => useProject.getState().undo())
    expect(sceneOne().objects.map((o) => o.asset)).toEqual(['car', 'rickshaw'])
  })

  it('puts the place a move goes to in the middle of the set when the set has no such place — and only that one', async () => {
    const user = userEvent.setup()
    const api = await openWithObjects()
    showing([problem('POSITION_SLOT', 'scenes[1].objects[0].motions[0].to')])
    await renderScreen(<ProblemsDrawer />, { api })
    await user.click(await screen.findByRole('button', { name: /use “center”/i }))
    expect(sceneOne().objects[0].motions[0].to).toBe('center')
    expect(sceneOne().objects[0].position).toBe('sofa') // not what the problem was about
  })

  it('puts an object in the middle of the set when the set has no such place', async () => {
    const user = userEvent.setup()
    const api = await openWithObjects()
    showing([problem('POSITION_SLOT', 'scenes[1].objects[0].position')])
    await renderScreen(<ProblemsDrawer />, { api })
    await user.click(await screen.findByRole('button', { name: /use “center”/i }))
    expect(sceneOne().objects[0].position).toBe('center')
    expect(sceneOne().objects[0].motions[0].to).toBe('sofa')
  })

  it('fits objects and their motions inside the scene, with the other clips', async () => {
    const user = userEvent.setup()
    const api = await openWithObjects()
    showing([problem('TIME_OVERFLOW', 'scenes[1].objects[0].motions[1]', {}, 'warning')])
    await renderScreen(<ProblemsDrawer />, { api })
    await user.click(await screen.findByRole('button', { name: /fit inside the scene/i }))
    expect(sceneOne().objects[0].motions[1]).toMatchObject({ t0: 4, t1: 5.9 }) // it ran to 9 s in a 5.9 s scene
    expect(sceneOne().objects[1]).toMatchObject({ t0: 5.8, t1: null }) // it appeared at 7 s: now the scene's last moments
  })

  it('repairs several problems at once, objects among them, as one undo step', async () => {
    const user = userEvent.setup()
    const api = await openWithObjects()
    showing([problem('REGISTRY_MISSING', 'scenes[1].objects[1].asset', { kind: 'object', name: 'rickshaw' }), problem('POSITION_SLOT', 'scenes[1].objects[0].position')])
    await renderScreen(<ProblemsDrawer />, { api })
    const before = useProject.getState().past.length
    await user.click(await screen.findByRole('button', { name: /fix all 2/i }))
    expect(sceneOne().objects).toHaveLength(1)
    expect(sceneOne().objects[0].position).toBe('center')
    expect(useProject.getState().past.length).toBe(before + 1)
  })
})

