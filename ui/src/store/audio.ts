import { create } from 'zustand'
import type { AudioResult } from '@/api/types'

/** The latest soundtrack prepared for the open project (kept while you edit, so the timeline can draw its waveforms). */
interface AudioState {
  projectId: string | null
  result: AudioResult | null
  /** a fingerprint of the spec it was made for, to tell the user when the picture has moved on */
  specKey: string | null
  /** the speech engine that spoke it (a different engine makes it a different soundtrack) */
  engine: string | null
  set: (projectId: string, result: AudioResult, specKey: string, engine?: string) => void
  clear: () => void
}

export const useAudio = create<AudioState>((set) => ({
  projectId: null,
  result: null,
  specKey: null,
  engine: null,
  set: (projectId, result, specKey, engine) => set({ projectId, result, specKey, engine: engine ?? null }),
  clear: () => set({ projectId: null, result: null, specKey: null, engine: null }),
}))

/** What the soundtrack depends on: captions, speakers, voices, sfx, music (and, with automatic effects, the actions). */
export function audioKey(spec: import('@/api/types').ReelSpec): string {
  return JSON.stringify([
    spec.audio,
    spec.characters.map((c) => [c.id, c.voice, c.name, c.archetype]),
    spec.scenes.map((s) => [s.duration_sec, s.transition_out, s.captions, s.sfx, spec.audio.auto_sfx ? s.layers.map((l) => l.actions.map((a) => [a.name, a.t0, a.t1])) : null]),
  ])
}

/** Is `result` the soundtrack of this spec, spoken by this engine? */
export function isFresh(spec: import('@/api/types').ReelSpec | null, state: Pick<AudioState, 'result' | 'specKey' | 'engine'>, engine: string): boolean {
  return !!spec && !!state.result && state.specKey === audioKey(spec) && (state.engine === null || state.engine === engine)
}
