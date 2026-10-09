import { act, fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMockApi } from '@/api/mock'
import { newObject } from '@/lib/spec'
import { sceneSlots } from '@/lib/timeline'
import { useClipboard } from '@/store/clipboard'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { renderScreen } from '@/test/harness'
import { Timeline } from './Timeline'
import { copySelection, deleteSelected, duplicateSelected, pasteAtPlayhead } from './clipboardActions'
import { withObjects } from './objects.fixture'

async function open() {
  window.history.replaceState({}, '', '/')
  const api = await createMockApi()
  useProject.getState().load(await api.getProject('story-50s'))
  return api
}

const clips = () => screen.getAllByRole('button', { name: /seconds$/ })
const clipsOf = (lane: string) => within(screen.getByText(lane, { selector: 'button' }).closest('div[style]')!.parentElement as HTMLElement)

beforeEach(() => {
  useProject.getState().unload()
  useClipboard.getState().set(null)
})

describe('selecting several clips on the timeline', () => {
  it('adds a clip with Shift+click, takes it out again, and a plain click starts over', async () => {
    const api = await open()
    await renderScreen(<Timeline />, { api })
    const [a, b, c] = clips().filter((el) => el.dataset.clip?.startsWith('a-'))

    fireEvent.pointerDown(a)
    fireEvent.pointerUp(a)
    expect(a).toHaveAttribute('aria-pressed', 'true')

    fireEvent.pointerDown(b, { shiftKey: true })
    expect(b).toHaveAttribute('aria-pressed', 'true')
    expect(a).toHaveAttribute('aria-pressed', 'true')
    expect(useProject.getState().extra).toHaveLength(1)

    fireEvent.pointerDown(c, { shiftKey: true })
    expect(useProject.getState().extra).toHaveLength(2)
    fireEvent.pointerDown(b, { shiftKey: true }) // Shift again takes it out
    expect(b).toHaveAttribute('aria-pressed', 'false')
    expect(useProject.getState().extra).toHaveLength(1)

    fireEvent.pointerDown(c)
    fireEvent.pointerUp(c)
    expect(useProject.getState().extra).toHaveLength(0) // a click on a lone clip makes it the only one
    expect(a).toHaveAttribute('aria-pressed', 'false')
  })

  it('deletes and duplicates the whole selection, and one undo takes it back', async () => {
    const api = await open()
    await renderScreen(<Timeline />, { api })
    const st = useProject.getState
    const count = () => st().spec!.scenes.reduce((n, sc) => n + sc.layers.reduce((m, l) => m + l.actions.length, 0), 0)
    const before = count()
    const [a, b] = clips().filter((el) => el.dataset.clip?.startsWith('a-'))
    fireEvent.pointerDown(a)
    fireEvent.pointerUp(a)
    fireEvent.pointerDown(b, { shiftKey: true })
    const picked = 1 + st().extra.length
    expect(picked).toBe(2)

    act(() => duplicateSelected())
    expect(count()).toBe(before + 2)
    expect(1 + st().extra.length).toBe(2) // the copies are what is selected now

    act(() => deleteSelected())
    expect(count()).toBe(before)
    act(() => st().undo())
    expect(count()).toBe(before + 2)
  })

  it('moves the focused clip and the rest of the selection with the arrow keys', async () => {
    const api = await open()
    await renderScreen(<Timeline />, { api })
    const st = useProject.getState
    const [a, b] = clips().filter((el) => el.dataset.clip?.startsWith('a-'))
    const read = (key: string) => {
      const [, si, li, ai] = key.split('-').map(Number)
      return st().spec!.scenes[si].layers[li].actions[ai].t0
    }
    const [ta, tb] = [read(a.dataset.clip!), read(b.dataset.clip!)]
    fireEvent.pointerDown(a)
    fireEvent.pointerUp(a)
    fireEvent.pointerDown(b, { shiftKey: true })
    fireEvent.keyDown(a, { key: 'ArrowRight' })
    expect(read(a.dataset.clip!)).toBeCloseTo(ta + 0.1, 2)
    expect(read(b.dataset.clip!)).toBeCloseTo(tb + 0.1, 2)
  })
})

describe('sound effects on the timeline', () => {
  it('plays a sound when its marker is clicked, but not when it is dragged', async () => {
    const api = await open()
    const url = vi.spyOn(api, 'sfxUrl')
    await renderScreen(<Timeline />, { api })
    const marker = clips().find((el) => el.dataset.clip?.startsWith('s-'))!
    const name = useProject.getState().spec!.scenes[0].sfx[0].name

    fireEvent.pointerDown(marker, { clientX: 100 })
    fireEvent.pointerUp(marker, { clientX: 100 })
    expect(url).toHaveBeenCalledWith(name)

    url.mockClear()
    fireEvent.pointerDown(marker, { clientX: 100 })
    fireEvent.pointerMove(marker, { clientX: 160 }) // dragged: it moves, it does not play
    fireEvent.pointerUp(marker, { clientX: 160 })
    expect(url).not.toHaveBeenCalled()
  })
})

describe('copy and paste between characters', () => {
  it('copies a clip from one lane and pastes it onto another character at the playhead', async () => {
    const api = await open()
    await renderScreen(<Timeline />, { api })
    const st = useProject.getState
    const spec = st().spec!
    const si = 0
    const li = spec.scenes[si].layers.findIndex((l) => l.actions.length > 0)
    const from = spec.scenes[si].layers[li].character
    const other = spec.characters.find((c) => c.id !== from)!.id
    const action = spec.scenes[si].layers[li].actions[0]

    act(() => st().select({ kind: 'action', scene: si, layer: li, action: 0 }))
    act(() => void copySelection())
    expect(useClipboard.getState().board?.items).toHaveLength(1)

    act(() => {
      st().setPlayhead(0.2)
      st().select({ kind: 'character', id: other }) // the lane name was clicked
    })
    act(() => void pasteAtPlayhead())

    const after = st().spec!.scenes[si].layers.find((l) => l.character === other)!
    expect(after.actions.some((a) => a.name === action.name && Math.abs(a.t0 - 0.2) < 0.01)).toBe(true)
    expect(st().selection.kind).toBe('action') // the pasted clip is what is selected
    // and the original stays where it was
    expect(st().spec!.scenes[si].layers[li].actions[0]).toEqual(action)
  })

  it('offers Copy, Cut and Paste in the right-click menu, and pastes into the lane that was clicked', async () => {
    const user = userEvent.setup()
    const api = await open()
    await renderScreen(<Timeline />, { api })
    const [a] = clips().filter((el) => el.dataset.clip?.startsWith('a-'))
    await user.pointer({ keys: '[MouseRight]', target: a })
    expect(await screen.findByRole('menuitem', { name: /^copy/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /^cut/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /^paste/i })).toHaveAttribute('data-disabled') // nothing copied yet
    await user.click(screen.getByRole('menuitem', { name: /^copy/i }))
    expect(useClipboard.getState().board).not.toBeNull()
  })
})

describe('the audio lane', () => {
  it('has nothing to show before the voice is made', async () => {
    const api = await open()
    await renderScreen(<Timeline />, { api })
    expect(screen.getByText(/generate the voice in voice & audio to see waveforms/i)).toBeInTheDocument()
    expect(clipsOf).toBeTypeOf('function')
  })
})

// ------------------------------------------------------------------------------- the Objects lane group
async function openWithObjects() {
  window.history.replaceState({}, '', '/')
  const api = withObjects(await createMockApi())
  useProject.getState().load(await api.getProject('story-50s'))
  useProject.getState().edit((d) => {
    d.scenes[0].objects.push({ ...newObject('car', [0.2, 0.74], 0.675), t0: 1, motions: [{ type: 'hop', t0: 1, t1: 2, ease: 'ease_in_out' }, { type: 'move', t0: 3, t1: 5, to: 'right', ease: 'ease_in_out' }] })
    d.scenes[0].objects.push({ ...newObject('tree', 'left'), t1: 4 })
    d.scenes[2].objects.push(newObject('cake', [0.5, 0.74]))
  })
  useStudio.getState().set({ collapsed: {}, zoom: 22 })
  return api
}
const clip = (key: string) => document.querySelector<HTMLElement>(`[data-clip="${key}"]`) as HTMLElement
const objectsOf = (scene: number) => useProject.getState().spec!.scenes[scene].objects

describe('the Objects lanes', () => {
  it('has a group under the characters, with one lane per object and the scene it is in', async () => {
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    const group = screen.getByRole('button', { name: 'Objects', expanded: true })
    expect(group.parentElement).toHaveTextContent('3') // how many
    for (const [name, scene] of [['car', 1], ['tree', 1], ['cake', 3]] as const) expect(screen.getByTitle(`${name}, scene ${scene}`)).toBeInTheDocument()
    // after the characters' lanes and before the captions
    const order = [screen.getByTitle('Bolt'), group, screen.getByTitle('car, scene 1'), screen.getByTitle('cake, scene 3'), screen.getByTitle('Captions')]
    for (let i = 1; i < order.length; i++) expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING, `${i}`).toBeTruthy()
  })

  it('draws when an object is on screen as a bar, and each motion as a clip in the same lane', async () => {
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    expect(clip('ob-0-0')).toHaveAccessibleName('car, 1.00 to 5.50 seconds') // stays to the end of its 5.5 s scene
    expect(clip('ob-0-0')).toHaveAttribute('title', expect.stringMatching(/stays until the end of the scene/))
    expect(clip('ob-0-1')).toHaveAccessibleName('tree, 0.00 to 4.00 seconds')
    expect(clip('mo-0-0-0')).toHaveAccessibleName('Hop, 1.00 to 2.00 seconds')
    expect(clip('mo-0-0-1')).toHaveAccessibleName('Move, to right, 3.00 to 5.00 seconds')
    expect(clip('ob-2-0')).toHaveAccessibleName(/^cake, 0\.00 to 5\.50 seconds$/)
    // the motions are in the lane of their object, under its bar
    const lane = clip('ob-0-0').parentElement
    expect(lane).toContainElement(clip('mo-0-0-0'))
    expect(lane).toContainElement(clip('mo-0-0-1'))
    expect(lane).not.toContainElement(clip('ob-0-1'))
    expect(parseFloat(clip('mo-0-0-0').style.top)).toBeGreaterThan(parseFloat(clip('ob-0-0').style.top))
    // the objects are clips like any other for the screen reader and the keyboard
    expect(screen.getAllByRole('button', { name: /seconds$/ }).filter((el) => el.dataset.clip?.match(/^(ob|mo)-/))).toHaveLength(5)
  })

  it('puts motions that overlap in time on rows of their own', async () => {
    const api = await openWithObjects()
    useProject.getState().edit((d) => void d.scenes[0].objects[0].motions.push({ type: 'float', t0: 1.5, t1: 4, ease: 'ease_in_out' }))
    await renderScreen(<Timeline />, { api })
    const rows = ['mo-0-0-0', 'mo-0-0-1', 'mo-0-0-2'].map((k) => parseFloat(clip(k).style.top))
    expect(new Set(rows).size).toBe(3 - 1) // the hop (1-2) and the float (1.5-4) overlap; the move (3-5) overlaps the float too, but not the hop
    expect(rows[0]).not.toBe(rows[2])
  })

  it('says how to add the first object when the reel has none', async () => {
    window.history.replaceState({}, '', '/')
    const api = withObjects(await createMockApi())
    useProject.getState().load(await api.getProject('story-50s'))
    await renderScreen(<Timeline />, { api })
    expect(screen.getByText(/no objects yet: add one with \+, or drop it from the library/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Objects', expanded: true })).toBeInTheDocument()
  })

  it('hides the lanes of the objects, and shows them again', async () => {
    const user = userEvent.setup()
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    await user.click(screen.getByRole('button', { name: 'Objects', expanded: true }))
    expect(screen.queryByTitle('car, scene 1')).not.toBeInTheDocument()
    expect(clip('ob-0-0')).toBeNull()
    expect(screen.getByRole('button', { name: 'Objects', expanded: false }).parentElement).toHaveTextContent('3')
    await user.click(screen.getByRole('button', { name: 'Objects', expanded: false }))
    expect(clip('ob-0-0')).not.toBeNull()
  })
})

describe('working with the bars of objects and their motions', () => {
  it('selects an object by its bar and a motion by its clip', async () => {
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    fireEvent.pointerDown(clip('ob-0-1'))
    fireEvent.pointerUp(clip('ob-0-1'))
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 0, object: 1 })
    expect(clip('ob-0-1')).toHaveAttribute('aria-pressed', 'true')
    fireEvent.pointerDown(clip('mo-0-0-1'))
    fireEvent.pointerUp(clip('mo-0-0-1'))
    expect(useProject.getState().selection).toEqual({ kind: 'motion', scene: 0, object: 0, motion: 1 })
    expect(clip('ob-0-1')).toHaveAttribute('aria-pressed', 'false')
    // the lane's name selects the object too
    await userEvent.setup().click(screen.getByTitle('cake, scene 3'))
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 2, object: 0 })
  })

  it('selects several motions with Shift+click, but an object always on its own', async () => {
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    fireEvent.pointerDown(clip('mo-0-0-0'))
    fireEvent.pointerUp(clip('mo-0-0-0'))
    fireEvent.pointerDown(clip('mo-0-0-1'), { shiftKey: true })
    expect(useProject.getState().extra).toEqual([{ kind: 'motion', scene: 0, object: 0, motion: 1 }])
    fireEvent.pointerDown(clip('ob-0-0'), { shiftKey: true })
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 0, object: 0 })
    expect(useProject.getState().extra).toEqual([])
  })

  it('moves a motion by dragging it, as one undo step, and keeps it inside the scene', async () => {
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    const before = useProject.getState().past.length
    fireEvent.pointerDown(clip('mo-0-0-0'), { clientX: 100 })
    fireEvent.pointerMove(clip('mo-0-0-0'), { clientX: 100 + 22 * 0.5, altKey: true }) // half a second, without snapping
    fireEvent.pointerUp(clip('mo-0-0-0'))
    expect(objectsOf(0)[0].motions[0]).toMatchObject({ t0: 1.5, t1: 2.5 })
    expect(useProject.getState().past.length).toBe(before + 1)
    fireEvent.pointerDown(clip('mo-0-0-1'), { clientX: 100 })
    fireEvent.pointerMove(clip('mo-0-0-1'), { clientX: 100 + 22 * 9, altKey: true }) // far past the end of the 5.5 s scene
    fireEvent.pointerUp(clip('mo-0-0-1'))
    expect(objectsOf(0)[0].motions[1]).toMatchObject({ t0: 3.5, t1: 5.5 })
  })

  it('puts a motion dragged past another in time order, and keeps it selected', async () => {
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    fireEvent.pointerDown(clip('mo-0-0-0'), { clientX: 100 })
    fireEvent.pointerMove(clip('mo-0-0-0'), { clientX: 100 + 22 * 3, altKey: true }) // the hop (1-2) goes to 4-5, after the move (3-5)
    fireEvent.pointerUp(clip('mo-0-0-0'))
    expect(objectsOf(0)[0].motions.map((m) => m.type)).toEqual(['move', 'hop'])
    expect(useProject.getState().selection).toEqual({ kind: 'motion', scene: 0, object: 0, motion: 1 })
    act(() => useProject.getState().undo()) // the drag and the reordering are one step
    expect(objectsOf(0)[0].motions.map((m) => m.type)).toEqual(['hop', 'move'])
  })

  it('moves the moment an object appears when its bar is dragged and it stays to the end, and reaching the end makes it stay again', async () => {
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    fireEvent.pointerDown(clip('ob-0-0'), { clientX: 100 })
    fireEvent.pointerMove(clip('ob-0-0'), { clientX: 100 + 22, altKey: true })
    fireEvent.pointerUp(clip('ob-0-0'))
    expect(objectsOf(0)[0]).toMatchObject({ t0: 2, t1: null })
    // its end handle gives it an end ...
    const [, end] = clip('ob-0-0').querySelectorAll<HTMLElement>('.cursor-ew-resize')
    fireEvent.pointerDown(end, { clientX: 200 })
    fireEvent.pointerMove(end, { clientX: 200 - 22 * 2, altKey: true })
    fireEvent.pointerUp(end)
    expect(objectsOf(0)[0]).toMatchObject({ t0: 2, t1: 3.5 })
    // ... and pulling it back to the end of the scene takes the end away again
    const [, end2] = clip('ob-0-0').querySelectorAll<HTMLElement>('.cursor-ew-resize')
    fireEvent.pointerDown(end2, { clientX: 200 })
    fireEvent.pointerMove(end2, { clientX: 200 + 22 * 4, altKey: true })
    fireEvent.pointerUp(end2)
    expect(objectsOf(0)[0]).toMatchObject({ t0: 2, t1: null })
  })

  it('moves a bar that has an end as a whole', async () => {
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    fireEvent.pointerDown(clip('ob-0-1'), { clientX: 100 }) // the tree, 0 to 4
    fireEvent.pointerMove(clip('ob-0-1'), { clientX: 100 + 22, altKey: true })
    fireEvent.pointerUp(clip('ob-0-1'))
    expect(objectsOf(0)[1]).toMatchObject({ t0: 1, t1: 5 })
    fireEvent.pointerDown(clip('ob-0-1'), { clientX: 100 })
    fireEvent.pointerMove(clip('ob-0-1'), { clientX: 100 + 22 * 3, altKey: true }) // it cannot leave the scene: it stays to its end
    fireEvent.pointerUp(clip('ob-0-1'))
    expect(objectsOf(0)[1].t0).toBeLessThanOrEqual(1.5)
    expect(objectsOf(0)[1].t1).toBeNull()
  })

  it('nudges a focused clip with the arrow keys, says so, and deletes it with Delete', async () => {
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    fireEvent.keyDown(clip('mo-0-0-0'), { key: 'ArrowRight' })
    expect(objectsOf(0)[0].motions[0]).toMatchObject({ t0: 1.1, t1: 2.1 })
    expect(screen.getByText('Moved Hop to 1.10–2.10 s')).toBeInTheDocument()
    fireEvent.keyDown(clip('ob-0-1'), { key: 'ArrowRight', shiftKey: true })
    expect(objectsOf(0)[1]).toMatchObject({ t0: 1, t1: 5 })
    expect(screen.getByText('Moved tree to 1.00–5.00 s')).toBeInTheDocument()
    fireEvent.keyDown(clip('mo-0-0-0'), { key: 'Delete' })
    expect(objectsOf(0)[0].motions.map((m) => m.type)).toEqual(['move'])
    fireEvent.keyDown(clip('ob-0-1'), { key: 'Delete' })
    expect(objectsOf(0).map((o) => o.asset)).toEqual(['car'])
  })

  it('offers copy, paste, duplicate and delete in the right-click menu, and no split', async () => {
    const user = userEvent.setup()
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    await user.pointer({ keys: '[MouseRight]', target: clip('ob-0-1') })
    expect(await screen.findByRole('menuitem', { name: /^copy/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /^cut/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /^paste/i })).toHaveAttribute('data-disabled')
    expect(screen.getByRole('menuitem', { name: /^split/i })).toHaveAttribute('data-disabled')
    await user.click(screen.getByRole('menuitem', { name: /^duplicate/i }))
    expect(objectsOf(0).map((o) => o.asset)).toEqual(['car', 'tree', 'tree'])
  })
})

describe('adding objects from the timeline', () => {
  it('adds one at the playhead from the picker beside "Objects", and selects it', async () => {
    const user = userEvent.setup()
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    useProject.getState().setPlayhead(0.5)
    await user.click(screen.getByRole('button', { name: 'Add an object at the playhead' }))
    await user.type(await screen.findByRole('textbox', { name: 'Find an object' }), 'cake')
    await user.click(within(screen.getByRole('list', { name: 'Library objects' })).getByRole('button', { name: /^cake/i }))
    expect(objectsOf(0).map((o) => o.asset)).toEqual(['car', 'tree', 'cake'])
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 0, object: 2 })
    expect(await screen.findByTitle('cake, scene 1')).toBeInTheDocument()
  })

  it('adds a motion to an object from the + beside its name, at the playhead', async () => {
    const user = userEvent.setup()
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    useProject.getState().setPlayhead(2.5)
    await user.click(screen.getByRole('button', { name: 'Add a motion for tree at the playhead' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Spin' }))
    expect(objectsOf(0)[1].motions).toEqual([{ type: 'spin', t0: 2.5, t1: 4, ease: 'ease_in_out' }])
    expect(useProject.getState().selection).toEqual({ kind: 'motion', scene: 0, object: 1, motion: 0 })
    expect(await screen.findByRole('button', { name: /^Spin, 2\.50 to 4\.00 seconds$/ })).toBeInTheDocument()
  })

  // jsdom has no DragEvent, so the pointer position and the payload are put on a plain event, where React reads them
  const drop = (target: HTMLElement, payload: object | null, clientX: number) => {
    const e = new Event('drop', { bubbles: true, cancelable: true })
    Object.assign(e, { clientX, clientY: 0, dataTransfer: { getData: () => (payload ? JSON.stringify(payload) : ''), types: payload ? ['application/x-reel-item'] : [] } })
    act(() => void target.dispatchEvent(e))
  }
  const lanesBody = (title: string) => screen.getByTitle(title).closest('.sticky')!.nextElementSibling as HTMLElement

  it('takes a library object dropped on the Objects lane into the scene it is dropped on', async () => {
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    const slots = sceneSlots(useProject.getState().spec!)
    drop(screen.getByRole('button', { name: 'Objects' }).closest('.sticky')!.nextElementSibling as HTMLElement, { kind: 'object', name: 'balloon' }, (slots[3].start + 1) * 22)
    expect(objectsOf(3).map((o) => o.asset)).toEqual(['balloon'])
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 3, object: 0 })
  })

  it('takes one dropped on the lane of an object, or on a scene, too — and nothing else', async () => {
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    const slots = sceneSlots(useProject.getState().spec!)
    drop(lanesBody('car, scene 1'), { kind: 'object', name: 'cake' }, (slots[1].start + 0.5) * 22)
    expect(objectsOf(1).map((o) => o.asset)).toEqual(['cake'])
    drop(screen.getByText(/rainy_street/).closest('.isolate') as HTMLElement, { kind: 'object', name: 'tree' }, (slots[4].start + 0.5) * 22)
    expect(objectsOf(4).map((o) => o.asset)).toEqual(['tree'])
    // an action dropped there, a drop that is not ours, and an object dropped on the captions lane do nothing
    const before = JSON.stringify(useProject.getState().spec)
    drop(lanesBody('car, scene 1'), { kind: 'action', name: 'wave' }, 40)
    drop(lanesBody('car, scene 1'), null, 40)
    drop(lanesBody('Captions'), { kind: 'object', name: 'tree' }, 40)
    expect(JSON.stringify(useProject.getState().spec)).toBe(before)
  })

  it('lets an object be dropped only where it can go', async () => {
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    const over = (el: HTMLElement) => {
      const e = new Event('dragover', { bubbles: true, cancelable: true }) as Event & { dataTransfer?: unknown }
      e.dataTransfer = { types: ['application/x-reel-item'] }
      el.dispatchEvent(e)
      return e.defaultPrevented
    }
    expect(over(lanesBody('car, scene 1'))).toBe(true)
    expect(over(screen.getByRole('button', { name: 'Objects' }).closest('.sticky')!.nextElementSibling as HTMLElement)).toBe(true)
    expect(over(lanesBody('Captions'))).toBe(false)
  })
})

describe('copying objects on the timeline', () => {
  it('copies an object to another scene at the playhead, with its motions, and the new lane carries that scene', async () => {
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    const slots = sceneSlots(useProject.getState().spec!)
    act(() => useProject.getState().select({ kind: 'object', scene: 0, object: 0 }))
    act(() => void copySelection())
    expect(useClipboard.getState().board?.items).toHaveLength(1)
    act(() => useProject.getState().setPlayhead(slots[5].start + 1))
    act(() => void pasteAtPlayhead())
    expect(objectsOf(5)).toHaveLength(1)
    expect(objectsOf(5)[0]).toMatchObject({ asset: 'car', position: [0.2, 0.74], scale: 0.675 })
    expect(objectsOf(5)[0].motions.map((m) => m.type)).toEqual(['hop', 'move'])
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 5, object: 0 })
    expect(await screen.findByTitle('car, scene 6')).toBeInTheDocument()
    expect(objectsOf(0)).toHaveLength(2) // the original stays
  })

  it('pastes copied motions onto the object whose lane name was clicked', async () => {
    const user = userEvent.setup()
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    act(() => useProject.getState().select({ kind: 'motion', scene: 0, object: 0, motion: 0 }))
    act(() => void copySelection())
    act(() => useProject.getState().setPlayhead(2))
    await user.pointer({ keys: '[MouseRight]', target: screen.getByTitle('tree, scene 1') })
    await user.click(await screen.findByRole('menuitem', { name: /paste onto tree at the playhead/i }))
    expect(objectsOf(0)[1].motions.map((m) => [m.type, m.t0, m.t1])).toEqual([['hop', 2, 3]])
    expect(objectsOf(0)[0].motions).toHaveLength(2)
  })

  it('duplicates and deletes through the same actions as every other clip', async () => {
    const api = await openWithObjects()
    await renderScreen(<Timeline />, { api })
    act(() => useProject.getState().select({ kind: 'object', scene: 0, object: 1 }))
    act(() => duplicateSelected())
    expect(objectsOf(0).map((o) => o.asset)).toEqual(['car', 'tree', 'tree'])
    act(() => deleteSelected())
    expect(objectsOf(0).map((o) => o.asset)).toEqual(['car', 'tree'])
  })
})

