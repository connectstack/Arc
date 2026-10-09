import type { QueryClient } from '@tanstack/react-query'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMockApi } from '@/api/mock'
import type { SceneObject } from '@/api/types'
import { newObject } from '@/lib/spec'
import { useProject } from '@/store/project'
import { useUi } from '@/store/ui'
import { renderScreen } from '@/test/harness'
import { Stage, StageOverlay } from './Preview'
import { withObjects } from './objects.fixture'

// the stage is 400 x 711 px at the origin of the page (jsdom has no layout: it is told)
const W = 400
const H = 711
let rect: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  rect = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, right: W, bottom: H, width: W, height: H, x: 0, y: 0, toJSON: () => ({}) } as DOMRect)
  useProject.getState().unload()
  useUi.getState().set({ overlays: { safe: false, grid: false, handles: true } })
})
afterEach(() => rect.mockRestore())

async function open(objects: Partial<SceneObject>[], playhead = 1.5) {
  window.history.replaceState({}, '', '/')
  const api = withObjects(await createMockApi())
  useProject.getState().load(await api.getProject('story-50s'))
  // scene 0 is a street, 5.5 s long, where Mia stands at 0.34, 0.7
  useProject.getState().edit((d) => void d.scenes[0].objects.push(...objects.map((o) => ({ ...newObject('car', [0.5, 0.74], 0.675), ...o }))))
  useProject.getState().setPlayhead(playhead)
  return api
}
/** The catalog (the sets' places, the objects' sizes) is what the handles are measured with: wait until the screen has it. */
async function ready(queries: QueryClient) {
  await waitFor(() => expect(queries.getQueryState(['catalog'])?.status).toBe('success'))
  await act(async () => undefined)
}
const object = (i: number) => useProject.getState().spec!.scenes[0].objects[i]
const handle = (kind: string, i = 0) => document.querySelectorAll<SVGElement>(`[data-object="${i}"] [data-handle="${kind}"], [data-handle="${kind}"]`)[0]
const press = (el: Element, x: number, y: number) => fireEvent.pointerDown(el, { clientX: x, clientY: y, pointerId: 1 })
const move = (x: number, y: number) => fireEvent.pointerMove(window, { clientX: x, clientY: y, pointerId: 1 })
const release = () => fireEvent.pointerUp(window, { pointerId: 1 })

describe('the objects on the stage', () => {
  it('draws a dashed box for each object around the point it stands on, sized as the engine will size it, and names it', async () => {
    const api = await open([{}, { asset: 'tree', position: 'left' }])
    await ready((await renderScreen(<StageOverlay box={{ w: W, h: H }} />, { api })).queries)
    await screen.findByText('car')
    const car = document.querySelector('[data-object="0"] rect[data-handle="object-box"]') as SVGRectElement
    // 300 design px × 1.12 × scale 0.675 × perspective (1 + (0.74 - 0.8)) = 213 px of the 1920 the frame is tall; the car is twice as wide
    const tall = (300 * 1.12 * 0.675 * 0.94) / 1920
    expect(parseFloat(car.getAttribute('height')!)).toBeCloseTo(tall * H, 1)
    expect(parseFloat(car.getAttribute('width')!)).toBeCloseTo(((tall * 1920 * 2) / 1080) * W, 1)
    expect(parseFloat(car.getAttribute('x')!) + parseFloat(car.getAttribute('width')!) / 2).toBeCloseTo(0.5 * W, 1) // centred on where it stands
    expect(parseFloat(car.getAttribute('y')!) + parseFloat(car.getAttribute('height')!)).toBeCloseTo(0.74 * H, 1) // standing on it
    expect(car).toHaveAttribute('stroke-dasharray')
    expect(screen.getByText('tree · left')).toBeInTheDocument()
    expect(document.querySelectorAll('[data-object]')).toHaveLength(2)
  })

  it('turns the outline with the object, and dims one that is not on screen at the playhead', async () => {
    const api = await open([{ rotation: 30 }, { asset: 'tree', t0: 3 }], 1.5)
    await ready((await renderScreen(<StageOverlay box={{ w: W, h: H }} />, { api })).queries)
    expect(document.querySelector('[data-object="0"] g[transform^="rotate(30"]')).not.toBeNull()
    expect(await screen.findByText('tree · not on screen now')).toBeInTheDocument()
    const hidden = document.querySelector('[data-object="1"] rect[data-handle="object-box"]') as SVGRectElement
    expect(hidden.getAttribute('pointer-events')).toBe('none') // only its marker can be grabbed
    expect(document.querySelector('[data-object="1"] [data-handle="object-marker"]')).not.toBeNull()
  })

  it('selects an object when it is pressed, by its box or its marker, and shows its corner handle', async () => {
    const api = await open([{}, { asset: 'tree', position: [0.2, 0.74] }])
    await ready((await renderScreen(<StageOverlay box={{ w: W, h: H }} />, { api })).queries)
    await screen.findByText('car')
    expect(document.querySelector('[data-handle="object-scale"]')).toBeNull()
    press(handle('object-box'), 200, 500)
    release()
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 0, object: 0 })
    expect(document.querySelectorAll('[data-handle="object-scale"]')).toHaveLength(1)
    press(document.querySelector('[data-object="1"] [data-handle="object-marker"]')!, 80, 526)
    release()
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 0, object: 1 })
    // a selected move leaves it selected as well
    act(() => useProject.getState().select({ kind: 'motion', scene: 0, object: 1, motion: 0 }))
    expect(document.querySelector('[data-object="1"] [data-handle="object-scale"]')).not.toBeNull()
  })

  it('moves it with the pointer as fractions of the frame, keeping the point it was grabbed by, in one undo step', async () => {
    const api = await open([{}])
    await ready((await renderScreen(<StageOverlay box={{ w: W, h: H }} />, { api })).queries)
    await screen.findByText('car')
    const before = useProject.getState().past.length
    const at = [0.5 * W, 0.74 * H]
    press(handle('object-box'), at[0] + 30, at[1] - 20) // grabbed 30 px right of the car's anchor and 20 px above
    move(at[0] + 30 + 50, at[1] - 20 - 100)
    expect(object(0).position).toEqual([0.625, 0.599]) // moved by the pointer's 50 px and 100 px, not jumped to the pointer
    move(at[0] + 30 + 60, at[1] - 20 - 100)
    release()
    expect(object(0).position).toEqual([0.65, 0.599])
    expect(useProject.getState().past.length).toBe(before + 1)
    act(() => useProject.getState().undo())
    expect(object(0).position).toEqual([0.5, 0.74])
  })

  it('snaps to a place of the set when it is let go near one, and a place turns into numbers once it is dragged away', async () => {
    const api = await open([{ asset: 'tree', position: 'left' }]) // "left" is at 0.27, 0.74 on a street
    await ready((await renderScreen(<StageOverlay box={{ w: W, h: H }} />, { api })).queries)
    await screen.findByText('tree · left')
    press(handle('object-marker'), 0.27 * W, 0.74 * H)
    move(0.27 * W + 2, 0.74 * H + 2) // a couple of pixels: still "left"
    expect(object(0).position).toBe('left')
    move(200, 300)
    expect(object(0).position).toEqual([0.5, 0.422])
    expect(await screen.findByText('left')).toBeInTheDocument() // while dragging, the places of the set are shown
    move(0.73 * W - 5, 0.74 * H + 4) // near "right"
    expect(object(0).position).toBe('right')
    release()
    expect(screen.queryByText('left')).not.toBeInTheDocument()
  })

  it('keeps a dragged object near the frame', async () => {
    const api = await open([{}])
    await ready((await renderScreen(<StageOverlay box={{ w: W, h: H }} />, { api })).queries)
    await screen.findByText('car')
    press(handle('object-box'), 0.5 * W, 0.74 * H)
    move(9 * W, -9 * H)
    release()
    expect(object(0).position).toEqual([1.1, 0])
  })

  it('is scaled by its corner handle about the point it stands on, as one undo step', async () => {
    const api = await open([{}])
    act(() => useProject.getState().select({ kind: 'object', scene: 0, object: 0 }))
    await ready((await renderScreen(<StageOverlay box={{ w: W, h: H }} />, { api })).queries)
    await screen.findByText('car')
    const [ax, ay] = [0.5 * W, 0.74 * H]
    const before = useProject.getState().past.length
    press(handle('object-scale'), ax + 100, ay - 100)
    move(ax + 200, ay - 200) // twice as far from where it stands
    expect(object(0).scale).toBe(1.35)
    move(ax + 50, ay - 50) // half as far
    release()
    expect(object(0).scale).toBe(0.34) // 0.675 × 0.5, to two places
    expect(useProject.getState().past.length).toBe(before + 1)
    expect(object(0).position).toEqual([0.5, 0.74]) // it did not move
    press(handle('object-scale'), ax + 100, ay - 100)
    move(ax + 90000, ay - 90000)
    release()
    expect(object(0).scale).toBe(6) // never more than the engine takes
  })

  it('shows where a move goes while the object or the move is selected, and its tip can be dragged', async () => {
    const api = await open([{ motions: [{ type: 'move', t0: 2, t1: 4, to: 'right', ease: 'ease_in_out' }] }], 0.2)
    await ready((await renderScreen(<StageOverlay box={{ w: W, h: H }} />, { api })).queries)
    await screen.findByText('car')
    expect(document.querySelectorAll('polygon')).toHaveLength(0) // not under way, and nothing selected
    act(() => useProject.getState().select({ kind: 'object', scene: 0, object: 0 }))
    expect(document.querySelectorAll('polygon')).toHaveLength(1)
    expect(handle('move-destination')).toBeUndefined() // the tip is a handle only when that move is the selection
    act(() => useProject.getState().select({ kind: 'motion', scene: 0, object: 0, motion: 0 }))
    const tip = handle('move-destination')
    expect(tip).toBeTruthy()
    press(tip, 0.73 * W, 0.74 * H)
    move(0.4 * W, 0.5 * H)
    release()
    expect(object(0).motions[0].to).toEqual([0.4, 0.5])
    press(handle('move-destination'), 0.4 * W, 0.5 * H)
    move(0.27 * W, 0.74 * H + 3) // onto a place of the set
    release()
    expect(object(0).motions[0].to).toBe('left')
    expect(useProject.getState().selection).toEqual({ kind: 'motion', scene: 0, object: 0, motion: 0 })
  })

  it('shows a move that is under way even when nothing is selected', async () => {
    const api = await open([{ motions: [{ type: 'move', t0: 1, t1: 3, to: 'right', ease: 'ease_in_out' }] }], 2)
    await ready((await renderScreen(<StageOverlay box={{ w: W, h: H }} />, { api })).queries)
    await screen.findByText('car')
    const arrows = [...document.querySelectorAll('polygon')].filter((p) => p.getAttribute('fill') === '#d9342b') // in the car's colour (a character may be walking too)
    expect(arrows).toHaveLength(1)
  })

  it('draws no handles when they are switched off', async () => {
    useUi.getState().set({ overlays: { safe: false, grid: false, handles: false } })
    const api = await open([{}])
    await ready((await renderScreen(<StageOverlay box={{ w: W, h: H }} />, { api })).queries)
    expect(document.querySelector('[data-object]')).toBeNull()
  })

  it('is drawn in front of the box of the neighbour when it is selected', async () => {
    const api = await open([{}, { asset: 'tree' }])
    act(() => useProject.getState().select({ kind: 'object', scene: 0, object: 0 }))
    await ready((await renderScreen(<StageOverlay box={{ w: W, h: H }} />, { api })).queries)
    await screen.findByText('car')
    const order = [...document.querySelectorAll('[data-object]')].map((g) => g.getAttribute('data-object'))
    expect(order).toEqual(['1', '0'])
  })
})

// ------------------------------------------------------------------------------- dropping a library object on the stage
describe('dropping a library object on the stage', () => {
  beforeEach(() => {
    class Resized {
      constructor(private cb: ResizeObserverCallback) {}
      observe() {
        this.cb([], this as unknown as ResizeObserver)
      }
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('ResizeObserver', Resized)
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(W + 32)
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(H + 24)
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  const session = { info: null, error: null, busy: false }
  const dispatch = (type: string, target: Element, payload: unknown, at = [0, 0]) => {
    const e = new Event(type, { bubbles: true, cancelable: true })
    Object.assign(e, { clientX: at[0], clientY: at[1], relatedTarget: null, dataTransfer: { getData: () => (typeof payload === 'string' ? payload : JSON.stringify(payload)), types: payload === null ? [] : ['application/x-reel-item'], dropEffect: 'none' } })
    act(() => void target.dispatchEvent(e))
    return e
  }

  it('puts the object where it was dropped, in the scene on show, and selects it', async () => {
    const api = await open([], 2.5)
    rect.mockReturnValue({ left: 100, top: 50, right: 100 + W, bottom: 50 + H, width: W, height: H, x: 100, y: 50, toJSON: () => ({}) } as DOMRect)
    await ready((await renderScreen(<Stage session={session} />, { api })).queries)
    const stage = await screen.findByTestId('stage')
    dispatch('drop', stage, { kind: 'object', name: 'cake' }, [100 + 0.25 * W, 50 + 0.8 * H])
    expect(object(0)).toMatchObject({ asset: 'cake', position: [0.25, 0.8], t0: 0, t1: null })
    expect(object(0).scale).toBe(1)
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 0, object: 0 })
  })

  it('does nothing for what is not a library object, or not ours', async () => {
    const api = await open([], 2.5)
    await ready((await renderScreen(<Stage session={session} />, { api })).queries)
    const stage = await screen.findByTestId('stage')
    dispatch('drop', stage, { kind: 'action', name: 'wave' }, [100, 100])
    dispatch('drop', stage, 'not json at all', [100, 100])
    dispatch('drop', stage, { kind: 'object' }, [100, 100])
    dispatch('drop', stage, '', [100, 100])
    expect(useProject.getState().spec!.scenes.flatMap((s) => s.objects)).toHaveLength(0)
  })

  it('says it will take the drop while an item is over the stage, and only then', async () => {
    const api = await open([], 2.5)
    await ready((await renderScreen(<Stage session={session} />, { api })).queries)
    const stage = await screen.findByTestId('stage')
    expect(dispatch('dragover', stage, null).defaultPrevented).toBe(false) // something else being dragged: not ours
    expect(screen.queryByText('Drop to put it here')).not.toBeInTheDocument()
    expect(dispatch('dragover', stage, { kind: 'object', name: 'cake' }).defaultPrevented).toBe(true)
    expect(await screen.findByText('Drop to put it here')).toBeInTheDocument()
    dispatch('dragleave', stage, { kind: 'object', name: 'cake' })
    expect(screen.queryByText('Drop to put it here')).not.toBeInTheDocument()
  })
})
