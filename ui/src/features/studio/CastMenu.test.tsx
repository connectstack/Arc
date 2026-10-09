import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { libraryApi } from '@/features/assets/testing'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { renderScreen } from '@/test/harness'
import { LeftPanel } from './LeftPanel'

beforeEach(() => useProject.getState().unload())

describe('adding a character', () => {
  async function openMenu() {
    const api = await libraryApi()
    useProject.getState().load(await api.getProject('story-50s'))
    useStudio.getState().set({ leftTab: 'cast' })
    await renderScreen(<LeftPanel />, { api })
    await userEvent.setup().click(await screen.findByRole('button', { name: /add a character/i }))
    return screen.findByRole('menu')
  }

  it('offers the engine’s bodies and, under their own heading, the pictures of the library', async () => {
    const menu = await openMenu()
    const labels = within(menu)
      .getAllByRole('menuitem')
      .map((m) => m.textContent)
    expect(within(menu).getByText('Body type')).toBeInTheDocument()
    expect(within(menu).getByText('Pictures from the library')).toBeInTheDocument()
    expect(labels).toEqual(expect.arrayContaining(['hero', 'robot', 'cat', 'cow']))
    // the bodies come first: the pictures start after the second heading
    const order = [...menu.querySelectorAll('[role="menuitem"], .eyebrow')].map((n) => n.textContent)
    expect(order.indexOf('Pictures from the library')).toBeGreaterThan(order.indexOf('hero'))
    expect(order.indexOf('Pictures from the library')).toBeLessThan(order.indexOf('cat'))
  })

  it('scrolls when the list is longer than the window (it holds every picture of the library)', async () => {
    const menu = await openMenu()
    expect(menu.className).toContain('overflow-y-auto')
    expect(menu.className).toContain('max-h-[var(--radix-dropdown-menu-content-available-height)]')
  })

  it('casts the picture that is chosen', async () => {
    const menu = await openMenu()
    await userEvent.setup().click(within(menu).getByRole('menuitem', { name: 'cow' }))
    const cast = useProject.getState().spec!.characters
    expect(cast[cast.length - 1].archetype).toBe('cow')
  })
})
