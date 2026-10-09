import { useCallback, useEffect, useMemo, useState } from 'react'
import { useApi } from '@/api/context'
import type { AudioResult, ReelSpec } from '@/api/types'
import { toast } from '@/components/ui'
import { applyRetimed } from '@/features/audio/retime'
import { useChosenEngine, useGenerateVoice } from '@/features/audio/useGenerateVoice'
import { clamp } from '@/lib/timeline'
import { isFresh, useAudio } from '@/store/audio'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { useUi } from '@/store/ui'

/** Does this spec have anything to hear (a voice-over, music or a sound effect)? */
export function hasSound(spec: ReelSpec | null): boolean {
  return !!spec && (spec.audio.voiceover !== 'none' || !!spec.audio.music || spec.scenes.some((s) => s.sfx.length > 0))
}

/**
 * The studio's playback with sound: the soundtrack (voice, music, effects) of the open project is made the first time you
 * press Play (and again after the words changed), then played in step with the picture, which follows the audio clock.
 * The preview shows the same retimed captions and lip-sync the final render will.
 */
export function useVoicePreview() {
  const api = useApi()
  const projectId = useProject((s) => s.id)
  const spec = useProject((s) => s.spec)
  const audio = useAudio()
  const sound = useUi((s) => s.previewSound)
  const setUi = useUi((s) => s.set)
  const playing = useStudio((s) => s.playing)
  const { name } = useChosenEngine()
  const { run, running: preparing } = useGenerateVoice()
  const el = useMemo(() => {
    const a = new Audio()
    a.preload = 'auto'
    return a
  }, [])
  const [blocked, setBlocked] = useState(false)

  const fresh = audio.projectId === projectId && isFresh(spec, audio, name)
  const wav = fresh ? audio.result?.wav_url : null
  const url = wav ? api.audioUrl(wav) : null
  const armed = sound && !!url && !blocked

  useEffect(() => {
    if (url) {
      if (!el.src.endsWith(url)) {
        el.src = url
        el.load()
      }
    } else {
      el.pause()
      el.removeAttribute('src')
      el.load()
    }
  }, [url, el])
  useEffect(
    () => () => {
      el.pause()
      el.removeAttribute('src')
    },
    [el],
  )

  // start and stop with the transport
  useEffect(() => {
    if (!playing) {
      setBlocked(false)
      return
    }
    if (!armed) return
    const at = useProject.getState().playhead
    el.currentTime = Number.isFinite(el.duration) ? clamp(at, 0, el.duration) : at
    el.play().catch(() => setBlocked(true)) // the browser may refuse sound it was not asked for: carry on silently
    return () => el.pause()
  }, [playing, armed, el])

  // moving the playhead while it plays (a click on the ruler, a scene jump) moves the sound too
  useEffect(() => {
    if (!playing || !armed) return
    return useProject.subscribe((s, prev) => {
      if (s.playhead !== prev.playhead && Math.abs(s.playhead - el.currentTime) > 0.3) el.currentTime = s.playhead
    })
  }, [playing, armed, el])

  /** Makes the soundtrack if it is missing or out of date; resolves when Play may start. */
  const ensure = useCallback(async (): Promise<void> => {
    const now = useProject.getState().spec
    if (!useUi.getState().previewSound || !hasSound(now)) return
    const state = useAudio.getState()
    if (state.projectId === useProject.getState().id && isFresh(now, state, name)) return
    const made: AudioResult | null = await run({ quiet: true })
    if (!made) {
      toast.info('Playing without the voice', 'The soundtrack was not made. Turn Sound off to hide this.')
      return
    }
    if (made.retimed.length > 0) {
      toast.action('info', 'The captions now follow the voice', { label: 'Apply to the timeline', onClick: () => applyRetimed(made) }, `${made.retimed.length} caption(s) are timed to the speech in this preview.`)
    }
  }, [name, run])

  return {
    el,
    sound,
    toggleSound: () => setUi({ previewSound: !sound }),
    /** the sound is playing, or about to: the playhead follows its clock */
    armed,
    fresh,
    preparing,
    ensure,
    result: fresh ? audio.result : null,
  }
}
