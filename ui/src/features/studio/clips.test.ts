import { produce } from 'immer'
import { describe, expect, it } from 'vitest'
import type { ReelSpec, Scene } from '@/api/types'
import { baseOf, copyClips, deleteMany, describeClipboard, duplicateMany, isClipSelection, nudgeMany, pasteClips, pasteTarget, selectedClips, shiftClips, type ClipSelection } from './clips'

const scene = (id: string): Scene => ({
  id,
  duration_sec: 10,
  background: { template: 'street', params: {} },
  camera: { moves: [{ type: 'pan', from: [0, 0], to: [0.1, 0], t0: 1, t1: 4, ease: 'linear', params: {} }] },
  layers: [
    { character: 'mia', position: 'left', scale: 1, depth: 'mid', facing: 'auto', actions: [{ name: 'walk', t0: 1, t1: 3, params: {} }, { name: 'wave', t0: 4, t1: 5, params: {} }, { name: 'idle', t0: 6, t1: 8, params: {} }] },
    { character: 'pip', position: 'right', scale: 1, depth: 'mid', facing: 'auto', actions: [{ name: 'talk', t0: 2, t1: 4, params: {} }] },
  ],
  captions: [
    { text: 'one', t0: 1, t1: 2, style: 'subtitle', anchor: 'auto', speaker: 'mia' },
    { text: 'two', t0: 3, t1: 4, style: 'subtitle', anchor: 'auto', speaker: 'pip' },
  ],
  sfx: [{ name: 'pop', t: 1, volume: 1 }, { name: 'gasp', t: 5, volume: 1 }],
  transition_out: { type: 'cut', duration: 0, params: {} },
})

const base = (): ReelSpec => ({
  version: '1.0',
  meta: { title: 't', style: 'paper_cutout', fps: 30, resolution: [1080, 1920], seed: 1, target_duration_sec: 50 },
  characters: [
    { id: 'mia', archetype: 'kid', name: 'Mia', props: [], palette: {} },
    { id: 'pip', archetype: 'elder', name: 'Pip', props: [], palette: {} },
    { id: 'bolt', archetype: 'robot', name: 'Bolt', props: [], palette: {} },
  ],
  scenes: [scene('a'), scene('b')],
  audio: { music: null, voiceover: 'tts', ducking: true, music_gain_db: -16, voice_gain_db: 0, sfx_gain_db: -8, auto_sfx: false },
})

const act = (scene: number, layer: number, action: number): ClipSelection => ({ kind: 'action', scene, layer, action })
const names = (s: ReelSpec, scene = 0, layer = 0) => s.scenes[scene].layers[layer].actions.map((a) => a.name)

describe('selecting several clips', () => {
  it('knows which selections are clips, and lists the main one first', () => {
    expect(isClipSelection({ kind: 'scene', scene: 0 })).toBe(false)
    expect(isClipSelection({ kind: 'sfx', scene: 0, sfx: 0 })).toBe(true)
    expect(selectedClips(act(0, 0, 0), [{ kind: 'caption', scene: 0, caption: 1 }, { kind: 'scene', scene: 1 }])).toEqual([act(0, 0, 0), { kind: 'caption', scene: 0, caption: 1 }])
    expect(selectedClips({ kind: 'reel' }, [])).toEqual([])
  })
})

describe('deleting, duplicating and nudging together', () => {
  it('deletes clips from the same list without the indices getting in each other’s way', () => {
    const next = produce(base(), (d) => void deleteMany(d, [act(0, 0, 0), act(0, 0, 2), { kind: 'caption', scene: 0, caption: 0 }, { kind: 'sfx', scene: 0, sfx: 1 }]))
    expect(names(next)).toEqual(['wave'])
    expect(next.scenes[0].captions.map((c) => c.text)).toEqual(['two'])
    expect(next.scenes[0].sfx.map((x) => x.name)).toEqual(['pop'])
    expect(names(next, 0, 1)).toEqual(['talk']) // the other layer is untouched
  })

  it('duplicates every clip next to itself and returns where the copies are, in the order asked', () => {
    let copies: ClipSelection[] = []
    const next = produce(base(), (d) => void (copies = duplicateMany(d, [act(0, 0, 2), act(0, 0, 0), act(0, 1, 0)])))
    expect(names(next)).toEqual(['walk', 'walk', 'wave', 'idle', 'idle'])
    expect(names(next, 0, 1)).toEqual(['talk', 'talk'])
    expect(copies).toEqual([act(0, 0, 4), act(0, 0, 1), act(0, 1, 1)])
    for (const c of copies) expect(isClipSelection(c)).toBe(true)
  })

  it('nudges each clip inside its scene', () => {
    const next = produce(base(), (d) => void nudgeMany(d, [act(0, 0, 0), { kind: 'sfx', scene: 0, sfx: 0 }, { kind: 'caption', scene: 0, caption: 1 }], 0.5))
    expect(next.scenes[0].layers[0].actions[0]).toMatchObject({ t0: 1.5, t1: 3.5 })
    expect(next.scenes[0].sfx[0].t).toBe(1.5)
    expect(next.scenes[0].captions[1]).toMatchObject({ t0: 3.5, t1: 4.5 })
    const edge = produce(base(), (d) => void nudgeMany(d, [act(0, 0, 2)], 9))
    expect(edge.scenes[0].layers[0].actions[2]).toMatchObject({ t0: 8, t1: 10 }) // stops at the end of the scene, keeping its length
  })
})

describe('moving a selection as one', () => {
  it('moves every clip by the same step, each stopping at its own scene edge', () => {
    const sels: ClipSelection[] = [act(0, 0, 0), act(0, 0, 2), { kind: 'sfx', scene: 0, sfx: 1 }]
    const start = base()
    const b = baseOf(start, sels)
    expect(b.map((x) => [x.t0, x.t1])).toEqual([[1, 3], [6, 8], [5, 5]])
    const next = produce(start, (d) => void shiftClips(d, b, 3))
    expect(next.scenes[0].layers[0].actions[0]).toMatchObject({ t0: 4, t1: 6 })
    expect(next.scenes[0].layers[0].actions[2]).toMatchObject({ t0: 8, t1: 10 }) // pinned by the scene end
    expect(next.scenes[0].sfx[1].t).toBe(8)
    // the step is always measured from where the drag began, so applying it again does not add up
    expect(produce(next, (d) => void shiftClips(d, b, 3))).toEqual(next)
    expect(produce(start, (d) => void shiftClips(d, b, -2)).scenes[0].layers[0].actions[0]).toMatchObject({ t0: 0, t1: 2 })
  })
})

describe('copy and paste', () => {
  it('copies deep, so later edits do not reach the clipboard', () => {
    const start = base()
    const board = copyClips(start, [act(0, 0, 0)])
    expect(board?.items).toHaveLength(1)
    const edited = produce(start, (d) => void (d.scenes[0].layers[0].actions[0].name = 'run'))
    expect(board?.items[0]).toMatchObject({ kind: 'action', character: 'mia', clip: { name: 'walk' } })
    expect(edited.scenes[0].layers[0].actions[0].name).toBe('run')
    expect(copyClips(start, [])).toBeNull()
    expect(copyClips(start, [act(9, 9, 9)])).toBeNull()
  })

  it('pastes an action at the playhead onto another character, keeping its length', () => {
    const start = base()
    const board = copyClips(start, [act(0, 0, 0)]) // walk 1-3 on Mia
    let pasted: ClipSelection[] | null = null
    const next = produce(start, (d) => void (pasted = pasteClips(d, board!, 5, 'pip')))
    expect(names(next, 0, 1)).toEqual(['talk', 'walk']) // Pip's lane, sorted by time
    expect(next.scenes[0].layers[1].actions[1]).toMatchObject({ t0: 5, t1: 7 })
    expect(pasted).toEqual([act(0, 1, 1)])
    expect(names(next)).toEqual(['walk', 'wave', 'idle']) // Mia's lane untouched
  })

  it('pastes into a scene by the playhead, and makes the lane when the character has none there', () => {
    const start = base()
    const board = copyClips(start, [act(0, 0, 1)]) // wave
    const next = produce(start, (d) => void pasteClips(d, board!, 12.5, 'bolt')) // 12.5 s is 2.5 s into scene b
    expect(next.scenes[1].layers.map((l) => l.character)).toEqual(['mia', 'pip', 'bolt'])
    expect(next.scenes[1].layers[2].actions[0]).toMatchObject({ name: 'wave', t0: 2.5, t1: 3.5 })
    expect(next.scenes[0].layers).toHaveLength(2)
  })

  it('keeps a group’s spacing and pulls it back when it would run past the end of the scene', () => {
    const start = base()
    const board = copyClips(start, [act(0, 0, 0), act(0, 0, 1)]) // walk 1-3, wave 4-5: spans 1..5
    const next = produce(start, (d) => void pasteClips(d, board!, 8, 'bolt')) // 8 + 4 s would overrun the 10 s scene
    const got = next.scenes[0].layers[2].actions
    expect(got.map((a) => [a.name, a.t0, a.t1])).toEqual([['walk', 6, 8], ['wave', 9, 10]])
  })

  it('pastes captions (speaker kept, or given to the chosen character), camera moves and sounds', () => {
    const start = base()
    const board = copyClips(start, [{ kind: 'caption', scene: 0, caption: 0 }, { kind: 'sfx', scene: 0, sfx: 0 }, { kind: 'camera', scene: 0, move: 0 }])!
    let pasted: ClipSelection[] | null = null
    const same = produce(start, (d) => void (pasted = pasteClips(d, board, 12, undefined)))
    // scene b already has its own captions, sounds and camera move: the copies sit among them in time order
    expect(same.scenes[1].captions.map((c) => [c.text, c.t0, c.t1, c.speaker])).toEqual([['one', 1, 2, 'mia'], ['one', 2, 3, 'mia'], ['two', 3, 4, 'pip']])
    expect(same.scenes[1].sfx.map((x) => [x.name, x.t])).toEqual([['pop', 1], ['pop', 2], ['gasp', 5]])
    expect(same.scenes[1].camera.moves.map((m) => [m.t0, m.t1])).toEqual([[1, 4], [2, 5]])
    expect(pasted).toEqual([{ kind: 'caption', scene: 1, caption: 1 }, { kind: 'sfx', scene: 1, sfx: 1 }, { kind: 'camera', scene: 1, move: 1 }])
    const given = produce(start, (d) => void pasteClips(d, board, 12, 'bolt'))
    expect(given.scenes[1].captions[1].speaker).toBe('bolt')
  })

  it('does nothing without a scene at the playhead or when the character is gone', () => {
    const start = base()
    const board = copyClips(start, [act(0, 0, 0)])!
    const none = produce(start, (d) => void expect(pasteClips(d, board, 99, 'pip')).not.toBeNull()) // past the end is the last scene
    expect(none).not.toBe(start)
    const gone = produce(start, (d) => void expect(pasteClips(d, board, 3, 'nobody')).toBeNull())
    expect(gone).toBe(start)
  })

  it('works out whose lane a paste goes to, and describes what was copied', () => {
    const s = base()
    expect(pasteTarget(s, { kind: 'character', id: 'bolt' })).toBe('bolt')
    expect(pasteTarget(s, act(0, 1, 0))).toBe('pip')
    expect(pasteTarget(s, { kind: 'layer', scene: 0, layer: 0 })).toBe('mia')
    expect(pasteTarget(s, { kind: 'scene', scene: 0 })).toBeUndefined()
    expect(describeClipboard(null)).toBe('nothing copied yet')
    expect(describeClipboard(copyClips(s, [act(0, 0, 0)]))).toBe('1 action')
    expect(describeClipboard(copyClips(s, [act(0, 0, 0), act(0, 0, 1)]))).toBe('2 actions')
    expect(describeClipboard(copyClips(s, [act(0, 0, 0), { kind: 'sfx', scene: 0, sfx: 0 }]))).toBe('2 clips')
  })
})
