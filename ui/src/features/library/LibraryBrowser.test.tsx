import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
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
