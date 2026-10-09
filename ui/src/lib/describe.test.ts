import { describe, expect, it } from 'vitest'
import type { ReelSpec, Scene } from '@/api/types'
import { describeChange } from './describe'

const scene = (id: string, dur = 6): Scene => ({
  id,
  duration_sec: dur,
  background: { template: 'street', params: {} },
  camera: { moves: [{ type: 'pan', from: [0, 0], to: [0.1, 0], t0: 0, t1: 4, ease: 'linear', params: {} }] },
  layers: [
    { character: 'mia', position: 'left', scale: 1, depth: 'mid', facing: 'auto', actions: [{ name: 'walk', t0: 1, t1: 3, params: {} }, { name: 'wave', t0: 3.5, t1: 5, params: {} }] },
    { character: 'pip', position: 'right', scale: 1, depth: 'mid', facing: 'auto', actions: [] },
  ],
  captions: [{ text: 'Hello there', t0: 1, t1: 3, style: 'subtitle', anchor: 'auto', speaker: 'mia' }],
  sfx: [{ name: 'pop', t: 1, volume: 1 }],
  transition_out: { type: 'cut', duration: 0, params: {} },
})

const base = (): ReelSpec => ({
  version: '1.0',
  meta: { title: 'The umbrella', style: 'paper_cutout', fps: 30, resolution: [1080, 1920], seed: 1, target_duration_sec: 50 },
  characters: [
    { id: 'mia', archetype: 'kid', name: 'Mia', props: [], palette: {} },
    { id: 'pip', archetype: 'elder', name: 'Mr. Pip', props: [], palette: {} },
  ],
  scenes: [scene('s1'), scene('s2')],
  audio: { music: null, voiceover: 'tts', ducking: true, music_gain_db: -16, voice_gain_db: 0, sfx_gain_db: -8, auto_sfx: false },
})

const edit = (fn: (d: ReelSpec) => void): [ReelSpec, ReelSpec] => {
  const a = base()
  const b = structuredClone(a)
  fn(b)
  return [a, b]
}

describe('describeChange', () => {
  it('names reel-level edits', () => {
    expect(describeChange(...edit((d) => void (d.meta.title = 'Rain day')))).toBe('Renamed the reel to “Rain day”')
    expect(describeChange(...edit((d) => void (d.meta.style = 'flat_vector')))).toBe('Changed the style to flat vector')
    expect(describeChange(...edit((d) => void (d.audio.ducking = false)))).toBe('Changed the ducking')
    expect(describeChange(...edit((d) => void ((d.audio.music = 'procedural'), (d.audio.auto_sfx = true))))).toBe('Changed the audio settings')
    expect(describeChange(base(), base())).toBe('No change')
  })

  it('names cast edits', () => {
    expect(describeChange(...edit((d) => void (d.characters[0].palette.shirt = '#ff0000')))).toBe('Recoloured Mia')
    expect(describeChange(...edit((d) => void (d.characters[1].archetype = 'boss')))).toBe('Changed Mr. Pip’s archetype')
    expect(describeChange(...edit((d) => void d.characters.push({ id: 'bolt', archetype: 'robot', name: 'Bolt', props: [], palette: {} })))).toBe('Added Bolt to the cast')
    expect(describeChange(...edit((d) => void d.characters.pop()))).toBe('Removed Mr. Pip from the cast')
  })

  it('tells moving a clip from resizing it', () => {
    expect(describeChange(...edit((d) => void ((d.scenes[0].layers[0].actions[0].t0 = 1.5), (d.scenes[0].layers[0].actions[0].t1 = 3.5))))).toBe('Moved walk')
    expect(describeChange(...edit((d) => void (d.scenes[0].layers[0].actions[0].t1 = 3.5)))).toBe('Resized walk')
    expect(describeChange(...edit((d) => void (d.scenes[0].layers[0].actions[0].params = { style: 'sneak' })))).toBe('Edited walk')
    expect(describeChange(...edit((d) => void (d.scenes[0].layers[0].actions[0].name = 'run')))).toBe('Changed walk to run')
    expect(describeChange(...edit((d) => void d.scenes[0].layers[0].actions.splice(1, 1)))).toBe('Removed wave for Mia')
    expect(describeChange(...edit((d) => void d.scenes[0].layers[1].actions.push({ name: 'idle', t0: 0, t1: 2, params: {} })))).toBe('Added idle for Mr. Pip')
  })

  it('names caption, sound and camera edits', () => {
    expect(describeChange(...edit((d) => void (d.scenes[0].captions[0].text = 'Oh no, my umbrella!')))).toBe('Edited caption “Oh no, my umbrella!”')
    expect(describeChange(...edit((d) => void ((d.scenes[0].captions[0].t0 = 2), (d.scenes[0].captions[0].t1 = 4))))).toBe('Moved a caption')
    expect(describeChange(...edit((d) => void d.scenes[1].captions.pop()))).toBe('Removed a caption “Hello there”')
    expect(describeChange(...edit((d) => void (d.scenes[0].sfx[0].t = 2)))).toBe('Moved a sound')
    expect(describeChange(...edit((d) => void d.scenes[0].sfx.push({ name: 'gasp', t: 2, volume: 1 })))).toBe('Added the gasp sound')
    expect(describeChange(...edit((d) => void (d.scenes[0].camera.moves[0].t1 = 5)))).toBe('Resized the pan move')
    expect(describeChange(...edit((d) => void d.scenes[0].camera.moves.pop()))).toBe('Removed a pan move')
  })

  it('names layer and scene edits', () => {
    expect(describeChange(...edit((d) => void (d.scenes[0].layers[0].position = [0.3, 0.7])))).toBe('Moved Mia')
    expect(describeChange(...edit((d) => void (d.scenes[0].layers[0].scale = 1.4)))).toBe('Resized Mia')
    expect(describeChange(...edit((d) => void (d.scenes[0].layers[1].depth = 'foreground')))).toBe('Changed Mr. Pip’s depth')
    expect(describeChange(...edit((d) => void d.scenes[0].layers.pop()))).toBe('Removed Mr. Pip from Scene 1')
    expect(describeChange(...edit((d) => void (d.scenes[1].duration_sec = 8)))).toBe('Scene 2: length 6 → 8 s')
    expect(describeChange(...edit((d) => void (d.scenes[0].background.template = 'forest')))).toBe('Scene 1: background → forest')
    expect(describeChange(...edit((d) => void (d.scenes[0].transition_out = { type: 'wipe', duration: 0.6, params: {} })))).toBe('Scene 1: changed the transition')
    expect(describeChange(...edit((d) => void d.scenes.push(scene('s3'))))).toBe('Added scene s3')
    expect(describeChange(...edit((d) => void d.scenes.pop()))).toBe('Removed scene s2')
    expect(describeChange(...edit((d) => void d.scenes.reverse()))).toBe('Reordered the scenes')
  })

  it('names each part when an edit touches several', () => {
    expect(describeChange(...edit((d) => void ((d.meta.title = 'Both'), (d.scenes[1].duration_sec = 8))))).toBe('Renamed the reel to “Both”; Scene 2: length 6 → 8 s')
    expect(describeChange(...edit((d) => void ((d.meta.title = 'x'), (d.audio.ducking = false), (d.characters[0].name = 'Z'), (d.scenes[0].duration_sec = 9))))).toBe('Changed 4 parts of the reel')
  })

  it('sums up edits that touch several scenes', () => {
    expect(describeChange(...edit((d) => d.scenes.forEach((s) => void (s.duration_sec *= 1.2))))).toBe('Rescaled 2 scenes')
    expect(describeChange(...edit((d) => d.scenes.forEach((s) => void (s.background.template = 'forest'))))).toBe('Edited 2 scenes')
  })
})
