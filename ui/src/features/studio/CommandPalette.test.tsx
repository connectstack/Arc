import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { createMockApi } from '@/api/mock'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { renderScreen } from '@/test/harness'
import { CommandPalette } from './CommandPalette'
import { withObjects } from './objects.fixture'

async function open() {
  window.history.replaceState({}, '', '/')
  const api = withObjects(await createMockApi())
  useProject.getState().load(await api.getProject('story-50s'))
  useStudio.getState().set({ paletteOpen: true })
  await renderScreen(<CommandPalette />, { api })
  const search = await screen.findByRole('textbox', { name: 'Search commands' })
  // the library arrives with the catalog: wait until it can be searched
  await screen.findByRole('option', { name: /add object…/i })
  return search
}
const objectsOf = (scene: number) => useProject.getState().spec!.scenes[scene].objects

beforeEach(() => {
  useProject.getState().unload()
  useStudio.getState().set({ paletteOpen: false })
})

describe('adding an object from the command palette', () => {
  it('offers "Add object…" among the commands, and does not list every object of the library unasked', async () => {
    await open()
    expect(screen.getByRole('option', { name: /add object…/i })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /add object: /i })).not.toBeInTheDocument()
  })

  it('lists the objects as "Add object: <name>" with their size, found by name, by a word of a script (Hindi too) and by what they are', async () => {
    const user = userEvent.setup()
    const search = await open()
    await user.type(search, 'tree')
    expect(await screen.findByRole('option', { name: /add object: tree/i })).toHaveTextContent('1.1× a person')
    await user.clear(search)
    await user.type(search, 'गाड़ी')
    expect(await screen.findByRole('option', { name: /add object: car/i })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /add object: tree/i })).not.toBeInTheDocument()
    await user.clear(search)
    await user.type(search, 'hatchback') // only in what the library says it is
    expect(await screen.findByRole('option', { name: /add object: car/i })).toBeInTheDocument()
  })

  it('puts the object in the scene under the playhead, selects it and closes the palette', async () => {
    const user = userEvent.setup()
    const search = await open()
    act(() => useProject.getState().setPlayhead(1))
    await user.type(search, 'cake')
    await user.click(await screen.findByRole('option', { name: /add object: cake/i }))
    expect(objectsOf(0).map((o) => o.asset)).toEqual(['cake'])
    expect(objectsOf(0)[0].scale).toBe(1)
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 0, object: 0 })
    expect(useStudio.getState().paletteOpen).toBe(false)
  })

  it('runs the first match with Enter', async () => {
    const user = userEvent.setup()
    const search = await open()
    await user.type(search, 'balloon{Enter}')
    expect(objectsOf(0).map((o) => o.asset)).toEqual(['balloon'])
    expect(useStudio.getState().paletteOpen).toBe(false)
  })

  it('"Add object…" lists the library and keeps the cursor in the search box', async () => {
    const user = userEvent.setup()
    const search = await open()
    await user.click(screen.getByRole('option', { name: /add object…/i }))
    expect(search).toHaveValue('Add object: ')
    expect(await screen.findAllByRole('option', { name: /add object: /i })).toHaveLength(5)
    await screen.findByRole('option', { name: /add object: billboard/i })
    await waitFor(() => expect(search).toHaveFocus())
    expect(useStudio.getState().paletteOpen).toBe(true)
    await user.keyboard('bill{Enter}')
    expect(objectsOf(0).map((o) => o.asset)).toEqual(['billboard'])
  })
})

describe('building by hand', () => {
  it('opens the Build panel of the studio, showing the left panel', async () => {
    const user = userEvent.setup()
    useStudio.getState().set({ leftTab: 'scenes' })
    await open()
    await user.click(await screen.findByRole('option', { name: /build a scene from the library/i }))
    expect(useStudio.getState()).toMatchObject({ leftTab: 'build', paletteOpen: false })
  })
})
