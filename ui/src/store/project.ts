import { produce } from 'immer'
import { create } from 'zustand'
import type { ProjectDoc, ReelSpec } from '@/api/types'
import { normalizeSpec } from '@/lib/normalize'
import { sameSelection, selectionExists, type Selection } from '@/lib/spec'

export type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'error' | 'conflict'

const HISTORY_LIMIT = 200

/** What stays selected when the spec changes under the selection (undo, redo, a jump in the history): nothing, if it is gone. */
const keep = (spec: ReelSpec, sel: Selection): Selection => (selectionExists(spec, sel) ? sel : { kind: 'reel' })

interface ProjectState {
  id: string | null
  spec: ReelSpec | null
  script: string
  etag: string | null
  /** what is on disk (for dirty detection and the JSON diff) */
  savedSpec: ReelSpec | null
  past: ReelSpec[]
  future: ReelSpec[]
  gesture: ReelSpec | null
  save: SaveState
  saveError: string | null
  conflict: ProjectDoc | null
  selection: Selection
  /** more clips selected with Shift+click: the others of a multiple selection (`selection` is the one the inspector shows) */
  extra: Selection[]
  /** global seconds */
  playhead: number

  load: (doc: ProjectDoc) => void
  unload: () => void
  /** Apply an edit. `live` edits (during a drag) change the spec without making an undo step each. */
  edit: (mutator: (draft: ReelSpec) => void, opts?: { live?: boolean }) => void
  /** Replace the whole spec as one undo step (generate, fit to length, retimed captions ...). */
  replace: (spec: ReelSpec) => void
  beginGesture: () => void
  endGesture: () => void
  undo: () => void
  redo: () => void
  /** Go to a point of the undo history: 0 is the oldest state remembered, `past.length` the present, beyond it the undone ones. */
  jumpTo: (index: number) => void
  setScript: (script: string) => void
  select: (sel: Selection) => void
  /** Shift+click: add a clip to the selection, or take it out of it again. */
  toggleSelect: (sel: Selection) => void
  setPlayhead: (t: number) => void
  setSaving: () => void
  markSaved: (doc: ProjectDoc) => void
  setSaveError: (message: string) => void
  setConflict: (doc: ProjectDoc | null) => void
  /** Resolve a conflict by keeping the disk version (dropping local edits) or the local one (taking the new etag). */
  resolveConflict: (keep: 'disk' | 'mine') => void
}

export const useProject = create<ProjectState>((set, get) => ({
  id: null,
  spec: null,
  script: '',
  etag: null,
  savedSpec: null,
  past: [],
  future: [],
  gesture: null,
  save: 'idle',
  saveError: null,
  conflict: null,
  selection: { kind: 'reel' },
  extra: [],
  playhead: 0,

  load: (doc) => {
    const spec = normalizeSpec(doc.spec) // files may leave out everything that has a default
    set({
      id: doc.id,
      spec,
      script: doc.script,
      etag: doc.etag,
      savedSpec: spec,
      past: [],
      future: [],
      gesture: null,
      save: 'saved',
      saveError: null,
      conflict: null,
      selection: { kind: 'reel' },
      extra: [],
      playhead: 0,
    })
  },
  unload: () => set({ id: null, spec: null, script: '', etag: null, savedSpec: null, past: [], future: [], gesture: null, save: 'idle', conflict: null, extra: [] }),

  edit: (mutator, opts) => {
    const { spec, past } = get()
    if (!spec) return
    const next = produce(spec, mutator)
    if (next === spec) return
    if (opts?.live) set({ spec: next, save: 'dirty' })
    else set({ spec: next, past: [...past.slice(-(HISTORY_LIMIT - 1)), spec], future: [], save: 'dirty' })
  },
  replace: (spec) => {
    const { spec: cur, past } = get()
    set({ spec, past: cur ? [...past.slice(-(HISTORY_LIMIT - 1)), cur] : past, future: [], save: 'dirty', extra: [], selection: keep(spec, get().selection) })
  },
  beginGesture: () => set({ gesture: get().spec }),
  endGesture: () => {
    const { gesture, spec, past } = get()
    if (gesture && spec && gesture !== spec) set({ past: [...past.slice(-(HISTORY_LIMIT - 1)), gesture], future: [], gesture: null })
    else set({ gesture: null })
  },
  undo: () => {
    const { past, spec, future } = get()
    if (!past.length || !spec) return
    const back = past[past.length - 1]
    set({ spec: back, past: past.slice(0, -1), future: [spec, ...future].slice(0, HISTORY_LIMIT), save: 'dirty', extra: [], selection: keep(back, get().selection) })
  },
  redo: () => {
    const { future, spec, past } = get()
    if (!future.length || !spec) return
    set({ spec: future[0], future: future.slice(1), past: [...past, spec], save: 'dirty', extra: [], selection: keep(future[0], get().selection) })
  },
  jumpTo: (index) => {
    const { past, spec, future } = get()
    if (!spec) return
    const all = [...past, spec, ...future]
    if (index < 0 || index >= all.length || index === past.length) return
    set({ spec: all[index], past: all.slice(0, index), future: all.slice(index + 1), save: 'dirty', extra: [], selection: keep(all[index], get().selection) })
  },
  setScript: (script) => set({ script, save: 'dirty' }),
  select: (selection) => set({ selection, extra: [] }),
  toggleSelect: (sel) => {
    const { selection, extra } = get()
    // the timeline's clips and an object's motions can be selected together; an object (the thing itself) is always on its own
    const clip = (x: Selection) => x.kind === 'action' || x.kind === 'caption' || x.kind === 'camera' || x.kind === 'sfx' || x.kind === 'motion'
    if (!clip(sel)) return set({ selection: sel, extra: [] })
    if (!clip(selection)) return set({ selection: sel, extra: [] })
    if (sameSelection(selection, sel)) {
      // taking the main one out: the next one in line takes over
      if (extra.length) set({ selection: extra[0], extra: extra.slice(1) })
      return
    }
    if (extra.some((x) => sameSelection(x, sel))) return set({ extra: extra.filter((x) => !sameSelection(x, sel)) })
    set({ extra: [...extra, sel] })
  },
  setPlayhead: (playhead) => set({ playhead }),
  setSaving: () => set({ save: 'saving' }),
  markSaved: (doc) => {
    const { spec } = get()
    const onDisk = normalizeSpec(doc.spec)
    // edits made while the save was in flight stay dirty: only what was sent is now on disk
    set({ etag: doc.etag, savedSpec: onDisk, save: spec === onDisk || JSON.stringify(spec) === JSON.stringify(onDisk) ? 'saved' : 'dirty', saveError: null, conflict: null })
  },
  setSaveError: (message) => set({ save: 'error', saveError: message }),
  setConflict: (conflict) => set({ conflict, save: conflict ? 'conflict' : get().save }),
  resolveConflict: (keep) => {
    const { conflict } = get()
    if (!conflict) return
    if (keep === 'disk') {
      const disk = normalizeSpec(conflict.spec)
      set({ spec: disk, script: conflict.script, etag: conflict.etag, savedSpec: disk, past: [], future: [], save: 'saved', conflict: null, extra: [] })
    }
    else set({ etag: conflict.etag, conflict: null, save: 'dirty' })
  },
}))

export const useSpec = (): ReelSpec | null => useProject((s) => s.spec)
