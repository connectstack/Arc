import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMockApi } from '@/api/mock'
import { applyRetimed } from '@/features/audio/retime'
import { renderScreen } from '@/test/harness'
import { audioKey, useAudio } from '@/store/audio'
import { useJobs } from '@/store/jobs'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { useUi } from '@/store/ui'
import { useVoicePreview } from './useVoicePreview'

function Probe() {
  const v = useVoicePreview()
  return (
    <>
      <button onClick={() => void v.ensure()}>make the voice</button>
      <output aria-label="state">{`fresh:${v.fresh} armed:${v.armed} sound:${v.sound}`}</output>
    </>
  )
}

async function open(withKey = false) {
  window.history.replaceState({}, '', withKey ? '/?mock=eleven' : '/')
  const api = await createMockApi()
  useProject.getState().load(await api.getProject('story-50s'))
  return api
}

beforeEach(() => {
  useProject.getState().unload()
  useAudio.getState().clear()
  useJobs.setState({ jobs: {}, order: [] })
  useStudio.setState({ playing: false })
  useUi.setState({ consent: {}, ttsEngine: 'auto', previewSound: true })
})

describe('playing the studio preview with the voice', () => {
  it('makes the soundtrack once, and again only when the words changed', async () => {
    const user = userEvent.setup()
    const api = await open()
    const prepare = vi.spyOn(api, 'prepareAudio')
    await renderScreen(<Probe />, { api })

    expect(screen.getByLabelText('state')).toHaveTextContent('fresh:false')
    await user.click(screen.getByRole('button', { name: /make the voice/i }))
    await waitFor(() => expect(screen.getByLabelText('state')).toHaveTextContent('fresh:true armed:true'), { timeout: 8000 })
    expect(prepare).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /make the voice/i }))
    expect(prepare).toHaveBeenCalledTimes(1) // nothing changed: the same soundtrack plays

    useProject.getState().edit((d) => void (d.scenes[0].captions[0].text = 'Something else is said now.'))
    await waitFor(() => expect(screen.getByLabelText('state')).toHaveTextContent('fresh:false'))
    await user.click(screen.getByRole('button', { name: /make the voice/i }))
    await waitFor(() => expect(prepare).toHaveBeenCalledTimes(2), { timeout: 8000 })
  })

  it('does nothing when the sound is off', async () => {
    const user = userEvent.setup()
    const api = await open()
    useUi.setState({ previewSound: false })
    const prepare = vi.spyOn(api, 'prepareAudio')
    await renderScreen(<Probe />, { api })
    await user.click(screen.getByRole('button', { name: /make the voice/i }))
    expect(prepare).not.toHaveBeenCalled()
  })

  it('asks before an online voice bills anything, and plays silently when the answer is no', async () => {
    const user = userEvent.setup()
    const api = await open(true)
    useUi.setState({ ttsEngine: 'elevenlabs' })
    const prepare = vi.spyOn(api, 'prepareAudio')
    await renderScreen(<Probe />, { api, withKey: true })

    await user.click(screen.getByRole('button', { name: /make the voice/i }))
    expect(await screen.findByText(/send text to api\.elevenlabs\.io\?/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^cancel$/i }))
    expect(prepare).not.toHaveBeenCalled()
    expect(await screen.findByText(/playing without the voice/i)).toBeInTheDocument()
  })

  it('a soundtrack for a reel with nothing to hear is not asked for', async () => {
    const user = userEvent.setup()
    const api = await open()
    useProject.getState().edit((d) => {
      d.audio.voiceover = 'none'
      d.audio.music = null
      for (const s of d.scenes) s.sfx = []
    })
    const prepare = vi.spyOn(api, 'prepareAudio')
    await renderScreen(<Probe />, { api })
    await user.click(screen.getByRole('button', { name: /make the voice/i }))
    expect(prepare).not.toHaveBeenCalled()
  })
})

describe('captions retimed to the voice', () => {
  it('apply the windows the voice was timed to, and the soundtrack stays current', async () => {
    const api = await open()
    const spec = useProject.getState().spec!
    const result = {
      wav_url: '/api/audio/x.wav',
      spec,
      retimed: [{ scene: 0, caption: 0, t0: 0.9, t1: 3.3, was_t0: spec.scenes[0].captions[0].t0, was_t1: spec.scenes[0].captions[0].t1 }],
      word_timings: {},
      warnings: [],
      notes: [],
      report: null,
      peaks: { voice: [], music: [], sfx: [] },
      total_sec: 50,
    }
    useAudio.getState().set('story-50s', result, audioKey(spec), 'say')
    await renderScreen(<Probe />, { api })
    applyRetimed(result)
    const c = useProject.getState().spec!.scenes[0].captions[0]
    expect([c.t0, c.t1]).toEqual([0.9, 3.3])
    expect(useAudio.getState().specKey).toBe(audioKey(useProject.getState().spec!))
  })
})
