import { act, fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { createMockApi } from '@/api/mock'
import type { Selection } from '@/lib/spec'
import { useProject } from '@/store/project'
import { renderScreen } from '@/test/harness'
import { Inspector } from './Inspector'

const st = useProject.getState

async function open(select: Selection) {
  window.history.replaceState({}, '', '/')
  const api = await createMockApi()
  st().load(await api.getProject('story-50s'))
  st().select(select)
  return api
}
const layer = (li = 0) => st().spec!.scenes[0].layers[li]
const settle = () => new Promise((r) => setTimeout(r, 1400)) // a drag's undo step closes a moment after the last change
// a slider thumb takes the keys once it has the focus, as it does for someone who clicked or tabbed to it
const key = (el: HTMLElement, k: string) => {
  act(() => el.focus())
  fireEvent.keyDown(el, { key: k })
}

beforeEach(() => st().unload())

describe('placing a character with sliders', () => {
  it('has a slider across and one down, beside the numbers, once the place is a point', async () => {
    const user = userEvent.setup()
    const api = await open({ kind: 'layer', scene: 0, layer: 0 })
    st().edit((d) => void (d.scenes[0].layers[0].position = 'center'))
    await renderScreen(<Inspector />, { api })
    expect(screen.queryByRole('slider', { name: 'Position x' })).not.toBeInTheDocument() // a named place has no numbers
    await user.click(await screen.findByRole('radio', { name: 'Exact point' }))
    expect(layer().position).toEqual([0.5, 0.74])

    const across = screen.getByRole('slider', { name: 'Position x' })
    const down = screen.getByRole('slider', { name: 'Position y' })
    key(across, 'ArrowRight')
    expect(layer().position).toEqual([0.51, 0.74])
    key(down, 'ArrowLeft')
    expect(layer().position).toEqual([0.51, 0.73])
    key(across, 'Home') // the slider covers a little past the edge of the frame, so someone can wait just out of shot
    expect(layer().position?.[0]).toBe(-0.4)
    key(across, 'End')
    expect(layer().position?.[0]).toBe(1.4)
    expect(screen.getByRole('textbox', { name: 'Position x' })).toHaveValue('1.4')
  })

  it('still takes an exact number, further out than the slider reaches, and stops a character at the picture’s bottom', async () => {
    const user = userEvent.setup()
    const api = await open({ kind: 'layer', scene: 0, layer: 0 })
    st().edit((d) => void (d.scenes[0].layers[0].position = [0.5, 0.74]))
    await renderScreen(<Inspector />, { api })
    const x = await screen.findByRole('textbox', { name: 'Position x' })
    await user.clear(x)
    await user.type(x, '1.8{Enter}')
    expect(layer().position?.[0]).toBe(1.8)
    const y = screen.getByRole('textbox', { name: 'Position y' })
    await user.clear(y)
    await user.type(y, '3{Enter}')
    expect(layer().position?.[1]).toBe(1) // a character's feet are on the picture
    await user.clear(x)
    await user.type(x, '0.25{Enter}')
    expect(layer().position?.[0]).toBe(0.25)
  })

  it('makes each keystroke an undo step, closed a moment after the last, and undo puts the character back', async () => {
    const api = await open({ kind: 'layer', scene: 0, layer: 0 })
    st().edit((d) => void (d.scenes[0].layers[0].position = [0.5, 0.74]))
    await renderScreen(<Inspector />, { api })
    const across = await screen.findByRole('slider', { name: 'Position x' })
    const before = st().past.length
    key(across, 'ArrowRight')
    key(across, 'ArrowRight')
    expect(layer().position).toEqual([0.52, 0.74])
    await settle()
    expect(st().past.length).toBe(before + 2)
    act(() => st().undo())
    expect(layer().position).toEqual([0.51, 0.74])
    act(() => st().undo())
    expect(layer().position).toEqual([0.5, 0.74])
  })
})

describe('timing a clip with one slider', () => {
  async function openAction() {
    window.history.replaceState({}, '', '/')
    const api = await createMockApi()
    st().load(await api.getProject('story-50s'))
    const li = st().spec!.scenes[0].layers.findIndex((l) => l.actions.length > 0)
    st().select({ kind: 'action', scene: 0, layer: li, action: 0 })
    return { api, li }
  }
  const clip = (li: number) => st().spec!.scenes[0].layers[li].actions[0]

  it('has a start thumb and an end thumb that move the clip, beside the two numbers', async () => {
    const { api, li } = await openAction()
    st().edit((d) => void ((d.scenes[0].layers[li].actions[0].t0 = 1), (d.scenes[0].layers[li].actions[0].t1 = 2)))
    await renderScreen(<Inspector />, { api })
    const start = await screen.findByRole('slider', { name: 'Start time slider' })
    const end = screen.getByRole('slider', { name: 'End time slider' })
    const { t0, t1 } = clip(li)
    expect([t0, t1]).toEqual([1, 2])
    key(end, 'ArrowRight')
    expect(clip(li).t1).toBeCloseTo(t1 + 0.05, 6)
    expect(clip(li).t0).toBe(t0)
    key(start, 'ArrowRight')
    expect(clip(li).t0).toBeCloseTo(t0 + 0.05, 6)
    expect(screen.getByRole('textbox', { name: 'Start time' })).toHaveValue(String(Number((t0 + 0.05).toFixed(2))))
    expect(screen.getByText(/^Length /)).toHaveTextContent(`Length ${(clip(li).t1 - clip(li).t0).toFixed(2)} s`)
  })

  it('never lets the start pass the end', async () => {
    const { api, li } = await openAction()
    st().edit((d) => void ((d.scenes[0].layers[li].actions[0].t0 = 1), (d.scenes[0].layers[li].actions[0].t1 = 1.1)))
    await renderScreen(<Inspector />, { api })
    const start = await screen.findByRole('slider', { name: 'Start time slider' })
    key(start, 'ArrowRight')
    key(start, 'ArrowRight')
    expect(clip(li).t0).toBeLessThanOrEqual(clip(li).t1 - 0.1 + 1e-9)
    expect(clip(li).t1).toBeCloseTo(1.1, 6)
  })
})

describe('the camera pan with sliders', () => {
  it('moves from and to across and down the frame', async () => {
    window.history.replaceState({}, '', '/')
    const api = await createMockApi()
    st().load(await api.getProject('story-50s'))
    st().edit((d) => void d.scenes[0].camera.moves.push({ type: 'pan', t0: 0, t1: 2, from: [0, 0], to: [0.2, 0], ease: 'linear' } as never))
    const mi = st().spec!.scenes[0].camera.moves.length - 1
    st().select({ kind: 'camera', scene: 0, move: mi })
    await renderScreen(<Inspector />, { api })
    const to = await screen.findByRole('slider', { name: 'To x' })
    key(to, 'ArrowRight')
    expect(st().spec!.scenes[0].camera.moves[mi].to).toEqual([0.21, 0])
    key(screen.getByRole('slider', { name: 'From y' }), 'ArrowRight')
    expect(st().spec!.scenes[0].camera.moves[mi].from).toEqual([0, 0.01])
  })
})
