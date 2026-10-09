import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { useApi } from '@/api/context'
import { useEngines } from '@/api/hooks'
import type { AudioResult } from '@/api/types'
import { ApiError } from '@/api/types'
import { toast } from '@/components/ui'
import { audioKey, useAudio } from '@/store/audio'
import { useJobs } from '@/store/jobs'
import { useProject } from '@/store/project'
import { useUi } from '@/store/ui'
import { useVoice } from '@/store/voice'

/** Which engine speaks: the one the person picked, or what "automatic" resolves to (never an online one). */
export function useChosenEngine() {
  const engines = useEngines()
  const pref = useUi((s) => s.ttsEngine)
  const name = pref === 'auto' ? (engines.data?.default ?? 'babble') : pref
  return { name, engine: engines.data?.engines.find((e) => e.name === name), engines: engines.data }
}

/**
 * Makes the soundtrack of the open project (voice, music, effects) as a job and keeps it in the audio store.
 * An online voice that would bill anything asks first (`useVoice`); one that has everything cached needs no question.
 * Resolves with the result, or null when it was declined, failed or cancelled.
 */
export function useGenerateVoice() {
  const api = useApi()
  const qc = useQueryClient()
  const { name, engine } = useChosenEngine()
  const track = useJobs((s) => s.track)
  const ask = useVoice((s) => s.ask)
  const [jobId, setJobId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined))
  const running = starting || (!!job && (job.status === 'running' || job.status === 'queued'))

  const run = useCallback(
    async (opts: { quiet?: boolean } = {}): Promise<AudioResult | null> => {
      const { spec, id: projectId } = useProject.getState()
      if (!spec || !projectId) return null
      if (engine && !engine.available) {
        toast.error(`${engine.label} is not ready`, engine.detail)
        return null
      }
      if (engine?.online) {
        const est = await api.estimateSpeech(spec, name)
        const chars = est.billable_characters ?? 0
        if (chars > 0 && !(await ask({ host: engine.destination ?? '', chars, what: 'generate the voice' }))) return null
      }
      const key = audioKey(spec) // the soundtrack is for the spec as it is now, even if it is edited while this runs
      setStarting(true)
      try {
        const { job_id } = await api.prepareAudio(spec, name)
        setJobId(job_id)
        return await new Promise<AudioResult | null>((resolve) => {
          track(
            api,
            job_id,
            { kind: 'audio', title: 'Voice and sound', projectId },
            {
              onDone: (r) => {
                const result = r as unknown as AudioResult
                useAudio.getState().set(projectId, result, key, name)
                void qc.invalidateQueries({ queryKey: ['estimate'] })
                if (!opts.quiet) toast.success('Voice and sound are ready', 'Waveforms are on the timeline now.')
                resolve(result)
              },
              onError: (e) => {
                toast.error('Voice generation failed', e.message)
                resolve(null)
              },
              onCancel: () => resolve(null),
            },
          )
        })
      } catch (e) {
        toast.error('Could not start', e instanceof ApiError ? `${e.detail}${e.hint ? ` — ${e.hint}` : ''}` : String(e))
        return null
      } finally {
        setStarting(false)
      }
    },
    [api, ask, engine, name, qc, track],
  )

  return { run, running, job }
}
