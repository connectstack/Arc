import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMockApi } from '@/api/mock'
import { newObject } from '@/lib/spec'
import { sceneSlots } from '@/lib/timeline'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { renderScreen } from '@/test/harness'
import { LeftPanel } from './LeftPanel'
import { SceneObjects, outlineScene } from './SceneObjects'
import { withObjects } from './objects.fixture'

// the real dialog is another part of the app; here it only has to be opened with what this part asks for, and say it saved something
vi.mock('@/features/assets/AddAssetDialog', () => ({
  AddAssetDialog: ({ open, initial, onAdded, onOpenChange }: { open: boolean; initial?: { kind?: string; hint?: string }; onAdded?: (a: { name: string }) => void; onOpenChange: (o: boolean) => void }) =>
    open ? (
      <div role="dialog" aria-label="Add asset">
        <p>{`${initial?.kind} · ${initial?.hint ?? ''}`}</p>
        <button onClick={() => onAdded?.({ name: 'cake' })}>Pretend it was saved</button>
        <button onClick={() => onOpenChange(false)}>Cancel</button>
      </div>
    ) : null,
}))

async function open() {
  window.history.replaceState({}, '', '/')
  const api = withObjects(await createMockApi())
  useProject.getState().load(await api.getProject('story-50s'))
  useProject.getState().edit((d) => {
    d.scenes[0].objects.push({ ...newObject('car', [0.2, 0.74], 0.675), t0: 1, motions: [{ type: 'hop', t0: 1, t1: 2, ease: 'ease_in_out' }, { type: 'spin', t0: 2, t1: 3, ease: 'ease_in_out' }] })
    d.scenes[0].objects.push(newObject('tree', 'left'))
    d.scenes[3].objects.push(newObject('cake', [0.5, 0.74]))
  })
  useStudio.getState().set({ leftTab: 'scenes' })
  return api
}
const objectsOf = (scene: number) => useProject.getState().spec!.scenes[scene].objects
const row = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}\\b`, 'i') })

beforeEach(() => useProject.getState().unload())

describe('which scene the outline is about', () => {
  it('is the one the selection belongs to, else the one under the playhead', async () => {
    const api = await open()
    const spec = useProject.getState().spec!
    const at = (i: number) => sceneSlots(spec)[i].start + 0.5
    expect(outlineScene(spec, { kind: 'reel' }, at(2))).toBe(2)
    expect(outlineScene(spec, { kind: 'character', id: 'mia' }, at(4))).toBe(4)
    expect(outlineScene(spec, { kind: 'object', scene: 3, object: 0 }, at(0))).toBe(3)
    expect(outlineScene(spec, { kind: 'scene', scene: 1 }, at(0))).toBe(1)
    expect(outlineScene(spec, { kind: 'scene', scene: 99 }, at(2))).toBe(2) // a scene that is gone
    void api
  })
})

describe('the objects of the scene on show', () => {
  it('lists them with their size, place and times, and the scene they are in', async () => {
    const api = await open()
    await renderScreen(<SceneObjects spec={useProject.getState().spec!} />, { api })
    expect(await screen.findByRole('region', { name: 'Objects in scene 1' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Objects · scene 1' })).toBeInTheDocument()
    expect(await screen.findByText('0.35× a person · 0.2, 0.74 · from 1 s · 2 motions')).toBeInTheDocument()
    expect(screen.getByText('1.1× a person · left · the whole scene')).toBeInTheDocument()
    expect(screen.queryByText(/cake/)).not.toBeInTheDocument() // that one is in scene 4
  })

  it('follows the selection to another scene, and says when there is nothing in it', async () => {
    const api = await open()
    const { rerender } = await renderScreen(<SceneObjects spec={useProject.getState().spec!} />, { api })
    act(() => useProject.getState().select({ kind: 'object', scene: 3, object: 0 }))
    expect(await screen.findByRole('region', { name: 'Objects in scene 4' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^cake/ })).toHaveAttribute('aria-pressed', 'true')
    act(() => useProject.getState().select({ kind: 'scene', scene: 5 }))
    expect(await screen.findByText(/no objects in this scene yet/i)).toBeInTheDocument()
    void rerender
  })

  it('selects an object, and stays marked while one of its motions is selected', async () => {
    const user = userEvent.setup()
    const api = await open()
    await renderScreen(<SceneObjects spec={useProject.getState().spec!} />, { api })
    await user.click(await screen.findByRole('button', { name: /^tree/ }))
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 0, object: 1 })
    expect(row('tree')).toHaveAttribute('aria-pressed', 'true')
    expect(row('car')).toHaveAttribute('aria-pressed', 'false')
    act(() => useProject.getState().select({ kind: 'motion', scene: 0, object: 0, motion: 1 }))
    expect(row('car')).toHaveAttribute('aria-pressed', 'true')
  })

  it('duplicates an object next to the first, and deletes one, both undoable', async () => {
    const user = userEvent.setup()
    const api = await open()
    await renderScreen(<SceneObjects spec={useProject.getState().spec!} />, { api })
    await user.click(await screen.findByRole('button', { name: 'Duplicate car' }))
    expect(objectsOf(0).map((o) => o.asset)).toEqual(['car', 'car', 'tree'])
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 0, object: 1 })
    await user.click(screen.getAllByRole('button', { name: 'Delete car' })[0])
    expect(objectsOf(0).map((o) => o.asset)).toEqual(['car', 'tree'])
    act(() => useProject.getState().undo())
    expect(objectsOf(0).map((o) => o.asset)).toEqual(['car', 'car', 'tree'])
    act(() => useProject.getState().undo())
    expect(objectsOf(0).map((o) => o.asset)).toEqual(['car', 'tree'])
  })
})

describe('adding an object', () => {
  it('searches the library by name or by a word of a script (Hindi too) and puts the choice in the scene', async () => {
    const user = userEvent.setup()
    const api = await open()
    await renderScreen(<SceneObjects spec={useProject.getState().spec!} />, { api })
    await user.click(await screen.findByRole('button', { name: 'Add object' }))
    const list = await screen.findByRole('list', { name: 'Library objects' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(5)
    await user.type(screen.getByRole('textbox', { name: 'Find an object' }), 'गुब्बारा')
    await user.click(within(list).getByRole('button', { name: /balloon/i }))
    expect(objectsOf(0).map((o) => o.asset)).toEqual(['car', 'tree', 'balloon'])
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 0, object: 2 })
    expect(screen.queryByRole('list', { name: 'Library objects' })).not.toBeInTheDocument() // the picker closed
  })

  it('adds to the scene the outline is about, not the one the playhead is in', async () => {
    const user = userEvent.setup()
    const api = await open()
    useProject.getState().select({ kind: 'scene', scene: 5 })
    await renderScreen(<SceneObjects spec={useProject.getState().spec!} />, { api })
    await user.click(await screen.findByRole('button', { name: 'Add object' }))
    await user.click(await screen.findByRole('button', { name: /tree/i }))
    expect(objectsOf(5).map((o) => o.asset)).toEqual(['tree'])
    expect(objectsOf(0)).toHaveLength(2)
  })

  it('opens the dialog for a drawing of your own, and puts the new object in the scene when it is saved', async () => {
    const user = userEvent.setup()
    const api = await open()
    useProject.getState().select({ kind: 'scene', scene: 2 })
    await renderScreen(<SceneObjects spec={useProject.getState().spec!} />, { api })
    await user.click(await screen.findByRole('button', { name: 'Add object' }))
    await user.click(await screen.findByRole('button', { name: /add your own/i }))
    const dialog = await screen.findByRole('dialog', { name: 'Add asset' })
    expect(dialog).toHaveTextContent('object · The drawing you add is put in scene 3.')
    expect(objectsOf(2)).toHaveLength(0) // nothing yet
    // the selection moving elsewhere meanwhile does not change where it goes
    act(() => useProject.getState().select({ kind: 'scene', scene: 6 }))
    await user.click(within(dialog).getByRole('button', { name: 'Pretend it was saved' }))
    expect(objectsOf(2).map((o) => o.asset)).toEqual(['cake'])
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 2, object: 0 })
    expect(screen.queryByRole('dialog', { name: 'Add asset' })).not.toBeInTheDocument()
  })

  it('adds nothing when the dialog is cancelled', async () => {
    const user = userEvent.setup()
    const api = await open()
    await renderScreen(<SceneObjects spec={useProject.getState().spec!} />, { api })
    await user.click(await screen.findByRole('button', { name: 'Add object' }))
    await user.click(await screen.findByRole('button', { name: /add your own/i }))
    await user.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog', { name: 'Add asset' })).not.toBeInTheDocument()
    expect(objectsOf(0)).toHaveLength(2)
  })
})

describe('in the left panel', () => {
  it('is part of the Scenes tab, under the scenes and above the button that adds a scene', async () => {
    const api = await open()
    await renderScreen(<LeftPanel />, { api })
    const region = await screen.findByRole('region', { name: 'Objects in scene 1' })
    const add = screen.getByRole('button', { name: 'Add a scene' })
    expect(region.compareDocumentPosition(add) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(within(region).getByRole('button', { name: 'Add object' })).toBeInTheDocument()
  })
})
