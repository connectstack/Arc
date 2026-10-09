import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { renderScreen } from '@/test/harness'
import { useUi } from '@/store/ui'
import { HealthPage } from './HealthPage'

beforeEach(() => useUi.setState({ consent: {}, ttsEngine: 'auto' }))

describe('Health & settings', () => {
  it('shows what is installed and what each online service needs, with the cost on the button', async () => {
    await renderScreen(<HealthPage />)
    expect(await screen.findByText(/skia draws every frame/i)).toBeInTheDocument()
    expect(screen.getByText(/ffmpeg turns frames/i)).toBeInTheDocument()
    expect(screen.getAllByText(/about 100 tokens, a fraction of a cent/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/free, runs here/i)).toBeInTheDocument()
    // an Ollama cloud model would send the script away: it is called out, not offered
    expect(screen.getByText(/send your script off this computer/i)).toBeInTheDocument()
  })

  it('reports a key by where it was found, and has no field for entering one', async () => {
    await renderScreen(<HealthPage />)
    const section = await screen.findByRole('region', { name: /api keys/i })
    expect(within(section).getAllByText(/not set/i)).toHaveLength(3)
    expect(within(section).getByText(/never asks for, displays or stores a key/i)).toBeInTheDocument()
    expect(within(section).queryAllByRole('textbox')).toHaveLength(0)
    expect(document.querySelector('input[type="password"]')).toBeNull()
  })

  it('warns that clearing generated voice lines re-bills an online voice', async () => {
    const user = userEvent.setup()
    await renderScreen(<HealthPage />)
    await user.click(await screen.findByRole('button', { name: /clear generated voice lines/i }))
    expect(await screen.findByText(/this can cost money/i)).toBeInTheDocument()
    expect(screen.getByText(/billed again/i)).toBeInTheDocument()
  })

  it('remembers preferences and lets the user withdraw a consent', async () => {
    const user = userEvent.setup()
    useUi.setState({ consent: { 'api.openai.com': true } })
    await renderScreen(<HealthPage />)
    await user.click(await screen.findByRole('button', { name: /stop allowing api\.openai\.com/i }))
    expect(useUi.getState().consent['api.openai.com']).toBe(false)
  })
})
