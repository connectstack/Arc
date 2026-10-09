import { act, fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import type { SceneObject } from '@/api/types'
import { createMockApi } from '@/api/mock'
import { newObject } from '@/lib/spec'
import { useProject } from '@/store/project'
import { renderScreen } from '@/test/harness'
import { withObjects } from '../objects.fixture'
import { Inspector } from './Inspector'

async function openWithCustomAction() {
  window.history.replaceState({}, '', '/')
  const api = await createMockApi()
  useProject.getState().load(await api.getProject('story-50s'))
  const st = useProject.getState
  const li = st().spec!.scenes[0].layers.findIndex((l) => l.actions.length > 0)
  st().edit((d) => {
    d.scenes[0].layers[li].actions[0].name = 'levitate' // an action from a plugin that is not loaded
    d.scenes[0].layers[li].actions[0].params = { height: 2 }
  })
  st().select({ kind: 'action', scene: 0, layer: li, action: 0 })
  return { api, li }
}
const action = (li: number) => useProject.getState().spec!.scenes[0].layers[li].actions[0]

beforeEach(() => useProject.getState().unload())

describe('an action the catalog does not know', () => {
  it('is shown as a custom action with its parameters as raw JSON', async () => {
    const { api, li } = await openWithCustomAction()
    await renderScreen(<Inspector />, { api })
    expect(await screen.findByText(/“levitate” is not in this catalog/i)).toBeInTheDocument()
    const box = screen.getByRole('textbox', { name: /parameters as json/i })
    expect(JSON.parse((box as HTMLTextAreaElement).value)).toEqual({ height: 2 })

    fireEvent.change(box, { target: { value: '{"height": 5, "spin": true}' } })
    expect(action(li).params).toEqual({ height: 5, spin: true })
  })

  it('keeps the last valid parameters and says what is wrong while the JSON is not valid', async () => {
    const { api, li } = await openWithCustomAction()
    await renderScreen(<Inspector />, { api })
    const box = await screen.findByRole('textbox', { name: /parameters as json/i })
    fireEvent.change(box, { target: { value: '{"height": ' } })
    expect(await screen.findByRole('alert')).toHaveTextContent(/not valid json/i)
    expect(action(li).params).toEqual({ height: 2 })
    fireEvent.change(box, { target: { value: '[1, 2]' } })
    expect(screen.getByRole('alert')).toHaveTextContent(/must be a json object/i)
    expect(action(li).params).toEqual({ height: 2 })
  })

  it('can be replaced with idle in one click, which is undoable', async () => {
    const user = userEvent.setup()
    const { api, li } = await openWithCustomAction()
    await renderScreen(<Inspector />, { api })
    await user.click(await screen.findByRole('button', { name: /replace with idle/i }))
    expect(action(li)).toMatchObject({ name: 'idle', params: {} })
    expect(screen.queryByText(/not in this catalog/i)).not.toBeInTheDocument()
    act(() => useProject.getState().undo())
    expect(action(li).name).toBe('levitate')
  })
})

// ------------------------------------------------------------------------------- objects
async function openWithObject(extra: Partial<SceneObject> = {}) {
  window.history.replaceState({}, '', '/')
  const api = withObjects(await createMockApi())
  useProject.getState().load(await api.getProject('story-50s'))
  const st = useProject.getState
  st().edit((d) => void d.scenes[0].objects.push({ ...newObject('car', [0.2, 0.74], 0.675), ...extra })) // scene 0 is a street, 5.5 s
  st().select({ kind: 'object', scene: 0, object: 0 })
  return { api }
}
const car = () => useProject.getState().spec!.scenes[0].objects[0]
const history = () => useProject.getState().past.length

describe('the inspector of an object', () => {
  it('shows which drawing it is and how tall it stands, in persons', async () => {
    const { api } = await openWithObject()
    await renderScreen(<Inspector />, { api })
    expect(await screen.findByRole('heading', { name: 'Scene 1 › car' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /asset: car/i })).toHaveTextContent('car')
    expect(screen.getByText('A small red hatchback, side view')).toBeInTheDocument()
    expect(await screen.findByText(/height ≈ 0\.35× a person/i)).toBeInTheDocument() // 0.52 × scale 0.675
  })

  it('scales it with the number box, as one undo step, and the height in persons follows', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject()
    await renderScreen(<Inspector />, { api })
    const box = await screen.findByRole('textbox', { name: 'Scale value' })
    const before = history()
    await user.clear(box)
    await user.type(box, '1.2{Enter}')
    expect(car().scale).toBe(1.2)
    expect(await screen.findByText(/height ≈ 0\.62× a person/i)).toBeInTheDocument() // 0.52 × 1.2
    expect(history()).toBe(before + 1)
    act(() => useProject.getState().undo())
    expect(car().scale).toBe(0.675)
    // the slider's reset goes back to the natural size
    act(() => useProject.getState().redo())
    await user.click(await screen.findByRole('button', { name: 'Reset Scale' }))
    expect(car().scale).toBe(1)
  })

  it('turns it, fades it and mirrors it', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject()
    await renderScreen(<Inspector />, { api })
    const rotation = await screen.findByRole('textbox', { name: 'Rotation value' })
    await user.clear(rotation)
    await user.type(rotation, '-30{Enter}')
    expect(car().rotation).toBe(-30)
    const opacity = screen.getByRole('textbox', { name: 'Opacity value' })
    await user.clear(opacity)
    await user.type(opacity, '0.5{Enter}')
    expect(car().alpha).toBe(0.5)
    await user.click(screen.getByRole('radio', { name: 'Left' }))
    expect(car().facing).toBe('left')
  })

  it('sets the depth plane, and only lets the layer be chosen on the mid plane', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject()
    await renderScreen(<Inspector />, { api })
    const behind = await screen.findByRole('radio', { name: 'Behind' })
    expect(behind).toBeEnabled()
    await user.click(screen.getByRole('radio', { name: 'In front' }))
    expect(car().layer).toBe('front')
    await user.click(screen.getByRole('radio', { name: 'Back' }))
    expect(car().depth).toBe('background')
    expect(screen.getByRole('radio', { name: 'Behind' })).toBeDisabled()
    expect(screen.getByRole('radio', { name: 'In front' })).toBeDisabled()
  })

  it('moves between a named place and exact numbers without the object jumping', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject({ position: 'right' })
    await renderScreen(<Inspector />, { api })
    await user.click(await screen.findByRole('radio', { name: 'Exact point' }))
    expect(car().position).toEqual([0.73, 0.74]) // where "right" is on a street
    const x = screen.getByRole('textbox', { name: 'Position x' })
    await user.clear(x)
    await user.type(x, '0.4{Enter}')
    expect(car().position).toEqual([0.4, 0.74])
    await user.click(screen.getByRole('radio', { name: 'Named place' }))
    expect(car().position).toBe('curb') // the place nearest to where it stood: a curb at 0.42, 0.775 on a street
  })

  it('says when its place is not on this set, or it is never seen', async () => {
    const { api } = await openWithObject({ position: 'sofa', t0: 5.5 })
    await renderScreen(<Inspector />, { api })
    expect(await screen.findByText(/“sofa” is not a place on the street set/i)).toBeInTheDocument()
    expect(screen.getByText(/it appears at 5\.5 s, at or after the end of the scene \(5\.5 s\)/i)).toBeInTheDocument()
  })

  it('takes another drawing from the searchable list and keeps its place, size and times', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject({ t0: 1, palette: { body: '#2a6fdb', leaves: '#00ff00' } })
    await renderScreen(<Inspector />, { api })
    await user.click(await screen.findByRole('button', { name: /asset: car/i }))
    await user.type(await screen.findByRole('textbox', { name: 'Find an object' }), 'पेड़')
    await user.click(screen.getByRole('button', { name: /tree/i }))
    expect(car()).toMatchObject({ asset: 'tree', position: [0.2, 0.74], scale: 0.675, t0: 1, palette: { leaves: '#00ff00' } })
    expect(car().palette.body).toBeUndefined() // a tree has no body to colour
    expect(await screen.findByRole('heading', { name: 'Scene 1 › tree' })).toBeInTheDocument()
  })

  it('says when it is on screen: from and until a time, or until the end of the scene', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject()
    await renderScreen(<Inspector />, { api })
    expect(await screen.findByText('the end of the scene')).toBeInTheDocument()
    const from = screen.getByRole('textbox', { name: 'Visible from time' })
    await user.clear(from)
    await user.type(from, '1.5{Enter}')
    expect(car()).toMatchObject({ t0: 1.5, t1: null })
    await user.click(screen.getByRole('switch', { name: 'Until the end of the scene' }))
    expect(car().t1).toBe(5.5)
    const until = await screen.findByRole('textbox', { name: 'Visible until time' })
    await user.clear(until)
    await user.type(until, '4{Enter}')
    expect(car()).toMatchObject({ t0: 1.5, t1: 4 })
    await user.click(screen.getByRole('switch', { name: 'Until the end of the scene' }))
    expect(car().t1).toBeNull()
  })
})

describe('the colours of an object', () => {
  it('has a field for each part the drawing lets you recolour, starting from the drawing’s own colour', async () => {
    const { api } = await openWithObject()
    await renderScreen(<Inspector />, { api })
    expect(await screen.findByRole('textbox', { name: 'Body hex' })).toHaveValue('#d9342b')
    expect(screen.getByRole('textbox', { name: 'Body Dark hex' })).toHaveValue('#a82620')
    expect(screen.getByRole('textbox', { name: 'Window hex' })).toHaveValue('#bfe3f2')
  })

  it('recolours a part, shows the shade that follows it, and resets', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject()
    await renderScreen(<Inspector />, { api })
    const body = await screen.findByRole('textbox', { name: 'Body hex' })
    await user.clear(body)
    await user.type(body, '#2A6FDB{Enter}')
    expect(car().palette).toEqual({ body: '#2a6fdb' })
    // the dark shade follows the new body colour, as the render will draw it
    expect(screen.getByText('follows Body')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Body Dark hex' })).not.toHaveValue('#a82620')
    expect(car().palette.body_dark).toBeUndefined()

    await user.click(screen.getByRole('button', { name: /reset body to the drawing’s own colour/i }))
    expect(car().palette).toEqual({})
    expect(screen.queryByText('follows Body')).not.toBeInTheDocument()
  })

  it('resets every colour at once', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject({ palette: { body: '#2a6fdb', window: '#000000' } })
    await renderScreen(<Inspector />, { api })
    await user.click(await screen.findByRole('button', { name: 'Reset all colours' }))
    expect(car().palette).toEqual({})
    expect(screen.queryByRole('button', { name: 'Reset all colours' })).not.toBeInTheDocument()
  })

  it('says so for a drawing with nothing to recolour', async () => {
    const { api } = await openWithObject({ asset: 'billboard' })
    await renderScreen(<Inspector />, { api })
    expect(await screen.findByText(/nothing in this drawing can be recoloured/i)).toBeInTheDocument()
  })
})

describe('the motions of an object', () => {
  const addMotionOfType = async (user: ReturnType<typeof userEvent.setup>, name: RegExp) => {
    await user.click(await screen.findByRole('button', { name: 'Add a motion' }))
    await user.click(await screen.findByRole('menuitem', { name }))
  }

  it('says what each kind does when you add one', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject()
    await renderScreen(<Inspector />, { api })
    await user.click(await screen.findByRole('button', { name: 'Add a motion' }))
    const items = await screen.findAllByRole('menuitem')
    expect(items.map((i) => i.textContent?.split(/(?<=[a-z])(?=[A-Z])/)[0])).toEqual(['Move', 'Hop', 'Float', 'Spin', 'Pulse', 'Fade', 'Grow', 'Shake'].map((n) => expect.stringContaining(n)))
    expect(screen.getByRole('menuitem', { name: /spin.*turns around/i })).toBeInTheDocument()
  })

  it('adds a motion of a kind at the playhead and opens it for editing', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject()
    await renderScreen(<Inspector />, { api })
    expect(await screen.findByText(/it stays as it is/i)).toBeInTheDocument()
    await addMotionOfType(user, /^hop/i)
    expect(car().motions).toEqual([{ type: 'hop', t0: 0, t1: 1, ease: 'ease_in_out' }])
    // only the settings a hop has: how many times, how high
    expect(await screen.findByRole('textbox', { name: 'Hops' })).toHaveValue('1')
    expect(screen.getByRole('textbox', { name: 'Height value' })).toHaveValue('0.35')
    expect(screen.queryByRole('textbox', { name: /destination/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Swell value' })).not.toBeInTheDocument()
    expect(screen.getByText(/jumps up and down/i)).toBeInTheDocument()
  })

  it('writes only what differs from the engine’s defaults', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject()
    await renderScreen(<Inspector />, { api })
    await addMotionOfType(user, /^hop/i)
    const hops = await screen.findByRole('textbox', { name: 'Hops' })
    await user.clear(hops)
    await user.type(hops, '3{Enter}')
    expect(car().motions[0]).toMatchObject({ count: 3 })
    const height = screen.getByRole('textbox', { name: 'Height value' })
    await user.clear(height)
    await user.type(height, '0.8{Enter}')
    expect(car().motions[0]).toMatchObject({ count: 3, amount: 0.8 })
    await user.click(screen.getByRole('button', { name: 'Reset Height' })) // back to the default: the field is dropped
    expect(car().motions[0]).toEqual({ type: 'hop', t0: 0, t1: 1, ease: 'ease_in_out', count: 3 })
    await user.clear(hops)
    await user.type(hops, '1{Enter}')
    expect(car().motions[0]).toEqual({ type: 'hop', t0: 0, t1: 1, ease: 'ease_in_out' })
  })

  it('asks a move for its destination, as a place or a point', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject()
    await renderScreen(<Inspector />, { api })
    await addMotionOfType(user, /^move/i)
    expect(car().motions[0]).toMatchObject({ type: 'move', to: [0.5, 0.74] })
    const x = await screen.findByRole('textbox', { name: 'Destination x' })
    await user.clear(x)
    await user.type(x, '0.9{Enter}')
    expect(car().motions[0].to).toEqual([0.9, 0.74])
    await user.click(within(screen.getByRole('radiogroup', { name: 'Destination mode' })).getByRole('radio', { name: 'Named place' }))
    expect(car().motions[0].to).toBe('far_right') // the place nearest to 0.9, 0.74
  })

  it('times a motion inside the scene, and says when it is not', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject({ motions: [{ type: 'spin', t0: 4, t1: 7, ease: 'linear' }] })
    await renderScreen(<Inspector />, { api })
    await user.click(await screen.findByRole('button', { name: /^spin/i }))
    expect(await screen.findByText(/it ends at 7 s, after the scene \(5\.5 s\): the rest is cut off/i)).toBeInTheDocument()
    const end = screen.getByRole('textbox', { name: 'Motion ends time' })
    await user.clear(end)
    await user.type(end, '5{Enter}')
    expect(car().motions[0]).toMatchObject({ t0: 4, t1: 5 })
    expect(screen.queryByText(/the rest is cut off/i)).not.toBeInTheDocument()
    // it cannot end before it starts
    await user.clear(end)
    await user.type(end, '1{Enter}')
    expect(car().motions[0].t1).toBeCloseTo(4.1, 6)
  })

  it('keeps the motions in time order when one is moved past another', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject({ motions: [{ type: 'hop', t0: 1, t1: 2, ease: 'ease_in_out' }, { type: 'spin', t0: 3, t1: 4, ease: 'ease_in_out' }] })
    await renderScreen(<Inspector />, { api })
    await user.click(await screen.findByRole('button', { name: /^hop/i }))
    const end = await screen.findByRole('textbox', { name: 'Motion ends time' })
    await user.clear(end)
    await user.type(end, '5{Enter}')
    const start = screen.getByRole('textbox', { name: 'Motion starts time' })
    await user.clear(start)
    await user.type(start, '3.5{Enter}')
    expect(car().motions.map((m) => m.type)).toEqual(['spin', 'hop'])
    // ... and the one being edited is still the open one
    expect(await screen.findByRole('textbox', { name: 'Motion starts time' })).toHaveValue('3.5')
  })

  it('removes a motion, which is one undo step', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject({ motions: [{ type: 'hop', t0: 1, t1: 2, ease: 'ease_in_out' }, { type: 'float', t0: 2, t1: 4, ease: 'ease_in_out' }] })
    await renderScreen(<Inspector />, { api })
    await user.click(await screen.findByRole('button', { name: 'Remove Hop' }))
    expect(car().motions.map((m) => m.type)).toEqual(['float'])
    act(() => useProject.getState().undo())
    expect(car().motions.map((m) => m.type)).toEqual(['hop', 'float'])
  })

  it('opens one motion at a time, and a motion of a kind Reel does not know shows what is wrong', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject({ motions: [{ type: 'hop', t0: 1, t1: 2, ease: 'ease_in_out' }, { type: 'levitate', t0: 2, t1: 4, ease: 'ease_in_out' }] })
    await renderScreen(<Inspector />, { api })
    await user.click(await screen.findByRole('button', { name: /^levitate/i }))
    expect(await screen.findByText(/“levitate” is not a motion reel knows/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^hop/i }))
    expect(screen.queryByText(/“levitate” is not a motion reel knows/i)).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Hops' })).toBeInTheDocument()
  })
})

describe('the inspector of a motion', () => {
  it('edits the one motion, and leads back to its object', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject({ motions: [{ type: 'pulse', t0: 1, t1: 3, ease: 'ease_in_out' }] })
    useProject.getState().select({ kind: 'motion', scene: 0, object: 0, motion: 0 })
    await renderScreen(<Inspector />, { api })
    expect(await screen.findByRole('heading', { name: 'Scene 1 › car › pulse' })).toBeInTheDocument()
    const swell = await screen.findByRole('textbox', { name: 'Swell value' })
    await user.clear(swell)
    await user.type(swell, '0.3{Enter}')
    expect(car().motions[0]).toMatchObject({ type: 'pulse', amount: 0.3 })
    expect(screen.getByRole('textbox', { name: 'Pulses' })).toHaveValue('3')
    await user.click(screen.getByRole('button', { name: /edit the car/i }))
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 0, object: 0 })
  })

  it('offers duplicate and delete in the header, and delete leaves the object selected', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject({ motions: [{ type: 'pulse', t0: 1, t1: 2, ease: 'ease_in_out' }] })
    useProject.getState().select({ kind: 'motion', scene: 0, object: 0, motion: 0 })
    await renderScreen(<Inspector />, { api })
    await user.click(await screen.findByRole('button', { name: /duplicate/i }))
    expect(car().motions).toHaveLength(2)
    expect(useProject.getState().selection).toEqual({ kind: 'motion', scene: 0, object: 0, motion: 1 })
    await user.click(screen.getByRole('button', { name: /delete/i }))
    expect(car().motions).toHaveLength(1)
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 0, object: 0 })
  })
})

describe('the inspector of a scene', () => {
  it('lists its objects and adds one from the library', async () => {
    const user = userEvent.setup()
    const { api } = await openWithObject({ motions: [{ type: 'hop', t0: 1, t1: 2, ease: 'ease_in_out' }] })
    useProject.getState().select({ kind: 'scene', scene: 0 })
    await renderScreen(<Inspector />, { api })
    const list = await screen.findByRole('button', { name: 'Add an object to this scene' })
    expect(screen.getByRole('button', { name: /^car 1 motion$/i })).toBeInTheDocument()
    await user.click(list)
    await user.click(await screen.findByRole('button', { name: /cake/i }))
    expect(useProject.getState().spec!.scenes[0].objects.map((o) => o.asset)).toEqual(['car', 'cake'])
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 0, object: 1 })
  })
})

