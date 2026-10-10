import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { libraryApi } from '@/features/assets/testing'
import { scratchSpec } from '@/lib/spec'
import { totalDuration } from '@/lib/timeline'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { renderScreen } from '@/test/harness'
import { BuildPanel } from './BuildPanel'

beforeEach(() => {
  useProject.getState().unload()
  useStudio.getState().set({ buildStep: 'background' })
})

const spec = () => useProject.getState().spec!

/** A reel started from scratch, with the Build panel on it (the mock engine with a small library: car, cake, cat, beach ...). */
async function start() {
  const api = await libraryApi()
  useProject.getState().load(await api.createProject({ spec: scratchSpec('Café', 'paper_cutout'), title: 'Café' }))
  await renderScreen(<BuildPanel />, { api })
  await screen.findByRole('group', { name: 'Backgrounds' })
  return userEvent.setup()
}

/** Open a step (its heading is a button named with what the scene holds of it). */
async function open(user: ReturnType<typeof userEvent.setup>, step: RegExp) {
  const head = await screen.findByRole('button', { name: step })
  if (head.getAttribute('aria-expanded') !== 'true') await user.click(head)
  return screen.findByRole('region', { name: step })
}

describe('the Build panel of a reel started from scratch', () => {
  it('says which scene is being built, how long the reel is, and what to do first', async () => {
    await start()
    expect(screen.getByRole('heading', { name: 'Scene 1 of 1' })).toBeInTheDocument()
    expect(screen.getByRole('meter', { name: 'Reel length' })).toHaveAttribute('aria-valuetext', '5 s, 40 s more to reach 45 s')
    expect(screen.getByText(/Open a step and pick from the library/)).toBeInTheDocument()
    // the seven steps are there, the first one open
    for (const step of ['Background', 'Characters', 'Objects', 'Actions', 'Sounds', 'Words', 'Camera']) expect(screen.getByRole('button', { name: new RegExp(`^${step}`) })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Background/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: /^Characters/ })).toHaveAttribute('aria-expanded', 'false')
    // one scene cannot be stretched to a reel: there is no button that promises it
    expect(screen.queryByRole('button', { name: /fit to 50 s/i })).not.toBeInTheDocument()
  })

  it('offers the engine’s sets and the library’s places, and the one chosen is the scene’s background', async () => {
    const user = await start()
    const sets = within(screen.getByRole('group', { name: 'Backgrounds' }))
    expect(sets.getByRole('button', { name: 'abstract' })).toHaveAttribute('aria-pressed', 'true')
    for (const name of ['forest', 'rooftop', 'beach', 'park']) expect(sets.getByRole('button', { name })).toBeInTheDocument()

    await user.click(sets.getByRole('button', { name: 'beach' }))
    expect(spec().scenes[0].background.template).toBe('beach')
    expect(useProject.getState().selection).toEqual({ kind: 'scene', scene: 0 }) // the inspector shows its settings
    expect(sets.getByRole('button', { name: 'beach' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /^Background/ })).toHaveTextContent('beach') // the step says what the scene has
  })

  it('finds a place by what it is called in any language the library knows', async () => {
    const user = await start()
    await user.type(screen.getByRole('textbox', { name: 'Find a background' }), 'बीच')
    const sets = within(screen.getByRole('group', { name: 'Backgrounds' }))
    expect(sets.getByRole('button', { name: 'beach' })).toBeInTheDocument()
    expect(sets.queryByRole('button', { name: 'forest' })).not.toBeInTheDocument()
    await user.clear(screen.getByRole('textbox', { name: 'Find a background' }))
    await user.type(screen.getByRole('textbox', { name: 'Find a background' }), 'zzzz')
    expect(screen.getByText(/No background matches “zzzz”/)).toBeInTheDocument()
  })

  it('sets the time of day, and keeps it when another background is chosen', async () => {
    const user = await start()
    await user.click(screen.getByRole('radio', { name: 'Night' }))
    expect(spec().scenes[0].background.params).toEqual({ time_of_day: 'night' })
    await user.click(within(screen.getByRole('group', { name: 'Backgrounds' })).getByRole('button', { name: 'forest' }))
    expect(spec().scenes[0].background).toEqual({ template: 'forest', params: { time_of_day: 'night' } })
    await user.click(screen.getByRole('radio', { name: 'Day' }))
    expect(spec().scenes[0].background.params).toEqual({})
  })

  it('every pick is one undo step', async () => {
    const user = await start()
    await user.click(within(screen.getByRole('group', { name: 'Backgrounds' })).getByRole('button', { name: 'forest' }))
    expect(spec().scenes[0].background.template).toBe('forest')
    useProject.getState().undo()
    expect(spec().scenes[0].background.template).toBe('abstract')
  })
})

describe('casting', () => {
  it('adds a character from the library to the reel and to this scene, then shows them in the list', async () => {
    const user = await start()
    const region = await open(user, /^Characters/)
    expect(within(region).getByText('Nobody yet. Choose a character below.')).toBeInTheDocument()
    const group = within(within(region).getByRole('group', { name: 'Characters' }))
    // the engine's bodies and the library's pictures are both there
    expect(group.getByRole('button', { name: 'hero' })).toBeInTheDocument()
    await user.click(group.getByRole('button', { name: 'cat' }))

    expect(spec().characters.map((c) => [c.id, c.archetype])).toEqual([['cat', 'cat']])
    expect(spec().scenes[0].layers.map((l) => [l.character, l.position])).toEqual([['cat', 'center']])
    expect(useProject.getState().selection).toEqual({ kind: 'layer', scene: 0, layer: 0 })
    expect(within(region).getByRole('list')).toHaveTextContent('Cat')
    expect(screen.getByRole('button', { name: /^Characters/ })).toHaveTextContent('1')
  })

  it('takes someone out of the scene without removing them from the reel, and puts them back from the reel’s own list', async () => {
    const user = await start()
    const region = await open(user, /^Characters/)
    await user.click(within(within(region).getByRole('group', { name: 'Characters' })).getByRole('button', { name: 'cat' }))
    await user.click(within(region).getByRole('button', { name: 'Take Cat out of this scene' }))
    expect(spec().scenes[0].layers).toEqual([])
    expect(spec().characters).toHaveLength(1)

    await user.click(within(region).getByRole('button', { name: 'Put Cat in this scene' }))
    expect(spec().scenes[0].layers.map((l) => l.character)).toEqual(['cat'])
    expect(within(region).queryByText('Already in your reel')).not.toBeInTheDocument()
  })

  it('stands several people in different places', async () => {
    const user = await start()
    const region = await open(user, /^Characters/)
    const group = within(within(region).getByRole('group', { name: 'Characters' }))
    await user.click(group.getByRole('button', { name: 'hero' }))
    await user.click(group.getByRole('button', { name: 'cow' }))
    await user.click(group.getByRole('button', { name: 'robot' }))
    expect(spec().scenes[0].layers.map((l) => l.position)).toEqual(['center', 'left', 'right'])
  })

  it('finds a character by a word in any language', async () => {
    const user = await start()
    const region = await open(user, /^Characters/)
    await user.type(within(region).getByRole('textbox', { name: 'Find a character' }), 'बिल्ली')
    const group = within(within(region).getByRole('group', { name: 'Characters' }))
    expect(group.getByRole('button', { name: 'cat' })).toBeInTheDocument()
    expect(group.queryByRole('button', { name: 'cow' })).not.toBeInTheDocument()
  })
})

describe('actions', () => {
  async function withCat() {
    const user = await start()
    const cast = await open(user, /^Characters/)
    await user.click(within(within(cast).getByRole('group', { name: 'Characters' })).getByRole('button', { name: 'cat' }))
    return { user, actions: await open(user, /^Actions/) }
  }

  it('asks for a character first when nobody is in the scene', async () => {
    const user = await start()
    const region = await open(user, /^Actions/)
    expect(within(region).getByText(/cast a character first/i)).toBeInTheDocument()
    await user.click(within(region).getByRole('button', { name: /Next: Characters/ }))
    expect(useStudio.getState().buildStep).toBe('cast')
  })

  it('gives the character actions that follow one another, and the scene grows to hold them', async () => {
    const { user, actions } = await withCat()
    const list = within(within(actions).getByRole('group', { name: 'Actions' }))
    await user.click(list.getByRole('button', { name: 'Add walk for Cat' }))
    await user.click(list.getByRole('button', { name: 'Add wave for Cat' }))
    await user.click(list.getByRole('button', { name: 'Add walk for Cat' }))
    const clips = spec().scenes[0].layers[0].actions
    expect(clips.map((a) => a.name)).toEqual(['walk', 'wave', 'walk'])
    expect(clips[1].t0).toBe(clips[0].t1)
    expect(clips[2].t0).toBe(clips[1].t1)
    expect(spec().scenes[0].duration_sec).toBeGreaterThanOrEqual(clips[2].t1)
    // what Cat does in this scene is listed, to select or to take out
    const mine = within(actions).getByRole('region', { name: 'Cat in scene 1' })
    expect(within(mine).getAllByRole('listitem')).toHaveLength(3)
    await user.click(within(mine).getAllByRole('button', { name: 'Take walk out' })[0])
    expect(spec().scenes[0].layers[0].actions.map((a) => a.name)).toEqual(['wave', 'walk'])
  })

  it('lists the actions by kind and finds one by its name', async () => {
    const { user, actions } = await withCat()
    await user.type(within(actions).getByRole('textbox', { name: 'Find an action' }), 'wav')
    const list = within(within(actions).getByRole('group', { name: 'Actions' }))
    expect(list.getByRole('button', { name: 'Add wave for Cat' })).toBeInTheDocument()
    expect(list.queryByRole('button', { name: 'Add walk for Cat' })).not.toBeInTheDocument()
  })

  it('chooses who does it', async () => {
    const user = await start()
    const cast = await open(user, /^Characters/)
    const group = within(within(cast).getByRole('group', { name: 'Characters' }))
    await user.click(group.getByRole('button', { name: 'cat' }))
    await user.click(group.getByRole('button', { name: 'cow' }))
    const actions = await open(user, /^Actions/)
    await user.click(within(within(actions).getByRole('group', { name: 'Who does it' })).getByRole('button', { name: 'Cow' }))
    await user.click(within(within(actions).getByRole('group', { name: 'Actions' })).getByRole('button', { name: 'Add talk for Cow' }))
    expect(spec().scenes[0].layers.map((l) => [l.character, l.actions.length])).toEqual([['cat', 0], ['cow', 1]])
  })
})

describe('objects', () => {
  it('adds a library object to the scene, and lists it, and takes it out again', async () => {
    const user = await start()
    const region = await open(user, /^Objects/)
    expect(within(region).getByText(/No objects yet/)).toBeInTheDocument()
    await user.type(within(region).getByRole('textbox', { name: 'Find an object' }), 'गाड़ी')
    await user.click(await within(region).findByRole('button', { name: /^car/ }))
    expect(spec().scenes[0].objects.map((o) => o.asset)).toEqual(['car'])
    expect(useProject.getState().selection).toEqual({ kind: 'object', scene: 0, object: 0 })
    expect(within(region).getByRole('region', { name: 'In scene 1' })).toHaveTextContent('the whole scene')
    await user.click(within(region).getByRole('button', { name: 'Take car out of this scene' }))
    expect(spec().scenes[0].objects).toEqual([])
  })
})

describe('sounds', () => {
  it('adds a sound at the playhead, listens to it first on request, and takes it out', async () => {
    const play = vi.spyOn(window.HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
    const user = await start()
    const region = await open(user, /^Sounds/)
    expect(within(region).getByText('0 s', { selector: 'b' })).toBeInTheDocument()
    await user.click(within(region).getByRole('button', { name: 'Listen to applause' }))
    expect(play).toHaveBeenCalled()
    expect(spec().scenes[0].sfx).toEqual([])

    useProject.getState().setPlayhead(1.5)
    await user.click(await within(region).findByRole('button', { name: 'Add applause' }))
    expect(spec().scenes[0].sfx).toEqual([{ name: 'applause', t: 1.5, volume: 1 }])
    expect(within(region).getByRole('region', { name: 'In scene 1' })).toHaveTextContent('at 1.5 s')
    await user.click(within(region).getByRole('button', { name: 'Take applause out' }))
    expect(spec().scenes[0].sfx).toEqual([])
    play.mockRestore()
  })
})

describe('music', () => {
  it('is chosen for the whole reel in the Sounds step: a mood, the look’s own bed, or none, and a mood can be listened to', async () => {
    const play = vi.spyOn(window.HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
    const user = await start()
    const region = await open(user, /^Sounds/)
    expect(spec().audio.music).toBe('procedural')
    const listen = within(region).getByRole('button', { name: 'Listen to the music' })
    expect(listen).toBeDisabled() // the look's own bed has no mood to play

    await user.click(within(region).getByRole('combobox', { name: 'Music' }))
    await user.click(await screen.findByRole('option', { name: 'Calm' }))
    expect(spec().audio.music).toBe('procedural:calm')
    await user.click(listen)
    expect(play).toHaveBeenCalled()

    await user.click(within(region).getByRole('combobox', { name: 'Music' }))
    await user.click(await screen.findByRole('option', { name: 'No music' }))
    expect(spec().audio.music).toBeNull()
    expect(listen).toBeDisabled()
    useProject.getState().undo()
    expect(spec().audio.music).toBe('procedural:calm')
    play.mockRestore()
  })
})

describe('music that is a file', () => {
  it('is shown as custom music, left alone until another is chosen, and cannot be previewed here', async () => {
    const user = await start()
    useProject.getState().edit((d) => void (d.audio.music = 'sounds/bed.mp3'))
    const region = await open(user, /^Sounds/)
    expect(within(region).getByRole('combobox', { name: 'Music' })).toHaveTextContent('Custom music')
    expect(within(region).getByRole('button', { name: 'Listen to the music' })).toBeDisabled()
    expect(spec().audio.music).toBe('sounds/bed.mp3')
  })
})

describe('words', () => {
  it('adds lines one after another, with who says them, and clears the box for the next', async () => {
    const user = await start()
    const cast = await open(user, /^Characters/)
    await user.click(within(within(cast).getByRole('group', { name: 'Characters' })).getByRole('button', { name: 'cat' }))
    const region = await open(user, /^Words/)
    const add = within(region).getByRole('button', { name: /add line/i })
    expect(add).toBeDisabled()

    const box = within(region).getByRole('textbox', { name: 'What is said' })
    await user.type(box, 'Hello there')
    await user.click(add)
    expect(box).toHaveValue('')
    await user.type(box, 'Nice to see you again{Control>}{Enter}{/Control}')
    const caps = spec().scenes[0].captions
    expect(caps.map((c) => c.text)).toEqual(['Hello there', 'Nice to see you again'])
    expect(caps[1].t0).toBe(caps[0].t1)
    expect(caps[0].speaker).toBeUndefined() // narration
    expect(within(region).getByRole('region', { name: 'In scene 1' })).toHaveTextContent('Nice to see you again')
  })

  it('says a line in the voice of a character', async () => {
    const user = await start()
    const cast = await open(user, /^Characters/)
    await user.click(within(within(cast).getByRole('group', { name: 'Characters' })).getByRole('button', { name: 'cat' }))
    const region = await open(user, /^Words/)
    await user.click(within(region).getByRole('combobox', { name: 'Who says it' }))
    await user.click(await screen.findByRole('option', { name: 'Cat' }))
    await user.type(within(region).getByRole('textbox', { name: 'What is said' }), 'Meow')
    await user.click(within(region).getByRole('button', { name: /add line/i }))
    expect(spec().scenes[0].captions[0]).toMatchObject({ text: 'Meow', speaker: 'cat' })
  })
})

describe('camera', () => {
  it('adds a move to the scene and takes it out', async () => {
    const user = await start()
    const region = await open(user, /^Camera/)
    await user.click(within(region).getByRole('button', { name: 'Add a zoom' }))
    expect(spec().scenes[0].camera.moves.map((m) => m.type)).toEqual(['zoom'])
    await user.click(within(region).getByRole('button', { name: 'Take the zoom out' }))
    expect(spec().scenes[0].camera.moves).toEqual([])
  })
})

describe('scenes', () => {
  it('adds a scene on the same set with the same cast, and builds in it from then on', async () => {
    const user = await start()
    await user.click(within(screen.getByRole('group', { name: 'Backgrounds' })).getByRole('button', { name: 'forest' }))
    const cast = await open(user, /^Characters/)
    await user.click(within(within(cast).getByRole('group', { name: 'Characters' })).getByRole('button', { name: 'cat' }))

    await user.click(screen.getByRole('button', { name: /scene like this/i }))
    expect(spec().scenes).toHaveLength(2)
    expect(spec().scenes[1]).toMatchObject({ background: { template: 'forest' }, objects: [], captions: [] })
    expect(spec().scenes[1].layers.map((l) => [l.character, l.actions.length])).toEqual([['cat', 0]])
    await screen.findByRole('heading', { name: 'Scene 2 of 2' })
    expect(useProject.getState().selection).toEqual({ kind: 'scene', scene: 1 })
    // the playhead is in the new scene, so the stage shows it
    expect(useProject.getState().playhead).toBeGreaterThanOrEqual(5)

    // what is added now goes to scene 2
    const words = await open(user, /^Words/)
    await user.type(within(words).getByRole('textbox', { name: 'What is said' }), 'Second scene')
    await user.click(within(words).getByRole('button', { name: /add line/i }))
    expect(spec().scenes[0].captions).toEqual([])
    expect(spec().scenes[1].captions.map((c) => c.text)).toEqual(['Second scene'])
  })

  it('adds a blank scene, with the plain stage and nobody in it', async () => {
    const user = await start()
    await user.click(within(screen.getByRole('group', { name: 'Backgrounds' })).getByRole('button', { name: 'forest' }))
    await user.click(screen.getByRole('button', { name: /blank scene/i }))
    expect(spec().scenes[1]).toMatchObject({ background: { template: 'abstract' }, layers: [] })
  })

  it('goes to the scene before and the scene after, and adds to the one being built even when the playhead is elsewhere', async () => {
    const user = await start()
    await user.click(screen.getByRole('button', { name: /scene like this/i }))
    await screen.findByRole('heading', { name: 'Scene 2 of 2' })
    expect(screen.getByRole('button', { name: 'Next scene' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Previous scene' }))
    await screen.findByRole('heading', { name: 'Scene 1 of 2' })
    expect(screen.getByRole('button', { name: 'Previous scene' })).toBeDisabled()

    // the playhead wanders into scene 2 while scene 1 is being built: the sound still goes to scene 1
    useProject.getState().select({ kind: 'scene', scene: 0 })
    useProject.getState().setPlayhead(7)
    const sounds = await open(user, /^Sounds/)
    await user.click(await within(sounds).findByRole('button', { name: 'Add bell' }))
    expect(spec().scenes[0].sfx.map((x) => x.name)).toEqual(['bell'])
    expect(spec().scenes[1].sfx).toEqual([])
    expect(useProject.getState().playhead).toBeLessThan(5) // and the stage is back on scene 1 to show it
  })

  it('sets how long the scene lasts', async () => {
    const user = await start()
    const box = screen.getByRole('textbox', { name: 'Scene length' })
    await user.clear(box)
    await user.type(box, '12{Enter}')
    expect(spec().scenes[0].duration_sec).toBe(12)
  })
})

describe('the length of the reel', () => {
  it('turns green inside 45–60 s, and scales the scenes to 50 s when asked', async () => {
    const user = await start()
    await user.click(screen.getByRole('button', { name: /scene like this/i }))
    await user.click(screen.getByRole('button', { name: /scene like this/i }))
    expect(totalDuration(spec())).toBe(15)
    expect(screen.getByRole('meter', { name: 'Reel length' })).toHaveAttribute('aria-valuetext', '15 s, 30 s more to reach 45 s')

    await user.click(screen.getByRole('button', { name: /fit to 50 s/i }))
    await waitFor(() => expect(totalDuration(spec())).toBeCloseTo(50, 1))
    expect(screen.getByRole('meter', { name: 'Reel length' })).toHaveAttribute('aria-valuetext', expect.stringMatching(/^50 s, within 45–60 s$/))
    expect(screen.queryByRole('button', { name: /fit to 50 s/i })).not.toBeInTheDocument() // nothing to fit any more
    useProject.getState().undo() // one step puts the lengths back
    expect(totalDuration(spec())).toBe(15)
  })
})

describe('steps', () => {
  it('opens one at a time, closes the open one when it is chosen again, and remembers the choice', async () => {
    const user = await start()
    await user.click(screen.getByRole('button', { name: /^Sounds/ }))
    expect(useStudio.getState().buildStep).toBe('sounds')
    expect(screen.getByRole('button', { name: /^Background/ })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('group', { name: 'Backgrounds' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Sounds/ }))
    expect(useStudio.getState().buildStep).toBeNull()
    expect(screen.queryByRole('region', { name: /^Sounds/ })).not.toBeInTheDocument()
  })

  it('leads on from one step to the next', async () => {
    const user = await start()
    await user.click(screen.getByRole('button', { name: /next: characters/i }))
    expect(useStudio.getState().buildStep).toBe('cast')
    // the button that was pressed went away with its step: the keyboard focus goes to the step that opened
    expect(screen.getByRole('button', { name: /^Characters/ })).toHaveFocus()
    await user.click(screen.getByRole('button', { name: /next: objects/i }))
    expect(useStudio.getState().buildStep).toBe('objects')
    expect(screen.getByRole('textbox', { name: 'Find an object' })).toHaveFocus() // this step is a search: ready to type
  })
})
