import { MOD } from './hotkeys'

export interface Shortcut {
  /** the keys, one chip each ("⌘", "Z") */
  keys: string[]
  what: string
}

export interface ShortcutGroup {
  title: string
  items: Shortcut[]
}

/** Every keyboard shortcut the app answers to, for the `?` dialog. Keep in step with the bindings (useStudioHotkeys, the preview transport, the timeline). */
export const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    title: 'Playback',
    items: [
      { keys: ['Space'], what: 'Play or pause (the voice plays with it when sound is on)' },
      { keys: ['←', '→'], what: 'Back or forward one frame' },
      { keys: ['⇧', '← →'], what: 'Back or forward one second' },
      { keys: ['↑', '↓'], what: 'Previous or next scene' },
      { keys: ['Home', 'End'], what: 'Jump to the start or the end' },
      { keys: ['[', ']'], what: 'Set the loop’s In and Out points at the playhead' },
    ],
  },
  {
    title: 'Editing',
    items: [
      { keys: [MOD, 'Z'], what: 'Undo' },
      { keys: ['⇧', MOD, 'Z'], what: 'Redo' },
      { keys: [MOD, 'S'], what: 'Save now (it also saves by itself)' },
      { keys: [MOD, 'C'], what: 'Copy the selected clips' },
      { keys: [MOD, 'X'], what: 'Cut the selected clips' },
      { keys: [MOD, 'V'], what: 'Paste at the playhead (into the selected character’s lane)' },
      { keys: [MOD, 'D'], what: 'Duplicate the selection' },
      { keys: ['S'], what: 'Split the selected clip at the playhead' },
      { keys: ['C'], what: 'Add a caption at the playhead' },
      { keys: ['Delete'], what: 'Delete the selection' },
      { keys: ['Esc'], what: 'Close the problems list, or deselect' },
    ],
  },
  {
    title: 'Timeline',
    items: [
      { keys: ['Click'], what: 'Select a clip (Enter opens it in the inspector)' },
      { keys: ['⇧', 'Click'], what: 'Add a clip to the selection, or take it out' },
      { keys: ['Drag'], what: 'Move a clip, or the whole selection together' },
      { keys: ['Alt', 'Drag'], what: 'Move without snapping' },
      { keys: ['← →'], what: 'Nudge the focused clip by 0.1 s (⇧ for 1 s)' },
      { keys: ['Alt', '← →'], what: 'Nudge the selection by one frame' },
      { keys: [MOD, 'Wheel'], what: 'Zoom the timeline' },
    ],
  },
  {
    title: 'Everywhere',
    items: [
      { keys: [MOD, 'K'], what: 'Command palette' },
      { keys: [MOD, '↵'], what: 'Render a draft' },
      { keys: [MOD, '\\'], what: 'Hide or show both side panels' },
      { keys: ['?'], what: 'This list' },
    ],
  },
]
