import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { createMockApi } from '@/api/mock'
import { useProject } from '@/store/project'
import { renderScreen } from '@/test/harness'
import { HistoryButton } from './HistoryPopover'

beforeEach(() => useProject.getState().unload())

describe('the undo history', () => {
  it('names each step by what it changed, and going back to a step undoes everything after it', async () => {
    const user = userEvent.setup()
    window.history.replaceState({}, '', '/')
    const api = await createMockApi()
    useProject.getState().load(await api.getProject('story-50s'))
    const st = useProject.getState
    const original = st().spec!.meta.title
    st().edit((d) => void (d.meta.title = 'Rain day'))
    st().edit((d) => void (d.meta.style = 'stickman'))
    st().edit((d) => void (d.scenes[0].duration_sec += 1))
    await renderScreen(<HistoryButton />, { api })

    await user.click(screen.getByRole('button', { name: 'Undo history' }))
    const steps = within(await screen.findByRole('list', { name: 'Steps' })).getAllByRole('listitem')
    expect(steps.map((li) => li.textContent?.replace(/now|undone/g, '').trim())).toEqual([expect.stringMatching(/^Scene 1: length/), 'Changed the style to stickman', 'Renamed the reel to “Rain day”', 'Start of the history'])
    expect(within(steps[0]).getByRole('button')).toHaveAttribute('aria-current', 'step')

    await user.click(within(steps[2]).getByRole('button')) // back to just after the rename
    expect(st().spec!.meta.title).toBe('Rain day')
    expect(st().spec!.meta.style).not.toBe('stickman')
    expect(st().future).toHaveLength(2)

    await user.click(screen.getByRole('button', { name: 'Undo history' }))
    const again = within(await screen.findByRole('list', { name: 'Steps' })).getAllByRole('listitem')
    expect(again[0]).toHaveTextContent(/undone/)
    expect(again[2]).toHaveTextContent(/now/)

    await user.click(within(again[0]).getByRole('button')) // forward again, to the very last step
    expect(st().spec!.meta.style).toBe('stickman')
    await user.click(screen.getByRole('button', { name: 'Undo history' }))
    await user.click(within(await screen.findByRole('list', { name: 'Steps' })).getByRole('button', { name: /start of the history/i }))
    expect(st().spec!.meta.title).toBe(original)
  })

  it('is disabled until there is something to go back to', async () => {
    window.history.replaceState({}, '', '/')
    const api = await createMockApi()
    useProject.getState().load(await api.getProject('story-50s'))
    await renderScreen(<HistoryButton />, { api })
    expect(screen.getByRole('button', { name: 'Undo history' })).toBeDisabled()
  })
})
