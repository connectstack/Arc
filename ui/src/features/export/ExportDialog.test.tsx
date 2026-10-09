import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMockApi } from '@/api/mock'
import { renderScreen } from '@/test/harness'
import { useJobs } from '@/store/jobs'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { useUi } from '@/store/ui'
import { ExportDialog } from './ExportDialog'

async function projectApi(withKey = false) {
  window.history.replaceState({}, '', withKey ? '/?mock=eleven' : '/')
  const api = await createMockApi()
  useProject.getState().load(await api.getProject('story-50s'))
  return api
}

beforeEach(() => {
  useProject.getState().unload()
  useJobs.setState({ jobs: {}, order: [] })
  useStudio.setState({ exportOpen: true, exportPreset: 'draft', exportShowResult: false })
  useUi.setState({ consent: {}, ttsEngine: 'auto' })
})

describe('the export dialog', () => {
  it('plans the render, renders a draft and shows the finished video', async () => {
    const user = userEvent.setup()
    const api = await projectApi()
    await renderScreen(<ExportDialog />, { api, route: '/p/story-50s', path: '/p/:id' })

    expect(await screen.findByText(/chunks are already cached/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /render draft/i }))

    expect(await screen.findByText(/your video is ready/i, {}, { timeout: 8000 })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /download mp4/i })).toHaveAttribute('download')
    expect(screen.getByLabelText(/rendered video/i)).toBeInTheDocument()
  })

  it('says so when the voice is not generated yet, because its retiming changes the chunks', async () => {
    const api = await projectApi()
    const plan = await api.renderPlan({ preset: 'draft' })
    vi.spyOn(api, 'renderPlan').mockResolvedValue({ ...plan, voice: 'pending' })
    await renderScreen(<ExportDialog />, { api, route: '/p/story-50s', path: '/p/:id' })
    expect(await screen.findByText(/the voice is not generated yet/i)).toBeInTheDocument()
  })

  it('never bills an online voice without an explicit confirmation', async () => {
    const user = userEvent.setup()
    const api = await projectApi(true)
    useUi.setState({ ttsEngine: 'elevenlabs' })
    const render = vi.spyOn(api, 'render')
    await renderScreen(<ExportDialog />, { api, route: '/p/story-50s', path: '/p/:id', withKey: true })

    // the cost is stated before anything can be clicked
    expect(await screen.findByText(/will bill [\d,]+ characters/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /render draft/i }))
    expect(await screen.findByText(/send text to api\.elevenlabs\.io/i)).toBeInTheDocument()
    expect(render).not.toHaveBeenCalled()

    // saying no sends nothing
    await user.click(screen.getByRole('button', { name: /^cancel$/i }))
    await user.click(screen.getByRole('button', { name: /render draft/i }))
    await user.click(await screen.findByRole('button', { name: /yes, render and bill/i }))

    await waitFor(() => expect(render).toHaveBeenCalledTimes(1))
    expect(useUi.getState().consent['api.elevenlabs.io']).toBe(true)
  })

  it('asks only once per service: a remembered consent renders straight away', async () => {
    const user = userEvent.setup()
    const api = await projectApi(true)
    useUi.setState({ ttsEngine: 'elevenlabs', consent: { 'api.elevenlabs.io': true } })
    const render = vi.spyOn(api, 'render')
    await renderScreen(<ExportDialog />, { api, route: '/p/story-50s', path: '/p/:id', withKey: true })

    await screen.findByText(/will bill [\d,]+ characters/i)
    await user.click(screen.getByRole('button', { name: /render draft/i }))
    await waitFor(() => expect(render).toHaveBeenCalledTimes(1))
    expect(screen.queryByText(/send text to api\.elevenlabs\.io/i)).not.toBeInTheDocument()
  })

  it('blocks a full render while the spec has errors, but a draft always goes ahead', async () => {
    const user = userEvent.setup()
    const api = await projectApi()
    // an unknown background is an error the engine would refuse for a real render
    useProject.getState().edit((d) => void (d.scenes[0].background.template = 'nowhere'))
    const { LintRunner } = await import('@/store/lint')
    await renderScreen(
      <>
        <LintRunner />
        <ExportDialog />
      </>,
      { api, route: '/p/story-50s', path: '/p/:id' },
    )
    await user.click(await screen.findByRole('radio', { name: /full hd/i }))
    expect(await screen.findByText(/the spec has \d+ errors?/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /render full hd/i })).toBeDisabled()

    await user.click(screen.getByRole('radio', { name: /draft/i }))
    expect(screen.getByRole('button', { name: /render draft/i })).toBeEnabled()
  })
})
