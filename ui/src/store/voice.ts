import { create } from 'zustand'

/** What an online voice is about to do, for the person to agree to (or not) before anything is sent. */
export interface VoiceAsk {
  host: string
  /** characters the provider will bill for this */
  chars: number
  /** "generate the voice", "audition Mia" ... (finishes the sentence on the confirm button) */
  what: string
}

interface VoiceState {
  asking: (VoiceAsk & { resolve: (ok: boolean) => void }) | null
  /** Opens the confirmation (mounted once in the shell); resolves with the answer. A second question cancels the first. */
  ask: (a: VoiceAsk) => Promise<boolean>
  answer: (ok: boolean) => void
}

export const useVoice = create<VoiceState>((set, get) => ({
  asking: null,
  ask: (a) =>
    new Promise<boolean>((resolve) => {
      get().asking?.resolve(false)
      set({ asking: { ...a, resolve } })
    }),
  answer: (ok) => {
    const cur = get().asking
    set({ asking: null })
    cur?.resolve(ok)
  },
}))
