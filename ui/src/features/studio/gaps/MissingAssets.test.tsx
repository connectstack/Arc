import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import type { Api } from '@/api/client'
import type { LibraryGap } from '@/api/types'
import { libraryApi, svgFile } from '@/features/assets/testing'
import type { Selection } from '@/lib/spec'
import { useLint } from '@/store/lint'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { renderScreen } from '@/test/harness'
import { Inspector } from '../inspector/Inspector'
import { LeftPanel } from '../LeftPanel'
import { ProblemsDrawer } from '../ProblemsDrawer'
import { MissingChip } from './MissingAssets'
import { useIgnored } from './ignored'

const st = useProject.getState
const SCENES = ['rainy_street', 'pip_panics', 'park_search']

async function open(gaps: LibraryGap[], select: Selection = { kind: 'scene', scene: 0 }): Promise<Api> {
  window.localStorage.clear()
  useIgnored.setState({ lists: {} })
  const api = await libraryApi()
  const doc = await api.getProject('story-50s')
  st().load(doc)
  st().edit((d) => void (d.meta.library_gaps = gaps))
  st().select(select)
  return api
}

const castle: LibraryGap = { kind: 'object', name: 'castle', scenes: [SCENES[0], SCENES[1]] }
const objectsOf = (scene: number) => st().spec!.scenes[scene].objects.map((o) => o.asset)
const gapsLeft = () => st().spec!.meta.library_gaps
const slow = { timeout: 4000 }
const dropOn = (el: Element, ...files: File[]) => fireEvent.drop(el, { dataTransfer: { files, types: ['Files'] } })

beforeEach(() => st().unload())

describe('a scene says what the library cannot draw', () => {
  it('lists what this scene says that is missing, and nothing about the other scenes', async () => {
    const api = await open([castle, { kind: 'object', name: 'sword', scenes: [SCENES[2]] }])
    await renderScreen(<Inspector />, { api })
    const section = await screen.findByRole('list', { name: 'Missing from the library' }, slow)
    expect(within(section).getByText('castle')).toBeInTheDocument()
    expect(within(section).getByText(/the library has no such object\. Add it and it goes into this scene/i)).toBeInTheDocument()
    expect(within(section).queryByText('sword')).not.toBeInTheDocument() // that one is for scene 3
    expect(screen.getByRole('button', { name: /add castle to the library and this scene/i })).toBeInTheDocument()
  })

  it('has no section for a scene that misses nothing', async () => {
    const api = await open([castle], { kind: 'scene', scene: 2 })
    await renderScreen(<Inspector />, { api })
    await screen.findByRole('textbox', { name: 'Scene name' })
    expect(screen.queryByRole('list', { name: 'Missing from the library' })).not.toBeInTheDocument()
  })

  it('says which scenes the script says it in, and what stands in for a place', async () => {
    const api = await open([{ kind: 'place', name: 'barn', scenes: [SCENES[0]], stand_in: 'forest' }])
    await renderScreen(<Inspector />, { api })
    expect(await screen.findByText(/forest stands in for “barn”\. Add it and the set of this scene becomes it/i, {}, slow)).toBeInTheDocument()
  })

  it('adds the asset to the library and puts it in this scene only, as one undo step', async () => {
    const user = userEvent.setup()
    const api = await open([castle])
    await renderScreen(<Inspector />, { api })
    const undoDepth = st().past.length
    await user.click(await screen.findByRole('button', { name: /add castle to the library and this scene/i }, slow))
    // the form opens already filled in from what the script says
    expect(await screen.findByText(/your script mentions “castle” in scene 1 and the library does not have it/i)).toBeInTheDocument()
    dropOn(screen.getByRole('button', { name: /drop an svg, png, jpg or webp/i }), svgFile('castle.svg'))
    expect(await screen.findByRole('textbox', { name: 'Name' })).toHaveValue('castle')
    await user.click(screen.getByRole('button', { name: /save to library/i }))

    await waitFor(() => expect(objectsOf(0)).toEqual(['castle']))
    expect(objectsOf(1)).toEqual([]) // the other scene that says it was not asked for
    expect(gapsLeft()).toEqual([{ kind: 'object', name: 'castle', scenes: [SCENES[1]] }]) // ... and still lacks it
    expect(st().past.length).toBe(undoDepth + 1)
    expect(st().selection).toMatchObject({ kind: 'object', scene: 0 })
    expect(await screen.findByText(/castle is in scene 1/i)).toBeInTheDocument()
    expect(await screen.findByText(/scene 2 says “castle” too: each has a button to use it there/i)).toBeInTheDocument()
    act(() => st().undo())
    expect(objectsOf(0)).toEqual([])
    expect(gapsLeft()).toEqual([castle])
  })

  it('offers an asset the library has now for this scene, or for all the scenes that say it', async () => {
    const user = userEvent.setup()
    const api = await open([{ kind: 'object', name: 'car', scenes: [SCENES[0], SCENES[1], SCENES[2]] }])
    await renderScreen(<Inspector />, { api })
    expect(await screen.findByText(/the library has “car” now/i, {}, slow)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /use it in this scene: car/i }))
    await waitFor(() => expect(objectsOf(0)).toEqual(['car']))
    expect(objectsOf(1)).toEqual([])
    expect(gapsLeft()).toEqual([{ kind: 'object', name: 'car', scenes: [SCENES[1], SCENES[2]] }])
  })

  it('can use it in every scene that says it', async () => {
    const user = userEvent.setup()
    const api = await open([{ kind: 'object', name: 'car', scenes: [SCENES[0], SCENES[1]] }])
    await renderScreen(<Inspector />, { api })
    await user.click(await screen.findByRole('button', { name: /use it in all 2 scenes/i }, slow))
    await waitFor(() => expect(objectsOf(1)).toEqual(['car']))
    expect(objectsOf(0)).toEqual(['car'])
    expect(gapsLeft()).toBeUndefined()
  })

  it('puts a character nobody stood in for into this scene, as a character of its own', async () => {
    const user = userEvent.setup()
    const api = await open([{ kind: 'character', name: 'cat', scenes: [SCENES[0], SCENES[1]] }])
    await renderScreen(<Inspector />, { api })
    expect(await screen.findByText(/the library has “cat” now/i, {}, slow)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /use it in this scene: cat/i }))
    await waitFor(() => expect(st().spec!.characters.some((c) => c.archetype === 'cat')).toBe(true))
    const spec = st().spec!
    const cat = spec.characters.find((c) => c.archetype === 'cat')!
    expect(spec.scenes[0].layers.map((l) => l.character)).toContain(cat.id)
    expect(spec.scenes[1].layers.map((l) => l.character)).not.toContain(cat.id)
    expect(gapsLeft()).toEqual([{ kind: 'character', name: 'cat', scenes: [SCENES[1]] }])
    expect(st().selection).toMatchObject({ kind: 'layer', scene: 0 })
  })

  it('says a character something stands in for is drawn as it in every scene', async () => {
    const api = await open([{ kind: 'character', name: 'astronaut', scenes: [SCENES[0]], character: 'mia', stand_in: 'everyman' }])
    await renderScreen(<Inspector />, { api })
    expect(await screen.findByText(/Mia is drawn as everyman because the library has no “astronaut”\. Add it and Mia is drawn as it in every scene/i, {}, slow)).toBeInTheDocument()
  })

  it('does not offer what the scene already shows', async () => {
    const api = await open([{ kind: 'object', name: 'car', scenes: [SCENES[0], SCENES[1]] }])
    st().edit((d) => void d.scenes[0].objects.push({ asset: 'car', position: [0.5, 0.8], scale: 1, depth: 'mid', layer: 'behind', facing: 'auto', rotation: 0, alpha: 1, t0: 0, t1: null, motions: [], palette: {} }))
    await renderScreen(<Inspector />, { api })
    await screen.findByRole('textbox', { name: 'Scene name' })
    await new Promise((r) => setTimeout(r, 1500)) // the gaps are read first
    expect(screen.queryByRole('list', { name: 'In the library now' })).not.toBeInTheDocument() // somebody placed the car here already
    act(() => st().select({ kind: 'scene', scene: 1 }))
    expect(await screen.findByRole('list', { name: 'In the library now' }, slow)).toBeInTheDocument() // the other scene still lacks it
  })

  it('stops warning about one the person does not need, in this reel only', async () => {
    const user = userEvent.setup()
    const api = await open([castle])
    await renderScreen(<Inspector />, { api })
    await user.click(await screen.findByRole('button', { name: 'Not needed' }, slow))
    await waitFor(() => expect(screen.queryByRole('list', { name: 'Missing from the library' })).not.toBeInTheDocument())
    expect(JSON.parse(window.localStorage.getItem('reel:gaps-ignored:story-50s') ?? '[]')).toEqual(['object:castle'])
    expect(gapsLeft()).toEqual([castle]) // the reel itself is untouched
  })
})

describe('the Studio shows the warning wherever you are', () => {
  it('marks the scenes in the scene list', async () => {
    const api = await open([castle])
    useStudio.setState({ leftTab: 'scenes' })
    await renderScreen(<LeftPanel />, { api })
    const first = await screen.findByRole('button', { name: /scene\s*1\.\s*rainy_street\s*,\s*missing from the library: castle/i }, slow)
    expect(first).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /scene\s*3\.\s*park_search/i }).textContent).not.toMatch(/missing/i)
  })

  it('lists them all in the Script tab, each with the scenes that say it', async () => {
    const user = userEvent.setup()
    const api = await open([castle, { kind: 'place', name: 'barn', scenes: [], stand_in: 'forest' }], { kind: 'reel' })
    useStudio.setState({ leftTab: 'script' })
    await renderScreen(<LeftPanel />, { api })
    expect(await screen.findByRole('heading', { name: /2 things missing from the library/i }, slow)).toBeInTheDocument()
    expect(screen.getAllByText(/scenes 1, 2/).length).toBeGreaterThan(0)
    await user.click(screen.getAllByRole('button', { name: 'Not needed' })[1])
    expect(await screen.findByRole('heading', { name: /1 thing missing from the library/i })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /show the 1 you set aside/i }))
    expect(await screen.findByRole('heading', { name: /2 things missing from the library/i })).toBeInTheDocument()
  })

  it('shows a count in the top bar that opens the list', async () => {
    const user = userEvent.setup()
    const api = await open([castle, { kind: 'object', name: 'sword', scenes: [SCENES[2]] }])
    await renderScreen(<MissingChip />, { api })
    const chip = await screen.findByRole('button', { name: /2 things missing from the library/i }, slow)
    await user.click(chip)
    expect(useStudio.getState().leftTab).toBe('script')
  })

  it('says what the library can draw now apart from what it cannot', async () => {
    const api = await open([castle, { kind: 'object', name: 'car', scenes: [SCENES[0]] }])
    await renderScreen(<Inspector />, { api })
    expect(await screen.findByRole('list', { name: 'Missing from the library' }, slow)).toBeInTheDocument()
    const ready = await screen.findByRole('list', { name: 'In the library now' }, slow)
    expect(within(ready).getByText('car')).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: 'Missing from the library' })).queryByText('car')).not.toBeInTheDocument()
  })

  it('counts only what is missing as a warning, and says when the rest is ready to use', async () => {
    const user = userEvent.setup()
    const api = await open([castle, { kind: 'object', name: 'car', scenes: [SCENES[0]] }])
    const { unmount } = await renderScreen(<MissingChip />, { api })
    expect(await screen.findByRole('button', { name: /^1 thing missing from the library$/i }, slow)).toBeInTheDocument()
    unmount()
    st().edit((d) => void (d.meta.library_gaps = [{ kind: 'object', name: 'car', scenes: [SCENES[0]] }]))
    await renderScreen(<MissingChip />, { api })
    const chip = await screen.findByRole('button', { name: /1 thing from your script ready to use from the library/i }, slow)
    await user.click(chip)
    expect(useStudio.getState().leftTab).toBe('script')
  })

  it('marks a scene that only waits for something the library has with a different badge', async () => {
    const api = await open([{ kind: 'object', name: 'car', scenes: [SCENES[0]] }])
    useStudio.setState({ leftTab: 'scenes' })
    await renderScreen(<LeftPanel />, { api })
    const card = await screen.findByRole('button', { name: /scene\s*1\.\s*rainy_street\s*,\s*in the library now: car/i }, slow)
    expect(card.textContent).not.toMatch(/missing from the library/i)
  })

  it('is not there when nothing is missing', async () => {
    const api = await open([])
    await renderScreen(<MissingChip />, { api })
    await waitFor(() => expect(screen.queryByRole('button', { name: /missing from the library/i })).not.toBeInTheDocument())
  })

  it('turns the note in Problems into a warning with the way to add it', async () => {
    const user = userEvent.setup()
    const api = await open([castle])
    useLint.setState({
      report: {
        ok: true,
        total_sec: 50,
        n_scenes: 13,
        counts: { errors: 0, warnings: 0, infos: 1 },
        missing: {},
        issues: [{ severity: 'info', code: 'LIBRARY_GAP', path: 'meta.library_gaps[0]', message: "the script wants an object 'castle' that the asset library does not have; it was left out", hint: 'add it: `reel assets add FILE`', kind: 'object', name: 'castle' }],
      },
      busy: false,
    })
    await renderScreen(<ProblemsDrawer />, { api })
    expect(await screen.findByLabelText('warning', {}, slow)).toBeInTheDocument()
    expect(screen.getByText(/add the asset and it is put to use in the scenes that say it/i)).toBeInTheDocument()
    expect(screen.queryByText(/reel assets add/)).not.toBeInTheDocument() // not a command to type
    // counted as a warning, not as a note
    expect(screen.getByRole('button', { name: '1 warnings' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '0 notes' })).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Add asset…' }, slow))
    expect(await screen.findByText(/your script mentions “castle” in scenes 1, 2/i)).toBeInTheDocument()
  })

  it('offers to use what the library has now, for the scenes that still lack it', async () => {
    const user = userEvent.setup()
    const api = await open([{ kind: 'object', name: 'car', scenes: [SCENES[0], SCENES[1]] }])
    st().edit((d) => void d.scenes[0].objects.push({ asset: 'car', position: [0.5, 0.8], scale: 1, depth: 'mid', layer: 'behind', facing: 'auto', rotation: 0, alpha: 1, t0: 0, t1: null, motions: [], palette: {} }))
    useLint.setState({
      report: {
        ok: true,
        total_sec: 50,
        n_scenes: 13,
        counts: { errors: 0, warnings: 0, infos: 1 },
        missing: {},
        issues: [{ severity: 'info', code: 'LIBRARY_GAP_FILLED', path: 'meta.library_gaps[0]', message: "the library has object 'car' now (the script asked for 'car')", kind: 'object', name: 'car' }],
      },
      busy: false,
    })
    await renderScreen(<ProblemsDrawer />, { api })
    expect(await screen.findByText(/the library has “car” now: use it in the scenes that say it/i, {}, slow)).toBeInTheDocument()
    expect(screen.getByText('scene 2')).toBeInTheDocument() // scene 1 shows it already
    await user.click(screen.getByRole('button', { name: 'Use it' }))
    await waitFor(() => expect(objectsOf(1)).toEqual(['car']))
    expect(objectsOf(0)).toEqual(['car']) // not placed twice
  })

  it('does not list a note the person set aside', async () => {
    const api = await open([castle])
    useLint.setState({
      report: {
        ok: true,
        total_sec: 50,
        n_scenes: 13,
        counts: { errors: 0, warnings: 0, infos: 1 },
        missing: {},
        issues: [{ severity: 'info', code: 'LIBRARY_GAP', path: 'meta.library_gaps[0]', message: "the script wants an object 'castle' that the asset library does not have; it was left out", kind: 'object', name: 'castle' }],
      },
      busy: false,
    })
    await renderScreen(<ProblemsDrawer />, { api })
    expect(await screen.findByLabelText('warning', {}, slow)).toBeInTheDocument()
    act(() => useIgnored.getState().ignore('story-50s', 'object:castle'))
    await waitFor(() => expect(screen.queryByLabelText('warning')).not.toBeInTheDocument())
    expect(screen.getByText(/no problems/i)).toBeInTheDocument()
  })
})

describe('renaming a scene', () => {
  it('keeps the gaps that name it', async () => {
    const user = userEvent.setup()
    const api = await open([castle])
    await renderScreen(<Inspector />, { api })
    const name = await screen.findByRole('textbox', { name: 'Scene name' })
    await user.clear(name)
    await user.type(name, 'street')
    expect(gapsLeft()).toEqual([{ kind: 'object', name: 'castle', scenes: ['street', SCENES[1]] }])
    expect(await screen.findByRole('list', { name: 'Missing from the library' }, slow)).toBeInTheDocument()
  })
})
