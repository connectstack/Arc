import { act, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMockApi } from '@/api/mock'
import { useProject } from '@/store/project'
import { renderScreen } from '@/test/harness'
import { addScene } from './ops'
import { useAutosave } from './useAutosave'

function Autosaver() {
  useAutosave()
  return null
}

beforeEach(() => useProject.getState().unload())

describe('autosave', () => {
  it('saves an edit once and settles on "saved", also when the edit made a scene (the file comes back with its keys in another order)', async () => {
    window.history.replaceState({}, '', '/')
    const api = await createMockApi()
    useProject.getState().load(await api.getProject('story-50s'))
    const save = vi.spyOn(api, 'saveProject')
    await renderScreen(<Autosaver />, { api })

    act(() => useProject.getState().edit((d) => void addScene(d)))
    expect(useProject.getState().save).toBe('dirty')
    await waitFor(() => expect(useProject.getState().save).toBe('saved'), { timeout: 4000 })
    // it used to save again and again: the saved file read as an edit
    await new Promise((r) => setTimeout(r, 2200))
    expect(save).toHaveBeenCalledTimes(1)
    expect(useProject.getState().save).toBe('saved')
  })

  it('saves again for an edit made while a save is on its way', async () => {
    window.history.replaceState({}, '', '/')
    const api = await createMockApi()
    useProject.getState().load(await api.getProject('story-50s'))
    const real = api.saveProject.bind(api)
    // a slow disk: the save is on its way for a while
    const save = vi.spyOn(api, 'saveProject').mockImplementation(async (...args) => {
      await new Promise((r) => setTimeout(r, 500))
      return real(...args)
    })
    await renderScreen(<Autosaver />, { api })

    act(() => useProject.getState().edit((d) => void (d.meta.title = 'first')))
    await waitFor(() => expect(useProject.getState().save).toBe('saving'), { timeout: 4000 })
    act(() => useProject.getState().edit((d) => void (d.meta.title = 'second')))
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2), { timeout: 4000 })
    await waitFor(() => expect(useProject.getState().save).toBe('saved'), { timeout: 4000 })
    expect(save.mock.calls[1][1].meta.title).toBe('second')
  })
})
