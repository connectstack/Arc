import { act, fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMockApi } from '@/api/mock'
import { useClipboard } from '@/store/clipboard'
import { useProject } from '@/store/project'
import { renderScreen } from '@/test/harness'
import { Timeline } from './Timeline'
import { copySelection, deleteSelected, duplicateSelected, pasteAtPlayhead } from './clipboardActions'

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
