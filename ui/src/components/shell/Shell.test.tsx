import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { renderScreen } from '@/test/harness'
import { Shell } from './Shell'

describe('the app shell', () => {
  it('marks the current section in the navigation rail (it is a link, not just a colour)', async () => {
    await renderScreen(<Shell />, { route: '/library', path: '/*' })
    const library = await screen.findByRole('link', { name: 'Library' })
    expect(library).toHaveAttribute('aria-current', 'page')
    expect(library.className).toContain('text-accent')
    expect(screen.getByRole('link', { name: 'Projects' })).not.toHaveAttribute('aria-current')
  })

  it('shows the engine as ready once the server answers', async () => {
    await renderScreen(<Shell />, { route: '/', path: '/*' })
    expect(await screen.findByText(/engine ready/i)).toBeInTheDocument()
  })
})
