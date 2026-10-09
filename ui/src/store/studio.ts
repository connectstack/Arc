import { create } from 'zustand'

export type LeftTab = 'scenes' | 'cast' | 'script' | 'library'
export type RightTab = 'inspector' | 'json'

/** Studio-only view state (nothing here is saved): what is open, the timeline zoom, the transport. */
interface StudioState {
  leftTab: LeftTab
  rightTab: RightTab
  problemsOpen: boolean
  exportOpen: boolean
  exportPreset: 'draft' | 'standard' | 'full' | 'custom'
  /** open the export dialog on the finished video (set by the "Render finished" notification) */
  exportShowResult: boolean
  paletteOpen: boolean
  /** the `?` list of keyboard shortcuts */
  shortcutsOpen: boolean
  /** below 1280 px the side panels are drawers: which one is open */
  drawer: 'left' | 'right' | null
  /** timeline pixels per second */
  zoom: number
  playing: boolean
  loop: [number, number] | null
  /** lanes the user collapsed */
  collapsed: Record<string, boolean>
  set: (patch: Partial<Omit<StudioState, 'set'>>) => void
}

export const useStudio = create<StudioState>((set) => ({
  leftTab: 'scenes',
  rightTab: 'inspector',
  problemsOpen: false,
  exportOpen: false,
  exportPreset: 'draft',
  exportShowResult: false,
  paletteOpen: false,
  shortcutsOpen: false,
  drawer: null,
  zoom: 22,
  playing: false,
  loop: null,
  collapsed: {},
  set: (patch) => set(patch),
}))
