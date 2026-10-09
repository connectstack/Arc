// What the keyboard, the menus and the palette do to the selection (Copy, Cut, Paste, Duplicate, Delete, Nudge): one place,
// so every route to an edit treats one clip and several clips the same way.
import { toast } from '@/components/ui'
import { MOD } from '@/lib/hotkeys'
import { sameSelection, type Selection } from '@/lib/spec'
import { useClipboard } from '@/store/clipboard'
import { useProject } from '@/store/project'
import { copyClips, deleteMany, describeClipboard, duplicateMany, nudgeMany, pasteClips, pasteTarget, selectedClips } from './clips'
import { deleteSelection, duplicateSelection, nudge } from './ops'

/** Make `sel` the selection unless it is already part of one (so a right-click on a selected clip keeps the group). */
export function ensureSelected(sel: Selection): void {
  const st = useProject.getState()
  if (!selectedClips(st.selection, st.extra).some((s) => sameSelection(s, sel))) st.select(sel)
}

export function duplicateSelected(): void {
  const st = useProject.getState()
  const sels = selectedClips(st.selection, st.extra)
  if (sels.length > 1) {
    st.edit((d) => {
      const copies = duplicateMany(d, sels)
      useProject.setState({ selection: copies[0], extra: copies.slice(1) })
    })
  } else st.edit((d) => st.select(duplicateSelection(d, st.selection)))
}

export function deleteSelected(): void {
  const st = useProject.getState()
  const sels = selectedClips(st.selection, st.extra)
  if (sels.length > 1) st.edit((d) => st.select(deleteMany(d, sels)))
  else if (st.selection.kind !== 'reel') st.edit((d) => st.select(deleteSelection(d, st.selection)))
}

/** Move the selected clips by `dt` seconds (each stays inside its scene). */
export function nudgeSelected(dt: number): void {
  const st = useProject.getState()
  const sels = selectedClips(st.selection, st.extra)
  if (sels.length > 1) st.edit((d) => nudgeMany(d, sels, dt))
  else st.edit((d) => nudge(d, st.selection, dt))
}

let toldHow = false

/** Copy the selected clips (and with `cut`, take them out). Returns false when no clip is selected. */
export function copySelection(cut = false): boolean {
  const st = useProject.getState()
  if (!st.spec) return false
  const board = copyClips(st.spec, selectedClips(st.selection, st.extra))
  if (!board) return false
  useClipboard.getState().set(board)
  const what = describeClipboard(board)
  if (cut) {
    const sels = selectedClips(st.selection, st.extra)
    st.edit((d) => st.select(deleteMany(d, sels)))
  }
  toast.info(`${cut ? 'Cut' : 'Copied'} ${what}`, toldHow ? undefined : `Put the playhead where you want ${board.items.length === 1 ? 'it' : 'them'}, click a character’s name to paste onto them, and press ${MOD}V.`)
  toldHow = true
  return true
}

/** Paste at the playhead. `character` aims actions at a lane; without it the selected character (or the clip's own) is used. Returns false when nothing was pasted. */
export function pasteAtPlayhead(character?: string): boolean {
  const st = useProject.getState()
  const board = useClipboard.getState().board
  if (!st.spec) return false
  if (!board) {
    toast.info('Nothing to paste yet', `Select a clip, press ${MOD}C, then move the playhead and press ${MOD}V.`)
    return false
  }
  const who = character ?? pasteTarget(st.spec, st.selection)
  let pasted: ReturnType<typeof pasteClips> = null
  st.edit((d) => {
    pasted = pasteClips(d, board, st.playhead, who)
  })
  const got = pasted as ReturnType<typeof pasteClips>
  if (!got) {
    toast.warning('Could not paste here', 'The character is no longer in the cast, or the playhead is outside every scene.')
    return false
  }
  // the first pasted clip is the one the inspector shows; the rest join the selection
  useProject.setState({ selection: got[0], extra: got.slice(1) })
  return true
}
