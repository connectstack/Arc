import { create } from 'zustand'
import type { Clipboard } from '@/features/studio/clips'

/** What Copy and Cut put aside for Paste. Kept in memory (not saved): it follows you from one project to another within a session. */
interface ClipboardState {
  board: Clipboard | null
  set: (board: Clipboard | null) => void
}

export const useClipboard = create<ClipboardState>((set) => ({ board: null, set: (board) => set({ board }) }))
