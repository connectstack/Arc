import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { SHORTCUT_GROUPS } from '@/lib/shortcuts'
import { useStudio } from '@/store/studio'
import { renderScreen } from '@/test/harness'
import { Shell } from './Shell'

beforeEach(() => useStudio.setState({ shortcutsOpen: false }))

describe('the keyboard shortcuts list', () => {
  it('opens with the question mark, lists every group, and closes with Escape', async () => {
    const user = userEvent.setup()
    await renderScreen(<Shell />, { route: '/library', path: '/*' })
    await screen.findByRole('link', { name: 'Library' })
    expect(screen.queryByRole('dialog', { name: /keyboard shortcuts/i })).not.toBeInTheDocument()

    await user.keyboard('?')
    const dialog = await screen.findByRole('dialog', { name: /keyboard shortcuts/i })
    for (const g of SHORTCUT_GROUPS) expect(within(dialog).getByRole('region', { name: g.title })).toBeInTheDocument()
    expect(within(dialog).getByText(/paste at the playhead/i)).toBeInTheDocument()
    expect(within(dialog).getByText(/add a clip to the selection/i)).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: /keyboard shortcuts/i })).not.toBeInTheDocument()
  })

  it('does not open while typing a question mark into a text box', async () => {
    const user = userEvent.setup()
    await renderScreen(
      <>
        <Shell />
        <input aria-label="a text box" />
      </>,
      { route: '/library', path: '/*' },
    )
    const search = await screen.findByRole('textbox', { name: 'a text box' })
    await user.click(search)
    await user.keyboard('why?')
    expect(search).toHaveValue('why?')
    expect(screen.queryByRole('dialog', { name: /keyboard shortcuts/i })).not.toBeInTheDocument()
  })

  it('has no empty groups and no duplicate descriptions', () => {
    const all = SHORTCUT_GROUPS.flatMap((g) => g.items.map((i) => i.what))
    expect(SHORTCUT_GROUPS.every((g) => g.items.length > 0 && g.items.every((i) => i.keys.length > 0))).toBe(true)
    expect(new Set(all).size).toBe(all.length)
  })
})
