import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { addUserAsset, libraryApi, svgFile } from '@/features/assets/testing'
import { sceneAt, sceneSlots } from '@/lib/timeline'
import { useProject } from '@/store/project'
import { renderScreen } from '@/test/harness'
import { LibraryBrowser } from './LibraryBrowser'

describe('the library browser', () => {
  it('in the studio, every action can be added at the playhead or dragged onto the timeline', async () => {
    await renderScreen(<LibraryBrowser compact />)
    expect((await screen.findAllByRole('button', { name: /^add .+ at the playhead$/i })).length).toBeGreaterThan(5)
    expect(document.querySelector('[draggable="true"]')).not.toBeNull()
  })

  it('on its own page (no project open) it only shows things: nothing to add, nothing to drag', async () => {
    await renderScreen(<LibraryBrowser browse />)
    expect(await screen.findByText('walk')).toBeInTheDocument()
    expect(screen.queryAllByRole('button', { name: /at the playhead/i })).toHaveLength(0)
    expect(document.querySelector('[draggable="true"]')).toBeNull()
  })

  it('searches by name and by what the entry says it does', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    await renderScreen(<LibraryBrowser browse />)
    await screen.findByText('walk')
    await user.type(screen.getByRole('textbox', { name: /search the library/i }), 'tears')
    expect(await screen.findByText('cry')).toBeInTheDocument()
    expect(screen.queryByText('walk')).not.toBeInTheDocument()
  })
})

describe('the Objects section in the studio', () => {
  beforeEach(() => useProject.getState().unload())

  async function studio() {
    const api = await libraryApi()
    await addUserAsset(api, 'kite', { summary: 'A red paper kite', tags: ['kite', 'पतंग'] })
    useProject.getState().load(await api.getProject('story-50s'))
    const user = userEvent.setup()
    await renderScreen(<LibraryBrowser compact />, { api })
    await user.click(await screen.findByRole('radio', { name: 'Objects' }))
    return { api, user }
  }

  it('is one of the four sections of the narrow panel, and has no From filter', async () => {
    await studio()
    expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['Actions', 'Objects', 'Sounds', 'Camera'])
    expect(screen.queryByRole('radiogroup', { name: 'From' })).not.toBeInTheDocument()
  })

  it('lists small rows: a picture, the name, the size and an add button', async () => {
    await studio()
    const add = await screen.findByRole('button', { name: 'Add car at the playhead' })
    const row = add.closest('[draggable="true"]') as HTMLElement
    expect(within(row).getByText('car')).toBeInTheDocument()
    expect(within(row).getByText('0.52 × a person')).toBeInTheDocument()
    expect(within(row).queryByText('Library')).not.toBeInTheDocument() // 70 of them: the badge is for what is yours
    // yours first, marked as such
    const names = screen.getAllByRole('button', { name: /^Add .+ at the playhead$/ }).map((b) => b.getAttribute('aria-label'))
    expect(names[0]).toBe('Add kite at the playhead')
    expect(within((await screen.findByRole('button', { name: 'Add kite at the playhead' })).closest('[draggable="true"]') as HTMLElement).getByText('Yours')).toBeInTheDocument()
    expect(names).toEqual(expect.arrayContaining(['Add cake at the playhead', 'Add tree at the playhead']))
  })

  it('adds the object to the scene under the playhead, selects it, and one undo takes it away', async () => {
    const { user } = await studio()
    useProject.getState().setPlayhead(12)
    const spec = useProject.getState().spec!
    const scene = sceneAt(sceneSlots(spec), 12)!.index
    const before = spec.scenes[scene].objects.length

    await user.click(await screen.findByRole('button', { name: 'Add car at the playhead' }))

    const now = useProject.getState()
    expect(now.spec!.scenes[scene].objects.map((o) => o.asset)).toContain('car')
    expect(now.spec!.scenes[scene].objects).toHaveLength(before + 1)
    expect(now.selection).toEqual({ kind: 'object', scene, object: before })
    expect(now.save).toBe('dirty')
    useProject.getState().undo()
    expect(useProject.getState().spec!.scenes[scene].objects).toHaveLength(before)
  })

  it('drags with the item the timeline and the stage understand', async () => {
    await studio()
    const row = (await screen.findByRole('button', { name: 'Add cake at the playhead' })).closest('[draggable="true"]') as HTMLElement
    const setData = vi.fn()
    const dataTransfer = { setData, effectAllowed: '' }
    fireEvent.dragStart(row, { dataTransfer })
    expect(setData).toHaveBeenCalledWith('application/x-reel-item', JSON.stringify({ kind: 'object', name: 'cake' }))
    expect(dataTransfer.effectAllowed).toBe('copy')
  })

  it('searches by the words of the object, in any language', async () => {
    const { user } = await studio()
    await screen.findByRole('button', { name: 'Add kite at the playhead' })
    await user.type(screen.getByRole('textbox', { name: /search the library/i }), 'पतंग')
    expect(screen.getAllByRole('button', { name: /^Add .+ at the playhead$/ })).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Add kite at the playhead' })).toBeInTheDocument()
  })

  it('lets you add your own from there, with no project needed by the dialog', async () => {
    const { user, api } = await studio()
    await user.click(await screen.findByRole('button', { name: /add your own/i }))
    const dialog = await screen.findByRole('dialog', { name: 'Add asset' })
    fireEvent.drop(within(dialog).getByRole('button', { name: /drop an svg/i }), { dataTransfer: { files: [svgFile('lamp.svg')], types: ['Files'] } })
    await within(dialog).findByRole('textbox', { name: 'Name' })
    expect(within(dialog).getByRole('radio', { name: 'Object' })).toBeChecked()
    await user.click(within(dialog).getByRole('button', { name: /save to library/i }))
    expect(await screen.findByRole('button', { name: 'Add lamp at the playhead' })).toBeInTheDocument()
    expect((await api.listAssets()).assets.some((a) => a.name === 'lamp')).toBe(true)
  })

  it('says so when the library has no objects yet, with a way to add the first', async () => {
    const user = userEvent.setup()
    await renderScreen(<LibraryBrowser compact />) // the plain mock engine: an empty library
    await user.click(await screen.findByRole('radio', { name: 'Objects' }))
    expect(await screen.findByText(/the library has no objects yet/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /add asset/i }))
    expect(await screen.findByRole('dialog', { name: 'Add asset' })).toBeInTheDocument()
  })

  it('on the library page itself (no project) lists objects as cards with nothing to add or drag', async () => {
    const api = await libraryApi()
    const user = userEvent.setup()
    await renderScreen(<LibraryBrowser browse />, { api })
    await user.click(await screen.findByRole('tab', { name: 'Objects' }))
    await screen.findByText('cake')
    expect(screen.queryAllByRole('button', { name: /at the playhead/i })).toHaveLength(0)
    expect(document.querySelector('[draggable="true"]')).toBeNull()
  })

  it('says so when the search finds no object', async () => {
    const api = await libraryApi()
    await addUserAsset(api, 'kite', { summary: 'x' })
    useProject.getState().load(await api.getProject('story-50s'))
    const user = userEvent.setup()
    await renderScreen(<LibraryBrowser compact />, { api })
    await user.click(await screen.findByRole('radio', { name: 'Objects' }))
    await user.type(screen.getByRole('textbox', { name: /search the library/i }), 'zebra')
    await waitFor(() => expect(screen.getByText('Nothing matches.')).toBeInTheDocument())
  })
})
