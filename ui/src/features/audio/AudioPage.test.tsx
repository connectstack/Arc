import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMockApi } from '@/api/mock'
import { renderScreen } from '@/test/harness'
import { useAudio } from '@/store/audio'
import { useJobs } from '@/store/jobs'
import { useProject } from '@/store/project'
import { useUi } from '@/store/ui'
import { AudioPage } from './AudioPage'

async function projectApi(withKey: boolean) {
  window.history.replaceState({}, '', withKey ? '/?mock=eleven' : '/')
  const api = await createMockApi()
  useProject.getState().load(await api.getProject('story-50s'))
  return api
}

beforeEach(() => {
  useProject.getState().unload()
  useAudio.getState().clear?.()
  useJobs.setState({ jobs: {}, order: [] })
  useUi.setState({ consent: {}, ttsEngine: 'auto' })
})

describe('Voice & Audio', () => {
  it('states the cost of an online voice and asks before generating it', async () => {
    const user = userEvent.setup()
    const api = await projectApi(true)
    useUi.setState({ ttsEngine: 'elevenlabs' })
    const prepare = vi.spyOn(api, 'prepareAudio')
    await renderScreen(<AudioPage />, { api, withKey: true })

    expect(await screen.findByText(/characters will be billed by api\.elevenlabs\.io/i)).toBeInTheDocument()
    const generate = await screen.findByRole('button', { name: /generate voice \([\d,]+ characters billed\)/i })

    await user.click(generate)
    expect(await screen.findByText(/send text to api\.elevenlabs\.io\?/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^cancel$/i }))
    expect(prepare).not.toHaveBeenCalled()

    await user.click(generate)
    await user.click(await screen.findByRole('button', { name: /yes, generate the voice/i }))
    await waitFor(() => expect(prepare).toHaveBeenCalledWith(expect.anything(), 'elevenlabs'))
    expect(await screen.findByText(/the soundtrack/i, {}, { timeout: 8000 })).toBeInTheDocument()
  })

  it('an offline voice needs no confirmation and says it costs nothing', async () => {
    const user = userEvent.setup()
    const api = await projectApi(false)
    const prepare = vi.spyOn(api, 'prepareAudio')
    await renderScreen(<AudioPage />, { api })

    expect(await screen.findByText(/runs on your machine: no network, no cost/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /generate voice & sound/i }))
    await waitFor(() => expect(prepare).toHaveBeenCalledTimes(1))
    expect(screen.queryByText(/send text to/i)).not.toBeInTheDocument()
  })

  it('explains what an online engine needs when there is no key, and never asks for one', async () => {
    const api = await projectApi(false)
    await renderScreen(<AudioPage />, { api })
    expect((await screen.findAllByText(/no key found/i)).length).toBeGreaterThan(0)
    expect(screen.getByText(/never asks for the key itself/i)).toBeInTheDocument()
    // there is nowhere on the page to type a key
    expect(screen.queryByLabelText(/api key/i)).not.toBeInTheDocument()
    expect(document.querySelector('input[type="password"]')).toBeNull()
  })
})
