import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type ThemePref = 'system' | 'dark' | 'light'
export type Density = 'comfortable' | 'compact'

interface UiState {
  theme: ThemePref
  density: Density
  leftW: number
  rightW: number
  timelineH: number
  leftOpen: boolean
  rightOpen: boolean
  overlays: { safe: boolean; grid: boolean; handles: boolean }
  /** providers the user agreed to send data to, by host (api.openai.com ...) */
  consent: Record<string, boolean>
  defaultStyle: string
  defaultPlanner: string
  lastProject: string | null
  /** which speech engine renders voice-over and auditions: 'auto' or an engine name */
  ttsEngine: string
  /** the studio's Play speaks the voice-over and plays the music and effects (made on the first play) */
  previewSound: boolean
  setTheme: (t: ThemePref) => void
  setDensity: (d: Density) => void
  set: (patch: Partial<Omit<UiState, 'set' | 'setTheme' | 'setDensity' | 'setConsent'>>) => void
  setConsent: (host: string, ok: boolean) => void
}

export function applyTheme(pref: ThemePref): void {
  const dark = pref === 'dark' || (pref === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light')
}

export const useUi = create<UiState>()(
  persist(
    (set) => ({
      theme: 'system',
      density: 'comfortable',
      leftW: 280,
      rightW: 340,
      timelineH: 330,
      leftOpen: true,
      rightOpen: true,
      overlays: { safe: false, grid: false, handles: true },
      consent: {},
      defaultStyle: 'paper_cutout',
      defaultPlanner: 'offline',
      lastProject: null,
      ttsEngine: 'auto',
      previewSound: true,
      setTheme: (theme) => {
        try {
          localStorage.setItem('reel.theme', theme)
        } catch {
          /* storage unavailable: the choice lasts for this session only */
        }
        applyTheme(theme)
        set({ theme })
      },
      setDensity: (density) => {
        try {
          localStorage.setItem('reel.density', density)
        } catch {
          /* ignore */
        }
        document.documentElement.setAttribute('data-density', density)
        set({ density })
      },
      set: (patch) => set(patch),
      setConsent: (host, ok) => set((s) => ({ consent: { ...s.consent, [host]: ok } })),
    }),
    {
      name: 'reel.ui',
      // UI preferences only: projects live in the workspace folder, never in the browser
      partialize: (s) => ({ theme: s.theme, density: s.density, leftW: s.leftW, rightW: s.rightW, timelineH: s.timelineH, leftOpen: s.leftOpen, rightOpen: s.rightOpen, overlays: s.overlays, consent: s.consent, defaultStyle: s.defaultStyle, defaultPlanner: s.defaultPlanner, lastProject: s.lastProject, ttsEngine: s.ttsEngine, previewSound: s.previewSound }),
    },
  ),
)
