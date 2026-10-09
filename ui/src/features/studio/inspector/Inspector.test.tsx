import { act, fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { createMockApi } from '@/api/mock'
import { useProject } from '@/store/project'
import { renderScreen } from '@/test/harness'
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
